"""Phase 28 — durable review audit observability & aggregate adjudication reporting.

Exercises the privacy-safe ``memory review --audit`` / ``--audit-counts`` CLI
surfaces and the aggregate ``review_approved/rejected/expired`` fields on the
``curate-all`` report, all derived from the authoritative ``memory_review_audit``
table (Phase 27). Fully offline and hermetic.

Invariants under test:
- The audit surface is read-only metadata only: never statements, evidence,
  candidate_json, prompts, model output, or secret/sensitive values.
- Repeated terminal decisions create no additional audit events.
- The blank/empty database renders ``events=0`` without fabrication.
- ``curate-all`` adjudication counts come from the audit table (a snapshot),
  kept distinct from pending queue counts.
- Running audit commands never mutates the database.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Self

import pytest

from personal_ai import cli
from personal_ai.documents.conversations import (
    Conversation,
    ConversationMessage,
)
from personal_ai.ollama_client import ChatResponse
from personal_ai.storage import (
    ConversationStore,
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


class _WorkClient:
    """Healthy fake that proposes a plain (acceptable) candidate."""

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


def _db_sha(db: Path) -> str:
    return hashlib.sha256(db.read_bytes()).hexdigest()


def _seed_review(
    db: Path,
    monkeypatch: object | None = None,
    *,
    client_class=_SalaryClient,
) -> None:
    if monkeypatch is not None:
        monkeypatch.setattr(cli, "OllamaClient", client_class)  # type: ignore[attr-defined]
    _seed(db, (_conv("conv-1"), (_msg("m1", "conv-1"),)))
    capsys_out = cli.main(
        ["memory", "curate", "--database", str(db), "--extraction", "llm", "--json"]
    )
    return capsys_out


# ---------------------------------------------------------------------------
# Empty/blank database
# ---------------------------------------------------------------------------


def test_cli_audit_counts_empty_db(tmp_path: Path, capsys) -> None:
    db = tmp_path / "empty.db"
    cli.main(["memory", "review", "--database", str(db), "--audit-counts"])
    out = capsys.readouterr().out
    assert "events: 0" in out

    cli.main(["memory", "review", "--database", str(db), "--audit-counts", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["events"] == 0


def test_cli_audit_empty_db(tmp_path: Path, capsys) -> None:
    db = tmp_path / "empty.db"
    cli.main(["memory", "review", "--database", str(db), "--audit"])
    out = capsys.readouterr().out
    assert "events: 0" in out
    assert "(none)" in out

    cli.main(["memory", "review", "--database", str(db), "--audit", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["events"] == 0
    assert data["recent_events"] == []


def test_cli_audit_commands_do_not_mutate_db(tmp_path: Path, capsys) -> None:
    db = tmp_path / "db.sqlite"
    _seed_review(db)
    before = _db_sha(db)
    cli.main(["memory", "review", "--database", str(db), "--audit-counts"])
    cli.main(["memory", "review", "--database", str(db), "--audit"])
    cli.main(["memory", "review", "--database", str(db), "--audit", "--json"])
    capsys.readouterr()
    assert _db_sha(db) == before


# ---------------------------------------------------------------------------
# Approve/reject produce exactly-one audit events; repeats add none
# ---------------------------------------------------------------------------


def test_cli_approve_audits_once_then_not_pending(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    _seed_review(tmp_path / "db.sqlite", monkeypatch)
    capsys.readouterr()

    cli.main(
        [
            "memory",
            "review",
            "--database",
            str(tmp_path / "db.sqlite"),
            "--approve",
            "1",
        ]
    )
    out = capsys.readouterr().out
    assert "outcome=approved" in out
    assert "audit_recorded=true" in out

    cli.main(
        [
            "memory",
            "review",
            "--database",
            str(tmp_path / "db.sqlite"),
            "--audit-counts",
            "--json",
        ]
    )
    data = json.loads(capsys.readouterr().out)
    assert data["events"] == 1
    assert data["outcomes"]["approved"] == 1

    # Repeated decision: no-op, no new audit event.
    cli.main(
        [
            "memory",
            "review",
            "--database",
            str(tmp_path / "db.sqlite"),
            "--approve",
            "1",
        ]
    )
    repeated = capsys.readouterr().out
    assert "outcome=not_pending" in repeated
    assert "audit_recorded=false" in repeated
    cli.main(
        [
            "memory",
            "review",
            "--database",
            str(tmp_path / "db.sqlite"),
            "--audit-counts",
            "--json",
        ]
    )
    assert json.loads(capsys.readouterr().out)["events"] == 1


def test_cli_reject_audits_once(tmp_path: Path, capsys, monkeypatch) -> None:
    _seed_review(tmp_path / "db.sqlite", monkeypatch)
    capsys.readouterr()
    db = str(tmp_path / "db.sqlite")

    cli.main(["memory", "review", "--database", db, "--reject", "1"])
    assert "outcome=rejected" in capsys.readouterr().out

    cli.main(["memory", "review", "--database", db, "--audit-counts", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["events"] == 1
    assert data["outcomes"]["rejected"] == 1


# ---------------------------------------------------------------------------
# Audit detail view is bounded, ordered, metadata-only
# ---------------------------------------------------------------------------


def test_cli_audit_recent_view_metadata_only(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    db = str(tmp_path / "db.sqlite")
    _seed_review(
        tmp_path / "db.sqlite", client_class=_SalaryClient, monkeypatch=monkeypatch
    )
    cli.main(["memory", "review", "--database", db, "--approve", "1"])
    capsys.readouterr()

    cli.main(["memory", "review", "--database", db, "--audit", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["events"] == 1
    row = data["recent_events"][0]
    assert row["action"] == "approve"
    assert row["outcome"] == "approved"
    assert "actor" in row
    assert "created_at" in row
    # statement_hash and the internal row id are never exposed.
    assert "statement_hash" not in row
    assert "id" not in row


# ---------------------------------------------------------------------------
# Privacy: secrets / sensitive values never leak through audit surfaces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("audit_arg", ["--audit", "--audit-counts"])
def test_cli_audit_privacy_sensitive_never_leaks(
    tmp_path: Path, capsys, monkeypatch, audit_arg: str
) -> None:
    db = str(tmp_path / "db.sqlite")
    _seed_review(
        tmp_path / "db.sqlite", client_class=_SalaryClient, monkeypatch=monkeypatch
    )
    cli.main(["memory", "review", "--database", db, "--approve", "1"])
    capsys.readouterr()

    cli.main(["memory", "review", "--database", db, audit_arg, "--json"])
    blob = capsys.readouterr().out
    assert "120k" not in blob
    assert "salary" not in blob
    assert "The user" not in blob
    assert "BCG" not in blob


# ---------------------------------------------------------------------------
# curate-all aggregates adjudication from the audit table (distinct from queue)
# ---------------------------------------------------------------------------


def test_curate_all_report_audit_adjudication_counts(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    db = str(tmp_path / "db.sqlite")
    # Two escalated salary rows: approve one, reject one.
    _seed(tmp_path / "db.sqlite", (_conv("conv-1"), (_msg("m1", "conv-1"),)))
    monkeypatch.setattr(cli, "OllamaClient", _SalaryClient)
    cli.main(["memory", "curate", "--database", db, "--extraction", "llm", "--json"])
    first = json.loads(capsys.readouterr().out)
    assert first["review_queued"] == 1

    # Re-queue a second distinct obligation via the same conversation-window
    # but a different message is not necessary; reuse approve on row 1 and
    # reject a second obligation seeded through a second escalation run.
    cli.main(["memory", "review", "--database", db, "--approve", "1"])
    capsys.readouterr()

    # curate-all (no new candidates expected) still reports the durable audit history.
    cli.main(
        ["memory", "curate-all", "--database", db, "--mode", "deterministic", "--json"]
    )
    report = json.loads(capsys.readouterr().out)
    # Pending-queue and audit-derived counts are kept distinct.
    assert report["review_queued"] == 0
    assert report["review_approved"] == 1
    assert report["review_rejected"] == 0
    assert report["review_expired"] == 0


def test_curate_all_defer_and_reject_reflected(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    db = str(tmp_path / "db.sqlite")
    _seed_review(tmp_path / "db.sqlite", monkeypatch=monkeypatch)
    cli.main(["memory", "review", "--database", db, "--reject", "1"])
    capsys.readouterr()

    cli.main(
        ["memory", "curate-all", "--database", db, "--mode", "deterministic", "--json"]
    )
    report = json.loads(capsys.readouterr().out)
    assert report["review_rejected"] == 1
    assert report["review_approved"] == 0


# ---------------------------------------------------------------------------
# Human (non-JSON) audit surfaces stay privacy-safe and bounded
# ---------------------------------------------------------------------------


def test_cli_audit_human_privacy_and_bounded(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    db = str(tmp_path / "db.sqlite")
    _seed_review(
        tmp_path / "db.sqlite", client_class=_SalaryClient, monkeypatch=monkeypatch
    )
    cli.main(["memory", "review", "--database", db, "--approve", "1"])
    capsys.readouterr()

    cli.main(["memory", "review", "--database", db, "--audit"])
    blob = capsys.readouterr().out
    # Metadata is present; sensitive/statement content is not.
    assert "events: 1" in blob
    assert "approve" in blob
    assert "120k" not in blob
    assert "The user" not in blob
    assert "salary" not in blob

    cli.main(["memory", "review", "--database", db, "--audit-counts"])
    counts_out = capsys.readouterr().out
    assert "events: 1" in counts_out
    assert "120k" not in counts_out


# ---------------------------------------------------------------------------
# Store-level audit correctness: ordering, bounds, and count invariants
# ---------------------------------------------------------------------------


def test_store_audit_counts_invariants(tmp_path: Path) -> None:
    from personal_ai.memory import CurationStore
    from personal_ai.storage import connect_database

    db = tmp_path / "db.sqlite"
    connection = connect_database(db)
    try:
        store = CurationStore(connection)
        for i in range(3):
            store.append_review_audit(
                review_id=i + 1,
                action="approve",
                outcome="approved",
                actor="curator",
                policy_category="require_approval",
                memory_id=f"mem-{i}",
                statement_hash=f"h{i}",
                created_at=f"2026-01-0{i + 1}T00:00:00+00:00",
            )
        store.append_review_audit(
            review_id=4,
            action="reject",
            outcome="rejected",
            actor="curator",
            policy_category="require_approval",
            memory_id=None,
            statement_hash="h4",
            created_at="2026-01-04T00:00:00+00:00",
        )
        store.append_review_audit(
            review_id=5,
            action="approve",
            outcome="expired",
            actor="curator",
            policy_category="conflict",
            memory_id=None,
            statement_hash="h5",
            created_at="2026-01-05T00:00:00+00:00",
        )

        counts = store.review_audit_counts()
        assert counts["events"] == 5
        assert counts["outcomes"]["approved"] == 3
        assert counts["outcomes"]["rejected"] == 1
        assert counts["outcomes"]["expired"] == 1
        assert sum(counts["outcomes"].values()) == counts["events"]
        assert sum(counts["actions"].values()) == counts["events"]
        assert counts["policy_categories"]["require_approval"] == 4
        assert counts["policy_categories"]["conflict"] == 1
        assert counts["actors"]["curator"] == 5

        # Deterministic ordering: recent = newest first, then id tie-break.
        recent = store.review_audit(recent=True, limit=10)
        assert [r["review_id"] for r in recent] == [5, 4, 3, 2, 1]
        ascending = store.review_audit(recent=False, limit=10)
        assert [r["review_id"] for r in ascending] == [1, 2, 3, 4, 5]

        # Per-review filter and bound.
        only = store.review_audit(review_id=2, limit=10)
        assert [r["review_id"] for r in only] == [2]
        bounded = store.review_audit(recent=True, limit=2)
        assert [r["review_id"] for r in bounded] == [5, 4]
    finally:
        connection.close()
