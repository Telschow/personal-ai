"""Job normalization pipeline.

Turns a raw source :class:`Job` into the canonical, dedup-ready, salary- and
location-normalized form that gets persisted. Pure functions only — no I/O.
"""

from __future__ import annotations

from decimal import Decimal

from . import canonical
from .models import Job, RemoteMode
from .salary import SalaryInfo, normalize_salary


def normalize_remote_mode(location: str | None, description: str | None = None) -> RemoteMode:
    text = f"{location or ''} {description or ''}".casefold()
    if any(t in text for t in ("hybrid", "hybrid / on-site", "hybrid/on-site", "mix aus")):
        return "hybrid"
    if any(t in text for t in ("remote", "virtuell", "home office", "work from home", "fully remote")):
        return "remote"
    if any(t in text for t in ("on-site", "onsite", "in-office", "präsenz", "vor ort", "munich", "berlin", "germany")):
        return "onsite"
    return "unknown"


def normalized_location_tokens(location: str | None) -> list[str]:
    return canonical.split_locations(location)


def normalize_job(
    job: Job,
    source_type: str,
    *,
    rates: dict[str, Decimal] | None = None,
) -> Job:
    """Return a copy of ``job`` enriched with normalization fields.

    Idempotent: calling on an already-normalized job leaves identity fields
    intact but recomputes classification from the original location string.
    """
    out = job.model_copy(deep=True)
    out.source_type = source_type
    out.canonical_key = canonical.canonical_key(out.company, out.title, out.location)
    out.canonical_url = canonical.canonical_url(out.apply_url or out.url)
    out.normalized_location = " ".join(normalized_location_tokens(out.location))
    if out.status is None:
        out.status = "active"

    salary = normalize_salary(out.salary_min, out.salary_max, out.salary_currency, rates=rates)
    out.salary_min_eur = salary.min_eur
    out.salary_max_eur = salary.max_eur
    out.salary_converted = salary.converted

    # Remote/hybrid/onsite classification honors explicit location words first,
    # then falls back to description keywords.
    out.remote_mode = classify_remote_mode(out)

    # Apply role classification
    from .role_classifier import classify_role
    role_archetypes, role_family, confidence, reason, career_direction = classify_role(out)
    out.role_archetypes = list(role_archetypes)
    out.role_family = role_family
    out.role_classification_confidence = confidence
    out.role_classification_reason = reason
    out.career_direction = career_direction

    # Apply location classification
    from .location_classifier import classify_location
    location_city, location_country, location_scope, location_score, location_reason = classify_location(out)
    out.location_city = location_city
    out.location_country = location_country
    out.location_scope = location_scope
    out.location_score = location_score
    out.location_reason = location_reason

    return out


def classify_remote_mode(job: Job) -> RemoteMode:
    loc = job.location or ""
    text = f"{loc} {job.description or ''}".casefold()
    if any(t in text for t in ("fully remote", "100% remote", "remote-first", "remote position")):
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if any(t in text for t in ("remote", "home office", "work from home")):
        return "remote"
    if any(t in text for t in ("on-site", "onsite", "on site", "präsenz")):
        return "onsite"
    if loc and any(c in loc for c in "münchenmunichberlinstuttgartfrankfurt"):
        return "onsite"
    return "unknown"


def salary_info(job: Job) -> SalaryInfo:
    return normalize_salary(job.salary_min, job.salary_max, job.salary_currency)
