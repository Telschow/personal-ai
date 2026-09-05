"""Tests for the deterministic, bounded corpus-layer memory ingestion.

Coverage focuses on the safe, conservative extraction rules and on the fact
that corpus candidates still travel through the policy-gated curator: deferred
and rejected candidates never write, accepted candidates persist exactly once
(idempotent), evidence is provenance-only (no URLs, titles, or content), and
repeated runs are deterministic.
"""

from __future__ import annotations

import sqlite3

import pytest

from personal_ai.agents.policy import ApprovalRequiredError
from personal_ai.documents import Document
from personal_ai.events.models import EVENT_TYPE_URL_VISIT, Event, compute_event_id
from personal_ai.memory.corpus import (
    ACTIVITY,
    EMAIL,
    FINANCIAL,
    WORKOUTS,
    CorpusMemoryIngestor,
    CorpusSourceError,
    extract_activity_patterns,
    extract_workout_routine,
)
from personal_ai.memory.models import (
    MemoryCandidate,
    MemoryEvidenceRef,
    MemoryKind,
    MemorySourceType,
    MemoryStatus,
)
from personal_ai.memory.policy import MemoryDecision, MemoryPolicy
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryStore
from personal_ai.storage import DocumentStore, connect_database
from personal_ai.workouts.models import WorkoutSummary


def _service() -> MemoryService:
    return MemoryService(MemoryStore(sqlite3.connect(":memory:")))


def _workout(
    workout_id: str,
    started_at: str,
) -> WorkoutSummary:
    return WorkoutSummary(
        workout_id=workout_id,
        name="Session",
        started_at=started_at,
        ended_at=None,
        activity_type="strength",
        duration_seconds=3600.0,
        program_id=None,
        exercise_count=5,
        set_count=20,
        completed_set_count=20,
        total_volume_kg=1000.0,
    )


def _visit(
    url: str,
    event_time: str,
    *,
    source: str = "chrome_history",
    event_type: str = EVENT_TYPE_URL_VISIT,
) -> Event:
    return Event(
        id=compute_event_id(source, event_type, event_time, url),
        event_type=event_type,
        event_time=event_time,
        source=source,
        url=url,
        metadata={},
    )


def _implicit_candidate(**overrides) -> MemoryCandidate:
    base = {
        "statement": "The user prefers local-first tools.",
        "kind": "preference",
        "confidence": 0.9,
        "durability": 0.8,
        "relevance": 0.8,
        "specificity": 0.7,
        "recurrence": 1,
        "utility": 0.7,
        "temporal_scope": "current",
        "assertion_status": "asserted",
        "evidence": (
            MemoryEvidenceRef(
                source_type="email",
                source_id="doc-1",
                source_timestamp="2026-01-05T10:00:00+00:00",
            ),
        ),
    }
    base.update(overrides)
    return MemoryCandidate(**base)


class _FakeWorkoutStore:
    def __init__(self, workouts: list[WorkoutSummary]) -> None:
        self._workouts = workouts

    def list_workouts(self, *, limit: int | None = None) -> list[WorkoutSummary]:
        if limit is None:
            return list(self._workouts)
        return self._workouts[:limit]


class _FakeEventStore:
    def __init__(self, events: list[Event]) -> None:
        self._events = sorted(events, key=lambda e: (e.event_time, e.id))

    def list_events(
        self,
        *,
        source: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Event, ...]:
        rows = [
            event
            for event in self._events
            if (source is None or event.source == source)
            and (event_type is None or event.event_type == event_type)
        ]
        return tuple(rows[offset : offset + limit])


# ---- workout extraction ----------------------------------------------------


