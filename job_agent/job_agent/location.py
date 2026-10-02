"""Deterministic location intelligence.

A job's location string is mapped onto a small, explicit *tier* model so that
ranking can reason about how local a posting is without ever turning a city
into a binary filter. Remote is its own tier and is never treated as a place;
an unrecognized location resolves to ``unknown`` and never silently becomes a
preferred city.

Pure functions only: no I/O, no network, no model calls.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

_WS = re.compile(r"\s+")


class LocationTier(StrEnum):
    """Ordered preference tiers (higher rank = preferred)."""

    # Tier names describe relative proximity, not a specific geography. The
    # vocabularies behind them are real place names; the *ordering* is a
    # generic commute ladder (city core -> wider metro -> region -> country).
    CITY_CORE = "A_city_core"
    METRO = "B_metro"
    REGION = "C_region"
    COUNTRY = "D_country"
    REMOTE = "F_remote"
    EUROPE = "E_europe"
    UNKNOWN = "unknown"


# Explicit ranking so 'best tier among tokens' is deterministic and auditable.
TIER_RANK: dict[LocationTier, int] = {
    LocationTier.CITY_CORE: 6,
    LocationTier.METRO: 5,
    LocationTier.REGION: 4,
    LocationTier.COUNTRY: 3,
    LocationTier.REMOTE: 2,
    LocationTier.EUROPE: 1,
    LocationTier.UNKNOWN: 0,
}


class RemoteScope(StrEnum):
    NONE = "none"
    DE = "de"
    EU = "eu"
    WORLDWIDE = "worldwide"
    UNKNOWN = "unknown"


class LocationWeights(BaseModel):
    """Ranking contribution of each proximity tier.

    Defaults rank the most local tier highest and fall off outwards, which is
    a generic commute-preference ladder. Every value is configurable.
    """

    exact_city: float = 1.00
    metro: float = 0.92
    regional: float = 0.88
    country: float = 0.85
    remote: float = 0.85
    europe: float = 0.65
    unknown: float = 0.60
    relocate_without_offer: float = 0.25  # non-willing applicant, international role


@dataclass(frozen=True)
class LocationParse:
    """Deterministic parse result, kept explainable and content-free."""

    location_raw: str
    location_normalized: str
    tier: LocationTier
    remote_scope: RemoteScope
    region: str
    confidence: float
    matched: tuple[str, ...]


# --- Tier vocabularies -----------------------------------------------------

_MUNICH_CORE = (
    # City and immediate surroundings / suburbs commonly listed by employers.
    "munich",
    "münchen",
    "muenchen",
    "garching",
    "taufkirchen",
    "ottobrunn",
    "unterhaching",
    "oberhaching",
    "ismaning",
    "martinsried",
    "planegg",
    "grünwald",
    "gruenwald",
    "pullach",
    "neubiberg",
    "haar",
    "poing",
    "neufahrn",
    "oberpfaffenhofen",
    "weßling",
    "wessling",
    "aschheim",
    "kirchheim",
    "feldkirchen",
    "putzbrunn",
    "hohenbrunn",
    "karlsfeld",
    "dachau",
    "germering",
    "fürstenfeldbruck",
    "fuerstenfeldbruck",
    "starnberg",
    "gräfelfing",
    "graefelfing",
    "unterföhring",
    "unterfoehring",
    "hallbergmoos",
    "grasbrunn",
    "brunnthal",
    "sauerlach",
    "höhenkirchen",
    "hoehenkirchen",
    "gilching",
    "olching",
)

_MUNICH_METRO = (
    "freising",
    "erding",
    "landshut",
    "rosenheim",
    "holzkirchen",
    "weilheim",
    "miesbach",
    "bad tölz",
    "bad toelz",
    "wolfratshausen",
    "landsberg",
    "pfaffenhofen",
    "ebersberg",
    "grafing",
    "murnau",
    "garmisch",
)

_BAVARIA = (
    "augsburg",
    "ingolstadt",
    "regensburg",
    "nuremberg",
    "nürnberg",
    "nuernberg",
    "fürth",
    "fuerth",
    "erlangen",
    "würzburg",
    "wuerzburg",
    "ulm",
    "passau",
    "bayreuth",
    "bamberg",
    "kempten",
    "straubing",
    "amberg",
    "coburg",
    "hof",
    "ansbach",
    "schweinfurt",
    "memmingen",
    "bavaria",
    "bayern",
)

_GERMANY = (
    "germany",
    "deutschland",
    "berlin",
    "hamburg",
    "münster",
    "muenster",
    "frankfurt",
    "wiesbaden",
    "mainz",
    "stuttgart",
    "karlsruhe",
    "mannheim",
    "heidelberg",
    "cologne",
    "köln",
    "koeln",
    "düsseldorf",
    "duesseldorf",
    "dusseldorf",
    "bonn",
    "aachen",
    "essen",
    "dortmund",
    "bochum",
    "duisburg",
    "wuppertal",
    "bielefeld",
    "münchengladbach",
    "leipzig",
    "dresden",
    "chemnitz",
    "halle",
    "magdeburg",
    "erfurt",
    "jena",
    "göttingen",
    "goettingen",
    "hannover",
    "hanover",
    "braunschweig",
    "bremen",
    "kiel",
    "lübeck",
    "luebeck",
    "rostock",
    "freiburg",
    "tübingen",
    "tuebingen",
    "darmstadt",
    "kassel",
    "osnabrück",
    "osnabrueck",
    "potsdam",
    "saarbrücken",
    "saarbruecken",
    "konstanz",
)

_EUROPE = (
    "europe",
    "european union",
    "eu",
    "austria",
    "österreich",
    "oesterreich",
    "vienna",
    "wien",
    "graz",
    "linz",
    "salzburg",
    "switzerland",
    "schweiz",
    "zurich",
    "zürich",
    "zuerich",
    "zug",
    "basel",
    "bern",
    "geneva",
    "genève",
    "geneve",
    "lausanne",
    "france",
    "paris",
    "lyon",
    "toulouse",
    "sophia antipolis",
    "netherlands",
    "nederland",
    "holland",
    "amsterdam",
    "rotterdam",
    "utrecht",
    "eindhoven",
    "delft",
    "belgium",
    "brussels",
    "bruxelles",
    "antwerp",
    "leuven",
    "luxembourg",
    "london",
    "united kingdom",
    "uk",
    "england",
    "scotland",
    "manchester",
    "cambridge",
    "oxford",
    "bristol",
    "edinburgh",
    "cambridgeshire",
    "ireland",
    "dublin",
    "cork",
    "spain",
    "españa",
    "espana",
    "madrid",
    "barcelona",
    "valencia",
    "bilbao",
    "málaga",
    "malaga",
    "italy",
    "italia",
    "milan",
    "milano",
    "rome",
    "roma",
    "turin",
    "torino",
    "portugal",
    "lisbon",
    "lisboa",
    "porto",
    "poland",
    "polska",
    "warsaw",
    "warszawa",
    "krakow",
    "kraków",
    "wroclaw",
    "wrocław",
    "czech",
    "czechia",
    "prague",
    "praha",
    "brno",
    "slovakia",
    "bratislava",
    "hungary",
    "budapest",
    "romania",
    "bucharest",
    "bulgaria",
    "sofia",
    "greece",
    "athens",
    "denmark",
    "copenhagen",
    "københavn",
    "sweden",
    "stockholm",
    "gothenburg",
    "göteborg",
    "malmö",
    "malmo",
    "norway",
    "oslo",
    "finland",
    "helsinki",
    "espoo",
    "tampere",
    "estonia",
    "tallinn",
    "latvia",
    "riga",
    "lithuania",
    "vilnius",
    "croatia",
    "zagreb",
    "slovenia",
    "ljubljana",
    "serbia",
    "belgrade",
    "turkey",
    "istanbul",
    "ankara",
)

_INTERNATIONAL = (
    "united states",
    "usa",
    "u.s.",
    "new york",
    "san francisco",
    "bay area",
    "seattle",
    "boston",
    "austin",
    "los angeles",
    "chicago",
    "denver",
    "toronto",
    "vancouver",
    "montreal",
    "canada",
    "singapore",
    "tokyo",
    "japan",
    "seoul",
    "south korea",
    "shanghai",
    "beijing",
    "shenzhen",
    "hong kong",
    "taipei",
    "taiwan",
    "bangalore",
    "bengaluru",
    "hyderabad",
    "pune",
    "india",
    "dubai",
    "abu dhabi",
    "tel aviv",
    "israel",
    "sydney",
    "melbourne",
    "australia",
    "auckland",
    "new zealand",
    "cape town",
    "johannesburg",
    "south africa",
    "mexico city",
    "guadalajara",
    "brazil",
    "são paulo",
    "sao paulo",
    "argentina",
    "buenos aires",
    "chile",
    "santiago",
    "china",
    "korea",
    "uae",
)

_REMOTE_TERMS = (
    "remote",
    "fully remote",
    "100% remote",
    "remote-first",
    "remote first",
    "work from home",
    "home office",
    "homeoffice",
    "wfh",
    "telework",
    "virtual",
    "anywhere",
    "worldwide",
    "work from anywhere",
)

_REMOTE_GLOBAL = ("worldwide", "anywhere", "global", "work from anywhere")

_SPLIT_RE = re.compile(r"[,;/|()\[\]]|\s+[-–—]\s+")

_TOKEN_ALIASES = {
    "münchen": "munich",
    "muenchen": "munich",
    "nürnberg": "nuremberg",
    "nuernberg": "nuremberg",
    "köln": "cologne",
    "koeln": "cologne",
    "wien": "vienna",
    "zürich": "zurich",
    "zuerich": "zurich",
    "genève": "geneva",
    "geneve": "geneva",
    "wrocław": "wroclaw",
    "kraków": "krakow",
    "malmö": "malmo",
    "göteborg": "gothenburg",
    "københavn": "copenhagen",
    "lisboa": "lisbon",
    "españa": "spain",
}


def normalize_location(raw: str | None) -> str:
    """NFKC + casefold + whitespace collapse + alias folding, order preserved."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw).casefold()
    text = _WS.sub(" ", text).strip()
    parts = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
    out: list[str] = []
    for part in parts:
        out.append(_TOKEN_ALIASES.get(part, part))
    if out:
        return ", ".join(out)
    return _TOKEN_ALIASES.get(text, text)


