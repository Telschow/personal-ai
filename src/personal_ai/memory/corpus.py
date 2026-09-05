"""Deterministic, bounded corpus-layer memory ingestion.

The corpus memory layer turns *already-ingested* structured material (the
document store, the event store, and the workout store) into ``MemoryCandidate``
instances, and routes those candidates through the exact same policy-gated
write path as every other automatic memory: :class:`AutomaticMemoryCurator`.
``curate()`` classifies with the deterministic :class:`MemoryPolicy`, and only
a policy-``accept`` outcome ever reaches the ``propose_memory`` gate and hence
:meth:`MemoryService.apply_candidate`. This module never touches SQLite
directly and never writes memory itself.

Extraction is deliberately **conservative and deterministic** (no LLM, no body
parsing, no invented facts):

* workout logs produce a recurring ``habit`` candidate when the session count
  and the number of distinct months clear fixed thresholds — nothing is
  derived from exercise names or note text;
* URL-visit events are aggregated per normalized domain, and only domains with
  sustained, repeated visits (and no obviously sensitive/operational name)
  can yield ``interest`` candidates — raw URLs, titles, and search queries are
  never used as memory content or provenance identifiers;
* email and financial documents are scanned for counts only: their metadata
  does not yet carry facts that can be proposed *safely and deterministically*
  without reading content, so this slice produces no candidates for them.

All real signals here are behavioral provenance, which ``MemoryPolicy``
classifies as ``behavioral_evidence`` and therefore **defers**; that is the
correct conservative outcome. The auto-accept path is proven by tests using
personal-document/conversation provenance, and will produce durable memories
once Slice 4 (conversation exports) and Slice 5 (LLM-assisted candidate
proposals) feed stronger evidence through the same machinery.

Boundedness: every step reads in fixed-size batches, never more than
``max_records_per_source`` records per source, and never proposes more than
``max_candidates_per_source`` candidates per source. Diagnostics are
aggregate-only — counts and decision codes, never document content.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from personal_ai.events.models import EVENT_TYPE_URL_VISIT, Event
from personal_ai.memory.models import (
    AssertionStatus,
    MemoryCandidate,
    MemoryEvidenceRef,
    MemoryKind,
    TemporalScope,
)
from personal_ai.memory.policy import MemoryDecision, MemoryPolicy
from personal_ai.memory.service import MemoryService
from personal_ai.workouts.models import WorkoutSummary

# Deterministic extraction thresholds.
WORKOUT_MIN_SESSIONS = 10
WORKOUT_MIN_DISTINCT_MONTHS = 3
ACTIVITY_MIN_VISITS = 5
ACTIVITY_MIN_DISTINCT_MONTHS = 2

# Processing bounds.
DEFAULT_BATCH_SIZE = 500
DEFAULT_MAX_RECORDS = 50_000
DEFAULT_MAX_CANDIDATES = 20
MAX_EVIDENCE = 5

# Corpus source labels (also the evidence ``source_type`` where the policy
# vocabulary already recognizes them: "workout" and chrome_history are the
# behavioral provenance strings used by ``MemoryPolicy``).
WORKOUTS = "workout"
ACTIVITY = "activity"
EMAIL = "email"
FINANCIAL = "financial"

_SUPPORTED_SOURCES: frozenset[str] = frozenset({WORKOUTS, ACTIVITY, EMAIL, FINANCIAL})

# Domains whose visits are operational/communication or content-sensitive and
# therefore never proposal-worthy as an "interest" (conservative block-list).
_ACTIVITY_SKIP_FRAGMENTS: frozenset[str] = frozenset(
    {
        "auth",
        "login",
        "signin",
        "signon",
        "oauth",
        "secure",
        "bank",
        "pay",
        "payment",
        "billing",
        "account",
        "health",
        "clinic",
        "doctor",
        "medic",
        "tax",
        "mail",
        "email",
        "outlook",
    }
)
_ACTIVITY_SKIP_HOSTS: frozenset[str] = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "0:0:0:0:0:0:0:1"}
)


class CorpusSourceError(ValueError):
    """Raised for an unsupported or misconfigured corpus source."""


@dataclass
class OutcomeTally:
    """Count-only outcomes for one run, source, or candidate set.

    Deliberately content-free: decisions, write counts, and reconciliation
    statuses — never statements, evidence identifiers, or document content.
    """

    candidates: int = 0
    accepted: int = 0
    deferred: int = 0
    rejected: int = 0
    require_approval: int = 0
    writes: int = 0
    created: int = 0
    updated: int = 0
    conflicts: int = 0

    def add(self, result: dict[str, object]) -> None:
        self.candidates += 1
        decision = str(result.get("decision", "unknown"))
        if decision == MemoryDecision.ACCEPT.value:
            self.accepted += 1
        elif decision == MemoryDecision.DEFER.value:
            self.deferred += 1
        elif decision == MemoryDecision.REJECT.value:
            self.rejected += 1
        elif decision == MemoryDecision.REQUIRE_APPROVAL.value:
            self.require_approval += 1
        if result.get("applied"):
            self.writes += 1
            status = str(result.get("status", ""))
            if status == "created":
                self.created += 1
            elif status == "updated":
                self.updated += 1
            elif status == "conflict":
                self.conflicts += 1

    def merge(self, other: OutcomeTally) -> None:
        self.candidates += other.candidates
        self.accepted += other.accepted
        self.deferred += other.deferred
        self.rejected += other.rejected
        self.require_approval += other.require_approval
        self.writes += other.writes
        self.created += other.created
        self.updated += other.updated
        self.conflicts += other.conflicts

    def to_dict(self) -> dict[str, int]:
        return {
            "candidates": self.candidates,
            "accepted": self.accepted,
            "deferred": self.deferred,
            "rejected": self.rejected,
            "require_approval": self.require_approval,
            "writes": self.writes,
            "created": self.created,
            "updated": self.updated,
            "conflicts": self.conflicts,
        }


@dataclass(frozen=True, slots=True)
class CorpusIngestionReport:
    """Aggregate-only outcome of one corpus ingestion pass."""

    records_scanned: dict[str, int]
    by_source: dict[str, OutcomeTally]
    tally: OutcomeTally

    def to_dict(self) -> dict[str, object]:
        return {
            "records_scanned": dict(self.records_scanned),
            "by_source": {
                source: tally.to_dict() for source, tally in self.by_source.items()
            },
            "tally": self.tally.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class _SourceStepResult:
    records_scanned: int
    candidates: tuple[MemoryCandidate, ...]
    tally: OutcomeTally


def _iso_month(value: str | None) -> str | None:
    """Normalize an ISO timestamp to its ``YYYY-MM`` bucket (or None)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m")
    except ValueError:
        return None