def test_workout_extractor_emits_recurring_habit_candidate() -> None:
    workouts = [
        _workout(f"w-{month}-{index}", f"2026-0{month}-0{index + 1}T08:00:00+00:00")
        for month in (1, 2, 3)
        for index in range(4)
    ]
    (candidate,) = extract_workout_routine(workouts)

    assert candidate.statement == "The user maintains a regular workout routine."
    assert candidate.kind is MemoryKind.HABIT
    assert candidate.temporal_scope.value == "recurring"
    assert candidate.assertion_status.value == "asserted"
    assert candidate.recurrence == 3
    assert len(candidate.evidence) == 3
    assert {ref.source_type for ref in candidate.evidence} == {"workout"}
    assert all(ref.source_id.startswith("w-") for ref in candidate.evidence)
    assert all(ref.source_timestamp for ref in candidate.evidence)


def test_workout_extractor_too_few_sessions_yields_nothing() -> None:
    workouts = [
        _workout(f"w-{month}-{index}", f"2026-0{month}-0{index + 1}T08:00:00+00:00")
        for month in (1, 2, 3)
        for index in range(2)
    ]
    assert extract_workout_routine(workouts) == ()


def test_workout_extractor_single_month_yields_nothing() -> None:
    workouts = [
        _workout(f"w-{index}", f"2026-01-0{index + 1}T08:00:00+00:00")
        for index in range(15)
    ]
    assert extract_workout_routine(workouts) == ()


def test_workout_extractor_empty_input_yields_nothing() -> None:
    assert extract_workout_routine([]) == ()


def test_workout_extractor_evidence_capped_at_five() -> None:
    workouts = [
        _workout(f"w-{month}-0", f"2026-{month:02d}-05T08:00:00+00:00")
        for month in range(1, 13)
    ]
    (candidate,) = extract_workout_routine(workouts)
    assert len(candidate.evidence) == 5
    assert len({ref.source_id for ref in candidate.evidence}) == 5


def test_workout_extractor_identical_rerun_is_deterministic() -> None:
    workouts = [
        _workout(f"w-{month}-0", f"2026-{month:02d}-05T08:00:00+00:00")
        for month in (1, 2, 3)
    ]
    first = extract_workout_routine(workouts)
    second = extract_workout_routine(
        [_workout(w.workout_id, w.started_at) for w in workouts]
    )
    assert first == second


# ---- activity extraction ---------------------------------------------------


def test_activity_extractor_emits_interest_from_recurring_domain() -> None:
    events = [
        _visit(
            "https://github.com/user/repo?tab=readme",
            f"2026-01-0{d}T10:00:00+00:00",
        )
        for d in range(1, 7)
    ]
    events += [_visit("https://github.com/", "2026-02-05T10:00:00+00:00")]
    events += [_visit("https://github.com/", "2026-03-05T10:00:00+00:00")]

    (candidate,) = extract_activity_patterns(events)

    assert candidate.statement == "The user frequently visits the website github.com."
    assert candidate.kind is MemoryKind.INTEREST
    assert candidate.temporal_scope.value == "recurring"
    assert len(candidate.evidence) == 3
    assert {ref.source_type for ref in candidate.evidence} == {"chrome_history"}
    assert all(ref.source_id for ref in candidate.evidence)


def test_activity_extractor_ignores_weak_and_single_month_domains() -> None:
    events = [
        _visit("https://news.example.com/", "2026-01-05T10:00:00+00:00"),
        _visit("https://opinions.example.com/", "2026-01-06T10:00:00+00:00"),
    ]
    assert extract_activity_patterns(events) == ()


def test_activity_extractor_skips_sensitive_and_operational_domains() -> None:
    events = [
        _visit("https://mybank.example.com/accounts", "2026-01-05T10:00:00+00:00"),
        _visit("https://mail.example.com/inbox", "2026-01-06T10:00:00+00:00"),
        _visit("https://login.example.com/", "2026-01-07T10:00:00+00:00"),
    ]
    for month in (1, 2, 3):
        events.append(
            _visit("https://docs.example.se/", f"2026-0{month}-05T10:00:00+00:00")
        )

    assert extract_activity_patterns(events) == ()


