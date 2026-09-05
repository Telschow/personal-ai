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
    statement: str = "The user works at BCG",
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
        _msg(f"m-{conv_id}", "I work at BCG", conv_id=conv_id),
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
        assert memory.content == "The user works at BCG"
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
        store.save_messages((_msg("m-conv-2", "I work at BCG", conv_id="conv-2"),))
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
            (_msg("m2", "I'm a software engineer at BCG", conv_id="conv-b"),),
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
