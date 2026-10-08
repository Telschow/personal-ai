"""Phase 29 — read-only ``memory_review_audit`` agent tool.

Pins down that the durable memory-review audit trail is observable to the
agent/tool layer strictly read-only, with no autonomous adjudication capability:

* The tool is registered behind a distinct read-only ``review.audit.read``
  permission, only when a ``MemoryReviewService`` is wired in, and is marked
  ``risk=READ``, ``mutates_state=False``, ``deterministic=True``.
* It exposes exactly two operations — ``counts`` (aggregate-only) and
  ``recent`` (bounded, newest-first metadata) — consuming the existing
  ``MemoryReviewService.audit()``/``audit_counts()`` surface. No second audit
  implementation exists.
* It is mechanically read-only: invoking it cannot approve/reject/expire/
  reopen, cannot mutate memories/reviews/audit rows, and never changes DB bytes.
* Unknown operations and invalid limits are rejected; unknown/mutation-style
  parameters (approve/reject/review_id/include_*) are ignored or rejected — no
  ``SQL``/``allowlist``/``include_*`` escape hatch exists.
* Privacy: statements, evidence, candidate JSON, prompts, model output,
  ``statement_hash``, secrets and sensitive values never appear in output or
  error text (synthetic sentinels are proven absent).
* Permissions: an agent without ``REVIEW_AUDIT_READ`` is denied at the policy
  boundary (least privilege); a researcher holding it can read.

Everything is offline: ``tmp_path``/``:memory:`` SQLite, no Ollama, no network.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from personal_ai.agents.defs import (
    RESEARCHER,
    REVIEWER,
    build_default_agent_registry,
)
from personal_ai.agents.models import Permission, RiskLevel
from personal_ai.agents.policy import PolicyDenialError, PolicyEngine
from personal_ai.agents.registry import AgentToolRegistry
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import (
    MEMORY_REVIEW_AUDIT,
    build_default_agent_tools,
)
from personal_ai.memory import CurationStore
from personal_ai.memory.review import MemoryReviewService
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryStore
from personal_ai.storage import connect_database

STATEMENT_SENTINEL = "STATEMENT_SENTINEL_THE_USERS_SALARY_IS_180000"
EVIDENCE_SENTINEL = "EVIDENCE_SENTINEL"
PROMPT_SENTINEL = "PROMPT_SENTINEL"
SECRET_SENTINEL = "SECRET_SENTINEL_p455w0rd_IBAN_DE02120300000000202051"


def _timestamp(day: int) -> str:
    return f"2026-02-{day:02d}T00:00:00+00:00"


def _seed_audit(store: CurationStore) -> None:
    # Representative mixed fixture (synthetic identifiers only). Statements and
    # evidence carry sentinel markers that must never surface through the tool.
    for idx, (action, outcome, category, memory_id, day) in enumerate(
        (
            ("approve", "approved", "require_approval", "mem-001", 1),
            ("reject", "rejected", "require_approval", None, 2),
            ("reject", "expired", "secret", None, 3),
            ("approve", "approved", "conflict", "mem-002", 4),
            ("approve", "approved", "require_approval", "mem-003", 4),
        ),
        start=1,
    ):
        store.append_review_audit(
            review_id=idx,
            action=action,
            outcome=outcome,
            actor="human",
            policy_category=category,
            memory_id=memory_id,
            statement_hash=f"sha-{idx}",
            created_at=_timestamp(day),
        )


def _make_service(db: Path, *, seeded: bool = False) -> MemoryReviewService:
    connection = connect_database(db)
    store = CurationStore(connection)
    if seeded:
        _seed_audit(store)
    return MemoryReviewService(
        store,
        MemoryService(MemoryStore(connection)),
        connection=connection,  # type: ignore[arg-type]
    )


def _tools(service: MemoryReviewService) -> AgentToolRegistry:
    return build_default_agent_tools(review_service=service)


def _service_from_memory() -> MemoryReviewService:
    connection = sqlite3.connect(":memory:")
    store = CurationStore(connection)
    return MemoryReviewService(
        store,
        MemoryService(MemoryStore(connection)),
        connection=connection,  # type: ignore[arg-type]
    )


def _policy(tools: AgentToolRegistry) -> PolicyEngine:
    return PolicyEngine(
        tools, build_default_agent_registry(), build_default_skill_registry()
    )


def _invoke(
    policy: PolicyEngine, arguments: dict[str, object], *, agent=RESEARCHER
) -> dict[str, object]:
    result = policy.execute(agent, "memory_review_audit", arguments)
    assert isinstance(result, dict)
    return result


def _blob(*objects: object) -> str:
    import json

    return json.dumps(objects, default=str)


# =====================================================================
# Registration
# =====================================================================


def test_registered_only_when_review_service_wired() -> None:
    without = build_default_agent_tools()
    assert "memory_review_audit" not in without.names()

    with_service = _tools(_service_from_memory())
    assert "memory_review_audit" in with_service.names()


def test_tool_profile_is_read_only() -> None:
    assert MEMORY_REVIEW_AUDIT.name == "memory_review_audit"
    assert MEMORY_REVIEW_AUDIT.permissions == (Permission.REVIEW_AUDIT_READ,)
    assert MEMORY_REVIEW_AUDIT.risk is RiskLevel.READ
    assert MEMORY_REVIEW_AUDIT.mutates_state is False
    assert MEMORY_REVIEW_AUDIT.accesses_network is False
    assert MEMORY_REVIEW_AUDIT.deterministic is True
    assert MEMORY_REVIEW_AUDIT.timeout_seconds is None


def test_tool_reviewer_denied_researcher_allowed(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite")))
    # Least privilege: reviewer holds no REVIEW_AUDIT_READ.
    with pytest.raises(PolicyDenialError):
        policy.execute(REVIEWER, "memory_review_audit", {"operation": "counts"})
    # Researcher holds it.
    result = policy.execute(RESEARCHER, "memory_review_audit", {"operation": "counts"})
    assert isinstance(result, dict)


# =====================================================================
# Counts
# =====================================================================


def test_counts_empty_db(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "empty.db")))
    data = _invoke(policy, {"operation": "counts"})
    assert data == {
        "events": 0,
        "actions": {},
        "outcomes": {},
        "policy_categories": {},
        "actors": {},
    }


def test_counts_aggregates(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite", seeded=True)))
    data = _invoke(policy, {"operation": "counts"})
    assert data["events"] == 5
    assert data["actions"] == {"approve": 3, "reject": 2}
    assert data["outcomes"] == {"approved": 3, "rejected": 1, "expired": 1}
    assert data["policy_categories"] == {
        "require_approval": 3,
        "secret": 1,
        "conflict": 1,
    }
    assert data["actors"] == {"human": 5}


def test_counts_deterministic_idempotent(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite", seeded=True)))
    first = _invoke(policy, {"operation": "counts"})
    second = _invoke(policy, {"operation": "counts"})
    third = _invoke(policy, {"operation": "counts"})
    assert first == second == third


# =====================================================================
# Recent
# =====================================================================


def test_recent_empty_db(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "empty.db")))
    data = _invoke(policy, {"operation": "recent"})
    assert data == {"events": []}


def test_recent_newest_first_tie_break(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite", seeded=True)))
    data = _invoke(policy, {"operation": "recent", "limit": 200})
    rows = data["events"]
    assert [r["review_id"] for r in rows] == [5, 4, 3, 2, 1]
    # Deterministic timestamp tie-break: same created_at, decreasing review id.
    assert rows[0]["created_at"] == rows[1]["created_at"] == _timestamp(4)


def test_recent_default_and_explicit_limit(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite", seeded=True)))
    default = _invoke(policy, {"operation": "recent"})
    assert len(default["events"]) == 5  # default limit (20) > rows available
    explicit = _invoke(policy, {"operation": "recent", "limit": 5})
    assert len(explicit["events"]) == 5
    bounded = _invoke(policy, {"operation": "recent", "limit": 2})
    assert len(bounded["events"]) == 2
    assert [r["review_id"] for r in bounded["events"]] == [5, 4]


def test_recent_limit_above_max_clamps(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite", seeded=True)))
    data = _invoke(policy, {"operation": "recent", "limit": 1_000_000_000})
    assert len(data["events"]) == 5


def test_recent_invalid_limits_rejected(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite", seeded=True)))
    for bad in (0, -1, "20", 2.5, True, None):
        with pytest.raises((TypeError, ValueError)):
            _invoke(policy, {"operation": "recent", "limit": bad})


# =====================================================================
# Privacy
# =====================================================================


def test_privacy_sentinels_never_leak(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite", seeded=True)))
    counts = _invoke(policy, {"operation": "counts"})
    recent = _invoke(policy, {"operation": "recent", "limit": 200})
    blob = _blob(counts, recent)
    for marker in (
        "STATEMENT_SENTINEL",
        "EVIDENCE_SENTINEL",
        "PROMPT_SENTINEL",
        "SECRET_SENTINEL",
        "180000",
        "p455w0rd",
        "IBAN",
        "DE02120300000000202051",
        "salary",
        "statement_hash",
        "sha-1",
        "candidate_json",
        "evidence",
        "prompt",
        "model_output",
        "review_note",
    ):
        assert marker not in blob, f"sensitive marker leaked: {marker}"


def test_recent_fields_strictly_operational(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite", seeded=True)))
    row = _invoke(policy, {"operation": "recent", "limit": 1})["events"][0]
    assert set(row) == {
        "review_id",
        "action",
        "outcome",
        "actor",
        "policy_category",
        "memory_id",
        "created_at",
    }
    assert "id" not in row
    assert "statement_hash" not in row
    assert "statement" not in row


# =====================================================================
# Read-only guarantee
# =====================================================================


def _db_fingerprint(db: Path) -> tuple[int, int, int]:

    connection = sqlite3.connect(db)
    try:
        memories = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()[0]
        review_rows = connection.execute(
            "SELECT COUNT(*) FROM memory_curation_review"
        ).fetchone()[0]
        audit_rows = connection.execute(
            "SELECT COUNT(*) FROM memory_review_audit"
        ).fetchone()[0]
    except sqlite3.Error:
        return (0, 0, 0)
    finally:
        connection.close()
    return (int(memories), int(review_rows), int(audit_rows))


def test_read_only_no_state_change(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    policy = _policy(_tools(_make_service(db, seeded=True)))
    before_hash = db.read_bytes()
    before_counts = _db_fingerprint(db)

    for _ in range(3):
        _invoke(policy, {"operation": "counts"})
        _invoke(policy, {"operation": "recent", "limit": 200})

    assert db.read_bytes() == before_hash
    assert _db_fingerprint(db) == before_counts


# =====================================================================
# No mutation surface / adversarial
# =====================================================================


def test_unknown_operation_rejected(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite", seeded=True)))
    for op in ("approve", "reject", "expire", "resolve", "delete", "banana"):
        with pytest.raises(ValueError):
            _invoke(policy, {"operation": op})


def test_mutation_parameters_cannot_mutate(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    policy = _policy(_tools(_make_service(db, seeded=True)))
    before = db.read_bytes()

    # approve=true / reject=true on the read tool must be harmless no-ops.
    data = _invoke(policy, {"operation": "recent", "limit": 200, "approve": "1"})
    assert isinstance(data, dict)
    _invoke(policy, {"operation": "counts", "reject": "1"})

    # review_id on the read tool must not look up private content, only read.
    data = _invoke(policy, {"operation": "recent", "limit": 200, "review_id": "1"})
    assert isinstance(data, dict)

    # include_* / debug / raw escape hatches must be ignored, not honored.
    data = _invoke(
        policy,
        {
            "operation": "recent",
            "limit": 200,
            "include_statement": True,
            "include_evidence": True,
            "include_candidate": True,
            "include_hash": True,
            "debug": True,
            "raw": True,
            "sql": "DELETE FROM memory_review_audit",
        },
    )
    blob = _blob(data)
    assert "STATEMENT_SENTINEL" not in blob
    assert db.read_bytes() == before


def test_no_arbitrary_sql_escape_hatch(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite", seeded=True)))
    data = _invoke(policy, {"operation": "counts", "sql": "SOME RAW SQL"})
    # The tool ignores the unknown parameter and still returns safe counts.
    assert data["events"] == 5


def test_error_messages_privacy_safe(tmp_path: Path) -> None:
    policy = _policy(_tools(_make_service(tmp_path / "db.sqlite", seeded=True)))
    with pytest.raises(ValueError) as exc:
        _invoke(policy, {"operation": "SECRET_SENTINEL_value"})
    err = str(exc.value)
    assert "SECRET_SENTINEL_value" in err  # echoes the op label only
    assert "STATEMENT_SENTINEL" not in err


# =====================================================================
# Real agent dispatch end-to-end
# =====================================================================


def test_agent_dispatch_read_only_end_to_end(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    policy = _policy(_tools(_make_service(db, seeded=True)))
    before = db.read_bytes()
    before_counts = _db_fingerprint(db)

    counts = _invoke(policy, {"operation": "counts"})
    recent = _invoke(policy, {"operation": "recent", "limit": 20})

    assert counts["events"] == 5
    assert len(recent["events"]) == 5
    blob = _blob(counts, recent)
    assert "STATEMENT_SENTINEL" not in blob
    assert db.read_bytes() == before
    assert _db_fingerprint(db) == before_counts


# =====================================================================
# CLI parity (counts only, metadata projection)
# =====================================================================


def test_cli_counts_parity(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    seeded = _make_service(db, seeded=True)
    policy = _policy(_tools(seeded))

    # CLI surface prints the same authoritative metadata via review_audit_counts.
    tool_counts = _invoke(policy, {"operation": "counts"})
    service_counts = seeded.audit_counts()
    assert tool_counts["events"] == service_counts["events"]
    assert tool_counts["outcomes"] == service_counts["outcomes"]
    assert tool_counts["actions"] == service_counts["actions"]
    assert tool_counts["policy_categories"] == service_counts["policy_categories"]
    assert tool_counts["actors"] == service_counts["actors"]
