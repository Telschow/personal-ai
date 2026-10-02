"""Typed application configuration.

All secrets are expected from environment variables or a local, git-ignored
``.env`` overlay; this module never stores secrets.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from .career_tracks import CareerTracksSettings
from .location import LocationWeights

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class SalaryPolicy(BaseModel):
    """Compensation expectations, in EUR.

    ``minimum_eur`` / ``target_eur`` default to ``0``, which means *no
    preference is configured*: the compensation gate is skipped entirely and
    published salary carries no ranking weight. There is deliberately no
    built-in floor or target — any such number is a personal career policy
    decision that belongs in your own git-ignored ``config.yaml``, not in
    shipped source. Set both to positive values to enable the gate.
    """

    minimum_eur: float = 0
    target_eur: float = 0


class JobsSettings(BaseModel):
    salary: SalaryPolicy = Field(default_factory=SalaryPolicy)
    freshness_days: int = 30
    close_after_missing_scans: int = 2
    unknown_salary_reviewable: bool = True


class RankingWeights(BaseModel):
    similarity_weight: float = 0.25
    ai_relevance_weight: float = 0.20
    compensation_weight: float = 0.15
    location_weight: float = 0.10
    leadership_weight: float = 0.20
    purpose_weight: float = 0.05
    wlb_weight: float = 0.05


class SearchSettings(BaseModel):
    # Network discovery is opt-in. A bare Config() must never reach the
    # network; enable it deliberately in your own config.yaml.
    global_enabled: bool = False
    max_jobs_per_source: int = 250
    result_query_cap: int = 120
    results_per_query: int = 25
    max_global_pages: int = 400
    countries: list[str] = Field(default_factory=list)
    global_locations: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    keywords_positive: list[str] = Field(default_factory=list)
    keywords_negative: list[str] = Field(default_factory=list)
    # Sectors that count as "purpose" alignment. Empty by default: which
    # industries you want to work in is a personal direction, not a ranking
    # constant.
    industries_preferred: list[str] = Field(default_factory=list)
    # Free-text markers of purpose-aligned work. Deliberately separate from
    # keywords_positive: that list steers which postings are fetched, while
    # this one steers how a fetched posting is scored. Sharing them would mean
    # adding a term for discovery silently also moves scores.
    purpose_keywords: list[str] = Field(default_factory=list)


class LLMSettings(BaseModel):
    provider: str = "ollama"
    model: str = "qwen3.5:9b"
    # Loopback by default: correct for a local checkout. Point this at your own
    # host from config.yaml (or JOB_AGENT_LLM_BASE_URL) when running elsewhere.
    base_url: str = "http://127.0.0.1:11434"
    temperature: float = 0.15
    timeout_seconds: float = 300.0


class DigestSettings(BaseModel):
    type: str = "filesystem"
    directory: str = "output/reports"


class ProjectsSettings(BaseModel):
    sandbox_root: str = "output/projects"
    auto_publish: bool = False


class ApplicationSettings(BaseModel):
    auto_submit: bool = False
    never_submit: bool = True


class CareerKnowledgeSettings(BaseModel):
    provider: str = "none"  # "none" | "personal_ai"
    database_path: str = ""  # parent personal_ai DB; "" => env PERSONAL_AI_DATABASE
    memory_kinds: list[str] = Field(default_factory=list)  # empty => default kinds
    max_corpus_evidence: int = 40
    max_memory_evidence: int = 20


class CareerFitWeights(BaseModel):
    role_family: float = 0.22
    ai_relevance: float = 0.18
    leadership: float = 0.18
    technical_depth: float = 0.14
    product_scope: float = 0.10
    seniority: float = 0.08
    domain: float = 0.10


class LocationSettings(BaseModel):
    """Location preferences for ranking *and* discovery.

    ``preferred_city`` defaults to empty: with no configured city, discovery
    falls back to national/remote scope and ranking weights apply to the
    location tiers a posting actually resolves to. Any specific city is a
    personal geography decision and belongs in your own config.
    """

    preferred_city: str = ""
    national: bool = True
    remote: bool = True
    weights: LocationWeights = Field(default_factory=LocationWeights)


class DiscoveryPacing(BaseModel):
    """Rate-limit-aware pacing for the discovery search engine.

    ``interval_by_class`` maps a catalog ``rate_limit_class`` to the
    minimum seconds between queries via that class. ``backoff_base_s`` /
    ``backoff_max_s`` drive exponential backoff after a rate-limit response
    (doubled each consecutive failure); ``max_consecutive_failures`` pauses a
    pervasively rate-limited source for the rest of the run after that many
    consecutive failures, while other sources continue. ``max_retries_per_query``
    must stay 0 or 1 — retry storms are never acceptable.
    """

    interval_by_class: dict[str, float] = Field(default_factory=lambda: {"low": 0.5, "medium": 1.0, "high": 2.0})
    backoff_base_s: float = 2.0
    backoff_max_s: float = 60.0
    max_consecutive_failures: int = 3
    max_retries_per_query: int = 0


class DiscoveryBudgets(BaseModel):
    """Hard, bounded budgets for discovery; throttling is never infinite."""

    max_queries_total: int = 120
    max_queries_per_track: int = 24
    max_queries_per_source: int = 6
    max_sources_per_track: int = 8
    max_results_per_query: int = 25


class DiscoverySettings(BaseModel):
    """Discovery execution settings."""

    provider_only: bool = False
    skip_search_engines: bool = False
    max_provider_requests: int = 500
    min_jobs_per_provider_request: int = 10


class CareerLlmSettings(BaseModel):
    enabled: bool = False
    model: str = ""  # empty => reuse llm.model
    base_url: str = ""  # empty => reuse llm.base_url
    temperature: float = 0.15
    timeout_seconds: float = 0.0  # <=0 => reuse llm.timeout_seconds
    semantic: bool = True  # allow the semantic mapping refinement alongside the LLM proposal


class CareerSettings(BaseModel):
    knowledge: CareerKnowledgeSettings = Field(default_factory=CareerKnowledgeSettings)
    weights: CareerFitWeights = Field(default_factory=CareerFitWeights)
    llm: CareerLlmSettings = Field(default_factory=CareerLlmSettings)
    tracks: CareerTracksSettings = Field(default_factory=CareerTracksSettings)
    location: LocationSettings = Field(default_factory=LocationSettings)
    discovery: DiscoveryBudgets = Field(default_factory=DiscoveryBudgets)
    discovery_settings: DiscoverySettings = Field(default_factory=DiscoverySettings)
    pacing: DiscoveryPacing = Field(default_factory=DiscoveryPacing)


class SourceEntry(BaseModel):
    name: str | None = None
    token: str | None = None
    site: str | None = None
    board: str | None = None
    company: str | None = None
    subdomain: str | None = None
    url: str | None = None


class SourcesSettings(BaseModel):
    catalog_path: str = "sources_catalog.yaml"
    use_catalog: bool = True
    greenhouse: list[SourceEntry] = Field(default_factory=list)
    lever: list[SourceEntry] = Field(default_factory=list)
    ashby: list[SourceEntry] = Field(default_factory=list)
    smartrecruiters: list[SourceEntry] = Field(default_factory=list)
    workable: list[SourceEntry] = Field(default_factory=list)
    rss_json: list[SourceEntry] = Field(default_factory=list)
    sitemap: list[SourceEntry] = Field(default_factory=list)
    direct_company_domains: list[str] = Field(default_factory=list)


class Config(BaseModel):
    """Validated, typed application configuration."""

    profile_path: str = "profile/profile.yaml"
    database_path: str = "output/jobs.sqlite3"
    report_dir: str = "output/reports"
    cv_template_path: str = "templates/cv_master.docx"

    llm: LLMSettings = Field(default_factory=LLMSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    jobs: JobsSettings = Field(default_factory=JobsSettings)
    ranking: RankingWeights = Field(default_factory=RankingWeights)
    digest: DigestSettings = Field(default_factory=DigestSettings)
    projects: ProjectsSettings = Field(default_factory=ProjectsSettings)
    application: ApplicationSettings = Field(default_factory=ApplicationSettings)
    career: CareerSettings = Field(default_factory=CareerSettings)
    sources: SourcesSettings = Field(default_factory=SourcesSettings)

    @model_validator(mode="after")
    def _check_safety(self) -> Config:
        if self.application.auto_submit:
            raise ValueError("application.auto_submit must be false in this version")
        if self.projects.auto_publish:
            raise ValueError("projects.auto_publish must be false in this version")
        if self.jobs.salary.minimum_eur < 0 or self.jobs.salary.target_eur < 0:
            raise ValueError("salary thresholds must be non-negative")
        salary = self.jobs.salary
        # 0 means "no preference configured" (gate skipped). A partial policy
        # is rejected: half a gate would silently rank on a missing bound.
        if (salary.minimum_eur == 0) != (salary.target_eur == 0):
            raise ValueError("salary thresholds must both be 0 (disabled) or both be positive (enabled)")
        if salary.minimum_eur > 0 and salary.target_eur < salary.minimum_eur:
            raise ValueError("salary target_eur must be >= minimum_eur")
        if self.career.knowledge.provider not in ("none", "personal_ai"):
            raise ValueError("career.knowledge.provider must be 'none' or 'personal_ai'")
        weight_total = sum(self.career.weights.model_dump().values())
        if weight_total > 0 and abs(weight_total - 1.0) > 1e-6:
            raise ValueError("career.weights must sum to 1.0")
        if self.career.knowledge.max_corpus_evidence < 0 or self.career.knowledge.max_memory_evidence < 0:
            raise ValueError("career evidence caps must be non-negative")
        d = self.career.discovery
        if (
            min(
                d.max_queries_total,
                d.max_queries_per_track,
                d.max_queries_per_source,
                d.max_sources_per_track,
                d.max_results_per_query,
            )
            <= 0
        ):
            raise ValueError("career.discovery budgets must be positive integers")
        pacing = self.career.pacing
        missing = {"low", "medium", "high"} - set(pacing.interval_by_class)
        if missing:
            raise ValueError(f"career.pacing.interval_by_class must cover low/medium/high (missing: {sorted(missing)})")
        if min(pacing.interval_by_class.values()) <= 0:
            raise ValueError("career.pacing.interval_by_class intervals must be positive")
        if min(pacing.backoff_base_s, pacing.backoff_max_s) <= 0:
            raise ValueError("career.pacing backoff values must be positive")
        if pacing.backoff_max_s < pacing.backoff_base_s:
            raise ValueError("career.pacing.backoff_max_s must be >= backoff_base_s")
        if not 0 <= pacing.max_retries_per_query <= 1:
            raise ValueError("career.pacing.max_retries_per_query must be 0 or 1")
        if pacing.max_consecutive_failures < 1:
            raise ValueError("career.pacing.max_consecutive_failures must be >= 1")
        if min(self.career.location.weights.model_dump().values()) <= 0:
            raise ValueError("career.location.weights must be positive")
        return self

    def catalog_path_resolved(self) -> Path:
        """Resolve the source-catalog path relative to the project root when relative."""
        p = Path(self.sources.catalog_path)
        if not p.is_absolute():
            return PROJECT_ROOT / p
        return p

    def with_env_overrides(self, environ: dict[str, str] | None = None) -> Config:
        """Apply environment variable overrides for infrastructure settings.

        Secrets (llm API tokens etc.) are read from env only and never echoed.
        """
        env = environ if environ is not None else os.environ
        if "JOB_AGENT_DATABASE_PATH" in env:
            self.database_path = env["JOB_AGENT_DATABASE_PATH"]
        if "JOB_AGENT_REPORT_DIR" in env:
            self.report_dir = env["JOB_AGENT_REPORT_DIR"]
        if "JOB_AGENT_LLM_MODEL" in env:
            self.llm.model = env["JOB_AGENT_LLM_MODEL"]
        if "JOB_AGENT_LLM_BASE_URL" in env:
            self.llm.base_url = env["JOB_AGENT_LLM_BASE_URL"]
        if "JOB_AGENT_SOURCE_CATALOG" in env:
            self.sources.catalog_path = env["JOB_AGENT_SOURCE_CATALOG"]
        return self


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load a YAML config file from ``path`` (default ``config.yaml`` next to the
    package root) and return a validated :class:`Config`.
    """
    cfg_path = Path(path) if path is not None else PROJECT_ROOT / "config.yaml"

    raw: dict[str, Any]
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"invalid config structure in {cfg_path}")
        raw = data
    else:
        raw = {}

    cfg = Config.model_validate(raw)
    return cfg.with_env_overrides()


def default_config() -> Config:
    """Return a Config containing only built-in default values."""
    return Config()
