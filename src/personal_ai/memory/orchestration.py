"""Phase 18: production corpus-wide curation orchestration.

A top-level, bounded orchestration over the existing
:class:`~personal_ai.memory.curation.CurationAdapterRegistry` and
:class:`~personal_ai.memory.curation.MemoryCurationRunner`. Each source is
discovered independently and curates through the exact same policy-gated
write path the per-source ``memory curate`` command uses; orchestration only
*sequences* sources and aggregates their count-only reports.

Guarantees:

* **bounded** — one bounded unit at a time, bounded windows/prompts/
  candidates/retries and a per-source model-call budget (``max_model_calls``)
  and per-unit wall-clock timeout; nothing is ever loaded wholesale into
  memory or into one prompt;
* **per-source checkpoints** — every source writes its own durable
  run/unit rows (``source_type + extraction + run_id``), so ``resume``
  completes exactly the unfinished units of the newest *incomplete* run
  (``running`` or ``failed``) per source; a completed unit is never
  reprocessed and a completed run is never resumed, even when a newer run
  finished first;
* **source isolation** — a failure in one source (including a denied
  ``memory.write`` approval gate) is recorded count-only and never aborts the
  remaining sources, never invalidates their checkpoints;
* **adaptive mode** — conversation sources, email, and generic documents get
  bounded LLM proposal windows (subject to ``min_signal``/``sample``), while
  financial/workout/activity stay deterministic (financial content is never
  sent to the model);
* **content-free output** — reports carry totals, decision counts, and
  versions only; statements, email subjects, URLs, window text, prompts, and
  responses are never printed or logged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from personal_ai.agents.policy import ApprovalRequiredError
from personal_ai.memory.adapters import GENERIC_DOCUMENT_SOURCE
from personal_ai.memory.conversations import CONVERSATION_SOURCE_TYPES
from personal_ai.memory.corpus import ACTIVITY, EMAIL, FINANCIAL, WORKOUTS, OutcomeTally
from personal_ai.memory.curation import (
    CurationAdapterRegistry,
    CurationConfig,
    CurationError,
    CurationExtractionMode,
    CurationReport,
    CurationStore,
    MemoryCurationRunner,
    MemoryStats,
)
from personal_ai.memory.policy import MemoryPolicy
from personal_ai.memory.service import MemoryService

# Priority order per the Phase 18 source-priority guidance: identity density
# first, activity corroboration next, financial deliberately last (counts
# only).
DEFAULT_CORPUS_SOURCES: tuple[str, ...] = (
    *CONVERSATION_SOURCE_TYPES,
    EMAIL,
    GENERIC_DOCUMENT_SOURCE,
    WORKOUTS,
    ACTIVITY,
    FINANCIAL,
)

# Sources eligible for bounded LLM proposal windows in adaptive mode.
_LLM_SOURCES = frozenset((*CONVERSATION_SOURCE_TYPES, EMAIL, GENERIC_DOCUMENT_SOURCE))

DEFAULT_ORCHESTRATION_LIMIT = 25
DEFAULT_ORCHESTRATION_MAX_MODEL_CALLS = 25
DEFAULT_ORCHESTRATION_SAMPLE = 5

STATUS_SOURCE_COMPLETED = "completed"
STATUS_SOURCE_FAILED = "failed"
STATUS_SOURCE_PARTIAL = "partial"

ORCHESTRATION_COMPLETED = "completed"
ORCHESTRATION_PARTIAL = "partial"
ORCHESTRATION_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CorpusCurationConfig:
    """Bounded settings shared by every per-source run of an orchestration."""

    sources: tuple[str, ...] = DEFAULT_CORPUS_SOURCES
    mode: str = "adaptive"  # "adaptive" or "deterministic"
    limit: int = DEFAULT_ORCHESTRATION_LIMIT
    offset: int = 0
    batch_size: int = 500
    max_messages: int = 20
    max_prompt_chars: int = 4000
    max_candidates_per_unit: int = 5
    max_retries: int = 2
    max_model_calls: int = DEFAULT_ORCHESTRATION_MAX_MODEL_CALLS
    unit_timeout_seconds: float = 300.0
    min_signal: int = 0
    sample: int = DEFAULT_ORCHESTRATION_SAMPLE
    dry_run: bool = False
    resume: bool = False
    model_name: str | None = None

    def __post_init__(self) -> None:
        if not self.sources:
            object.__setattr__(self, "sources", DEFAULT_CORPUS_SOURCES)
        mode = self.mode.lower().strip()
        if mode == "llm":
            mode = "adaptive"  # legacy alias
        if mode not in {"adaptive", "deterministic"}:
            raise ValueError(f"unsupported orchestration mode: {self.mode!r}")
        object.__setattr__(self, "mode", mode)
        if self.dry_run and self.resume:
            raise ValueError("dry_run and resume are mutually exclusive")

    def to_curation_config(self, source: str) -> CurationConfig:
        """Derive the bounded per-source curation config for this run."""
        return CurationConfig(
            source_type=source,
            extraction=self._source_mode(source),
            limit=self.limit,
            offset=self.offset,
            batch_size=self.batch_size,
            max_messages=self.max_messages,
            max_prompt_chars=self.max_prompt_chars,
            max_candidates_per_unit=self.max_candidates_per_unit,
            max_retries=self.max_retries,
            max_model_calls=self.max_model_calls,
            unit_timeout_seconds=self.unit_timeout_seconds,
            min_signal=self.min_signal,
            sample=self.sample,
            dry_run=self.dry_run,
            resume=self.resume,
            model_name=self.model_name,
        )

    def _source_mode(self, source: str) -> CurationExtractionMode:
        if self.mode == "deterministic":
            return CurationExtractionMode.DETERMINISTIC
        if source in _LLM_SOURCES:
            return CurationExtractionMode.LLM
        return CurationExtractionMode.DETERMINISTIC


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """Aggregate-only outcome of one source within an orchestration."""

    source: str
    extraction: str
    status: str
    run_id: str
    dry_run: bool
    units_discovered: int
    units_completed: int
    units_failed: int
    units_skipped: int
    model_calls: int
    review_queued: int
    review_deduplicated: int
    evidence_added: int
    errors: dict[str, int]
    tally: dict[str, int]
    failure_reason: str = ""

    @classmethod
    def from_report(cls, report: CurationReport) -> SourceOutcome:
        return cls(
            source=report.source_type,
            extraction=report.extraction,
            status=report.status,
            run_id=report.run_id,
            dry_run=report.dry_run,
            units_discovered=report.counters.units_discovered,
            units_completed=report.counters.units_completed,
            units_failed=report.counters.units_failed,
            units_skipped=report.counters.units_skipped,
            model_calls=report.counters.model_calls,
            review_queued=report.counters.review_queued,
            review_deduplicated=report.counters.review_deduplicated,
            evidence_added=report.counters.evidence_added,
            errors=dict(report.counters.errors),
            tally=report.counters.tally.to_dict(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "extraction": self.extraction,
            "status": self.status,
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "units": {
                "discovered": self.units_discovered,
                "completed": self.units_completed,
                "failed": self.units_failed,
                "skipped": self.units_skipped,
            },
            "model_calls": self.model_calls,
            "review_queued": self.review_queued,
            "review_deduplicated": self.review_deduplicated,
            "evidence_added": self.evidence_added,
            "errors": dict(self.errors),
            "tally": dict(self.tally),
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True, slots=True)
class SourceFailure:
    """Count-only record of a source that aborted before a report."""

    source: str
    extraction: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "extraction": self.extraction,
            "reason": self.reason,
        }


@dataclass
class CorpusCurationReport:
    """Aggregate-only outcome of one corpus-wide orchestration."""

    mode: str
    dry_run: bool
    resume: bool
    status: str
    elapsed_seconds: float
    sources: tuple[SourceOutcome, ...]
    failures: tuple[SourceFailure, ...]
    memory: MemoryStats
    candidate_tally: OutcomeTally
    evidence_added: int = 0
    model_calls: int = 0
    review_queued: int = 0
    review_deduplicated: int = 0
    review_approved: int = 0
    review_rejected: int = 0
    review_expired: int = 0
    superseded: int = 0
    messages_scanned: int = 0

    def summary(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "dry_run": self.dry_run,
            "resume": self.resume,
            "status": self.status,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "sources": [outcome.to_dict() for outcome in self.sources],
            "failures": [failure.to_dict() for failure in self.failures],
            "source_count": len(self.sources) + len(self.failures),
            "candidates_proposed": self.candidate_tally.candidates,
            "candidates_accepted": self.candidate_tally.accepted,
            "candidates_rejected": self.candidate_tally.rejected,
            "candidates_deferred": self.candidate_tally.deferred,
            "candidates_require_approval": self.candidate_tally.require_approval,
            "memories_created": self.candidate_tally.created,
            "memories_updated": self.candidate_tally.updated,
            "memories_superseded": self.superseded,
            "conflicts": self.candidate_tally.conflicts,
            "writes": self.candidate_tally.writes,
            "evidence_added": self.evidence_added,
            "model_calls": self.model_calls,
            "messages_scanned": self.messages_scanned,
            "review_queued": self.review_queued,
            "review_deduplicated": self.review_deduplicated,
            "review_approved": self.review_approved,
            "review_rejected": self.review_rejected,
            "review_expired": self.review_expired,
            "memory": self.memory.to_dict(),
        }


class CorpusCurationOrchestrator:
    """Sequences bounded per-source curation over the full adapter registry.

    A fresh :class:`MemoryCurationRunner` is created per source (runners
    resolve the registry to a single adapter, so they are not reusable across
    sources). All writes flow through the same policy-gated curator as
    per-source curation.
    """

    def __init__(
        self,
        *,
        curation_store: CurationStore,
        registry: CurationAdapterRegistry,
        memory_service: MemoryService | None = None,
        policy: MemoryPolicy | None = None,
    ) -> None:
        self._store = curation_store
        self._registry = registry
        self._service = memory_service
        self._policy = policy

    def run(self, config: CorpusCurationConfig) -> CorpusCurationReport:
        started = time.monotonic()
        outcomes: list[SourceOutcome] = []
        failures: list[SourceFailure] = []
        candidate_tally = OutcomeTally()
        evidence_added = 0
        model_calls = 0
        review_queued = 0
        review_deduplicated = 0
        superseded = 0
        messages_scanned = 0
        for source in config.sources:
            self._registry.for_source(source)  # raise early if unsupported
            source_config = config.to_curation_config(source)
            try:
                report = self._run_source(source_config)
            except (CurationError, ApprovalRequiredError) as exc:
                source_config_value = source_config.extraction.value
                failures.append(
                    SourceFailure(
                        source=source,
                        extraction=source_config_value,
                        reason=_failure_code(exc),
                    )
                )
                continue
            except Exception:  # noqa: BLE001 - source isolation boundary
                failures.append(
                    SourceFailure(
                        source=source,
                        extraction=source_config.extraction.value,
                        reason="internal_error",
                    )
                )
                continue
            outcome = SourceOutcome.from_report(report)
            if outcome.units_failed:
                failures.append(
                    SourceFailure(
                        source=source,
                        extraction=outcome.extraction,
                        reason="unit_failures",
                    )
                )
            candidate_tally.merge(report.counters.tally)
            evidence_added += report.counters.evidence_added
            model_calls += report.counters.model_calls
            review_queued += report.counters.review_queued
            review_deduplicated += report.counters.review_deduplicated
            superseded += report.counters.superseded
            messages_scanned += report.counters.messages_scanned
            outcomes.append(outcome)
        status = _orchestration_status(outcomes, failures)
        # Terminal adjudication snapshot, derived from the authoritative audit
        # table (not from queue counts). Pending obligations and terminal
        # outcomes are kept distinct: ``review_queued`` reflects queue growth
        # this run, while ``review_approved/rejected/expired`` reflect the
        # durable audit history at run end.
        audit_counts = self._store.review_audit_counts()
        audit_outcomes = dict(audit_counts.get("outcomes", {}) or {})
        return CorpusCurationReport(
            mode=config.mode,
            dry_run=config.dry_run,
            resume=config.resume,
            status=status,
            elapsed_seconds=max(0.0, time.monotonic() - started),
            sources=tuple(outcomes),
            failures=tuple(failures),
            memory=MemoryStats.from_stores(self._service, self._store),
            candidate_tally=candidate_tally,
            evidence_added=evidence_added,
            model_calls=model_calls,
            review_queued=review_queued,
            review_deduplicated=review_deduplicated,
            review_approved=int(audit_outcomes.get("approved", 0)),
            review_rejected=int(audit_outcomes.get("rejected", 0)),
            review_expired=int(audit_outcomes.get("expired", 0)),
            superseded=superseded,
            messages_scanned=messages_scanned,
        )

    def _run_source(self, config: CurationConfig) -> CurationReport:
        from personal_ai.memory.reconcile import MemoryReconciler

        runner = MemoryCurationRunner(
            store=self._store,
            adapter=self._registry,
            memory_service=self._service,
            policy=self._policy or MemoryPolicy(),
            reconciler=MemoryReconciler(self._service),  # type: ignore[arg-type]
        )
        return runner.run(config)


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, ApprovalRequiredError):
        return "approval_required"
    if isinstance(exc, ValueError):
        return "config_error"
    return type(exc).__name__.replace("Curation", "").replace("Error", "").lower()


def _orchestration_status(
    outcomes: list[SourceOutcome], failures: list[SourceFailure]
) -> str:
    if not outcomes:
        return ORCHESTRATION_FAILED
    if failures or any(
        outcome.units_failed or outcome.status != STATUS_SOURCE_COMPLETED
        for outcome in outcomes
    ):
        return ORCHESTRATION_PARTIAL
    return ORCHESTRATION_COMPLETED


__all__: tuple[str, ...] = (
    "DEFAULT_CORPUS_SOURCES",
    "DEFAULT_ORCHESTRATION_LIMIT",
    "DEFAULT_ORCHESTRATION_MAX_MODEL_CALLS",
    "DEFAULT_ORCHESTRATION_SAMPLE",
    "ORCHESTRATION_COMPLETED",
    "ORCHESTRATION_FAILED",
    "ORCHESTRATION_PARTIAL",
    "CorpusCurationConfig",
    "CorpusCurationOrchestrator",
    "CorpusCurationReport",
    "SourceFailure",
    "SourceOutcome",
)