_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _pattern(phrase: str) -> re.Pattern[str]:
    cached = _PATTERN_CACHE.get(phrase)
    if cached is None:
        cached = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)")
        _PATTERN_CACHE[phrase] = cached
    return cached


def _match_group(tokens: tuple[str, ...], vocab: tuple[str, ...]) -> list[str]:
    """Word-boundary phrase match over the normalized location string."""
    joined = " ".join(tokens)
    hits: list[str] = []
    for phrase in vocab:
        if _pattern(phrase).search(joined):
            hits.append(phrase)
    return hits


def _detect_remote(text: str, location_raw: str) -> bool:
    return any(t in text for t in _REMOTE_TERMS) or "hybrid" in location_raw


def parse_location(raw: str | None, *, remote_mode: str | None = None) -> LocationParse:
    """Parse a raw location string into a tiered, explainable result."""
    normalized = normalize_location(raw)
    tokens = tuple(t for t in re.split(r",\s*", normalized) if t)
    raw_text = (raw or "").casefold()

    if not tokens:
        if remote_mode == "remote":
            return LocationParse("", "", LocationTier.REMOTE, RemoteScope.UNKNOWN, "remote", 0.4, ())
        return LocationParse("", "", LocationTier.UNKNOWN, RemoteScope.NONE, "unknown", 0.0, ())

    matched: list[str] = []
    tier = LocationTier.UNKNOWN
    region = "unknown"
    confidence = 0.3

    for group, group_region, group_tier in (
        (_MUNICH_CORE, "munich", LocationTier.CITY_CORE),
        (_MUNICH_METRO, "munich", LocationTier.METRO),
        (_BAVARIA, "bavaria", LocationTier.REGION),
        (_GERMANY, "germany", LocationTier.COUNTRY),
        (_EUROPE, "europe", LocationTier.EUROPE),
        (_INTERNATIONAL, "international", LocationTier.EUROPE),
    ):
        hits = _match_group(tokens, group)
        if not hits:
            continue
        matched.extend(hits)
        if TIER_RANK[group_tier] > TIER_RANK[tier]:
            tier = group_tier
            region = group_region
            confidence = 1.0 if group_tier in (LocationTier.CITY_CORE, LocationTier.METRO) else 0.7
            if group_region == "international":
                confidence = 0.6

    is_remote = _detect_remote(raw_text, (raw or "").casefold()) or remote_mode == "remote"
    remote_scope = RemoteScope.NONE
    if is_remote:
        scope = RemoteScope.UNKNOWN
        if any(x in raw_text for x in _REMOTE_GLOBAL) or _match_group(tokens, _INTERNATIONAL):
            scope = RemoteScope.WORLDWIDE
        elif _match_group(tokens, _EUROPE):
            scope = RemoteScope.EU
        elif _match_group(tokens, _GERMANY + _BAVARIA + _MUNICH_CORE + _MUNICH_METRO) or "germany" in raw_text:
            scope = RemoteScope.DE
        remote_scope = scope
        # A remote/hybrid posting is its own tier: it is never treated as a
        # city, even when a location word is also present.
        tier = LocationTier.REMOTE
        region = "remote"
        confidence = 0.4 if scope is RemoteScope.UNKNOWN else 0.6

    if tier is LocationTier.UNKNOWN and remote_mode == "hybrid":
        # Hybrid without a recognized place: keep unknown, do not presume a city.
        confidence = 0.2

    return LocationParse(
        location_raw=raw or "",
        location_normalized=normalized,
        tier=tier,
        remote_scope=remote_scope,
        region=region,
        confidence=confidence,
        matched=tuple(dict.fromkeys(matched)),
    )