def test_activity_extractor_never_leaks_url_path_or_query() -> None:
    events = [
        _visit(
            "https://github.com/private/repo?q=token%3Dsecret",
            f"2026-01-0{d}T10:00:00+00:00",
        )
        for d in range(1, 7)
    ]
    events += [_visit("https://github.com/", "2026-03-05T10:00:00+00:00")]
    events += [_visit("https://github.com/", "2026-04-05T10:00:00+00:00")]

    (candidate,) = extract_activity_patterns(events)

    assert "https://" not in candidate.statement
    assert "repo" not in candidate.statement
    for ref in candidate.evidence:
        assert ref.source_id
        assert "github" not in ref.source_id
        assert "repo" not in ref.source_id


def test_activity_extractor_ignores_non_http_and_invalid_urls() -> None:
    events = [
        _visit("ftp://github.com/files", "2026-01-05T10:00:00+00:00"),
        _visit("not a url", "2026-01-06T10:00:00+00:00"),
        _visit("", "2026-01-07T10:00:00+00:00"),
    ]
    assert extract_activity_patterns(events) == ()


def test_activity_extractor_respects_max_candidates_and_deterministic_order() -> None:
    events = [
        _visit(f"https://{domain}.example.com/", f"2026-{month:02d}-05T10:00:00+00:00")
        for month in (1, 2, 3)
        for domain in ("alpha", "bravo", "charlie")
        for _ in range(2)
    ]
    first = extract_activity_patterns(events, max_candidates=2)
    assert len(first) == 2

    all_order = [
        c.statement for c in extract_activity_patterns(events, max_candidates=10)
    ]
    assert all_order == [
        "The user frequently visits the website alpha.example.com.",
        "The user frequently visits the website bravo.example.com.",
        "The user frequently visits the website charlie.example.com.",
    ]
    assert [c.statement for c in first] == all_order[:2]


# ---- policy integration and curator gating ---------------------------------


def test_behavioral_corpus_candidates_are_deferred_by_policy() -> None:
    workout_candidate = extract_workout_routine(
        [
            _workout(
                f"w-{month}-{index}", f"2026-{month:02d}-0{index + 1}T08:00:00+00:00"
            )
            for month in (1, 2, 3)
            for index in range(4)
        ]
    )[0]
    activity_candidate = extract_activity_patterns(
        [
            _visit("https://docs.example.com/", f"2026-{month:02d}-05T10:00:00+00:00")
            for month in (1, 2, 3)
            for _ in range(3)
        ]
    )[0]
    policy = MemoryPolicy()

    decisions = [
        (policy.evaluate(c).decision, c.kind)
        for c in (workout_candidate, activity_candidate)
    ]

    assert all(decision is MemoryDecision.DEFER for decision, _ in decisions)
    assert {kind for _, kind in decisions} == {MemoryKind.HABIT, MemoryKind.INTEREST}


def test_behavioral_corpus_candidates_never_write() -> None:
    service = _service()
    ingestor = CorpusMemoryIngestor(service)
    workout_candidate = extract_workout_routine(
        [
            _workout(
                f"w-{month}-{index}", f"2026-{month:02d}-0{index + 1}T08:00:00+00:00"
            )
            for month in (1, 2, 3)
            for index in range(4)
        ]
    )[0]

    result = ingestor._curator.curate(workout_candidate)

    assert result == {
        "applied": False,
        "decision": "defer",
        "reason": "weak_source_quality",
        "sensitivity": "ordinary",
        "source_quality": "behavioral_evidence",
    }
    assert service.counts()["memories"] == 0


# ---- end-to-end acceptance + idempotency -----------------------------------


def test_accepted_corpus_candidate_persists_as_corpus_sourced_memory() -> None:
    service = _service()
    ingestor = CorpusMemoryIngestor(service)

    result = ingestor._curator.curate(_implicit_candidate())

    assert result["applied"] is True
    assert result["decision"] == "accept"
    assert result["status"] == "created"
    memory = service.get(result["memory_id"])  # type: ignore[arg-type]
    assert memory.status is MemoryStatus.ACTIVE
    assert memory.source_type is MemorySourceType.CORPUS
    assert len(service.evidence_for(result["memory_id"])) == 1  # type: ignore[union-attr]