def extract_workout_routine(
    workouts: list[WorkoutSummary],
) -> tuple[MemoryCandidate, ...]:
    """Propose a recurring workout habit from a bounded set of workout logs.

    Emits at most one candidate, and only when the log contains a minimum
    number of sessions spread across a minimum number of distinct calendar
    months. The statement is deliberately generic — no exercise names, note
    text, or dates — and carries up to five evidence references, one per
    distinct month (the earliest session in that month).
    """
    if not workouts:
        return ()
    ordered = sorted(workouts, key=lambda w: (w.started_at, w.workout_id))
    months = [m for m in (_iso_month(w.started_at) for w in ordered) if m is not None]
    distinct_months = set(months)
    if (
        len(ordered) < WORKOUT_MIN_SESSIONS
        or len(distinct_months) < WORKOUT_MIN_DISTINCT_MONTHS
    ):
        return ()

    by_month: dict[str, WorkoutSummary] = {}
    for workout in ordered:
        month = _iso_month(workout.started_at)
        if month is not None and month not in by_month:
            by_month[month] = workout
    evidence = tuple(
        MemoryEvidenceRef(
            source_type=WORKOUTS,
            source_id=workout.workout_id,
            source_timestamp=workout.started_at,
        )
        for workout in list(by_month.values())[:MAX_EVIDENCE]
    )
    span = len(distinct_months)
    candidate = MemoryCandidate(
        statement="The user maintains a regular workout routine.",
        kind=MemoryKind.HABIT,
        confidence=min(0.9, 0.6 + 0.05 * (span - WORKOUT_MIN_DISTINCT_MONTHS)),
        durability=min(0.9, 0.5 + 0.05 * (span - WORKOUT_MIN_DISTINCT_MONTHS)),
        relevance=0.8,
        specificity=0.5,
        recurrence=span,
        utility=0.6,
        temporal_scope=TemporalScope.RECURRING,
        assertion_status=AssertionStatus.ASSERTED,
        evidence=evidence,
    )
    return (candidate,)


