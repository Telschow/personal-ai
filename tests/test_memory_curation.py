"""Tests for durable, resumable, policy-gated corpus-wide memory curation.

Coverage focuses on the Slice 6 invariants:

* every auto-write still travels through the deterministic policy-gated
  curator — including the mandatory regression: a policy-ACCEPTED candidate
  whose ``memory.write`` gate is denied never writes (run fails, zero rows);
* dry runs analyze but write nothing and create no rows at all;
* deterministic runs are idempotent and accumulate evidence across reruns;
* resume completes exactly the unfinished units of a failed/interrupted run
  and never re-processes completed units;
* ``require_approval`` candidates are parked in a review queue (the only
  content-bearing table) and never auto-written, with id-only evidence;
* reconciliation conflicts are counted and never silently written;
* unit timeout and ``max_model_calls`` bound the run;
* reports are aggregate-only (no statement text leaks into ``summary()``);
* the adapter rejects unsupported source types.

All tests are hermetic: no Ollama, no network, no real personal data. Model
output is scripted through a fake client, and every write flows through a real
``MemoryService``/``MemoryReconciler`` backed by an in-memory SQLite store.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from personal_ai.agents.policy import ApprovalRequiredError
from personal_ai.documents.conversations import (
    Conversation,
    ConversationMessage,
)
from personal_ai.memory import (
    ConversationCurationAdapter,
    CurationConfig,
    CurationConfigError,
    CurationResumeError,
    CurationSourceError,
    CurationStore,
    MemoryCurationRunner,
    MemoryService,
    MemoryStore,
)
from personal_ai.memory.models import (
    MemoryCandidate,
    MemoryEvidenceRef,
)
from personal_ai.ollama_client import ChatResponse
from personal_ai.storage import ConversationStore, connect_database


def _service() -> MemoryService:
    return MemoryService(MemoryStore(sqlite3.connect(":memory:")))


def _conv(
    conv_id: str = "conv-1",
    *,
    source_type: str = "chatgpt",
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> Conversation:
    return Conversation(
        id=conv_id,
        title=f"Conversation {conv_id}",
        source_type=source_type,
        created_at=created_at,
        modified_at=created_at,
        metadata={},
    )


def _msg(
    msg_id: str,
    content: str,
    *,
    role: str = "user",
    index: int = 0,
    conv_id: str = "conv-1",
) -> ConversationMessage:
    return ConversationMessage(
        id=msg_id,
        conversation_id=conv_id,
        message_index=index,
        role=role,
        speaker=role.capitalize(),
        content_text=content,
        content_type="text",
        timestamp=None,
        is_active_branch=True,
        metadata={},
    )


def _prop(
    statement: str = "The user works at Example Corp",
    kind: str = "work",
    temporal: str = "current",
    confidence: float = 0.9,
    evidence: tuple[str, ...] = ("m1",),
) -> dict[str, object]:
    return {
        "statement": statement,
        "kind": kind,
        "temporal_scope": temporal,
        "confidence": confidence,
        "evidence_message_ids": list(evidence),
    }


def _batch_json(*proposals: dict[str, object]) -> str:
    return json.dumps({"proposals": list(proposals)})


class _FakeModelClient:
    """Scripted stand-in for the narrow ``chat`` model surface."""

    def __init__(self, *responses: str | Exception) -> None:
        self._responses = list(responses)
        self._last: str | Exception = _batch_json(_prop())
        self.calls: list[tuple[object, bool, object]] = []

    def chat(self, messages: object, *, think: bool, format: object) -> ChatResponse:
        self.calls.append((messages, think, format))
        if self._responses:
            self._last = self._responses.pop(0)
        if isinstance(self._last, Exception):
            raise self._last
        return ChatResponse(content=self._last, model="fake", done=True)


class _EchoEvidenceClient:
    """Returns a proposal whose evidence references a real message in window."""

    def chat(self, messages: object, *, think: bool, format: object) -> ChatResponse:
        prompt = messages[-1].content if hasattr(messages[-1], "content") else ""  # type: ignore[union-attr,index]
        match = re.search(r"\[(m-[a-z0-9-]+)\] USER:", str(prompt))
        message_id = match.group(1) if match else "m1"
        return ChatResponse(
            content=_batch_json(_prop(evidence=(message_id,))),
            model="fake",
            done=True,
        )


def _seed(*conversations) -> tuple[sqlite3.Connection, ConversationStore]:
    connection = connect_database(":memory:")
    store = ConversationStore(connection)
    for conv, messages in conversations:
        store.save_conversation(conv)
        store.save_messages(messages)
    return connection, store


def _runner(
    connection: sqlite3.Connection,
    service: MemoryService | None = None,
    llm: object | None = None,
) -> MemoryCurationRunner:
    store = ConversationStore(connection)
    adapter = ConversationCurationAdapter(store, client=llm)
    curation_store = CurationStore(connection)
    return MemoryCurationRunner(
        store=curation_store, adapter=adapter, memory_service=service
    )


def _cfg(**overrides) -> CurationConfig:
    base = {"source_type": "chatgpt", "limit": 10}
    base.update(overrides)
    return CurationConfig(**base)


def _work_pair(created_at: str = "2026-01-01T00:00:00+00:00", conv_id: str = "conv-1"):
    return _conv(conv_id, created_at=created_at), (
        _msg(f"m-{conv_id}", "I work at Example Corp", conv_id=conv_id),
    )


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_config_rejects_dry_run_with_resume() -> None:
    with pytest.raises(CurationConfigError):
        _cfg(dry_run=True, resume=True).validate()


def test_config_rejects_zero_bounds() -> None:
    with pytest.raises(CurationConfigError):
        _cfg(limit=0).validate()
    with pytest.raises(CurationConfigError):
        _cfg(max_messages=0).validate()
    with pytest.raises(CurationConfigError):
        _cfg(unit_timeout_seconds=0).validate()


def test_config_rejects_empty_source() -> None:
    with pytest.raises(CurationConfigError):
        _cfg(source_type="").validate()


# ---------------------------------------------------------------------------
# Dry run: analyze only, no writes, no rows
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing() -> None:
    connection, _ = _seed(_work_pair())
    service = _service()
    try:
        report = _runner(connection, service).run(_cfg(dry_run=True))
        assert report.dry_run
        assert report.run_id == ""
        assert report.status == "dry_run"
        assert report.dry_tally is not None
        assert report.dry_tally.candidates == 1
        assert report.dry_tally.accepted == 1
        assert report.dry_tally.would_create == 1
        assert service.counts()["memories"] == 0
        assert CurationStore(connection).list_runs() == ()
        assert CurationStore(connection).list_review() == ()
        # No unit checkpoints are created by a dry run either.
        assert (
            connection.execute("SELECT COUNT(*) FROM memory_curation_units").fetchone()[
                0
            ]
            == 0
        )
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Deterministic idempotent run + evidence accumulation
# ---------------------------------------------------------------------------


def test_deterministic_fresh_run_creates_memory() -> None:
    connection, _ = _seed(_work_pair())
    service = _service()
    try:
        report = _runner(connection, service).run(_cfg())
        assert report.status == "completed"
        assert report.counters.units_discovered == 1
        assert report.counters.units_completed == 1
        assert report.counters.tally.candidates == 1
        assert report.counters.tally.accepted == 1
        assert report.counters.tally.writes == 1
        assert report.counters.tally.created == 1
        assert service.counts()["active"] == 1
        memory = service.list()[0]
        assert memory.content == "The user works at Example Corp"
        assert len(service.evidence_for(memory.memory_id)) == 1
    finally:
        connection.close()


def test_deterministic_rerun_is_idempotent_and_accumulates_evidence() -> None:
    connection, _ = _seed(_work_pair(conv_id="conv-1"))
    service = _service()
    store = ConversationStore(connection)
    try:
        runner = _runner(connection, service)
        first = runner.run(_cfg(limit=10))
        # A second conversation expressing the same fact adds evidence to the
        # single memory rather than creating a duplicate.
        store.save_conversation(_conv("conv-2", created_at="2026-01-02T00:00:00+00:00"))
        store.save_messages(
            (_msg("m-conv-2", "I work at Example Corp", conv_id="conv-2"),)
        )
        second = runner.run(_cfg(limit=1, offset=1))
        assert first.counters.tally.created == 1
        assert second.counters.tally.writes == 1
        assert second.counters.tally.created == 0
        assert second.counters.tally.updated == 1
        assert service.counts()["active"] == 1
        memory = service.list()[0]
        assert len(service.evidence_for(memory.memory_id)) == 2
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_resume_without_previous_run_raises() -> None:
    connection, _ = _seed()
    try:
        with pytest.raises(CurationResumeError):
            _runner(connection, _service()).run(_cfg(resume=True))
    finally:
        connection.close()


def test_resume_completes_failed_units_and_skips_completed() -> None:
    connection, _ = _seed(
        _work_pair(created_at="2026-01-01T00:00:00+00:00", conv_id="conv-a"),
        _work_pair(created_at="2026-01-02T00:00:00+00:00", conv_id="conv-b"),
    )
    service = _service()
    curation = CurationStore(connection)

    # Run with a failing model so all units fail (retries still fail).
    failing_llm = _FakeModelClient(RuntimeError("down"))
    runner = _runner(connection, service, llm=failing_llm)
    first = runner.run(_cfg(extraction="llm"))
    assert first.status == "failed"
    assert first.counters.units_failed == 2

    units_after = curation.list_units(first.run_id)
    failed_ids = {str(u["unit_id"]) for u in units_after if u["status"] == "failed"}

    # Now resume with a healthy LLM; failed units should be retried and the
    # run should complete with writes.
    healthy_llm = _EchoEvidenceClient()
    runner2 = _runner(connection, service, llm=healthy_llm)
    resumed = runner2.run(_cfg(extraction="llm", resume=True))
    assert resumed.status == "completed"
    assert resumed.run_id == first.run_id
    assert resumed.counters.units_retried == 2
    assert resumed.counters.units_completed == 2
    assert resumed.counters.tally.writes == 2
    # Both conversations assert the same fact, so evidence accumulates on a
    # single memory rather than creating a duplicate.
    assert service.counts()["active"] == 1
    # All units are now completed.
    assert all(u["status"] == "completed" for u in curation.list_units(first.run_id))
    assert failed_ids
    # The failed units were retried (not skipped).
    assert resumed.counters.units_skipped == 0
    assert resumed.counters.units_recovered_stale == 0
    connection.close()


def test_resume_recovers_stale_running_units() -> None:
    connection, _ = _seed(_work_pair(conv_id="conv-a"))
    service = _service()
    curation = CurationStore(connection)
    # Manually plant a run checkpointed as running, with a running unit row,
    # as though it were interrupted mid-flight.
    run_id = "cur-test-interrupted"
    curation.create_run(
        run_id=run_id,
        source_type="chatgpt",
        extraction="deterministic",
        status="running",
        dry_run=False,
        extractor_version="conversation-deterministic-v1",
        policy_version="memory-policy-v1",
        prompt_version="",
        model_name=None,
        started_at="2026-01-01T00:00:00+00:00",
        config_json=json.dumps({"limit": 10, "offset": 0, "batch_size": 500}),
    )
    curation.save_unit(
        run_id=run_id,
        unit_id="conv-a",
        unit_index=1,
        source_type="chatgpt",
        extraction="deterministic",
        model_name=None,
        status="running",
        attempts=1,
        started_at="2026-01-01T00:00:00+00:00",
    )
    runner = _runner(connection, service)
    resumed = runner.run(_cfg(resume=True))
    assert resumed.run_id == run_id
    assert resumed.status == "completed"
    assert resumed.counters.units_recovered_stale == 1
    assert service.counts()["active"] == 1
    connection.close()


# ---------------------------------------------------------------------------
# Denied gate (mandatory regression)
# ---------------------------------------------------------------------------


def test_denied_write_gate_fails_run_and_writes_nothing() -> None:
    connection, _ = _seed(_work_pair())
    service = _service()
    store_dep = ConversationStore(connection)
    adapter = ConversationCurationAdapter(store_dep, client=None)
    # Deterministic mode accepts the candidate; the curator's auto-approver is
    # overridden so the policy engine's ``memory.write`` gate is denied.
    from personal_ai.memory.curation import MemoryCurationRunner as Runner

    class DeniedCurator:
        def evaluate(self, candidate):
            from personal_ai.memory.policy import MemoryPolicy

            return MemoryPolicy().evaluate(candidate)

        def curate(self, candidate):
            raise ApprovalRequiredError("denied")

    curation = CurationStore(connection)
    runner = Runner(
        store=curation,
        adapter=adapter,
        memory_service=service,
        curator=DeniedCurator(),  # type: ignore[arg-type]
    )
    with pytest.raises(ApprovalRequiredError):
        runner.run(_cfg(dry_run=False))
    assert service.counts()["memories"] == 0
    # The run is checkpointed as failed with the approval_required reason.
    runs = curation.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["failure_reason"] == "approval_required"
    connection.close()


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


def _require_approval_conv() -> tuple[Conversation, tuple[ConversationMessage, ...]]:
    return _conv("conv-salary"), (
        _msg("m1", "My salary is 120k", conv_id="conv-salary"),
    )


def test_require_approval_review_row_with_id_only_evidence() -> None:
    # The review queue is only reachable when the model *proposes* a
    # statement the deterministic policy escalates (the deterministic
    # extractor drops sensitive content before policy). Use the LLM surface.
    connection, _ = _seed(_require_approval_conv())
    service = _service()
    curation = CurationStore(connection)
    salary_proposal = _prop(statement="The user's salary is 120k", kind="personal_fact")
    llm = _FakeModelClient(_batch_json(salary_proposal))
    runner = _runner(connection, service, llm=llm)
    report = runner.run(_cfg(extraction="llm"))
    assert report.status == "completed"
    assert report.counters.tally.require_approval == 1
    assert report.counters.tally.writes == 0
    assert report.counters.review_queued == 1
    assert service.counts()["memories"] == 0
    review = curation.list_review()
    assert len(review) == 1
    row = review[0]
    assert row["status"] == "pending"
    assert "120k" in str(row["statement"])
    # Evidence is id-only (never message content).
    evidence = row["evidence_json"]
    assert isinstance(evidence, list)
    assert len(evidence) == 1
    ref = evidence[0]
    assert ref["source_type"] == "chatgpt"
    assert ref["source_id"] == "conv-salary"
    assert ref["source_document_id"] == "m1"
    connection.close()


# ---------------------------------------------------------------------------
# Conflicts counted, never silently written
# ---------------------------------------------------------------------------


def test_conflict_is_counted_and_not_written() -> None:
    connection, _ = _seed(
        _work_pair(created_at="2026-01-01T00:00:00+00:00", conv_id="conv-a"),
        (
            _conv("conv-b", created_at="2026-01-02T00:00:00+00:00"),
            (_msg("m2", "I'm a software engineer at Example Corp", conv_id="conv-b"),),
        ),
    )
    service = _service()
    runner = _runner(connection, service)
    # The second, overlapping-but-ambiguous same-kind statement conflicts with
    # the first instead of being silently written.
    report = runner.run(_cfg(limit=10))
    assert report.counters.tally.created == 1
    assert report.counters.tally.conflicts == 1
    assert report.counters.tally.writes == 1  # only the first (create) wrote
    assert service.counts()["active"] == 1  # no duplicate written
    connection.close()


# ---------------------------------------------------------------------------
# Bounds: timeout and max_model_calls
# ---------------------------------------------------------------------------


class _SleepyClient:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def chat(self, messages: object, *, think: bool, format: object) -> ChatResponse:
        import time

        time.sleep(self.seconds)
        return ChatResponse(content=_batch_json(_prop()), model="fake", done=True)


def test_unit_timeout_marks_unit_failed() -> None:
    connection, _ = _seed(_work_pair())
    service = _service()
    runner = _runner(connection, service, llm=_SleepyClient(seconds=1.0))
    report = runner.run(_cfg(extraction="llm", unit_timeout_seconds=0.01))
    # The timeout marks the unit failed (so the run is resumable) but does not
    # abort the run.
    assert report.status == "failed"
    assert report.counters.units_completed == 0
    assert report.counters.units_failed == 1
    assert report.counters.errors.get("timeout", 0) == 1
    assert service.counts()["memories"] == 0
    connection.close()


def test_max_model_calls_breaks_before_next_unit() -> None:
    connection, _ = _seed(
        _work_pair(created_at="2026-01-01T00:00:00+00:00", conv_id="conv-a"),
        _work_pair(created_at="2026-01-02T00:00:00+00:00", conv_id="conv-b"),
    )
    service = _service()
    llm = _FakeModelClient(_batch_json(_prop()))
    runner = _runner(connection, service, llm=llm)
    report = runner.run(_cfg(extraction="llm", max_model_calls=1))
    assert report.counters.model_calls == 1
    assert report.counters.units_completed == 1
    connection.close()


# ---------------------------------------------------------------------------
# Source rejection
# ---------------------------------------------------------------------------


def test_adapter_rejects_unsupported_source() -> None:
    connection, _ = _seed()
    try:
        with pytest.raises(CurationSourceError):
            _runner(connection, _service()).run(_cfg(source_type="email"))
    finally:
        connection.close()


def test_llm_mode_without_client_raises() -> None:
    connection, _ = _seed(_work_pair())
    try:
        with pytest.raises(CurationConfigError):
            _runner(connection, _service()).run(_cfg(extraction="llm"))
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Aggregate-only reporting
# ---------------------------------------------------------------------------


def test_report_summary_is_aggregate_only() -> None:
    connection, _ = _seed(_require_approval_conv())
    service = _service()
    salary_proposal = _prop(statement="The user's salary is 120k", kind="personal_fact")
    llm = _FakeModelClient(_batch_json(salary_proposal))
    runner = _runner(connection, service, llm=llm)
    report = runner.run(_cfg(extraction="llm"))
    summary = json.dumps(report.summary(), sort_keys=True)
    # No statement text ever leaks into the summary.
    assert "120k" not in summary
    assert "salary" not in summary
    # Versioning is recorded.
    assert report.versions["policy"] == "memory-policy-v1"
    assert report.versions["extractor"] == "conversation-llm-proposals-v1"
    connection.close()


# ---------------------------------------------------------------------------
# Phase 25 (P3-1): review-queue deduplication
# ---------------------------------------------------------------------------


def _evidence_refs(*document_ids: str) -> str:
    refs = [
        {
            "source_type": "chatgpt",
            "source_id": f"conv-{doc_id}",
            "source_document_id": doc_id,
        }
        for doc_id in document_ids
    ]
    return json.dumps(list(refs), sort_keys=True)


def _review_kwargs(
    *,
    statement: str = "The user works at Example Corp",
    category: str = "conflict",
    reason: str = "ambiguous_related_fact",
    kind: str = "work",
    temporal_scope: str = "current",
    document_id: str = "m-conv-a",
    run_id: str = "cur-test",
    unit_id: str = "conv-a",
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "unit_id": unit_id,
        "source_type": "chatgpt",
        "kind": kind,
        "temporal_scope": temporal_scope,
        "confidence": 0.9,
        "importance": 3.0,
        "statement": statement,
        "evidence_json": _evidence_refs(document_id),
        "candidate_json": "{}",
        "created_at": "2026-01-01T00:00:00+00:00",
        "category": category,
        "reason": reason,
    }


def test_review_dedupe_same_obligation_single_row() -> None:
    connection = connect_database(":memory:")
    curation = CurationStore(connection)
    try:
        created, review_id = curation.enqueue_review_if_missing(**_review_kwargs())
        assert created is True
        again, same_review_id = curation.enqueue_review_if_missing(**_review_kwargs())
        assert again is False
        assert same_review_id == review_id
        assert len(curation.list_review()) == 1
        # The public enqueue_review surface stays idempotent too.
        assert curation.enqueue_review(**_review_kwargs()) == review_id
        assert len(curation.list_review()) == 1
    finally:
        connection.close()


def test_review_dedupe_categories_are_distinct_obligations() -> None:
    # Same fact and evidence, but an approval escalation and a reconciliation
    # conflict are different review obligations: the key is never just the
    # category, and never just the statement either.
    connection = connect_database(":memory:")
    curation = CurationStore(connection)
    try:
        conflict_id = curation.enqueue_review(**_review_kwargs(category="conflict"))
        approval_id = curation.enqueue_review(
            **_review_kwargs(category="require_approval")
        )
        assert conflict_id != approval_id
        assert len(curation.list_review()) == 2
        # Both are individually idempotent.
        assert (
            curation.enqueue_review(**_review_kwargs(category="conflict"))
            == conflict_id
        )
        assert (
            curation.enqueue_review(**_review_kwargs(category="require_approval"))
            == approval_id
        )
        assert len(curation.list_review()) == 2
    finally:
        connection.close()


def test_review_dedupe_identical_statement_distinct_evidence_stays_distinct() -> None:
    # Provenance participates in the obligation identity: the same statement
    # supported by two different messages is two review obligations.
    connection = connect_database(":memory:")
    curation = CurationStore(connection)
    try:
        first = curation.enqueue_review(
            **_review_kwargs(document_id="m-conv-a", unit_id="conv-a")
        )
        second = curation.enqueue_review(
            **_review_kwargs(document_id="m-conv-b", unit_id="conv-b")
        )
        assert first != second
        rows = curation.list_review()
        assert len(rows) == 2
        assert {row["evidence_json"][0]["source_document_id"] for row in rows} == {
            "m-conv-a",
            "m-conv-b",
        }
        assert {row["evidence_json"][0]["source_type"] for row in rows} == {"chatgpt"}
    finally:
        connection.close()


def test_review_dedupe_resolved_row_is_not_resurrected() -> None:
    # A rejected (or approved) obligation is never silently re-opened by a
    # later run: the queue stays idempotent across the whole review lifecycle.
    connection = connect_database(":memory:")
    curation = CurationStore(connection)
    try:
        created, review_id = curation.enqueue_review_if_missing(**_review_kwargs())
        assert created is True
        assert curation.set_review_status(
            review_id, "rejected", reviewed_at="2026-01-02T00:00:00+00:00"
        )
        again, same_id = curation.enqueue_review_if_missing(**_review_kwargs())
        assert again is False
        assert same_id == review_id
        assert len(curation.list_review()) == 1
        assert curation.list_pending_review() == ()
        assert curation.get_review(review_id)["status"] == "rejected"
    finally:
        connection.close()


def test_review_dedupe_is_language_agnostic() -> None:
    # Phase 21-23 multilingual behavior is untouched by deduplication:
    # German and Spanish obligations dedupe exactly like English ones.
    connection = connect_database(":memory:")
    curation = CurationStore(connection)
    try:
        de = _review_kwargs(statement="Der Nutzer arbeitet bei Example Corp")
        created, de_id = curation.enqueue_review_if_missing(**de)
        again, de_id2 = curation.enqueue_review_if_missing(**de)
        assert created and not again
        assert de_id2 == de_id

        es = _review_kwargs(
            statement="El usuario trabaja en Example Corp", category="require_approval"
        )
        created, es_id = curation.enqueue_review_if_missing(**es)
        again, es_id2 = curation.enqueue_review_if_missing(**es)
        assert created and not again
        assert es_id2 == es_id

        assert len(curation.list_review()) == 2
    finally:
        connection.close()


def test_review_dedupe_concurrent_enqueue_is_atomic(tmp_path: Path) -> None:
    # Two curators racing on the same file database cannot both pass the
    # existence check and enqueue duplicates (BEGIN IMMEDIATE check-and-insert).
    import threading

    db = tmp_path / "dedupe.db"
    barrier = threading.Barrier(3)
    ids: list[tuple[bool, int]] = []
    lock = threading.Lock()

    def worker() -> None:
        connection = connect_database(db)
        try:
            curation = CurationStore(connection)
            barrier.wait()
            result = curation.enqueue_review_if_missing(**_review_kwargs())
            with lock:
                ids.append(result)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    connection = connect_database(db)
    try:
        curation = CurationStore(connection)
        assert [created for created, _ in ids].count(True) == 1
        assert [created for created, _ in ids].count(False) == 1
        assert len(curation.list_review()) == 1
    finally:
        connection.close()


def test_review_dedupe_reruns_share_single_conflict_review_row() -> None:
    # The exact Phase 24 defect: the same reconciliation conflict detected by
    # three successive runs now yields exactly one review row.
    connection, _ = _seed(
        _work_pair(created_at="2026-01-01T00:00:00+00:00", conv_id="conv-a"),
        (
            _conv("conv-b", created_at="2026-01-02T00:00:00+00:00"),
            (
                _msg(
                    "m-conv-b",
                    "I'm a software engineer at Example Corp",
                    conv_id="conv-b",
                ),
            ),
        ),
    )
    service = _service()
    curation = CurationStore(connection)
    runner = _runner(connection, service)
    try:
        first = runner.run(_cfg(limit=10))
        assert first.counters.tally.conflicts == 1
        assert first.counters.review_queued == 1
        assert first.counters.review_deduplicated == 0
        second = runner.run(_cfg(limit=10))
        assert second.counters.review_queued == 0
        assert second.counters.review_deduplicated == 1
        third = runner.run(_cfg(limit=10))
        assert third.counters.review_queued == 0
        assert third.counters.review_deduplicated == 1
        rows = curation.list_review()
        assert len(rows) == 1
        assert rows[0]["category"] == "conflict"
        assert rows[0]["status"] == "pending"
        assert service.counts()["active"] == 1
    finally:
        connection.close()


def test_review_dedupe_distinct_conflicts_stay_distinct() -> None:
    # Three genuinely different conflicting facts keep three review rows
    # across reruns; deduplication never collapses distinct obligations.
    connection, _ = _seed(
        _work_pair(created_at="2026-01-01T00:00:00+00:00", conv_id="conv-a"),
        (
            _conv("conv-b", created_at="2026-01-02T00:00:00+00:00"),
            (
                _msg(
                    "m-conv-b",
                    "I'm a software engineer at Example Corp",
                    conv_id="conv-b",
                ),
            ),
        ),
        (
            _conv("conv-c", created_at="2026-01-03T00:00:00+00:00"),
            (
                _msg(
                    "m-conv-c", "I work as a manager at Example Corp", conv_id="conv-c"
                ),
            ),
        ),
    )
    service = _service()
    curation = CurationStore(connection)
    runner = _runner(connection, service)
    try:
        first = runner.run(_cfg(limit=10))
        assert first.counters.tally.conflicts == 2
        assert first.counters.review_queued == 2
        second = runner.run(_cfg(limit=10))
        assert second.counters.review_queued == 0
        assert second.counters.review_deduplicated == 2
        assert len(curation.list_review()) == 2
    finally:
        connection.close()


def test_review_dedupe_require_approval_rerun_single_row() -> None:
    # The approval category dedupes as well: the same escalated salary
    # candidate proposed again queues nothing new.
    connection, _ = _seed(_require_approval_conv())
    service = _service()
    curation = CurationStore(connection)
    salary_proposal = _prop(statement="The user's salary is 120k", kind="personal_fact")
    llm = _FakeModelClient(_batch_json(salary_proposal))
    try:
        first = _runner(connection, service, llm=llm).run(_cfg(extraction="llm"))
        assert first.counters.review_queued == 1
        second = _runner(connection, service, llm=llm).run(_cfg(extraction="llm"))
        assert second.counters.review_queued == 0
        assert second.counters.review_deduplicated == 1
        assert len(curation.list_review()) == 1
    finally:
        connection.close()


def test_provenance_participates_in_review_obligation_identity() -> None:
    # Two conversations each escalate the *same statement* (a salary fact)
    # backed by different messages -> two distinct review obligations.
    connection, _ = _seed(
        (
            _conv("conv-salary-1", created_at="2026-01-01T00:00:00+00:00"),
            (_msg("m-salary-1", "My salary is 120k", conv_id="conv-salary-1"),),
        ),
        (
            _conv("conv-salary-2", created_at="2026-01-02T00:00:00+00:00"),
            (_msg("m-salary-2", "My salary is 120k", conv_id="conv-salary-2"),),
        ),
    )
    service = _service()
    curation = CurationStore(connection)

    class _SalaryEchoClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(
            self, messages: object, *, think: bool, format: object
        ) -> ChatResponse:
            self.calls += 1
            prompt = messages[-1].content  # type: ignore[union-attr,index]
            match = re.search(r"\[(m-[a-z0-9-]+)\] USER:", str(prompt))
            message_id = match.group(1) if match else "m1"
            return ChatResponse(
                content=_batch_json(
                    _prop(
                        statement="The user's salary is 120k",
                        kind="personal_fact",
                        evidence=(message_id,),
                    )
                ),
                model="fake",
                done=True,
            )

    llm = _SalaryEchoClient()
    try:
        report = _runner(connection, service, llm=llm).run(_cfg(extraction="llm"))
        assert report.counters.model_calls == 2
        assert report.counters.review_queued == 2
        rows = curation.list_review()
        assert len(rows) == 2
        assert {row["statement"] for row in rows} == {"The user's salary is 120k"}
        assert {row["evidence_json"][0]["source_document_id"] for row in rows} == {
            "m-salary-1",
            "m-salary-2",
        }
    finally:
        connection.close()


def test_review_dedupe_is_not_a_security_classifier() -> None:
    # Deduplication is mechanical bookkeeping, never a security boundary:
    # secret content is rejected by the policy (never deferred, never
    # escalated) before the queue is consulted, so a secret candidate that
    # somehow reaches the runner leaves zero review rows and zero writes.
    connection, _ = _seed(
        (
            _conv("conv-secret"),
            (_msg("m-secret", "My password is hunter2", conv_id="conv-secret"),),
        )
    )
    service = _service()
    curation = CurationStore(connection)
    secret_proposal = _prop(
        statement="The user's password is a long random string",
        kind="preference",
        evidence=("m-secret",),
    )
    llm = _FakeModelClient(_batch_json(secret_proposal))
    try:
        report = _runner(connection, service, llm=llm).run(_cfg(extraction="llm"))
        assert report.counters.tally.candidates == 1
        assert report.counters.tally.rejected == 1
        assert report.counters.review_queued == 0
        assert report.counters.tally.writes == 0
        assert curation.list_review() == ()
        assert service.counts()["memories"] == 0
    finally:
        connection.close()

    # The deterministic policy itself hard-rejects the same statement.
    from personal_ai.memory.policy import MemoryDecision, MemoryPolicy

    candidate = _policy_candidate("The user's password is a long random string")
    assert MemoryPolicy().evaluate(candidate).decision is MemoryDecision.REJECT


def _policy_candidate(statement: str):
    return MemoryCandidate(
        statement=statement,
        kind="preference",
        confidence=0.9,
        durability=0.8,
        relevance=0.8,
        specificity=0.7,
        recurrence=1,
        utility=0.7,
        temporal_scope="current",
        assertion_status="asserted",
        summary="credential",
        evidence=(
            MemoryEvidenceRef(
                source_type="chatgpt",
                source_id="conv-secret",
                source_document_id="m-secret",
                source_timestamp="2026-01-01T00:00:00+00:00",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Phase 25 (P3-2): --resume picks the newest *incomplete* run
# ---------------------------------------------------------------------------


def _plant_run(
    curation: CurationStore,
    *,
    run_id: str,
    status: str,
    started_at: str,
    unit_id: str = "conv-a",
    unit_status: str = "running",
) -> None:
    curation.create_run(
        run_id=run_id,
        source_type="chatgpt",
        extraction="deterministic",
        status=status,
        dry_run=False,
        extractor_version="conversation-deterministic-v1",
        policy_version="memory-policy-v1",
        prompt_version="",
        model_name=None,
        started_at=started_at,
        config_json=json.dumps({"limit": 10, "offset": 0, "batch_size": 500}),
    )
    curation.save_unit(
        run_id=run_id,
        unit_id=unit_id,
        unit_index=1,
        source_type="chatgpt",
        extraction="deterministic",
        model_name=None,
        status=unit_status,
        attempts=1,
        started_at=started_at,
    )


def test_resume_selects_older_incomplete_over_newer_completed() -> None:
    # The Phase 24 defect: a newer completed run masked an older interrupted
    # one. Resume must select the interrupted run and leave the completed one
    # untouched.
    connection, _ = _seed(_work_pair(conv_id="conv-a"))
    service = _service()
    curation = CurationStore(connection)
    try:
        _plant_run(
            curation,
            run_id="cur-newer-completed",
            status="completed",
            started_at="2026-02-01T00:00:00+00:00",
            unit_status="completed",
        )
        _plant_run(
            curation,
            run_id="cur-older-running",
            status="running",
            started_at="2026-01-02T00:00:00+00:00",
        )
        resumed = _runner(connection, service).run(_cfg(resume=True))
        assert resumed.run_id == "cur-older-running"
        assert resumed.status == "completed"
        assert resumed.counters.units_recovered_stale == 1
        assert curation.get_run("cur-newer-completed")["status"] == "completed"
        assert service.counts()["active"] == 1
    finally:
        connection.close()


def test_resume_selects_newest_incomplete_when_multiple() -> None:
    connection, _ = _seed(_work_pair(conv_id="conv-a"))
    service = _service()
    curation = CurationStore(connection)
    try:
        _plant_run(
            curation,
            run_id="cur-a-older",
            status="failed",
            started_at="2026-01-02T00:00:00+00:00",
            unit_status="failed",
        )
        _plant_run(
            curation,
            run_id="cur-b-newer",
            status="running",
            started_at="2026-01-03T00:00:00+00:00",
        )
        resumed = _runner(connection, service).run(_cfg(resume=True))
        assert resumed.run_id == "cur-b-newer"
        assert resumed.counters.units_retried == 0
        assert resumed.counters.units_recovered_stale == 1
        # The older failed run was not touched.
        assert curation.get_run("cur-a-older")["status"] == "failed"
    finally:
        connection.close()


def test_resume_with_only_completed_runs_raises() -> None:
    # A completed run is terminal: there is nothing incomplete to resume and
    # --resume must not silently re-run it (or a newer completed run).
    connection, _ = _seed(_work_pair(conv_id="conv-a"))
    service = _service()
    curation = CurationStore(connection)
    try:
        _plant_run(
            curation,
            run_id="cur-completed",
            status="completed",
            started_at="2026-01-02T00:00:00+00:00",
            unit_status="completed",
        )
        with pytest.raises(CurationResumeError):
            _runner(connection, service).run(_cfg(resume=True))
        assert curation.get_run("cur-completed")["status"] == "completed"
    finally:
        connection.close()


def test_resume_after_a_completed_resume_is_an_error_not_a_silent_rerun() -> None:
    # Once a resume finishes, the run is terminal and a further --resume is an
    # explicit error mirroring the "only completed runs" case.
    connection, _ = _seed(_work_pair(conv_id="conv-a"))
    service = _service()
    try:
        _runner(connection, service).run(_cfg())
        with pytest.raises(CurationResumeError):
            _runner(connection, service).run(_cfg(resume=True))
    finally:
        connection.close()


def test_resume_after_conflict_deduplicates_review_and_memory() -> None:
    # Combined regression (Phase 24 findings): a newer completed run rewrote a
    # memory and queued one conflict review; the older interrupted run is then
    # resumed and re-encounters the same conflict. The resume must not
    # duplicate the memory, and must not grow the review queue.
    connection, _ = _seed(
        _work_pair(created_at="2026-01-01T00:00:00+00:00", conv_id="conv-a"),
        (
            _conv("conv-b", created_at="2026-01-02T00:00:00+00:00"),
            (
                _msg(
                    "m-conv-b",
                    "I'm a software engineer at Example Corp",
                    conv_id="conv-b",
                ),
            ),
        ),
    )
    service = _service()
    curation = CurationStore(connection)
    try:
        # Run B completes first: one memory written, one conflict review row.
        fresh = _runner(connection, service).run(_cfg(limit=10))
        assert fresh.counters.tally.conflicts == 1
        assert fresh.counters.review_queued == 1
        assert fresh.status == "completed"

        # Run A was interrupted mid-flight *before* B finished; it stays the
        # only incomplete run, so the (newer) completed run B cannot mask it.
        _plant_run(
            curation,
            run_id="cur-a-interrupted",
            status="running",
            started_at="2026-01-03T00:00:00+00:00",
            unit_id="conv-b",
        )

        resumed = _runner(connection, service).run(_cfg(resume=True))
        assert resumed.run_id == "cur-a-interrupted"
        assert resumed.counters.units_recovered_stale == 1
        assert resumed.counters.review_queued == 0
        assert resumed.counters.review_deduplicated == 1
        assert resumed.counters.tally.conflicts == 1
        rows = curation.list_review()
        assert len(rows) == 1
        assert rows[0]["category"] == "conflict"
        # No duplicate memory was created by the resume.
        assert service.counts()["active"] == 1
    finally:
        connection.close()


def test_orchestration_aggregates_review_queued_and_deduplicated() -> None:
    # Phase 25: the curate-all orchestration path must carry the per-source
    # review_queued/review_deduplicated counters up into the
    # CorpusCurationReport without altering the values.
    from personal_ai.memory.curation import CurationAdapterRegistry
    from personal_ai.memory.orchestration import (
        CorpusCurationConfig,
        CorpusCurationOrchestrator,
    )

    connection, _ = _seed(
        _work_pair(created_at="2026-01-01T00:00:00+00:00", conv_id="conv-a"),
        (
            _conv("conv-b", created_at="2026-01-02T00:00:00+00:00"),
            (
                _msg(
                    "m-conv-b",
                    "I'm a software engineer at Example Corp",
                    conv_id="conv-b",
                ),
            ),
        ),
    )
    service = _service()
    store = ConversationStore(connection)
    curation = CurationStore(connection)
    orchestrator = CorpusCurationOrchestrator(
        curation_store=curation,
        registry=CurationAdapterRegistry(
            ConversationCurationAdapter(store, client=None)
        ),
        memory_service=service,
    )
    config = CorpusCurationConfig(sources=("chatgpt",), mode="deterministic", limit=10)
    try:
        first = orchestrator.run(config)
        assert first.review_queued == 1
        assert first.review_deduplicated == 0
        assert first.sources[0].review_queued == 1

        second = orchestrator.run(config)
        assert second.review_queued == 0
        assert second.review_deduplicated == 1
        assert second.sources[0].review_deduplicated == 1
        assert second.summary()["review_deduplicated"] == 1
        assert len(curation.list_review()) == 1
    finally:
        connection.close()