def tier_weight(tier: LocationTier, weights: LocationWeights) -> float:
    """Map a tier onto its ranking contribution."""
    return {
        LocationTier.CITY_CORE: weights.exact_city,
        LocationTier.METRO: weights.metro,
        LocationTier.REGION: weights.regional,
        LocationTier.COUNTRY: weights.country,
        LocationTier.REMOTE: weights.remote,
        LocationTier.EUROPE: weights.europe,
        LocationTier.UNKNOWN: weights.unknown,
    }[tier]


def location_fit(
    location: str | None,
    weights: LocationWeights | None = None,
    *,
    remote_mode: str | None = None,
    willing_to_relocate: bool = True,
) -> tuple[float, LocationParse]:
    """Return ``(fit, parse)`` where fit is in [0, 1].

    Location influences *prioritization only*. Callers must never let this
    value alter requirement coverage; it is a ranking signal.
    """
    w = weights or LocationWeights()
    parse = parse_location(location, remote_mode=remote_mode)
    if parse.tier is LocationTier.UNKNOWN:
        return w.unknown, parse
    if parse.tier is LocationTier.EUROPE and not willing_to_relocate:
        return w.relocate_without_offer, parse
    return tier_weight(parse.tier, w), parse


def preferred_location_terms(city: str, *, national: bool = True, remote: bool = True) -> list[str]:
    """Deterministic, deduplicated location tokens for query generation."""
    base = normalize_location(city)
    terms: list[str] = []
    if base:
        # Preserve the human-facing city label (first token), plus normalized.
        raw_first = (city or "").split(",")[0].strip()
        if raw_first:
            terms.append(raw_first)
        if base not in terms:
            terms.append(base)
    if national:
        for token in ("Germany", "Deutschland"):
            if token not in terms:
                terms.append(token)
    if remote:
        for token in ("Remote", "Remote Europe"):
            if token not in terms:
                terms.append(token)
    return terms


__all__ = [
    "LocationParse",
    "LocationTier",
    "LocationWeights",
    "RemoteScope",
    "TIER_RANK",
    "location_fit",
    "normalize_location",
    "parse_location",
    "preferred_location_terms",
    "tier_weight",
]
