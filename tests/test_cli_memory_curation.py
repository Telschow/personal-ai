"""Tests for the ``memory`` verb of the agent CLI (curate/runs/review).

Runs the real argument parser, real SQLite database, and real curation runner
through :func:`personal_ai.cli.main` with ``argv``, asserting on stdout and
exit codes. Fully offline: Ollama is monkeypatched for the LLM path with a
scripted fake client.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Self

import pytest

from personal_ai import cli
from personal_ai.documents import Document, DocumentChunk
from personal_ai.documents.conversations import (
    Conversation,
    ConversationMessage,
)
from personal_ai.memory import (
    EMAIL,
    FINANCIAL,
    GENERIC_DOCUMENT_SOURCE,
    CurationStore,
)
from personal_ai.ollama_client import ChatResponse
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentStore,
    connect_database,
)


def _seed(db: Path, *conversations) -> None:
    connection = connect_database(db)
    try:
        store = ConversationStore(connection)
        for conv, messages in conversations:
            store.save_conversation(conv)
            store.save_messages(messages)
    finally:
        connection.close()


def _conv(conv_id: str, created_at: str = "2026-01-01T00:00:00+00:00") -> Conversation:
    return Conversation(
        id=conv_id,
        title=f"Conversation {conv_id}",
        source_type="chatgpt",
        created_at=created_at,
        modified_at=created_at,
        metadata={},
    )


def _msg(
    msg_id: str, conv_id: str, content: str = "I work at BCG"
) -> ConversationMessage:
    return ConversationMessage(
        id=msg_id,
        conversation_id=conv_id,
        message_index=0,
        role="user",
        speaker="User",
        content_text=content,
        content_type="text",
        timestamp=None,
        is_active_branch=True,
        metadata={},
    )


def _proposal(
    evidence: tuple[str, ...] = ("m1",), **overrides: object
) -> dict[str, object]:
    proposal: dict[str, object] = {
        "statement": "The user works at BCG",
        "kind": "work",
        "temporal_scope": "current",
        "confidence": 0.9,
        "evidence_message_ids": list(evidence),
    }
    proposal.update(overrides)
    return proposal


def _batch_json(*proposals: dict[str, object]) -> str:
    return json.dumps({"proposals": list(proposals)})


class _EchoClient:
    """Healthy fake: echoes the real message id from the current window."""

    def __init__(self, *, model: str = "fake") -> None:
        self.model = model

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def chat(self, messages: object, *, think: bool, format: object) -> ChatResponse:
        prompt = messages[-1].content if isinstance(messages, list) else ""  # type: ignore[union-attr,index]
        match = re.search(r"\[(m-[a-z0-9-]+)\] USER:", str(prompt))
        message_id = match.group(1) if match else "m1"
        return ChatResponse(
            content=_batch_json(_proposal(evidence=(message_id,))),
            model=self.model,
            done=True,
        )


class _SalaryClient:
    """Healthy fake that proposes a keyword-sensitive candidate (escalated)."""

    def __init__(self, *, model: str = "fake") -> None:
        self.model = model

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def chat(self, messages: object, *, think: bool, format: object) -> ChatResponse:
        prompt = messages[-1].content if isinstance(messages, list) else ""  # type: ignore[union-attr,index]
        match = re.search(r"\[(m-[a-z0-9-]+)\] USER:", str(prompt))
        message_id = match.group(1) if match else "m1"
        return ChatResponse(
            content=_batch_json(
                _proposal(
                    evidence=(message_id,),
                    statement="The user's salary is 120k",
                    kind="personal_fact",
                )
            ),
            model=self.model,
            done=True,
        )


class _FailingClient:
    """Unhealthy fake: raises on every call (all units fail)."""

    def __init__(self, *, model: str = "fake") -> None:
        self.model = model

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def chat(self, messages: object, *, think: bool, format: object) -> ChatResponse:
        raise RuntimeError("ollama down")


class _DocEchoClient:
    """Healthy fake echoing the first stable document id of the window."""

    def __init__(self, *, model: str = "fake") -> None:
        self.model = model

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def chat(self, messages: object, *, think: bool, format: object) -> ChatResponse:
        prompt = messages[-1].content if isinstance(messages, list) else ""  # type: ignore[union-attr,index]
        match = re.search(r"\[([^\]]+)\]", str(prompt))
        doc_id = match.group(1) if match else "d-1"
        return ChatResponse(
            content=json.dumps(
                {
                    "proposals": [
                        {
                            "statement": "The user works at BCG as a product manager.",
                            "kind": "work",
                            "temporal_scope": "current",
                            "confidence": 0.9,
                            "evidence_document_ids": [doc_id],
                            "rationale": "",
                        }
                    ]
                }
            ),
            model=self.model,
            done=True,
        )


def test_cli_memory_requires_database() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["memory", "runs"])


def test_cli_memory_curate_deterministic_writes_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "curation.db"
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))

    cli.main(["memory", "curate", "--database", str(db), "--source", "chatgpt"])
    out = capsys.readouterr().out

    assert "run_id: cur-" in out
    assert "status: completed" in out
    assert "tally:" in out
    assert "created: 1" in out
    # Aggregate-only: statement content never leaks into the report.
    assert "works at BCG" not in out
    assert "conv-1" not in out


def test_cli_memory_curate_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "curation.db"
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))

    cli.main(["memory", "curate", "--database", str(db), "--json"])
    data = json.loads(capsys.readouterr().out)

    assert data["run_id"].startswith("cur-")
    assert data["status"] == "completed"
    assert data["dry_run"] is False
    assert data["tally"]["created"] == 1
    assert data["versions"]["extractor"] == "conversation-deterministic-v1"


def test_cli_memory_curate_dry_run_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "curation.db"
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))

    cli.main(["memory", "curate", "--database", str(db), "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert data["status"] == "dry_run"
    assert data["run_id"] == ""
    assert data["dry_tally"]["candidates"] == 1
    assert data["dry_tally"]["would_create"] == 1

    # Nothing was written and no checkpoint rows were created.
    connection = connect_database(db)
    try:
        memories = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'"
        ).fetchone()[0]
        runs = connection.execute(
            "SELECT COUNT(*) FROM memory_curation_runs"
        ).fetchone()[0]
    finally:
        connection.close()
    assert memories == 0
    assert runs == 0


def test_cli_memory_curate_rejects_mutually_exclusive_flags(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit, match="mutually exclusive"):
        cli.main(
            [
                "memory",
                "curate",
                "--database",
                str(tmp_path / "x.db"),
                "--dry-run",
                "--resume",
            ]
        )


def test_cli_memory_curate_llm_and_resume(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "OllamaClient", _FailingClient)
    db = tmp_path / "curation.db"
    _seed(
        db,
        (_conv("conv-a"), (_msg("m-conv-a", "conv-a"),)),
        (
            _conv("conv-b", created_at="2026-01-02T00:00:00+00:00"),
            (_msg("m-conv-b", "conv-b"),),
        ),
    )

    cli.main(
        [
            "memory",
            "curate",
            "--database",
            str(db),
            "--extraction",
            "llm",
            "--json",
        ]
    )
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "failed"
    assert first["units"]["discovered"] == 2
    assert first["units"]["failed"] == 2

    monkeypatch.setattr(cli, "OllamaClient", _EchoClient)
    cli.main(
        [
            "memory",
            "curate",
            "--database",
            str(db),
            "--extraction",
            "llm",
            "--resume",
            "--json",
        ]
    )
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["status"] == "completed"
    assert resumed["run_id"] == first["run_id"]
    assert resumed["units"]["retried"] == 2
    assert resumed["units"]["completed"] == 2
    assert resumed["tally"]["created"] == 1
    assert resumed["tally"]["updated"] == 1


def test_cli_memory_runs_lists_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "curation.db"
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))
    cli.main(["memory", "curate", "--database", str(db)])
    curate_out = capsys.readouterr().out
    run_id_line = next(
        line for line in curate_out.splitlines() if line.startswith("run_id:")
    )
    run_id = run_id_line.split(": ", 1)[1]

    cli.main(["memory", "runs", "--database", str(db)])
    listing = capsys.readouterr().out
    assert run_id in listing
    assert "chatgpt" in listing
    assert "deterministic" in listing


def test_cli_memory_runs_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "curation.db"
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))
    cli.main(["memory", "curate", "--database", str(db), "--json"])
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    cli.main(["memory", "runs", "--database", str(db), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["runs"][0]["run_id"] == run_id
    assert data["runs"][0]["status"] == "completed"


def test_cli_memory_review_lists_escalated_candidates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "OllamaClient", _SalaryClient)
    db = tmp_path / "curation.db"
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))

    cli.main(
        [
            "memory",
            "curate",
            "--database",
            str(db),
            "--extraction",
            "llm",
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert report["review_queued"] == 1
    assert report["tally"]["require_approval"] == 1

    cli.main(["memory", "review", "--database", str(db), "--show"])
    out = capsys.readouterr().out
    assert out.startswith("1:")
    assert "kind=personal_fact" in out
    assert "status=pending" in out
    assert "The user's salary is 120k" in out

    # The escalated candidate was never auto-written.
    connection = connect_database(db)
    try:
        active = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'"
        ).fetchone()[0]
        pending = connection.execute(
            "SELECT COUNT(*) FROM memory_curation_review WHERE status = 'pending'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert active == 0
    assert pending == 1


def _seed_documents(db: Path, documents: list[Document]) -> None:
    connection = connect_database(db)
    try:
        store = DocumentStore(connection)
        for document in documents:
            store.add(document)
    finally:
        connection.close()


def _email_doc(doc_id: str, *, month: str, day: int, sender: str) -> Document:
    return Document(
        id=doc_id,
        source=f"mail:{doc_id}",
        source_type=EMAIL,
        content_hash=f"hash-{doc_id}",
        created_at=f"{month}-{day:02d}T08:00:00+00:00",
        modified_at=f"{month}-{day:02d}T08:00:00+00:00",
        mime_type="text/plain",
        metadata={"sender": sender, "subject": "Weekly update"},
    )


def test_cli_memory_curate_email_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "curation.db"
    docs = [
        _email_doc(
            f"email-{month}-{index}",
            month=month,
            day=index + 1,
            sender=f"c{index}@bcg.com",
        )
        for month in ("2026-01", "2026-02", "2026-03")
        for index in range(3)
    ]
    _seed_documents(db, docs)

    cli.main(["memory", "curate", "--database", str(db), "--source", "email", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert data["status"] == "completed"
    assert data["tally"]["candidates"] == 1
    # Recurring non-webmail correspondence is expected but behavioral-grade
    # signal is deferred, never silently written.
    assert data["tally"]["deferred"] == 1
    assert data["tally"]["writes"] == 0
    # Aggregate-only: the sender domain and subjects never leak.
    assert "bcg.com" not in json.dumps(data)
    assert "Weekly update" not in json.dumps(data)


def test_cli_memory_curate_financial_mode_alias_never_calls_model(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "OllamaClient", _FailingClient)
    db = tmp_path / "curation.db"
    _seed_documents(
        db,
        [
            Document(
                id=f"fin-{index}",
                source=f"fin/{index}",
                source_type=FINANCIAL,
                content_hash=f"hash-{index}",
                created_at="2026-01-01T00:00:00+00:00",
                modified_at="2026-01-01T00:00:00+00:00",
                metadata={"financial_kind": "bank", "row_count": 100},
            )
            for index in range(3)
        ],
    )

    cli.main(
        [
            "memory",
            "curate",
            "--database",
            str(db),
            "--source",
            "financial",
            "--mode",
            "llm",
            "--json",
        ]
    )
    data = json.loads(capsys.readouterr().out)

    assert data["status"] == "completed"
    assert data["units"]["discovered"] == 1
    assert data["model_calls"] == 0
    assert data["tally"]["candidates"] == 0
    assert data["versions"]["extractor"] == "document-curation-v1"


def test_cli_memory_curate_generic_document_llm(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "OllamaClient", _DocEchoClient)
    db = tmp_path / "curation.db"
    connection = connect_database(db)
    try:
        store = DocumentStore(connection)
        store.add(
            Document(
                id="d-1",
                source="notes/0001.md",
                source_type="md",
                content_hash="hash-d-1",
                created_at="2026-01-01T00:00:00+00:00",
                modified_at="2026-01-01T00:00:00+00:00",
            )
        )
        ChunkStore(connection).add(
            DocumentChunk(id="c-d-1", document_id="d-1", text="Product work at BCG.")
        )
    finally:
        connection.close()

    cli.main(
        [
            "memory",
            "curate",
            "--database",
            str(db),
            "--source",
            GENERIC_DOCUMENT_SOURCE,
            "--mode",
            "llm",
            "--json",
        ]
    )
    data = json.loads(capsys.readouterr().out)

    assert data["status"] == "completed"
    assert data["model_calls"] == 1
    assert data["tally"]["created"] == 1
    assert data["versions"]["prompt"] != ""
    connectivity = connect_database(db)
    try:
        created = connectivity.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'"
        ).fetchone()[0]
    finally:
        connectivity.close()
    assert created == 1


def test_cli_memory_curate_rejects_unknown_source(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(
            [
                "memory",
                "curate",
                "--database",
                str(tmp_path / "x.db"),
                "--source",
                "bogus",
            ]
        )


def test_cli_memory_review_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "curation.db"
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))
    cli.main(["memory", "curate", "--database", str(db)])
    capsys.readouterr()
    cli.main(["memory", "review", "--database", str(db)])
    assert "Pending reviews: 0 total" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Phase 25: CLI-level coverage for review dedupe + resume selection
# ---------------------------------------------------------------------------


def _plant_cli_run(
    db: Path,
    *,
    run_id: str,
    status: str,
    started_at: str,
) -> None:
    connection = connect_database(db)
    try:
        curation = CurationStore(connection)
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
            unit_id="conv-1",
            unit_index=1,
            source_type="chatgpt",
            extraction="deterministic",
            model_name=None,
            status="running" if status == "running" else "completed",
            attempts=1,
            started_at=started_at,
        )
    finally:
        connection.close()


def test_cli_resume_selects_older_incomplete_over_newer_completed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "curation.db"
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))
    _plant_cli_run(
        db,
        run_id="cur-newer-completed",
        status="completed",
        started_at="2026-02-01T00:00:00+00:00",
    )
    _plant_cli_run(
        db,
        run_id="cur-older-running",
        status="running",
        started_at="2026-01-02T00:00:00+00:00",
    )

    cli.main(["memory", "curate", "--database", str(db), "--resume", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert data["run_id"] == "cur-older-running"
    assert data["status"] == "completed"
    assert data["units"]["recovered_stale"] == 1


def test_cli_resume_with_only_completed_runs_errors(tmp_path: Path) -> None:
    db = tmp_path / "curation.db"
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))
    cli.main(["memory", "curate", "--database", str(db)])

    with pytest.raises(SystemExit, match="no resumable run"):
        cli.main(["memory", "curate", "--database", str(db), "--resume", "--json"])


def test_cli_review_dedupe_conflict_reruns_single_pending_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "curation.db"
    _seed(
        db,
        (_conv("conv-a"), (_msg("m-a", "conv-a", "I work at BCG"),)),
        (
            _conv("conv-b", created_at="2026-01-02T00:00:00+00:00"),
            (_msg("m-b", "conv-b", "I'm a software engineer at BCG"),),
        ),
    )

    cli.main(["memory", "curate", "--database", str(db), "--json"])
    first = json.loads(capsys.readouterr().out)
    assert first["review_queued"] == 1
    assert first["review_deduplicated"] == 0

    cli.main(["memory", "curate", "--database", str(db), "--json"])
    second = json.loads(capsys.readouterr().out)
    assert second["review_queued"] == 0
    assert second["review_deduplicated"] == 1

    cli.main(["memory", "review", "--database", str(db), "--show"])
    out = capsys.readouterr().out
    review_lines = [line for line in out.splitlines() if line.startswith("1:")]
    assert len(review_lines) == 1
    assert "kind=work" in out
    assert "status=pending" in out


# ---------------------------------------------------------------------------
# Phase 26: CLI-level approval / rejection adjudication
# ---------------------------------------------------------------------------


def test_cli_review_approve_writes_once_then_not_pending(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "OllamaClient", _SalaryClient)
    db = tmp_path / "curation.db"
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))

    cli.main(
        ["memory", "curate", "--database", str(db), "--extraction", "llm", "--json"]
    )
    report = json.loads(capsys.readouterr().out)
    assert report["review_queued"] == 1

    # First approval writes through the single policy-gated path.
    cli.main(["memory", "review", "--database", str(db), "--approve", "1"])
    out = capsys.readouterr().out
    assert "outcome=approved" in out
    assert "memory_id=mem-" in out

    connection = connect_database(db)
    try:
        active = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'"
        ).fetchone()[0]
        status = connection.execute(
            "SELECT status FROM memory_curation_review WHERE id = 1"
        ).fetchone()[0]
    finally:
        connection.close()
    assert active == 1
    assert status == "approved"

    # A second decision on the same row is a harmless no-op, no duplicate write.
    cli.main(["memory", "review", "--database", str(db), "--approve", "1"])
    assert "outcome=not_pending" in capsys.readouterr().out
    connection = connect_database(db)
    try:
        active = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert active == 1

    # Rerunning curation neither re-queues nor grows memory.
    cli.main(
        ["memory", "curate", "--database", str(db), "--extraction", "llm", "--json"]
    )
    rerun = json.loads(capsys.readouterr().out)
    assert rerun["review_queued"] == 0
    assert rerun["review_deduplicated"] == 1
    connection = connect_database(db)
    try:
        active = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert active == 1


def test_cli_review_reject_writes_nothing_and_rerun_does_not_reopen(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "OllamaClient", _SalaryClient)
    db = tmp_path / "curation.db"
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))

    cli.main(
        ["memory", "curate", "--database", str(db), "--extraction", "llm", "--json"]
    )
    assert json.loads(capsys.readouterr().out)["review_queued"] == 1

    cli.main(["memory", "review", "--database", str(db), "--reject", "1"])
    assert "outcome=rejected" in capsys.readouterr().out

    connection = connect_database(db)
    try:
        active = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'"
        ).fetchone()[0]
        status = connection.execute(
            "SELECT status FROM memory_curation_review WHERE id = 1"
        ).fetchone()[0]
    finally:
        connection.close()
    assert active == 0
    assert status == "rejected"

    # Rejecting again is a no-op.
    cli.main(["memory", "review", "--database", str(db), "--reject", "1"])
    assert "outcome=not_pending" in capsys.readouterr().out

    # Rerunning curation never re-opens a rejected obligation.
    cli.main(
        ["memory", "curate", "--database", str(db), "--extraction", "llm", "--json"]
    )
    rerun = json.loads(capsys.readouterr().out)
    assert rerun["review_queued"] == 0
    assert rerun["review_deduplicated"] == 1
    connection = connect_database(db)
    try:
        active = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert active == 0
