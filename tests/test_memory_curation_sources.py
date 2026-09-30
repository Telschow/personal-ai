"""Tests for Slice 7 source adapters: email/financial/document/workout/activity.

Every source adapter derives :class:`MemoryCandidate` objects that flow
through the exact same policy-gated write path as conversations
(``MemoryPolicy`` -> ``AutomaticMemoryCurator`` -> ``propose_memory`` gate ->
``MemoryService``). Coverage focuses on the Slice 7 invariants:

* adapter contracts: ``supports``, ``version``, ``llm_enabled``, bounded
  ``discover`` units and deterministic ``process`` results;
* email — recurring sender domains only, content-free deterministic windows,
  bounded LLM windows, id-only monthly evidence;
* financial — counts only, zero candidates and zero model calls in BOTH
  modes (content is never sent to the model);
* generic documents — deterministic mode proposes nothing and never reads
  chunks; LLM mode uses strict evidence gating over bounded windows;
* workout/activity — deterministic-only adapters (``llm_enabled=False``);
* adaptive gating — ``min_signal`` and ``sample`` downgrade LLM units;
* privacy — reports never leak statement text, document ids, or domains;
* the mandatory denied-gate regression — a denied ``memory.write`` gate
  raises ``ApprovalRequiredError`` and writes nothing.

All tests are hermetic: no Ollama, no network, no real personal data.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

from personal_ai.agents.policy import ApprovalRequiredError
from personal_ai.documents import Document, DocumentChunk
from personal_ai.events.models import EVENT_TYPE_URL_VISIT, Event, compute_event_id
from personal_ai.memory import (
    ACTIVITY,
    CHROME_HISTORY_EVENT_SOURCE,
    EMAIL,
    FINANCIAL,
    GENERIC_DOCUMENT_SOURCE,
    WORKOUTS,
    CurationConfig,
    CurationConfigError,
    CurationExtractionMode,
    CurationReport,
    CurationStore,
    MemoryCurationRunner,
    MemoryKind,
    MemoryService,
    MemoryStore,
)
from personal_ai.memory.adapters import (
    ACTIVITY_EXTRACTOR_VERSION,
    DOCUMENT_EXTRACTOR_VERSION,
    DOCUMENT_PROPOSAL_PROMPT_VERSION,
    WORKOUT_EXTRACTOR_VERSION,
    DocumentCurationAdapter,
    EmailWindow,
    EventCurationAdapter,
    WorkoutCurationAdapter,
)
from personal_ai.memory.curation import CurationAdapterRegistry
from personal_ai.memory.policy import MemoryPolicy
from personal_ai.ollama_client import ChatResponse
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EventStore,
    connect_database,
)
from personal_ai.workouts.models import WorkoutSummary

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _service() -> MemoryService:
    return MemoryService(MemoryStore(sqlite3.connect(":memory:")))


def _doc(
    doc_id: str,
    *,
    source_type: str = EMAIL,
    created_at: str,
    sender: str | None = None,
    subject: str = "",
) -> Document:
    metadata: dict[str, object] = {}
    if sender is not None:
        metadata["sender"] = sender
    if subject:
        metadata["subject"] = subject
    return Document(
        id=doc_id,
        source=f"src:{doc_id}",
        source_type=source_type,
        content_hash=f"hash-{doc_id}",
        created_at=created_at,
        modified_at=created_at,
        mime_type="text/plain",
        metadata=metadata,
    )


def _chunk(doc_id: str, text: str) -> DocumentChunk:
    return DocumentChunk(id=f"c-{doc_id}", document_id=doc_id, text=text)


def _email_docs(
    domain: str = "bcg.com",
    *,
    months: tuple[str, ...] = ("2026-01", "2026-02", "2026-03"),
    per_month: int = 3,
    prefix: str = "email",
) -> list[Document]:
    docs: list[Document] = []
    for month in months:
        for index in range(per_month):
            day = index + 1
            docs.append(
                _doc(
                    f"{prefix}-{month}-{index}",
                    created_at=f"{month}-{day:02d}T08:00:00+00:00",
                    sender=f"Contact {index} <c{index}@{domain}>",
                    subject="Weekly update",
                )
            )
    return docs


def _workout(workout_id: str, started_at: str) -> WorkoutSummary:
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


def _visit(url: str, event_time: str) -> Event:
    return Event(
        id=compute_event_id(
            CHROME_HISTORY_EVENT_SOURCE, EVENT_TYPE_URL_VISIT, event_time, url
        ),
        event_type=EVENT_TYPE_URL_VISIT,
        event_time=event_time,
        source=CHROME_HISTORY_EVENT_SOURCE,
        url=url,
        metadata={},
    )


def _cfg(source_type: str, **overrides) -> CurationConfig:
    return CurationConfig(source_type=source_type, **overrides)


def _doc_proposal(
    doc_id: str,
    *,
    statement: str = "The user works at BCG as a product manager.",
    kind: str = "work",
) -> str:
    proposal: dict[str, object] = {
        "statement": statement,
        "kind": kind,
        "temporal_scope": "current",
        "confidence": 0.9,
        "evidence_document_ids": [doc_id],
        "rationale": "",
    }
    return json.dumps({"proposals": [proposal]})


class _CountingEchoClient:
    """Healthy fake: counts calls and echoes the window's first document id."""

    def __init__(
        self,
        statements: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self.calls = 0
        self._statements = statements or {}

    def chat(self, messages: object, *, think: bool, format: object) -> ChatResponse:
        self.calls += 1
        prompt = getattr(messages[-1], "content", "") if messages else ""
        match = re.search(r"\[([^\]]+)\]", str(prompt))
        doc_id = match.group(1) if match else "email-2026-01-0"
        statement, kind = self._statements.get(
            doc_id,
            ("The user works at BCG as a product manager.", "work"),
        )
        return ChatResponse(
            content=_doc_proposal(doc_id, statement=statement, kind=kind),
            model="fake",
            done=True,
        )


class _RaisingChunkStore:
    """Fails hard if chunk text is ever read (content-free mode)."""

    def list_for_document(self, document_id: str) -> tuple[DocumentChunk, ...]:
        raise AssertionError(f"chunk content read for {document_id!r}")


def _seed_documents(
    documents: list[Document],
) -> tuple[sqlite3.Connection, DocumentStore]:
    connection = connect_database(":memory:")
    store = DocumentStore(connection)
    for document in documents:
        store.add(document)
    return connection, store


def _document_runner(
    connection: sqlite3.Connection,
    service: MemoryService | None = None,
    *,
    client: object | None = None,
    chunk_store: object | None = None,
) -> MemoryCurationRunner:
    adapter = DocumentCurationAdapter(
        DocumentStore(connection),
        chunk_store,  # type: ignore[arg-type]
        client=client,
    )
    return MemoryCurationRunner(
        store=CurationStore(connection),
        adapter=adapter,
        memory_service=service,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_resolves_sources_and_rejects_unknown() -> None:
    adapter = DocumentCurationAdapter(DocumentStore(sqlite3.connect(":memory:")))
    registry = CurationAdapterRegistry(adapter)

    assert registry.supports(EMAIL)
    assert registry.for_source(EMAIL) is adapter
    assert registry.for_source(FINANCIAL) is adapter
    assert registry.for_source(GENERIC_DOCUMENT_SOURCE) is adapter
    assert registry.for_source("chatgpt") is None
    assert registry.supports("chatgpt") is False


def test_registry_aggregates_disjoint_adapters() -> None:
    registry = CurationAdapterRegistry(
        DocumentCurationAdapter(DocumentStore(sqlite3.connect(":memory:"))),
        EventCurationAdapter(EventStore(sqlite3.connect(":memory:"))),
    )
    assert registry.supports(EMAIL)
    assert registry.supports(ACTIVITY)
    assert registry.for_source(EMAIL) is not None
    assert registry.for_source(EMAIL).supports(EMAIL)
    assert registry.for_source(FINANCIAL) is not None
    assert registry.for_source(ACTIVITY) is not None
    assert registry.for_source("chatgpt") is None


# ---------------------------------------------------------------------------
# Email adapter: discovery, content-free deterministic mode, evidence
# ---------------------------------------------------------------------------


def test_email_version_uses_document_extractor_version() -> None:
    adapter = DocumentCurationAdapter(DocumentStore(sqlite3.connect(":memory:")))
    assert adapter.version(CurationExtractionMode.DETERMINISTIC) == (
        DOCUMENT_EXTRACTOR_VERSION,
        "",
    )
    assert adapter.version(CurationExtractionMode.LLM) == (
        DOCUMENT_EXTRACTOR_VERSION,
        DOCUMENT_PROPOSAL_PROMPT_VERSION,
    )


def test_email_discover_keeps_only_recurring_non_webmail_domains() -> None:
    docs: list[Document] = []
    # One recurring corporate domain: kept.
    docs += _email_docs(
        "bcg.com", months=("2026-01", "2026-02", "2026-03"), prefix="email-bcg"
    )
    # A single month of mail from a small company: dropped.
    docs += _email_docs("acme.io", months=("2026-01",), prefix="email-acme")
    # Even recurring, a mass webmail provider carries no durable signal: dropped.
    docs += _email_docs(
        "gmail.com", months=("2026-01", "2026-02", "2026-03"), prefix="email-gmail"
    )
    connection, store = _seed_documents(docs)
    try:
        adapter = DocumentCurationAdapter(store, _RaisingChunkStore())  # type: ignore[arg-type]
        units = adapter.discover(_cfg(EMAIL, limit=10))
        assert len(units) == 1
        unit = units[0]
        assert unit.source_type == EMAIL
        assert str(unit.context.domain) == "bcg.com"  # type: ignore[union-attr]
        assert unit.signal == 9
        assert unit.records  # windows exist
        # Deterministic windows are content-free: no snippets at all and the
        # chunk store was never queried.
        for window in unit.records:
            assert isinstance(window, EmailWindow)
            assert window.snippet == ""
    finally:
        connection.close()


def test_email_windows_bounded_in_llm_mode() -> None:
    docs = _email_docs(months=("2026-01", "2026-02", "2026-03", "2026-04"))
    chunks = [_chunk(doc.id, " ".join(["body"] * 200)) for doc in docs]
    connection = connect_database(":memory:")
    try:
        store = DocumentStore(connection)
        for document in docs:
            store.add(document)
        chunk_store = ChunkStore(connection)
        for chunk in chunks:
            chunk_store.add(chunk)
        adapter = DocumentCurationAdapter(store, chunk_store)
        units = adapter.discover(_cfg(EMAIL, extraction="llm", max_messages=2))
        (unit,) = units
        windows = tuple(unit.records)
        assert sum(1 for _ in windows) == 2  # capped by max_messages
        for window in windows:
            assert 0 < len(window.snippet) <= 4096
    finally:
        connection.close()


def test_email_deterministic_candidate_uses_monthly_id_only_evidence() -> None:
    docs = _email_docs(months=("2026-01", "2026-02", "2026-03"))
    connection, store = _seed_documents(docs)
    try:
        adapter = DocumentCurationAdapter(store)
        units = adapter.discover(_cfg(EMAIL, limit=10))
        result = adapter.process(units[0])
        assert not result.failed
        assert result.model_calls == 0
        (candidate,) = result.candidates
    finally:
        connection.close()
    assert candidate.kind is MemoryKind.INTEREST  # type: ignore[union-attr]
    assert candidate.temporal_scope.value == "recurring"  # type: ignore[union-attr]
    assert "bcg.com" in candidate.statement  # type: ignore[union-attr,index]
    # One id-only evidence ref per distinct month, never content.
    assert len(candidate.evidence) == 3  # type: ignore[union-attr]
    assert sorted(ref.source_document_id for ref in candidate.evidence) == sorted(
        f"email-2026-0{m}-0" for m in (1, 2, 3)
    )
    assert all(ref.source_type == EMAIL for ref in candidate.evidence)
    assert all(ref.source_timestamp for ref in candidate.evidence)


def test_email_evidence_capped_at_max_five_refs() -> None:
    months = tuple(f"2026-{month:02d}" for month in range(1, 9))
    docs = _email_docs(months=months)
    connection, store = _seed_documents(docs)
    try:
        adapter = DocumentCurationAdapter(store)
        result = adapter.process(adapter.discover(_cfg(EMAIL))[0])
        (candidate,) = result.candidates
    finally:
        connection.close()
    assert len(candidate.evidence) == 5  # type: ignore[union-attr]


def test_email_runner_auto_accepts_high_signal_and_is_idempotent() -> None:
    # Six months of recurring, non-webmail correspondence pushes confidence
    # and durability past the deterministic auto-accept thresholds.
    months = tuple(f"2026-{month:02d}" for month in range(1, 7))
    docs = _email_docs(months=months, per_month=3)
    connection, _store = _seed_documents(docs)
    service = _service()
    try:
        runner = _document_runner(connection, service)
        first = runner.run(_cfg(EMAIL, limit=10))
        assert first.status == "completed"
        assert first.counters.tally.candidates == 1
        assert first.counters.tally.created == 1
        assert service.counts()["active"] == 1

        # Re-running the same window never duplicates the memory.
        second = runner.run(_cfg(EMAIL, limit=10))
        assert second.counters.tally.created == 0
        assert service.counts()["active"] == 1
        (memory,) = service.list()
        assert "bcg.com" in memory.content
        assert all(
            ref["source_type"] == EMAIL
            for ref in service.evidence_for(memory.memory_id)
        )
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Financial: counts only, never content, never a model call
# ---------------------------------------------------------------------------


def test_financial_discover_is_single_count_unit() -> None:
    docs = [
        _doc(
            f"fin-{index}",
            source_type=FINANCIAL,
            created_at="2026-01-01T00:00:00+00:00",
        )
        for index in range(3)
    ]
    connection, store = _seed_documents(docs)
    try:
        adapter = DocumentCurationAdapter(store)
        (unit,) = adapter.discover(_cfg(FINANCIAL))
        assert str(unit.context.count) == "3"  # type: ignore[union-attr]
        assert unit.records == ()
        assert unit.unit_id == "financial-corpus-v1"
    finally:
        connection.close()


def test_financial_never_emits_candidates_or_model_calls() -> None:
    docs = [
        _doc(
            f"fin-{index}",
            source_type=FINANCIAL,
            created_at="2026-01-01T00:00:00+00:00",
        )
        for index in range(3)
    ]
    connection, _store = _seed_documents(docs)
    service = _service()
    try:
        client = _CountingEchoClient()
        chunk_store = ChunkStore(connection)
        for mode in ("deterministic", "llm"):
            runner = _document_runner(
                connection, service, client=client, chunk_store=chunk_store
            )
            report = runner.run(_cfg(FINANCIAL, extraction=mode))
            assert report.status == "completed"
            assert report.counters.tally.candidates == 0
            assert report.counters.tally.writes == 0
            assert report.counters.model_calls == 0
            assert service.counts()["memories"] == 0
        assert client.calls == 0  # financial content never reaches the model
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Generic documents: deterministic proposes nothing; LLM gates evidence
# ---------------------------------------------------------------------------


def test_generic_document_deterministic_proposes_nothing_and_reads_no_chunks() -> None:
    doc = _doc(
        "d-1",
        source_type="pdf",
        created_at="2026-01-01T00:00:00+00:00",
    )
    connection, store = _seed_documents([doc])
    try:
        chunk_store = ChunkStore(connection)
        chunk_store.add(_chunk("d-1", "The user works at BCG."))
        adapter = DocumentCurationAdapter(store, chunk_store)
        units = adapter.discover(_cfg(GENERIC_DOCUMENT_SOURCE, limit=10))
        (unit,) = units
        assert unit.records == ()  # deterministic mode never loads chunk text
        result = adapter.process(unit)
        assert result.candidates == ()
        assert result.model_calls == 0
    finally:
        connection.close()


def test_generic_document_llm_proposal_is_gated_on_allowed_documents() -> None:
    from personal_ai.memory import to_document_candidate
    from personal_ai.memory.adapters import DocumentProposal

    good = to_document_candidate(
        DocumentProposal(
            statement="The user works at BCG",
            kind="work",
            temporal_scope="current",
            confidence=0.9,
            evidence_document_ids=("d-1",),
        ),
        source_type="pdf",
        source_id="d-1",
        allowed_ids=frozenset({"d-1"}),
        id_timestamps={"d-1": "2026-01-01T00:00:00+00:00"},
    )
    assert good[0] is not None
    assert good[0].evidence[0].source_document_id == "d-1"  # type: ignore[union-attr]

    unknown_ref = to_document_candidate(
        DocumentProposal(
            statement="The user works at BCG",
            kind="work",
            temporal_scope="current",
            confidence=0.9,
            evidence_document_ids=("other-1",),
        ),
        source_type="pdf",
        source_id="d-1",
        allowed_ids=frozenset({"d-1"}),
        id_timestamps={"d-1": "2026-01-01T00:00:00+00:00"},
    )
    assert unknown_ref[0] is None
    assert unknown_ref[1] == "evidence_unknown"


def test_generic_document_llm_runner_creates_and_reruns_idempotently() -> None:
    docs = [
        _doc(
            "d-1",
            source_type="pdf",
            created_at="2026-01-01T00:00:00+00:00",
        ),
        _doc(
            "d-2",
            source_type="txt",
            created_at="2026-02-01T00:00:00+00:00",
        ),
    ]
    connection = connect_database(":memory:")
    try:
        store = DocumentStore(connection)
        for document in docs:
            store.add(document)
        chunk_store = ChunkStore(connection)
        chunk_store.add(_chunk("d-1", "Product work at BCG, building AI tooling."))
        chunk_store.add(_chunk("d-2", "Personal notes on local-first software."))
        service = _service()
        client = _CountingEchoClient(
            {
                "d-1": ("The user works at BCG as a product manager.", "work"),
                "d-2": ("The user prefers local-first software tools.", "preference"),
            }
        )
        runner = MemoryCurationRunner(
            store=CurationStore(connection),
            adapter=DocumentCurationAdapter(store, chunk_store, client=client),
            memory_service=service,
        )
        report = runner.run(_cfg(GENERIC_DOCUMENT_SOURCE, extraction="llm", limit=10))
        assert report.status == "completed"
        assert report.counters.tally.candidates == 2
        assert report.counters.tally.created == 2
        assert client.calls == 2

        again = runner.run(_cfg(GENERIC_DOCUMENT_SOURCE, extraction="llm", limit=10))
        assert again.counters.tally.created == 0
        assert service.counts()["active"] == 2
        assert all(
            ref["source_type"] in {"pdf", "txt"}
            for memory in service.list()
            for ref in service.evidence_for(memory.memory_id)
        )
    finally:
        connection.close()


def test_generic_document_llm_without_client_raises() -> None:
    connection, _store = _seed_documents(
        [_doc("d-1", source_type="pdf", created_at="2026-01-01T00:00:00+00:00")]
    )
    try:
        with pytest.raises(CurationConfigError):
            _document_runner(connection).run(
                _cfg(GENERIC_DOCUMENT_SOURCE, extraction="llm")
            )
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Workout and activity: deterministic-only, single bounded aggregate unit
# ---------------------------------------------------------------------------


def test_workout_adapter_is_deterministic_only_and_reports_version() -> None:
    class _Query:
        def list_workouts(self, *, limit: int | None = None) -> list[WorkoutSummary]:
            return []

    adapter = WorkoutCurationAdapter(_Query())
    assert adapter.llm_enabled is False
    assert adapter.version(CurationExtractionMode.LLM) == (
        WORKOUT_EXTRACTOR_VERSION,
        "",
    )
    assert adapter.supports(WORKOUTS)
    assert not adapter.supports(EMAIL)


def test_workout_adapter_emits_habit_only_above_threshold() -> None:
    class _Query:
        def list_workouts(self, *, limit: int | None = None) -> list[WorkoutSummary]:
            return [
                _workout(
                    f"w-{month}-{index}",
                    f"2026-{month:02d}-{index + 1:02d}T08:00:00+00:00",
                )
                for month in (1, 2, 3)
                for index in range(4)
            ]

    adapter = WorkoutCurationAdapter(_Query())
    units = adapter.discover(_cfg(WORKOUTS))
    (unit,) = units
    assert unit.unit_id == "workout-corpus-v1"
    result = adapter.process(unit)
    (candidate,) = result.candidates
    assert candidate.statement == "The user maintains a regular workout routine."  # type: ignore[union-attr]
    assert candidate.kind is MemoryKind.HABIT  # type: ignore[union-attr]
    assert all(ref.source_type == WORKOUTS for ref in candidate.evidence)  # type: ignore[union-attr]


def test_workout_runner_defers_behavioral_and_reruns_idempotently() -> None:
    class _Query:
        def list_workouts(self, *, limit: int | None = None) -> list[WorkoutSummary]:
            return [
                _workout(
                    f"w-{month}-{index}",
                    f"2026-{month:02d}-{index + 1:02d}T08:00:00+00:00",
                )
                for month in (1, 2, 3)
                for index in range(4)
            ]

    connection = connect_database(":memory:")
    service = _service()
    try:
        adapter = WorkoutCurationAdapter(_Query())
        runner = MemoryCurationRunner(
            store=CurationStore(connection), adapter=adapter, memory_service=service
        )
        for _ in range(2):
            report = runner.run(_cfg(WORKOUTS))
            assert report.status == "completed"
            assert report.counters.tally.candidates == 1
            assert report.counters.tally.deferred == 1
            assert report.counters.tally.writes == 0
        assert service.counts()["memories"] == 0
    finally:
        connection.close()


def test_activity_runner_defers_behavioral_and_reruns_idempotently() -> None:
    connection = connect_database(":memory:")
    service = _service()
    try:
        event_store = EventStore(connection)
        events = [
            _visit("https://docs.example.com/", f"2026-0{m}-0{d}T10:00:00+00:00")
            for m in (1, 2, 3)
            for d in (1, 2, 3)
        ]
        for event in events:
            event_store.save_event(event)
        runner = MemoryCurationRunner(
            store=CurationStore(connection),
            adapter=EventCurationAdapter(event_store),
            memory_service=service,
        )
        for _ in range(2):
            report = runner.run(_cfg(ACTIVITY))
            assert report.status == "completed"
            assert report.counters.tally.candidates == 1
            assert report.counters.tally.deferred == 1
            assert report.counters.tally.writes == 0
        assert service.counts()["memories"] == 0
    finally:
        connection.close()


def test_activity_adapter_filters_chrome_history_url_visits() -> None:
    connection = connect_database(":memory:")
    try:
        event_store = EventStore(connection)
        events = [
            _visit(
                "https://docs.example.com/", f"2026-0{m}-0{index + 1}T10:00:00+00:00"
            )
            for m in (1, 2, 3)
            for index in range(3)
        ]
        # A non-chrome-history + non-url-visit event must be filtered out.
        events.append(
            Event(
                id="youtube-1",
                event_type="video_watch",
                event_time="2026-01-05T10:00:00+00:00",
                source="youtube",
                url="https://youtube.com/watch?v=abc",
                metadata={},
            )
        )
        for event in events:
            event_store.save_event(event)

        adapter = EventCurationAdapter(event_store)
        assert not adapter.llm_enabled
        assert adapter.version(CurationExtractionMode.LLM) == (
            ACTIVITY_EXTRACTOR_VERSION,
            "",
        )
        units = adapter.discover(_cfg(ACTIVITY))
        (unit,) = units
        assert str(unit.context.count) == "9"  # type: ignore[union-attr]
        (candidate,) = adapter.process(unit).candidates
        assert (
            candidate.statement
            == "The user frequently visits the website docs.example.com."
        )  # type: ignore[union-attr]
        assert candidate.kind is MemoryKind.INTEREST  # type: ignore[union-attr]
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Adaptive LLM gating: min_signal and sample downgrade units
# ---------------------------------------------------------------------------


def test_min_signal_downgrades_low_signal_llm_email_units() -> None:
    docs = _email_docs(months=("2026-01", "2026-02", "2026-03"))
    connection, store = _seed_documents(docs)
    service = _service()
    try:
        client = _CountingEchoClient()
        classifier = DocumentCurationAdapter(
            store, ChunkStore(connection), client=client
        )
        runner = MemoryCurationRunner(
            store=CurationStore(connection),
            adapter=classifier,
            memory_service=service,
        )
        report = runner.run(_cfg(EMAIL, extraction="llm", min_signal=10))
        assert report.counters.units_signal_skipped == 1
        assert report.counters.model_calls == 0
        assert client.calls == 0
    finally:
        connection.close()


def test_sample_budget_limits_llm_calls_across_units() -> None:
    docs: list[Document] = []
    docs += _email_docs(
        "bcg.com",
        months=("2026-01", "2026-02", "2026-03", "2026-04"),
        prefix="email-bcg",
    )
    docs += _email_docs(
        "acme.io", months=("2026-01", "2026-02", "2026-03"), prefix="email-acme"
    )
    connection, store = _seed_documents(docs)
    service = _service()
    try:
        client = _CountingEchoClient()
        adapter = DocumentCurationAdapter(store, ChunkStore(connection), client=client)
        runner = MemoryCurationRunner(
            store=CurationStore(connection),
            adapter=adapter,
            memory_service=service,
        )
        report = runner.run(_cfg(EMAIL, extraction="llm", sample=1))
        assert report.counters.units_discovered == 2
        assert report.counters.units_signal_skipped == 1
        assert report.counters.model_calls == 1
        assert client.calls == 1
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Denied gate (mandatory regression) and privacy
# ---------------------------------------------------------------------------


def test_denied_write_gate_fails_run_and_writes_nothing_for_documents() -> None:
    docs = _email_docs(months=("2026-01", "2026-02", "2026-03", "2026-04"))
    connection, store = _seed_documents(docs)
    service = _service()
    try:
        adapter = DocumentCurationAdapter(store, ChunkStore(connection))
        runner = MemoryCurationRunner(
            store=CurationStore(connection),
            adapter=adapter,
            memory_service=service,
            curator=DeniedCurator(),  # type: ignore[arg-type]
        )
        with pytest.raises(ApprovalRequiredError):
            runner.run(_cfg(EMAIL))
        assert service.counts()["memories"] == 0
        (run,) = CurationStore(connection).list_runs()
        assert run["status"] == "failed"
        assert run["failure_reason"] == "approval_required"
    finally:
        connection.close()


class DeniedCurator:
    """Deterministic policy agrees, but the ``memory.write`` gate is denied."""

    def evaluate(self, candidate):
        return MemoryPolicy().evaluate(candidate)

    def curate(self, candidate):
        raise ApprovalRequiredError("denied")


class _FakeModelClient:
    """Scripted stand-in for the narrow ``chat`` model surface."""

    def __init__(self, *responses: str | Exception) -> None:
        self._responses = list(responses)
        self._last: str | Exception = _doc_proposal("d-1")
        self.calls: list[tuple[object, bool, object]] = []

    def chat(self, messages: object, *, think: bool, format: object) -> ChatResponse:
        self.calls.append((messages, think, format))
        if self._responses:
            self._last = self._responses.pop(0)
        if isinstance(self._last, Exception):
            raise self._last
        return ChatResponse(content=self._last, model="fake", done=True)


def test_source_report_summary_is_aggregate_only() -> None:
    docs: list[Document] = []
    docs += _email_docs(months=("2026-01", "2026-02", "2026-03"))
    docs += [
        _doc(
            "d-1",
            source_type="pdf",
            created_at="2026-01-05T00:00:00+00:00",
            subject="Quarterly plans",
        )
    ]
    connection, store = _seed_documents(docs)
    service = _service()
    try:
        chunk_store = ChunkStore(connection)
        chunk_store.add(_chunk("d-1", "The user works at BCG as a product manager."))
        adapter = DocumentCurationAdapter(store, chunk_store, client=_FakeModelClient())
        reports: list[CurationReport] = []
        runner = MemoryCurationRunner(
            store=CurationStore(connection), adapter=adapter, memory_service=service
        )
        reports.append(runner.run(_cfg(EMAIL, limit=10)))
        reports.append(
            runner.run(_cfg(GENERIC_DOCUMENT_SOURCE, extraction="llm", limit=10))
        )
        for report in reports:
            summary = json.dumps(report.summary(), sort_keys=True)
            assert "bcg.com" not in summary
            assert "d-1" not in summary
            assert "product manager" not in summary
            assert "Weekly update" not in summary
    finally:
        connection.close()