def test_accepted_candidate_write_is_idempotent() -> None:
    service = _service()
    ingestor = CorpusMemoryIngestor(service)

    first = ingestor._curator.curate(_implicit_candidate())
    second = ingestor._curator.curate(_implicit_candidate())

    assert first["status"] == "created"
    assert second["status"] == "updated"
    assert service.counts()["active"] == 1
    memory_id = second["memory_id"]
    assert len(service.evidence_for(memory_id)) == 1  # type: ignore[union-attr]


def test_accepted_candidate_denied_gate_raises_and_never_writes() -> None:
    service = _service()
    ingestor = CorpusMemoryIngestor(service, auto_approver=lambda *args: False)

    with pytest.raises(ApprovalRequiredError):
        ingestor._curator.curate(_implicit_candidate())
    assert service.counts()["memories"] == 0


# ---- ingestor run orchestration ---------------------------------------------


def _ingestor_fixture(
    service: MemoryService, *, workouts: list[WorkoutSummary], events: list[Event]
) -> tuple[CorpusMemoryIngestor, DocumentStore]:
    document_store = DocumentStore(connect_database(":memory:"))
    ingestor = CorpusMemoryIngestor(
        service,
        workout_store=_FakeWorkoutStore(workouts),
        event_store=_FakeEventStore(events),
        document_store=document_store,
    )
    return ingestor, document_store


def test_ingestor_run_reports_aggregates_and_is_deterministic() -> None:
    service = _service()
    workouts = [
        _workout(f"w-{month}-{index}", f"2026-{month:02d}-0{index + 1}T08:00:00+00:00")
        for month in (1, 2, 3)
        for index in range(4)
    ]
    events = [
        _visit("https://docs.example.com/", f"2026-{month:02d}-05T10:00:00+00:00")
        for month in (1, 2, 3)
        for _ in range(3)
    ]
    ingestor, document_store = _ingestor_fixture(
        service, workouts=workouts, events=events
    )
    document_store.add(
        Document(
            id="email-1",
            source="msg-1",
            source_type="email",
            content_hash="hash-1",
            created_at="2026-01-01T00:00:00+00:00",
            modified_at="2026-01-01T00:00:00+00:00",
        )
    )
    document_store.add(
        Document(
            id="fin-1",
            source="fin/2024",
            source_type="financial",
            content_hash="hash-2",
            created_at="2026-02-01T00:00:00+00:00",
            modified_at="2026-02-01T00:00:00+00:00",
            metadata={"financial_kind": "bank", "row_count": 120},
        )
    )

    report = ingestor.run()
    again = ingestor.run()

    assert report.records_scanned == {
        WORKOUTS: 12,
        ACTIVITY: 9,
        EMAIL: 1,
        FINANCIAL: 1,
    }
    assert report.tally.candidates == 2  # one workout habit, one chrome interest
    assert report.tally.deferred == 2
    assert report.tally.writes == 0
    assert report.tally.accepted == 0
    assert report.to_dict() == again.to_dict()
    assert service.counts()["memories"] == 0


def test_ingestor_rejects_unknown_source() -> None:
    ingestor = CorpusMemoryIngestor(_service())
    with pytest.raises(CorpusSourceError):
        ingestor.run(sources=(EMAIL, "unknown"))


def test_reads_are_bounded_by_max_records() -> None:
    service = _service()
    events = [
        _visit(f"https://domain-{index}.example.com/", "2026-01-05T10:00:00+00:00")
        for index in range(40)
    ]
    ingestor = CorpusMemoryIngestor(
        service,
        event_store=_FakeEventStore(events),
        batch_size=10,
        max_records_per_source=25,
    )

    report = ingestor.run(sources=(ACTIVITY,))

    assert report.records_scanned == {ACTIVITY: 25}
