"""Company radar discovery.

Reads ``company_radar.yaml`` and materializes per-company ATS queries using
the existing provider abstraction. Jobs are tagged with provenance
``discovery_source="company_radar"`` and ``company_radar_id`` so they can be
tracked separately from generic provider discovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import Config
from .models import Job
from .normalizer import normalize_job
from .sources import Source

_RADAR_PATH = Path(__file__).parent.parent / "company_radar.yaml"


@dataclass(frozen=True)
class RadarCompany:
    name: str
    location: str | None
    industry: list[str]
    greenhouse: str | None
    ashby: str | None
    lever: str | None
    smartrecruiters: str | None
    disabled: bool = False


def _load_radar() -> list[RadarCompany]:
    if not _RADAR_PATH.exists():
        return []
    data = yaml.safe_load(_RADAR_PATH.read_text()) or {}
    companies = data.get("companies", [])
    out: list[RadarCompany] = []
    for c in companies:
        # optional disabled flag
        disabled = bool(c.get("disabled", False))
        out.append(
            RadarCompany(
                name=c.get("name", ""),
                location=c.get("location"),
                industry=c.get("industry", []),
                greenhouse=c.get("greenhouse"),
                ashby=c.get("ashby"),
                lever=c.get("lever"),
                smartrecruiters=c.get("smartrecruiters"),
                disabled=disabled,
            )
        )
    return out


def _find_source_for_provider(cfg: Config, provider_type: str, token: str) -> Source | None:
    """Return the configured source that matches the given provider token/board."""
    from .discovery import build_sources

    for src in build_sources(cfg):
        # match by token field
        if provider_type == "greenhouse" and hasattr(src, "token") and src.token == token:
            return src
        if provider_type == "ashby" and hasattr(src, "board") and src.board == token:
            return src
        if provider_type == "lever" and hasattr(src, "site") and src.site == token:
            return src
        if provider_type == "smartrecruiters" and hasattr(src, "company") and src.company == token:
            return src
    return None


def discover_radar(
    conn,
    cfg: Config,
    run_id: str | None = None,
) -> dict[str, int]:
    """Run radar discovery for all enabled companies.

    Returns a metrics dict:
        radar_companies_planned
        radar_companies_queried
        radar_candidates
        radar_persisted
        radar_duplicates
        radar_errors
    """
    companies = _load_radar()
    metrics = {
        "radar_companies_planned": 0,
        "radar_companies_queried": 0,
        "radar_candidates": 0,
        "radar_persisted": 0,
        "radar_duplicates": 0,
        "radar_errors": 0,
    }
    planned = [c for c in companies if not c.disabled and c.name]
    metrics["radar_companies_planned"] = len(planned)

    for comp in planned:
        provider_type = None
        token = None
        if comp.greenhouse:
            provider_type = "greenhouse"
            token = comp.greenhouse
        elif comp.ashby:
            provider_type = "ashby"
            token = comp.ashby
        elif comp.lever:
            provider_type = "lever"
            token = comp.lever
        elif comp.smartrecruiters:
            provider_type = "smartrecruiters"
            token = comp.smartrecruiters
        else:
            # no supported provider
            metrics["radar_errors"] += 1
            continue

        src = _find_source_for_provider(cfg, provider_type, token)
        if not src:
            metrics["radar_errors"] += 1
            continue

        metrics["radar_companies_queried"] += 1
        try:
            raw_jobs = src.fetch()
        except Exception:
            metrics["radar_errors"] += 1
            continue

        metrics["radar_candidates"] += len(raw_jobs)

        for raw in raw_jobs:
            if isinstance(raw, dict):
                # Ensure source field is present for Job construction
                raw_copy = dict(raw)
                if "source" not in raw_copy:
                    raw_copy["source"] = src.name
                job_obj = Job.model_construct(**raw_copy)
            else:
                job_obj = raw
            norm = normalize_job(job_obj, source_type=src.kind.value)
            norm.run_id = run_id
            # provenance tagging
            native_id = None
            if ":" in norm.id:
                native_id = norm.id.split(":", 1)[1]
            norm.discovery_source = "company_radar"
            norm.company_radar_id = comp.name
            norm.provider_native_id = native_id or norm.id
            # duplicate detection
            existing_row = conn.execute(
                "SELECT id, discovery_source FROM jobs WHERE id=?",
                (norm.id,),
            ).fetchone()
            if existing_row:
                metrics["radar_duplicates"] += 1
                from .db import _now

                now = _now()
                conn.execute(
                    "UPDATE jobs SET last_seen=?, last_checked=?, missing_scans=0 WHERE id=?",
                    (now, now, norm.id),
                )
                if not existing_row["discovery_source"]:
                    conn.execute(
                        "UPDATE jobs SET discovery_source=?, company_radar_id=?, run_id=?, provider_native_id=? WHERE id=?",
                        ("company_radar", comp.name, run_id, native_id, norm.id),
                    )
                continue

            from .db import upsert_job

            upsert_job(conn, norm)
            metrics["radar_persisted"] += 1

    conn.commit()
    return metrics
