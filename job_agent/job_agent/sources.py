"""Job source taxonomy and adapters.

Every source is a subclass of :class:`Source` carrying a ``kind`` from the
taxonomy below. Adapters are intentionally narrow (fetch → list[Job]
with normalization applied. A source must never touch the database, config,
policy, or any other source — failures are isolated per source by the caller.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .models import Job

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36 PersonalJobAgent/0.2"

MAX_RESPONSE_BYTES = 5_000_000
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0, read=20.0)


class SourceKind(StrEnum):
    API = "api"
    RSS_JSON = "rss_json"
    MCP = "mcp"
    STRUCTURED_PAGE = "structured_page"
    ATS_BOARD = "ats_board"
    BROWSER_AUTOMATION = "browser_automation"
    RESTRICTED = "restricted"


class SourceError(RuntimeError):
    """A source-level failure. Does not abort the whole scan."""


class Source:
    """Base class. Subclasses implement ``fetch`` returning raw Jobs; the
    caller runs normalization + scoring via the pipeline."""

    kind: SourceKind = SourceKind.API
    name = "base"

    def fetch(self) -> list[Job]:
        raise NotImplementedError


class ApiSource(Source):
    kind = SourceKind.API
    base_url: str | None = None

    def _get_json(self, url: str) -> dict | list:
        headers = {"User-Agent": UA}
        resp = httpx.get(url, headers=headers, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise SourceError(f"{self.name}: response too large")
        return resp.json()


class AtsBoardSource(ApiSource):
    """Board-style ATS API source (Greenhouse/Lever/Ashby/SmartRecruiters/
    Workable)."""

    kind = SourceKind.ATS_BOARD


class RssJsonSource(Source):
    """Feed-based source (RSS/JSON job feeds)."""

    kind = SourceKind.RSS_JSON


class StructuredPageSource(Source):
    kind = SourceKind.STRUCTURED_PAGE


class MCPSource(Source):
    kind = SourceKind.MCP


class BrowserAutomationSource(Source):
    kind = SourceKind.BROWSER_AUTOMATION


class RestrictedSource(Exception):
    """Marker for sources we deliberately do not access (auth/paywall/ToS)."""


class GreenhouseSource(AtsBoardSource):
    name = "greenhouse"

    def __init__(self, token: str) -> None:
        self.token = token

    def fetch(self) -> list[Job]:
        per_page = 100
        page = 1
        out: list[Job] = []
        seen_ids = set()
        while True:
            url = f"https://boards-api.greenhouse.io/v1/boards/{self.token}/jobs?content=true&per_page={per_page}&page={page}"
            data = self._get_json(url)
            jobs_dict = data if isinstance(data, dict) else {}
            jobs = jobs_dict.get("jobs") or []
            if not jobs:
                break
            for x in jobs:
                jid = x.get("id")
                if jid is None:
                    continue
                # deduplicate within same fetch
                if jid in seen_ids:
                    continue
                seen_ids.add(jid)
                location = (x.get("location") or {}).get("name", "") if isinstance(x.get("location"), dict) else ""
                out.append(
                    Job(
                        id=f"gh:{jid}",
                        title=x.get("title", ""),
                        company=self.token,
                        url=x.get("absolute_url", ""),
                        apply_url=x.get("absolute_url"),
                        source=self.name,
                        source_type=self.kind.value,
                        location=location or "",
                        date_posted=_dt(x.get("updated_at")),
                        description=_html(x.get("content", "")),
                        raw=x,
                    )
                )
            # stop if less than a full page
            if len(jobs) < per_page:
                break
            page += 1
        return out


class LeverSource(AtsBoardSource):
    name = "lever"

    def __init__(self, site: str) -> None:
        self.site = site

    def fetch(self) -> list[Job]:
        url = f"https://api.lever.co/v0/postings/{self.site}?mode=json"
        data = self._get_json(url)
        items = data if isinstance(data, list) else []
        out: list[Job] = []
        for x in items:
            cats = x.get("categories", {}) or {}
            out.append(
                Job(
                    id=f"lever:{x['id']}",
                    title=x.get("text", ""),
                    company=self.site,
                    url=x.get("hostedUrl", ""),
                    apply_url=x.get("applyUrl"),
                    source=self.name,
                    source_type=self.kind.value,
                    location=(cats.get("location") or ""),
                    description=x.get("descriptionPlain", "") or _html(x.get("description", "")),
                    raw=x,
                )
            )
        return out


class AshbySource(AtsBoardSource):
    name = "ashby"

    def __init__(self, board: str) -> None:
        self.board = board

    def fetch(self) -> list[Job]:
        limit = 100
        offset = 0
        out: list[Job] = []
        seen = set()
        while True:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{self.board}?includeCompensation=true&limit={limit}&offset={offset}"
            data = self._get_json(url)
            items = data.get("jobs", []) if isinstance(data, dict) else []
            if not items:
                break
            for x in items:
                comp = x.get("compensation") or {}
                jid = x.get("jobUrl") or x.get("applyUrl") or x.get("title")
                job_id = f"ashby:{jid}"
                if job_id in seen:
                    continue
                seen.add(job_id)
                out.append(
                    Job(
                        id=job_id,
                        title=x.get("title", ""),
                        company=self.board,
                        url=x.get("jobUrl", ""),
                        apply_url=x.get("applyUrl"),
                        source=self.name,
                        source_type=self.kind.value,
                        location=x.get("location", "") or "",
                        description=x.get("descriptionPlain", "") or _html(x.get("descriptionHtml", "")),
                        salary_min=_num(comp.get("minValue")),
                        salary_max=_num(comp.get("maxValue")),
                        salary_currency=comp.get("currencyCode"),
                        raw=x,
                    )
                )
            # stop if we received fewer than limit items and no more pages
            if len(items) < limit:
                break
            offset += limit
            # safety guard against infinite loops
            if offset > 10000:
                break
        return out


class SmartRecruitersSource(AtsBoardSource):
    name = "smartrecruiters"

    def __init__(self, company: str) -> None:
        self.company = company

    def fetch(self) -> list[Job]:
        url = f"https://api.smartrecruiters.com/v1/companies/{self.company}/postings"
        data = self._get_json(url)
        out: list[Job] = []
        for x in data.get("content", []) if isinstance(data, dict) else []:
            ref = x.get("ref") or {}
            url_final = ref.get("jobAdUrl") or f"https://careers.smartrecruiters.com/{self.company}/{x.get('id', '')}"
            out.append(
                Job(
                    id=f"sr:{self.company}:{x.get('id')}",
                    title=x.get("name", ""),
                    company=self.company,
                    url=url_final,
                    apply_url=ref.get("applyUrl"),
                    source=self.name,
                    source_type=self.kind.value,
                    location=_sr_loc(x),
                    raw=x,
                )
            )
        return out


class WorkableSource(AtsBoardSource):
    """Workable jobs board via the public ATS `job board` endpoint.

    Workable exposes a JSON jobs endpoint per subdomain; individual boards may
    require an API key / disabling anonymous access. We use the public
    endpoint when available and surface clear source errors otherwise — a
    restricted board must never abort the scan.
    """

    name = "workable"

    def __init__(self, subdomain: str) -> None:
        self.subdomain = subdomain

    def fetch(self) -> list[Job]:
        url = f"https://{self.subdomain}.workable.com/api/v3/accounts/{self.subdomain}/jobs"
        headers = {"User-Agent": UA, "Accept": "application/json"}
        resp = httpx.get(url, headers=headers, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        if resp.status_code in (401, 403):
            raise SourceError(f"workable:{self.subdomain}: board requires authentication (restricted)")
        resp.raise_for_status()
        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise SourceError(f"{self.name}: response too large")
        data = resp.json()
        items = (data.get("jobs") if isinstance(data, dict) else data) or []
        if not isinstance(items, list):
            items = list(items)
        out: list[Job] = []
        for x in items:
            if not isinstance(x, dict):
                continue
            out.append(
                Job(
                    id=f"workable:{self.subdomain}:{x.get('id', str(x.get('shortcode') or x.get('title')))}",
                    title=x.get("title", ""),
                    company=self.subdomain,
                    url=x.get("url") or x.get("application_url") or "",
                    apply_url=x.get("application_url"),
                    source=self.name,
                    source_type=self.kind.value,
                    location=f"{x.get('city', '')} {x.get('country', '')}".strip(),
                    date_posted=_dt(x.get("published_on") or x.get("created_at")),
                    description=_html(x.get("description") or ""),
                    salary_min=_num(x.get("salary_min")),
                    salary_max=_num(x.get("salary_max")),
                    salary_currency=x.get("salary_currency"),
                    raw=x,
                )
            )
        return out


class DirectPageSource(StructuredPageSource):
    name = "direct"

    def __init__(self, url: str) -> None:
        self.url = url

    def fetch(self) -> list[Job]:
        headers = {"User-Agent": UA}
        max_attempts = 3
        backoff_factor = 0.5
        for attempt in range(max_attempts):
            try:
                resp = httpx.get(self.url, headers=headers, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
                resp.raise_for_status()
                if len(resp.content) > MAX_RESPONSE_BYTES:
                    raise SourceError(f"{self.name}: page too large")
                break
            except (httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                if attempt == max_attempts - 1:
                    raise
                sleep_time = backoff_factor * (2 ** attempt)
                time.sleep(sleep_time)
                continue
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (502, 503, 504) and attempt < max_attempts - 1:
                    sleep_time = backoff_factor * (2 ** attempt)
                    time.sleep(sleep_time)
                    continue
                raise
        else:
            # This block runs if we didn't break (i.e., all attempts failed)
            # Should not happen because we raise in the last attempt
            raise
        soup = BeautifulSoup(resp.text, "html.parser")
        out: list[Job] = []
        for node in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(node.string or "")
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for x in items:
                if not isinstance(x, dict) or x.get("@type") != "JobPosting":
                    continue
                org = (x.get("hiringOrganization") or {}).get("name") or urlparse(self.url).netloc
                ident = x.get("identifier") or {}
                jid = ident.get("value") or x.get("url") or x.get("title")
                loc = x.get("jobLocation") or {}
                addr = (loc.get("address") if isinstance(loc, dict) else {}) or {}
                location = ", ".join(
                    [
                        p
                        for p in [
                            addr.get("addressLocality"),
                            addr.get("addressRegion"),
                            addr.get("addressCountry"),
                        ]
                        if p
                    ]
                )
                out.append(
                    Job(
                        id=f"jsonld:{org}:{jid}",
                        title=x.get("title", ""),
                        company=org,
                        url=x.get("url") or self.url,
                        apply_url=x.get("url") or self.url,
                        source=self.name,
                        source_type=self.kind.value,
                        location=location,
                        description=_html(x.get("description", "")),
                        date_posted=_dt(x.get("datePosted")),
                        employment_type=x.get("employmentType"),
                        raw=x,
                    )
                )
        return out


class RssJsonAdapter(RssJsonSource):
    """Fetch a JSON feed (array) or RSS XML and normalize to jobs.

    JSON feed contract (also produced by warpjobs-style boards):
    each item may use common keys: ``title``, ``link``/``url``, ``description``,
    ``company``/``organization``, ``location``, ``pubDate``/``date``,
    ``salary_min``/``salary_max``/``salary_currency``.
    """

    name = "rss_json"

    def __init__(self, label: str, url: str) -> None:
        self.label = label
        self.url = url

    def fetch(self) -> list[Job]:
        resp = httpx.get(self.url, headers={"User-Agent": UA}, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise SourceError(f"{self.name}:{self.label}: feed too large")
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype:
            return self._parse_json(resp.json())
        return self._parse_rss(resp.text)

    def _parse_json(self, data: object) -> list[Job]:
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("jobs") or data.get("items") or []
        out: list[Job] = []
        for i, x in enumerate(items):
            if not isinstance(x, dict):
                continue
            company = (x.get("company") or x.get("organization") or "").strip()
            link = x.get("link") or x.get("url") or ""
            out.append(
                Job(
                    id=f"rss:{self.label}:{x.get('id') or x.get('guid') or i}",
                    title=(x.get("title") or "").strip(),
                    company=company,
                    url=link,
                    apply_url=link or None,
                    source=self.name,
                    source_type=self.kind.value,
                    location=(x.get("location") or x.get("city") or x.get("country") or ""),
                    date_posted=_dt(x.get("pubDate") or x.get("date") or x.get("datePosted")),
                    description=(x.get("description") or x.get("summary") or ""),
                    salary_min=_num(x.get("salary_min") or x.get("salaryMin")),
                    salary_max=_num(x.get("salary_max") or x.get("salaryMax")),
                    salary_currency=x.get("salary_currency") or x.get("currency"),
                    raw=x,
                )
            )
        return out

    def _parse_rss(self, text: str) -> list[Job]:
        root = BeautifulSoup(text, "xml")
        out: list[Job] = []

        def _text(node) -> str:
            return node.get_text(strip=True) if node is not None else ""

        for i, item in enumerate(root.find_all("item") or []):
            title = _text(item.find("title"))
            link = _text(item.find("link"))
            desc = _text(item.find("description"))
            pub = _text(item.find("pubDate"))
            company = ""
            dc = item.find("dc:creator")
            if dc:
                company = dc.get_text(strip=True)
            out.append(
                Job(
                    id=f"rss:{self.label}:{i}",
                    title=title,
                    company=company,
                    url=link,
                    apply_url=link or None,
                    source=self.name,
                    source_type=self.kind.value,
                    date_posted=_dt(pub),
                    description=desc,
                    raw={"rss_index": i},
                )
            )
        return out


def _html(s: str) -> str:
    if not s:
        return ""
    return BeautifulSoup(s, "html.parser").get_text(" ", strip=True)


def _dt(v: object) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    text = str(v)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    # RFC 1123 / common RSS dates
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _num(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("€", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return None


def _sr_loc(x: dict) -> str:
    loc = x.get("location") or {}
    if isinstance(loc, str):
        return loc
    return ", ".join([str(v) for v in [loc.get("city"), loc.get("region"), loc.get("country")] if v])
