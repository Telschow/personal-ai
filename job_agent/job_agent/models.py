"""Typed domain models for the job agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Decision = Literal["reject", "review", "strong"]
JobStatus = Literal["active", "stale", "closed", "duplicate"]
UserJobStatus = Literal["NEW", "SAVED", "REJECTED", "APPLIED"]
RemoteMode = Literal["remote", "hybrid", "onsite", "unknown"]


class Job(BaseModel):
    id: str
    title: str
    company: str
    url: str
    apply_url: str | None = None
    source: str
    source_type: str | None = None
    canonical_url: str | None = None
    location: str = ""
    country: str | None = None
    normalized_location: str | None = None
    remote_mode: RemoteMode = "unknown"
    employment_type: str | None = None
    date_posted: datetime | None = None
    description: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_min_eur: float | None = None
    salary_max_eur: float | None = None
    salary_converted: bool = False
    canonical_key: str | None = None
    status: JobStatus | None = None
    closed_at: str | None = None
    discovered_at: str | None = None
    last_seen: str | None = None
    last_checked: str | None = None
    missing_scans: int = 0
    raw_json: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    # M3: user-facing status (distinct from system lifecycle status)
    user_status: UserJobStatus = "NEW"
    user_status_updated_at: str | None = None


class Score(BaseModel):
    """Deterministic rule-based evaluation of fit."""

    total: float
    decision: Decision
    reasons: list[str]
    gaps: list[str]
    hard_fail: bool = False
    confidence: float = 0.5
    # Sub-scores breadcrumb so historical decisions stay explainable.
    # Values are numeric sub-scores; non-numeric markers (e.g. "location_tier")
    # are carried as strings so the breakdown stays JSON-safe and inspectable.
    breakdown: dict[str, float | str] = Field(default_factory=dict)


class JobMatch(BaseModel):
    job: Job
    score: Score


class SourceStats(BaseModel):
    """Counts reported for a single source run."""

    source: str
    fetched: int = 0
    parsed: int = 0
    duplicates: int = 0
    errors: list[str] = Field(default_factory=list)


class SourceRate(BaseModel):
    """Per-source timing/behavioral model used for scan estimation."""

    source: str
    avg_duration_seconds: float = 5.0
    recent_count: int = 0
