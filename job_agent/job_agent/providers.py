"""Native discovery providers.

A provider is a source-native discovery adapter: it talks to a structured
public feed/API directly instead of routing the source through a generic
search engine. Whatever a provider returns flows through the exact same
normalize / dedup / score / persist pipeline as every other discovery path —
providers never score, never write to the database, and never touch the
config.

Contract
--------
A catalog entry selects a provider through its ``provider`` field
(``sources_catalog.yaml``); :data:`PROVIDERS` maps that name to an
implementation. :func:`provider_for` resolves an entry to its provider (or
``None`` when the entry has no/unknown provider — unknown names silently fall
back to the generic search-engine path).

``fetch()`` returns a :class:`ProviderResult` that keeps the run content-free:
identified jobs plus request/hit/latency counters and count-only errors.
Failures are isolated per provider and reported, never aborting a run.

Health diagnosis (:func:`classify_provider_health`) is diagnostic only:
a provider is never disabled automatically and its outcome never influences
fit scoring.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import httpx

from job_agent.sources import DEFAULT_TIMEOUT, MAX_RESPONSE_BYTES, UA

from .logging_setup import get_logger, log_event
from .models import Job

log = get_logger("providers")

REMOTEOK_FEED_URL = "https://remoteok.com/api"
REMOTIVE_FEED_URL = "https://remotive.com/api/remote-jobs"

_DEFAULT_PROVIDER_LIMIT = 100
_MAX_PROVIDER_LIMIT = 250


class ProviderStatus(StrEnum):
    OK = "ok"
    ZERO_YIELD = "zero_yield"
    FAILED = "failed"


class ProviderHealth(StrEnum):
    HEALTHY = "HEALTHY"
    ZERO_YIELD = "ZERO_YIELD"
    FAILED = "FAILED"
    DISABLED = "DISABLED"
    NOT_TESTED = "NOT_TESTED"


class ProviderError(RuntimeError):
    """A provider-level failure; does not abort a discovery run."""


class MalformedProviderPayload(ProviderError):
    """The feed responded successfully but with an unexpected shape."""


@dataclass(frozen=True)
class ProviderResult:
    """Content-free outcome of one provider fetch."""

    source_id: str
    provider: str
    jobs: tuple[Job, ...]
    status: ProviderStatus
    requests: int = 0
    hits: int = 0
    duplicates: int = 0
    errors: tuple[str, ...] = ()
    latency_ms: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "provider": self.provider,
            "status": self.status.value,
            "requests": self.requests,
            "hits": self.hits,
            "candidate_jobs": len(self.jobs),
            "duplicates": self.duplicates,
            "errors": list(self.errors),
            "latency_ms": self.latency_ms,
        }

    def to_run_stats(self) -> ProviderRunStats:
        return ProviderRunStats(
            source_id=self.source_id,
            provider=self.provider,
            status=self.status.value,
            requests=self.requests,
            hits=self.hits,
            candidate_jobs=len(self.jobs),
            duplicates=self.duplicates,
            errors=self.errors,
            latency_ms=self.latency_ms,
        )


@dataclass(frozen=True)
class ProviderRunStats:
    """Aggregate-only provider run for reports/checkpoints (never content)."""

    source_id: str
    provider: str
    status: str
    requests: int = 0
    hits: int = 0
    candidate_jobs: int = 0
    duplicates: int = 0
    errors: tuple[str, ...] = ()
    latency_ms: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "provider": self.provider,
            "status": self.status,
            "requests": self.requests,
            "hits": self.hits,
            "candidate_jobs": self.candidate_jobs,
            "duplicates": self.duplicates,
            "errors": list(self.errors),
            "latency_ms": self.latency_ms,
        }


class DiscoveryProvider(ABC):
    """Base class for a source-native discovery provider."""

    name = "base"

    def __init__(self, *, http=None) -> None:
        # ``http`` is injectable for hermetic tests: callable(url) ->
        # (status_code, content_bytes, headers_dict).
        self._http = http or _default_http

    @abstractmethod
    def fetch(
        self,
        *,
        source_id: str,
        entry=None,
        limit: int = _DEFAULT_PROVIDER_LIMIT,
        search: str | None = None,
    ) -> ProviderResult:
        raise NotImplementedError


def _default_http(url: str) -> tuple[int, bytes, dict[str, str]]:
    resp = httpx.get(
        url,
        headers={"User-Agent": UA},
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
    )
    return resp.status_code, resp.content, dict(resp.headers)


class HttpFeedProvider(DiscoveryProvider):
    """Base for a single-URL JSON feed provider with bounded responses."""

    feed_url = ""
    currency: str | None = None

    def _get_json(self, url: str) -> object:
        status, content, _headers = self._http(url)
        if status >= 400:
            raise ProviderError(f"{self.name}: HTTP {status} for {url}")
        if len(content) > MAX_RESPONSE_BYTES:
            raise ProviderError(f"{self.name}: response too large")
        import json

        try:
            return json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise MalformedProviderPayload(f"{self.name}: invalid JSON payload") from exc


def _empty_location(location: str) -> bool:
    return not location or set(location.strip()) <= {"🌍", "📍"}


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    from bs4 import BeautifulSoup

    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        try:
            return datetime.fromtimestamp(float(text))
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


_K_NUMBER = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*[kK]?")


def _parse_salary_text(value: object) -> tuple[float | None, float | None]:
    """Parse a salary token into (min, max).

    Accepts ``60``, ``"$60k"``, ``"70k - 80k"``, ``"€65.000"``. Unparseable
    or empty input yields ``(None, None)`` — salary is optional evidence, never
    a hard failure.
    """
    if value is None:
        return (None, None)
    if isinstance(value, (int, float)):
        return (float(value), None)
    text = str(value).strip().replace("€", "").replace("$", "").replace("£", "")
    if not text:
        return (None, None)
    if re.fullmatch(r"\d{1,3}\.\d{3}(?:\d{3})*", text):
        return (float(text.replace(".", "")), None)  # European thousands: "65.000"
    if set(text) <= set("0123456789.,"):
        try:
            return (float(text.replace(",", "")), None)
        except ValueError:
            return (None, None)
    nums: list[float] = []
    for part in re.split(r"[-–/]", text):
        part = part.strip()
        if not part:
            continue
        match = _K_NUMBER.fullmatch(part)
        if not match:
            continue
        try:
            val = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if "k" in part or "K" in part:
            val *= 1000
        nums.append(val)
    if not nums:
        return (None, None)
    if len(nums) == 1:
        return (nums[0], None)
    return (min(nums), max(nums))


class RemoteOkProvider(HttpFeedProvider):
    """RemoteOK board feed (``https://remoteok.com/api``).

    Returns a compact JSON array of the latest ~100 remote postings; index 0 is
    a ``{last_updated, legal}`` metadata object and is skipped. The feed is
    compact (title/company/location/tags/salary/url) — no full description.
    """

    name = "remoteok"
    feed_url = REMOTEOK_FEED_URL

    def fetch(
        self,
        *,
        source_id: str,
        entry=None,
        limit: int = _DEFAULT_PROVIDER_LIMIT,
        search: str | None = None,
    ) -> ProviderResult:
        start = time.monotonic()
        errors: list[str] = []
        status = ProviderStatus.OK
        try:
            payload = self._get_json(self.feed_url)
            jobs = self._parse_payload(payload, source_id=source_id, entry=entry, search=search, limit=limit)
        except ProviderError as exc:
            status = ProviderStatus.FAILED
            errors = [str(exc)[:200]]
            jobs = []
        latency_ms = int((time.monotonic() - start) * 1000)
        hits = len(jobs)
        if status == ProviderStatus.OK and not jobs:
            status = ProviderStatus.ZERO_YIELD
        result = ProviderResult(
            source_id=source_id,
            provider=self.name,
            jobs=tuple(jobs),
            status=status,
            requests=1,
            hits=hits,
            duplicates=0,
            errors=tuple(errors),
            latency_ms=latency_ms,
        )
        log_event(log, "provider_fetch", source=source_id, provider=self.name, status=status.value, hits=hits)
        return result

    def _parse_payload(self, payload: object, *, source_id: str, entry, search: str | None, limit: int) -> list[Job]:
        if not isinstance(payload, list):
            raise MalformedProviderPayload(f"{self.name}: expected a JSON array")
        out: list[Job] = []
        seen: set[str] = set()
        for x in payload:
            if len(out) >= limit:
                break
            if not isinstance(x, dict):
                continue
            title = (x.get("position") or "").strip()
            if not title:
                continue  # index-0 metadata object and junk rows
            ident = str(x.get("id") or x.get("slug") or x.get("url") or "")
            if ident in seen:
                continue  # duplicate source id within the feed
            if not ident:
                continue  # no stable id / url -> no provenance-able identity
            seen.add(ident)
            if search and not _matches_search(title, x.get("tags"), search):
                continue
            slug = x.get("slug")
            url = x.get("url") or (f"https://remoteok.com/l/{slug}" if slug else "")
            apply_url = x.get("apply_url") or url or None
            company = (x.get("company") or "").strip()
            if not company and entry is not None:
                company = getattr(entry, "name", "") or ""
            location = (x.get("location") or "").strip()
            if _empty_location(location):
                location = "Worldwide"
            tags = x.get("tags")
            description = " ".join(tags) if isinstance(tags, list) else (x.get("description") or "")
            salary_min, salary_max = _parse_salary_text(x.get("salary_min")), _parse_salary_text(x.get("salary_max"))
            minv = salary_min[0] if salary_min else None
            maxv = salary_max[0] if salary_max else None
            if minv is None and maxv is None:
                minextra, maxextra = _parse_salary_text(x.get("compensation") or x.get("salary"))
                minv = minv if minv is not None else minextra
                maxv = maxv if maxv is not None else maxextra
            out.append(
                Job(
                    id=f"remoteok:{ident}",
                    title=title,
                    company=company or "RemoteOK",
                    url=url,
                    apply_url=apply_url,
                    source=self.name,
                    source_type="structured_data",
                    canonical_url=url or None,
                    location=location,
                    description=description,
                    date_posted=_parse_dt(x.get("epoch") if x.get("epoch") is not None else x.get("date")),
                    salary_min=minv,
                    salary_max=maxv,
                    salary_currency=self.currency or "USD",
                    raw={"provider": self.name, "slug": slug},
                )
            )
        return out


class RemotiveProvider(HttpFeedProvider):
    """Remotive remote-jobs feed (``https://remotive.com/api/remote-jobs``).

    Returns ``{"jobs": [...]}`` with full descriptions, locations, salary
    strings and tags. An optional server-side ``search`` filter is honored; a
    missing optional field yields its neutral default, never a parse error.
    """

    name = "remotive"
    feed_url = REMOTIVE_FEED_URL

    def fetch(
        self,
        *,
        source_id: str,
        entry=None,
        limit: int = _DEFAULT_PROVIDER_LIMIT,
        search: str | None = None,
    ) -> ProviderResult:
        start = time.monotonic()
        errors: list[str] = []
        status = ProviderStatus.OK
        url = self.feed_url
        if search:
            import urllib.parse

            url = f"{self.feed_url}?search={urllib.parse.quote(search)}"
        try:
            payload = self._get_json(url)
            jobs = self._parse_payload(payload, entry=entry, limit=limit)
        except ProviderError as exc:
            status = ProviderStatus.FAILED
            errors = [str(exc)[:200]]
            jobs = []
        latency_ms = int((time.monotonic() - start) * 1000)
        if status == ProviderStatus.OK and not jobs:
            status = ProviderStatus.ZERO_YIELD
        result = ProviderResult(
            source_id=source_id,
            provider=self.name,
            jobs=tuple(jobs),
            status=status,
            requests=1,
            hits=len(jobs),
            duplicates=0,
            errors=tuple(errors),
            latency_ms=latency_ms,
        )
        log_event(log, "provider_fetch", source=source_id, provider=self.name, status=status.value, hits=len(jobs))
        return result

    def _parse_payload(self, payload: object, *, entry, limit: int) -> list[Job]:
        if not isinstance(payload, dict):
            raise MalformedProviderPayload(f"{self.name}: expected a JSON object")
        items = payload.get("jobs")
        if items is None and "jobs" not in payload:
            raise MalformedProviderPayload(f"{self.name}: missing 'jobs' array")
        if not isinstance(items, list):
            raise MalformedProviderPayload(f"{self.name}: 'jobs' must be an array")
        out: list[Job] = []
        seen: set[object] = set()
        for x in items:
            if len(out) >= limit:
                break
            if not isinstance(x, dict):
                continue
            title = (x.get("title") or "").strip()
            if not title:
                continue
            ident = x.get("id")
            if ident is None:
                ident = x.get("url") or x.get("title")
            if ident in seen:
                continue
            seen.add(ident)
            url = (x.get("url") or "").strip()
            company = (x.get("company_name") or "").strip()
            if not company and entry is not None:
                company = getattr(entry, "name", "") or ""
            location = (x.get("candidate_required_location") or "").strip()
            if _empty_location(location):
                location = "Worldwide"
            salary_min, salary_max = _parse_salary_text(x.get("salary"))
            tags = x.get("tags")
            tag_text = " ".join(tags) if isinstance(tags, list) else ""
            out.append(
                Job(
                    id=f"remotive:{ident}",
                    title=title,
                    company=company or "Remotive",
                    url=url,
                    apply_url=url or None,
                    source=self.name,
                    source_type="structured_data",
                    canonical_url=url or None,
                    location=location,
                    date_posted=_parse_dt(x.get("publication_date")),
                    description=(_strip_html(x.get("description") or "") + " " + tag_text).strip(),
                    employment_type=x.get("job_type"),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency="USD" if salary_min is not None or salary_max is not None else None,
                    raw={"provider": self.name, "category": x.get("category")},
                )
            )
        return out


def _matches_search(title: str, tags: object, search: str) -> bool:
    hay = f"{title} {' '.join(tags) if isinstance(tags, list) else ''}".casefold()
    term = search.strip().lower()
    return bool(term) and term in hay


PROVIDERS: dict[str, type[DiscoveryProvider]] = {
    "remoteok": RemoteOkProvider,
    "remotive": RemotiveProvider,
}


def provider_for(entry) -> DiscoveryProvider | None:
    """Resolve a catalog entry to its native provider, or ``None``.

    An entry without a ``provider`` field, or with an unknown provider name,
    resolves to ``None`` and keeps using the generic search-engine fallback
    (backward compatible).
    """
    if entry is None:
        return None
    name = getattr(entry, "provider", None)
    provider_cls = PROVIDERS.get(name) if name else None
    return provider_cls() if provider_cls is not None else None


def provider_health_for(entry, last_run: dict | None) -> ProviderHealth:
    """Classify the diagnostic health state of a provider-backed source.

    Pure, deterministic, and purely diagnostic: the outcome never disables a
    source and never affects fit scoring.
    """
    if entry is None or not getattr(entry, "provider", None):
        return ProviderHealth.DISABLED
    if not getattr(entry, "enabled", True):
        return ProviderHealth.DISABLED
    if last_run is None:
        return ProviderHealth.NOT_TESTED
    status = last_run.get("status")
    if status == "failed":
        return ProviderHealth.FAILED
    if status == "ok" and int(last_run.get("hits") or 0) > 0:
        return ProviderHealth.HEALTHY
    if status == "ok":
        return ProviderHealth.ZERO_YIELD
    return ProviderHealth.NOT_TESTED


__all__ = [
    "DiscoveryProvider",
    "HttpFeedProvider",
    "MalformedProviderPayload",
    "ProviderError",
    "ProviderHealth",
    "ProviderResult",
    "ProviderRunStats",
    "ProviderStatus",
    "PROVIDERS",
    "RemoteOkProvider",
    "RemotiveProvider",
    "provider_for",
    "provider_health_for",
]