@dataclass(frozen=True, slots=True)
class DomainStats:
    """Aggregated, content-free statistics for one normalized URL domain."""

    count: int
    months: int
    representative_events: tuple[Event, ...]


def _normalize_domain(url: str) -> str | None:
    """Return the lowercase registrable-style host for an http(s) URL."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host or host in _ACTIVITY_SKIP_HOSTS:
        return None
    return host.removeprefix("www.")


def _is_interest_domain(domain: str) -> bool:
    lower = domain.lower()
    return not any(fragment in lower for fragment in _ACTIVITY_SKIP_FRAGMENTS)


def _aggregate_domains(events: list[Event]) -> dict[str, DomainStats]:
    per_domain: dict[str, list[Event]] = {}
    for event in events:
        if event.event_type != EVENT_TYPE_URL_VISIT:
            continue
        domain = _normalize_domain(event.url)
        if domain is None:
            continue
        per_domain.setdefault(domain, []).append(event)

    result: dict[str, DomainStats] = {}
    for domain, raw_events in per_domain.items():
        ordered = sorted(raw_events, key=lambda e: (e.event_time, e.id))
        months = [
            m
            for m in sorted(
                {
                    month
                    for month in (_iso_month(event.event_time) for event in ordered)
                    if month is not None
                }
            )
        ]
        representative: list[Event] = []
        chosen_months: set[str] = set()
        for event in ordered:
            month = _iso_month(event.event_time)
            if month is not None and month not in chosen_months:
                representative.append(event)
                chosen_months.add(month)
        result[domain] = DomainStats(
            count=len(ordered),
            months=len(months),
            representative_events=tuple(representative),
        )
    return result


def extract_activity_patterns(
    events: list[Event],
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> tuple[MemoryCandidate, ...]:
    """Propose recurring ``interest`` candidates from URL-visit events.

    Only normalized domains with sustained repeated visits (count and distinct
    months above fixed thresholds) can qualify, and only if the domain name
    carries no obviously sensitive or operational markers. Candidates are
    ``recurring`` interests; the statement holds the domain only — never a raw
    URL, path, title, or search query. Evidence references individual events
    (stable ids and timestamps, one per distinct month), not URLs.
    """
    if max_candidates <= 0:
        return ()
    qualifying: list[tuple[str, DomainStats]] = [
        (domain, stats)
        for domain, stats in _aggregate_domains(events).items()
        if stats.count >= ACTIVITY_MIN_VISITS
        and stats.months >= ACTIVITY_MIN_DISTINCT_MONTHS
        and _is_interest_domain(domain)
    ]
    qualifying.sort(key=lambda item: (-item[1].count, item[0]))
    candidates: list[MemoryCandidate] = []
    for domain, stats in qualifying[:max_candidates]:
        evidence = tuple(
            MemoryEvidenceRef(
                source_type="chrome_history",
                source_id=event.id,
                source_timestamp=event.event_time,
            )
            for event in stats.representative_events[:MAX_EVIDENCE]
        )
        candidates.append(
            MemoryCandidate(
                statement=f"The user frequently visits the website {domain}.",
                kind=MemoryKind.INTEREST,
                confidence=min(
                    0.85, 0.55 + 0.03 * stats.months + 0.001 * min(stats.count, 100)
                ),
                durability=min(0.75, 0.45 + 0.03 * stats.months),
                relevance=0.7,
                specificity=0.6,
                recurrence=stats.months,
                utility=0.5,
                temporal_scope=TemporalScope.RECURRING,
                assertion_status=AssertionStatus.ASSERTED,
                evidence=evidence,
            )
        )
    return tuple(candidates)


class CorpusMemoryIngestor:
    """Runs bounded, deterministic corpus extraction into the memory pipeline.

    Reads each configured source in fixed-size batches (never more than
    ``max_records_per_source`` records), derives ``MemoryCandidate`` instances
    with the deterministic extractors, and routes every candidate through the
    policy-gated automatic curator. No raw SQL, no direct memory writes, and no
    body/content analysis: candidates come exclusively from the extractors.
    """

    def __init__(
        self,
        memory_service: MemoryService,
        *,
        policy: MemoryPolicy | None = None,
        workout_store: object | None = None,
        event_store: object | None = None,
        document_store: object | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_records_per_source: int = DEFAULT_MAX_RECORDS,
        max_candidates_per_source: int = DEFAULT_MAX_CANDIDATES,
        interactive_approver: Callable[[str, str, str], bool] | None = None,
        auto_approver: Callable[[str, str, str], bool] | None = None,
    ) -> None:
        # Imported lazily: pulling in the tools layer at module import time
        # would create a circular import through the chat tool registry.
        from personal_ai.tools.memory import AutomaticMemoryCurator

        self._curator = AutomaticMemoryCurator(
            memory_service,
            policy or MemoryPolicy(),
            interactive_approver=interactive_approver,
            auto_approver=auto_approver,
        )
        self._workout_store = workout_store
        self._event_store = event_store
        self._document_store = document_store
        self._batch_size = batch_size
        self._max_records = max_records_per_source
        self._max_candidates = max_candidates_per_source

    def _curate(self, candidates: tuple[MemoryCandidate, ...]) -> OutcomeTally:
        tally = OutcomeTally()
        for candidate in candidates:
            tally.add(self._curator.curate(candidate))
        return tally

    def ingest_workouts(self) -> _SourceStepResult:
        candidates: tuple[MemoryCandidate, ...] = ()
        records = 0
        if self._workout_store is not None:
            workouts = self._workout_store.list_workouts(  # type: ignore[attr-defined]
                limit=self._max_records
            )
            records = len(workouts)
            candidates = extract_workout_routine(workouts[: self._max_records])[
                : self._max_candidates
            ]
        return _SourceStepResult(records, candidates, self._curate(candidates))

    def ingest_activity(self) -> _SourceStepResult:
        events: list[Event] = []
        records = 0
        if self._event_store is not None:
            while records < self._max_records:
                batch = self._event_store.list_events(  # type: ignore[attr-defined]
                    source="chrome_history",
                    event_type=EVENT_TYPE_URL_VISIT,
                    limit=min(self._batch_size, self._max_records - records),
                    offset=records,
                )
                if not batch:
                    break
                events.extend(batch)
                records += len(batch)
        candidates = extract_activity_patterns(
            events, max_candidates=self._max_candidates
        )
        return _SourceStepResult(records, candidates, self._curate(candidates))

    def ingest_documents(self, source_type: str) -> _SourceStepResult:
        records = 0
        if self._document_store is not None:
            while records < self._max_records:
                batch = self._document_store.list_documents(  # type: ignore[attr-defined]
                    source_type=source_type,
                    limit=min(self._batch_size, self._max_records - records),
                    offset=records,
                )
                if not batch:
                    break
                records += len(batch)
        return _SourceStepResult(records, (), self._curate(()))

    def run(
        self,
        *,
        sources: tuple[str, ...] = (WORKOUTS, ACTIVITY, EMAIL, FINANCIAL),
    ) -> CorpusIngestionReport:
        by_source: dict[str, OutcomeTally] = {}
        records_scanned: dict[str, int] = {}
        tally = OutcomeTally()
        for source in sources:
            if source == WORKOUTS:
                step = self.ingest_workouts()
            elif source == ACTIVITY:
                step = self.ingest_activity()
            elif source in (EMAIL, FINANCIAL):
                step = self.ingest_documents(source)
            else:
                raise CorpusSourceError(f"unsupported corpus source: {source!r}")
            records_scanned[source] = step.records_scanned
            by_source[source] = step.tally
            tally.merge(step.tally)
        return CorpusIngestionReport(
            records_scanned=records_scanned,
            by_source=by_source,
            tally=tally,
        )
