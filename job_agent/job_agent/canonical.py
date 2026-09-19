"""Canonical job identity for persistent deduplication.

A single job may be published by an employer and several aggregators with
different URLs, slightly different titles, or slightly different location
spellings. We derive a deterministic canonical key from normalized
(company, title, location) and use it as the identity for merging duplicate
rows. Original URLs and provenance are always retained separately.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

# Company suffixes and legal forms removed for identity purposes.
_COMPANY_STOP_WORDS = {
    "gmbh",
    "ag",
    "inc",
    "inc.",
    "ltd",
    "ltd.",
    "llc",
    "corp",
    "corporation",
    "co",
    "company",
    "group",
    "holding",
    "s.a",
    "sa",
    "sarl",
    "sl",
    "b.v",
    "bv",
    "n.v",
    "nv",
    "oü",
    "ou",
}

# Title noise that commonly differs between copies of the same posting.
_TITLE_NORMALIZATIONS = [
    (re.compile(r"[\(\[].*?[\)\]]", re.IGNORECASE), ""),  # (m/w/d), (f/m/d), [...]
    (re.compile(r"\s+m/w/d\s*$", re.IGNORECASE), ""),  # trailing m/w/d
    (re.compile(r"\s+f/m/d\s*$", re.IGNORECASE), ""),
    (re.compile(r"\s+all genders\s*$", re.IGNORECASE), ""),
    (re.compile(r"[-–—_]+", re.UNICODE), " "),
    (re.compile(r"\s{2,}"), " "),
]

# Location normalization: keep city distinctions meaningful, but unify the
# common spelling/country differences between sources.
_LOCATION_ALIASES = {
    "münchen": "munich",
    "muenchen": "munich",
    "berlin": "berlin",
    "germany": "germany",
    "deutschland": "germany",
    "de": "germany",
    "remote": "remote",
    "hybrid": "hybrid",
    "online": "remote",
    "wfh": "remote",
    "work from home": "remote",
    "espana": "spain",
    "spain": "spain",
    "es": "spain",
    "austria": "austria",
    "at": "austria",
    "switzerland": "switzerland",
    "ch": "switzerland",
    "netherlands": "netherlands",
    "nl": "netherlands",
    "denmark": "denmark",
    "dk": "denmark",
    "sweden": "sweden",
    "se": "sweden",
    "norway": "norway",
    "no": "norway",
    "united kingdom": "uk",
    "england": "uk",
    "london": "london",
    "uk": "uk",
}


def _normalize(value: str) -> str:
    """NFKC normalize, casefold, and collapse whitespace while preserving
    letters of all Unicode scripts used in job text. Does NOT transliterate."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s{2,}", " ", value)


def normalize_company(raw: str | None) -> str:
    """Normalize the employer name for identity purposes."""
    if not raw:
        return ""
    name = _normalize(raw)
    name = re.sub(r"[^\w\s&.]", " ", name)  # drop punctuation but keep & . -
    tokens = [t for t in name.split() if t]
    tokens = [t for t in tokens if t.strip(".") not in _COMPANY_STOP_WORDS]
    return " ".join(tokens)


def normalize_title(raw: str | None) -> str:
    """Normalize the job title for identity purposes."""
    if not raw:
        return ""
    title = _normalize(raw)
    for pattern, repl in _TITLE_NORMALIZATIONS:
        title = pattern.sub(repl, title)
    title = title.strip()
    # collapse repeated separators introduced by removal
    title = re.sub(r"\s{2,}", " ", title)
    return title


def normalize_location(raw: str | None) -> str:
    """Normalize a single location token (used for keys and classification)."""
    if not raw:
        return ""
    loc = _normalize(raw)
    for token in re.split(r"[,\/|]", loc):
        token = token.strip()
        if token in _LOCATION_ALIASES:
            return _LOCATION_ALIASES[token]
    return loc


def split_locations(raw: str | None) -> list[str]:
    """Return normalized distinct location tokens from a raw location string."""
    if not raw:
        return []
    out: list[str] = []
    for token in re.split(r"[,\/|;]", raw):
        t = normalize_location(token)
        if t and t not in out:
            out.append(t)
    return out


def _key(company: str, title: str, location: str) -> str:
    return "\x1f".join([company, title, location])


def canonical_key(company: str | None, title: str | None, location: str | None) -> str:
    """Deterministic canonical identity for a job posting.

    ``location`` may be a list of tokens or a raw string; we normalize the most
    specific city-like token when several are present (first Africa/Europe
    generic tokens are dropped, the first concrete one wins).
    """
    company_n = normalize_company(company)
    title_n = normalize_title(title)

    tokens = split_locations(location)
    # Prefer a concrete city/country token over generic "germany"/"remote"
    # when multiple locations are listed; keep determinism by taking the first
    # token that is not a country/remote qualifier, else the first token.
    concrete = [
        t
        for t in tokens
        if t
        not in {
            "germany",
            "remote",
            "hybrid",
            "uk",
            "spain",
            "austria",
            "switzerland",
            "netherlands",
            "denmark",
            "sweden",
            "norway",
        }
    ]
    loc_n = " ".join(concrete if concrete else tokens[:1]) if tokens else ""

    return _key(company_n, title_n, loc_n)


def canonical_url(raw: str | None) -> str:
    """Deterministic canonical form of a posting URL (identity helper).

    Scheme/host are lowercased, credentials and default ports are dropped, the
    path trailing slash is removed and redundant fragments are stripped. The
    result is stable enough to act as a secondary dedup signal across sources
    that publish the same posting page.
    """
    if not raw:
        return ""
    value = raw.strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if "@" in netloc:
        netloc = netloc.rpartition("@")[2]
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def dedupe_candidates(iterable: Iterable[str]) -> list[str]:
    """Stable-order unique keys."""
    seen: set[str] = set()
    out = []
    for k in iterable:
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out
