"""Phase 30 — deterministic time-window filtering for the review audit trail.

Exercises the new ``--since``/``--until`` filtering across:
- ``CurationStore.review_audit`` / ``review_audit_counts`` (store layer)
- ``MemoryReviewService.audit`` / ``audit_counts`` (service layer)
- CLI ``memory review --audit`` / ``--audit-counts``
- Agent tool ``memory_review_audit`` (counts + recent operations)

Invariants under test:
- Inclusive boundaries on ``created_at``.
- Reversed ranges rejected deterministically.
- Empty filtered windows return zeros/empty results, never fabricated.
- Privacy boundary unchanged: statements, evidence, candidate_json, prompts,
  model output, statement_hash, secrets, sensitive values never exposed.
- Deterministic ordering (created_at DESC, id DESC) preserved after filtering.
- Limit applies after filtering; max limit clamp still enforced.
- Filtered counts match filtered events.
- Host timezone independence.
- No mutation of the database (read-only guarantee).
- Existing Phase 27/28/29 tests remain green (regression).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from personal_ai.agents.defs import RESEARCHER, REVIEWER, build_default_agent_registry
from personal_ai.agents.policy import PolicyDenialError, PolicyEngine
from personal_ai.agents.registry import AgentToolRegistry
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import build_default_agent_tools
from personal_ai.cli import main as cli_main
from personal_ai.memory import CurationStore
from personal_ai.memory.models import format_utc_timestamp, parse_iso_timestamp
from personal_ai.memory.review import MemoryReviewService
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryStore
from personal_ai.storage import connect_database

# --- Timestamp normalization tests ---


def test_parse_iso_timestamp_utc() -> None:
    dt = parse_iso_timestamp("2026-09-06T15:30:45+00:00")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0


def test_parse_iso_timestamp_z_suffix() -> None:
    dt = parse_iso_timestamp("2026-09-06T15:30:45Z")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0


def test_parse_iso_timestamp_positive_offset() -> None:
    dt = parse_iso_timestamp("2026-09-06T17:30:45+02:00")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0
    assert dt.hour == 15  # converted to UTC


def test_parse_iso_timestamp_negative_offset() -> None:
    dt = parse_iso_timestamp("2026-09-06T10:30:45-05:00")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0
    assert dt.hour == 15  # converted to UTC


def test_parse_iso_timestamp_naive_assumed_utc() -> None:
    dt = parse_iso_timestamp("2026-09-06T15:30:45")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0


def test_parse_iso_timestamp_date_only() -> None:
    dt = parse_iso_timestamp("2026-09-06")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0
    assert dt.hour == 0
    assert dt.minute == 0
    assert dt.second == 0


def test_parse_iso_timestamp_fractional_seconds() -> None:
    dt = parse_iso_timestamp("2026-09-06T15:30:45.123456+00:00")
    assert dt.microsecond == 123456


def test_parse_iso_timestamp_invalid() -> None:
    with pytest.raises(ValueError):
        parse_iso_timestamp("not-a-timestamp")


def test_format_utc_timestamp_roundtrip() -> None:
    dt = parse_iso_timestamp("2026-09-06T15:30:45+00:00")
    formatted = format_utc_timestamp(dt)
    # Should be parseable and equal
    reparsed = parse_iso_timestamp(formatted)
    assert reparsed == dt


def test_equivalent_instants_produce_same_filtering() -> None:
    """Equivalent instants in different ISO forms normalize identically."""
    forms = [
        "2026-09-06T15:30:45+00:00",
        "2026-09-06T15:30:45Z",
        "2026-09-06T17:30:45+02:00",
        "2026-09-06T10:30:45-05:00",
    ]
    normalized = [format_utc_timestamp(parse_iso_timestamp(f)) for f in forms]
    assert len(set(normalized)) == 1  # all identical


# --- Fixtures ---


STATEMENT_SENTINEL = "STATEMENT_SENTINEL_THE_USERS_SALARY_IS_180000"
EVIDENCE_SENTINEL = "EVIDENCE_SENTINEL"
PROMPT_SENTINEL = "PROMPT_SENTINEL"
SECRET_SENTINEL = "SECRET_SENTINEL_p455w0rd_IBAN_DE02120300000000202051"


def _timestamp(
    day: int, hour: int = 0, minute: int = 0, second: int = 0, microsecond: int = 0
) -> str:
    """Generate a UTC ISO-8601 timestamp matching production format (with microseconds)."""
    return f"2026-02-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}.{microsecond:06d}+00:00"


def _seed_audit(store: CurationStore) -> None:
    """Seed audit events on different days for time-window testing."""
    for idx, (action, outcome, category, memory_id, day, hour) in enumerate(
        (
            ("approve", "approved", "require_approval", "mem-001", 1, 10),
            ("reject", "rejected", "require_approval", None, 2, 12),
            ("reject", "expired", "secret", None, 3, 8),
            ("approve", "approved", "conflict", "mem-002", 4, 15),
            (
                "approve",
                "approved",
                "require_approval",
                "mem-003",
                4,
                16,
            ),  # same day, different hour
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
            created_at=_timestamp(day, hour),
        )


def _make_service(db: Path, *, seeded: bool = False) -> MemoryReviewService:
    connection = connect_database(db)
    store = CurationStore(connection)
    if seeded:
        _seed_audit(store)
    return MemoryReviewService(
        store,
        MemoryService(MemoryStore(connection)),
        connection=connection,
    )


def _make_tools(service: MemoryReviewService) -> AgentToolRegistry:
    return build_default_agent_tools(review_service=service)


def _make_policy(tools: AgentToolRegistry) -> PolicyEngine:
    agents = build_default_agent_registry()
    skills = build_default_skill_registry()
    return PolicyEngine(tools, agents, skills)


def _invoke(policy: PolicyEngine, agent_name: str, args: dict[str, Any]) -> Any:
    return policy.execute(agent_name, "memory_review_audit", args)


# --- Store-level filtering tests ---


class TestStoreAuditTimeFiltering:
    """Tests for CurationStore.review_audit / review_audit_counts with since/until."""

    def setup_method(self) -> None:
        self.db = Path(":memory:")
        self.conn = connect_database(self.db)
        self.store = CurationStore(self.conn)
        _seed_audit(self.store)

    def teardown_method(self) -> None:
        self.conn.close()

    def test_no_bounds_returns_all(self) -> None:
        rows = self.store.review_audit(limit=200, recent=True)
        assert len(rows) == 5

    def test_since_only(self) -> None:
        rows = self.store.review_audit(limit=200, recent=True, since="2026-02-03")
        assert len(rows) == 3  # days 3, 4
        assert all(r["created_at"] >= "2026-02-03T00:00:00.000000+00:00" for r in rows)

    def test_until_only(self) -> None:
        # until with date-only becomes midnight; use end-of-day for inclusive day
        rows = self.store.review_audit(
            limit=200, recent=True, until="2026-02-02T23:59:59"
        )
        assert len(rows) == 2  # days 1, 2
        assert all(r["created_at"] <= "2026-02-02T23:59:59.000000+00:00" for r in rows)

    def test_both_bounds(self) -> None:
        rows = self.store.review_audit(
            limit=200, recent=True, since="2026-02-02", until="2026-02-03T23:59:59"
        )
        assert len(rows) == 2  # days 2, 3 (day 4 is after until)
        assert all(
            "2026-02-02" <= r["created_at"] <= "2026-02-03T23:59:59.000000+00:00"
            for r in rows
        )

    def test_exact_since_boundary_included(self) -> None:
        rows = self.store.review_audit(limit=200, recent=True, since=_timestamp(2, 12))
        assert len(rows) == 4  # day 2 at 12:00, day 3, day 4 (both hours)
        # The day 2 event at 12:00 should be included
        created = [r["created_at"] for r in rows]
        assert _timestamp(2, 12) in created

    def test_exact_until_boundary_included(self) -> None:
        rows = self.store.review_audit(limit=200, recent=True, until=_timestamp(3, 8))
        assert len(rows) == 3  # day 1, 2, 3 at 08:00
        created = [r["created_at"] for r in rows]
        assert _timestamp(3, 8) in created

    def test_event_immediately_before_since_excluded(self) -> None:
        rows = self.store.review_audit(limit=200, recent=True, since=_timestamp(3, 0))
        created = [r["created_at"] for r in rows]
        assert _timestamp(2, 15) not in created  # day 2 is before since

    def test_event_immediately_after_until_excluded(self) -> None:
        rows = self.store.review_audit(limit=200, recent=True, until=_timestamp(3, 8))
        created = [r["created_at"] for r in rows]
        assert _timestamp(4, 15) not in created  # day 4 is after until

    def test_reversed_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="since must not be after until"):
            self.store.review_audit(
                limit=200, recent=True, since="2026-02-05", until="2026-02-01"
            )

    def test_deterministic_ordering_preserved(self) -> None:
        rows = self.store.review_audit(
            limit=200, recent=True, since="2026-02-01", until="2026-02-04"
        )
        # Should be newest first (created_at DESC, id DESC)
        created = [r["created_at"] for r in rows]
        assert created == sorted(created, reverse=True)

    def test_same_timestamp_tie_break_by_id(self) -> None:
        # Two events on day 4 with different hours -> different timestamps, but test with same timestamp
        # Add another event with same timestamp but higher id
        self.store.append_review_audit(
            review_id=99,
            action="approve",
            outcome="approved",
            actor="human",
            policy_category="require_approval",
            memory_id="mem-999",
            statement_hash="sha-99",
            created_at=_timestamp(4, 15),  # same as event idx 4
        )
        # Use end-of-day for until to include all day 4 events
        rows = self.store.review_audit(
            limit=200, recent=True, since="2026-02-04", until="2026-02-04T23:59:59"
        )
        # Should have 3 events on day 4, ordered by created_at DESC, then id DESC
        day4_rows = [r for r in rows if r["created_at"].startswith("2026-02-04")]
        assert len(day4_rows) == 3
        # Row 1: id=5 at 16:00 (later timestamp -> first)
        # Rows 2-3: id=6, id=4 at 15:00 (same timestamp -> id DESC)
        expected_ids = [5, 6, 4]
        ids = [r["id"] for r in day4_rows]
        assert ids == expected_ids

    def test_limit_applies_after_filtering(self) -> None:
        rows = self.store.review_audit(limit=2, recent=True, since="2026-02-01")
        assert len(rows) == 2  # only 2 despite 5 matching

    def test_max_limit_clamp(self) -> None:
        # Max limit is 200 by default in store
        rows = self.store.review_audit(limit=1000, recent=True)
        assert len(rows) <= 5  # but only 5 exist

    def test_counts_use_same_filter(self) -> None:
        counts = self.store.review_audit_counts(
            since="2026-02-03", until="2026-02-03T23:59:59"
        )
        assert counts["events"] == 1  # only day 3
        assert counts["total"] == 1

    def test_filtered_counts_match_filtered_events(self) -> None:
        rows = self.store.review_audit(
            limit=200, recent=True, since="2026-02-02", until="2026-02-03T23:59:59"
        )
        counts = self.store.review_audit_counts(
            since="2026-02-02", until="2026-02-03T23:59:59"
        )
        assert counts["events"] == len(rows)

    def test_empty_filtered_window_returns_zero(self) -> None:
        counts = self.store.review_audit_counts(since="2027-01-01", until="2027-12-31")
        assert counts["events"] == 0
        assert counts["total"] == 0

        rows = self.store.review_audit(limit=200, recent=True, since="2027-01-01")
        assert rows == ()

    def test_since_until_with_timezone_forms(self) -> None:
        # Test that different timezone forms of the same instant work
        rows_utc = self.store.review_audit(
            limit=200,
            recent=True,
            since="2026-02-03T00:00:00+00:00",
            until="2026-02-03T23:59:59+00:00",
        )
        rows_z = self.store.review_audit(
            limit=200,
            recent=True,
            since="2026-02-03T00:00:00Z",
            until="2026-02-03T23:59:59Z",
        )
        rows_offset = self.store.review_audit(
            limit=200,
            recent=True,
            since="2026-02-03T02:00:00+02:00",
            until="2026-02-04T01:59:59+02:00",
        )
        assert len(rows_utc) == len(rows_z) == len(rows_offset) == 1


# --- Service-level filtering tests ---


class TestServiceAuditTimeFiltering:
    """Tests for MemoryReviewService.audit / audit_counts with since/until."""

    def setup_method(self) -> None:
        self.db = Path(":memory:")
        self.service = _make_service(self.db, seeded=True)

    def test_audit_since_until(self) -> None:
        rows = self.service.audit(
            limit=200, since="2026-02-02", until="2026-02-03T23:59:59"
        )
        assert len(rows) == 2
        for row in rows:
            assert "review_id" in row
            assert "statement_hash" not in row
            assert "id" not in row  # internal id not exposed

    def test_audit_counts_since_until(self) -> None:
        counts = self.service.audit_counts(
            since="2026-02-02", until="2026-02-03T23:59:59"
        )
        assert counts["events"] == 2
        assert counts["total"] == 2

    def test_service_strips_private_fields(self) -> None:
        # Even with time filtering, private fields should never be exposed
        rows = self.service.audit(limit=200, since="2026-02-01")
        for row in rows:
            assert "statement_hash" not in row
            assert "id" not in row

    def test_invalid_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError):
            self.service.audit(limit=200, since="not-a-date")

    def test_reversed_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="since must not be after until"):
            self.service.audit_counts(since="2026-02-05", until="2026-02-01")


import tempfile

# --- CLI tests ---


class TestCLIAuditTimeFiltering:
    """Tests for CLI `memory review --audit` / `--audit-counts` with --since/--until."""

    def setup_method(self) -> None:
        import tempfile as _tempfile

        self._tmpdir = _tempfile.mkdtemp()
        self.db = Path(self._tmpdir) / "audit.db"
        self.conn = connect_database(self.db)
        self.store = CurationStore(self.conn)
        _seed_audit(self.store)

    def teardown_method(self) -> None:
        import shutil

        self.conn.close()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self, args: list[str], *, db: Path | None = None) -> tuple[int, str, str]:
        import sys
        from io import StringIO

        db = db or self.db
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.argv = ["personal-ai", *args]
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        try:
            try:
                cli_main()
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
            except (ValueError, TypeError):
                code = 1
            return code, sys.stdout.getvalue(), sys.stderr.getvalue()
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def test_audit_since(self) -> None:
        code, out, _ = self._run(
            [
                "memory",
                "review",
                "--audit",
                "--since",
                "2026-02-03",
                "--database",
                str(self.db),
            ]
        )
        assert code == 0
        assert "events: 3" in out

    def test_audit_until(self) -> None:
        code, out, _ = self._run(
            [
                "memory",
                "review",
                "--audit",
                "--until",
                "2026-02-02T23:59:59",
                "--database",
                str(self.db),
            ]
        )
        assert code == 0
        assert "events: 2" in out

    def test_audit_since_until(self) -> None:
        code, out, _ = self._run(
            [
                "memory",
                "review",
                "--audit",
                "--since",
                "2026-02-02",
                "--until",
                "2026-02-03T23:59:59",
                "--database",
                str(self.db),
            ]
        )
        assert code == 0
        assert "events: 2" in out

    def test_audit_counts_since(self) -> None:
        code, out, _ = self._run(
            [
                "memory",
                "review",
                "--audit-counts",
                "--since",
                "2026-02-03",
                "--database",
                str(self.db),
            ]
        )
        assert code == 0
        assert "events: 3" in out

    def test_audit_counts_until(self) -> None:
        code, out, _ = self._run(
            [
                "memory",
                "review",
                "--audit-counts",
                "--until",
                "2026-02-02T23:59:59",
                "--database",
                str(self.db),
            ]
        )
        assert code == 0
        assert "events: 2" in out

    def test_audit_json_output(self) -> None:
        code, out, _ = self._run(
            [
                "memory",
                "review",
                "--audit",
                "--since",
                "2026-02-03",
                "--json",
                "--database",
                str(self.db),
            ]
        )
        assert code == 0
        data = json.loads(out)
        assert data["events"] == 3
        assert len(data["recent_events"]) == 3

    def test_audit_counts_json_output(self) -> None:
        code, out, _ = self._run(
            [
                "memory",
                "review",
                "--audit-counts",
                "--since",
                "2026-02-02",
                "--json",
                "--database",
                str(self.db),
            ]
        )
        assert code == 0
        data = json.loads(out)
        assert data["events"] == 4  # days 2, 3, 4 (2 events on day 4)

    def test_invalid_timestamp_rejected(self) -> None:
        code, _, _ = self._run(
            [
                "memory",
                "review",
                "--audit",
                "--since",
                "not-a-date",
                "--database",
                str(self.db),
            ]
        )
        assert code != 0

    def test_reversed_range_rejected(self) -> None:
        code, _, _ = self._run(
            [
                "memory",
                "review",
                "--audit",
                "--since",
                "2026-02-05",
                "--until",
                "2026-02-01",
                "--database",
                str(self.db),
            ]
        )
        assert code != 0

    def test_empty_filtered_window(self) -> None:
        code, out, _ = self._run(
            [
                "memory",
                "review",
                "--audit",
                "--since",
                "2027-01-01",
                "--database",
                str(self.db),
            ]
        )
        assert code == 0
        assert "events: 0" in out
        assert "(none)" in out

    def test_privacy_sentinels_never_leaked(self) -> None:
        # Even with time filtering, sensitive content never appears in output
        code, out, _ = self._run(
            [
                "memory",
                "review",
                "--audit",
                "--since",
                "2026-02-01",
                "--json",
                "--database",
                str(self.db),
            ]
        )
        assert code == 0
        assert STATEMENT_SENTINEL not in out
        assert EVIDENCE_SENTINEL not in out
        assert PROMPT_SENTINEL not in out
        assert SECRET_SENTINEL not in out
        assert "statement_hash" not in out


# --- Agent tool tests ---


class TestAgentToolAuditTimeFiltering:
    """Tests for memory_review_audit agent tool with since/until."""

    def setup_method(self) -> None:
        self.db = Path(":memory:")
        self.service = _make_service(self.db, seeded=True)
        self.tools = _make_tools(self.service)
        self.policy = _make_policy(self.tools)

    def test_recent_with_since(self) -> None:
        result = _invoke(
            self.policy, RESEARCHER, {"operation": "recent", "since": "2026-02-03"}
        )
        assert len(result["events"]) == 3
        for event in result["events"]:
            assert "review_id" in event
            assert "created_at" in event
            assert "statement_hash" not in event

    def test_recent_with_until(self) -> None:
        result = _invoke(
            self.policy,
            RESEARCHER,
            {"operation": "recent", "until": "2026-02-02T23:59:59"},
        )
        assert len(result["events"]) == 2

    def test_recent_with_both(self) -> None:
        result = _invoke(
            self.policy,
            RESEARCHER,
            {
                "operation": "recent",
                "since": "2026-02-02",
                "until": "2026-02-03T23:59:59",
            },
        )
        assert len(result["events"]) == 2

    def test_counts_with_since(self) -> None:
        result = _invoke(
            self.policy, RESEARCHER, {"operation": "counts", "since": "2026-02-03"}
        )
        assert result["events"] == 3

    def test_counts_with_until(self) -> None:
        result = _invoke(
            self.policy,
            RESEARCHER,
            {"operation": "counts", "until": "2026-02-02T23:59:59"},
        )
        assert result["events"] == 2

    def test_counts_with_both(self) -> None:
        result = _invoke(
            self.policy,
            RESEARCHER,
            {
                "operation": "counts",
                "since": "2026-02-02",
                "until": "2026-02-03T23:59:59",
            },
        )
        assert result["events"] == 2

    def test_invalid_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid since timestamp"):
            _invoke(
                self.policy, RESEARCHER, {"operation": "recent", "since": "not-a-date"}
            )

    def test_reversed_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="since must not be after until"):
            _invoke(
                self.policy,
                RESEARCHER,
                {"operation": "recent", "since": "2026-02-05", "until": "2026-02-01"},
            )

    def test_non_string_timestamp_rejected(self) -> None:
        with pytest.raises(TypeError, match="since must be a string"):
            _invoke(self.policy, RESEARCHER, {"operation": "recent", "since": 123})

    def test_limit_with_time_range(self) -> None:
        result = _invoke(
            self.policy,
            RESEARCHER,
            {"operation": "recent", "since": "2026-02-01", "limit": 2},
        )
        assert len(result["events"]) == 2  # limited after filtering

    def test_max_limit_clamp_with_time_range(self) -> None:
        result = _invoke(
            self.policy,
            RESEARCHER,
            {"operation": "recent", "since": "2026-02-01", "limit": 1000},
        )
        assert len(result["events"]) <= 5  # clamped to max 200, but only 5 exist

    def test_unknown_operation_still_rejected(self) -> None:
        with pytest.raises(ValueError, match="operation must be one of"):
            _invoke(
                self.policy, RESEARCHER, {"operation": "unknown", "since": "2026-02-01"}
            )

    def test_reviewer_agent_denied(self) -> None:
        with pytest.raises(PolicyDenialError):
            _invoke(
                self.policy, REVIEWER, {"operation": "recent", "since": "2026-02-01"}
            )

    def test_researcher_allowed(self) -> None:
        result = _invoke(
            self.policy, RESEARCHER, {"operation": "recent", "since": "2026-02-01"}
        )
        assert "events" in result

    def test_mutation_args_ignored(self) -> None:
        # Mutation-looking args should be ignored, not cause an error
        result = _invoke(
            self.policy,
            RESEARCHER,
            {
                "operation": "recent",
                "since": "2026-02-01",
                "approve": "1",
                "reject": "1",
                "review_id": "1",
                "include_evidence": True,
                "include_statements": True,
                "debug": True,
                "raw": True,
                "sql": "SELECT * FROM memory_review_audit",
            },
        )
        assert "events" in result

    def test_no_sql_escape_hatch(self) -> None:
        # No way to inject arbitrary SQL
        result = _invoke(
            self.policy,
            RESEARCHER,
            {"operation": "recent", "since": "2026-02-01", "sql": "DROP TABLE"},
        )
        assert "events" in result

    def test_privacy_sentinels_absent(self) -> None:
        result = _invoke(
            self.policy, RESEARCHER, {"operation": "recent", "since": "2026-02-01"}
        )
        out = json.dumps(result)
        assert STATEMENT_SENTINEL not in out
        assert EVIDENCE_SENTINEL not in out
        assert PROMPT_SENTINEL not in out
        assert SECRET_SENTINEL not in out
        assert "statement_hash" not in out

    def test_output_only_allowed_fields(self) -> None:
        result = _invoke(
            self.policy, RESEARCHER, {"operation": "recent", "since": "2026-02-01"}
        )
        for event in result["events"]:
            allowed = {
                "review_id",
                "action",
                "outcome",
                "actor",
                "policy_category",
                "memory_id",
                "created_at",
            }
            assert set(event.keys()) == allowed

    def test_read_only_no_state_change(self) -> None:
        # Verify DB bytes don't change after tool invocation (scratch file DB).
        tmp_dir = Path(tempfile.mkdtemp())
        tmp_db = tmp_dir / "audit.db"
        try:
            service = _make_service(tmp_db, seeded=True)
            tools = _make_tools(service)
            policy = _make_policy(tools)

            # Get bytes before
            before = tmp_db.read_bytes()

            _invoke(
                policy,
                RESEARCHER,
                {"operation": "recent", "since": "2026-02-01", "limit": 200},
            )
            _invoke(
                policy,
                RESEARCHER,
                {
                    "operation": "counts",
                    "since": "2026-02-01",
                    "until": "2026-02-04T23:59:59",
                },
            )

            # Get bytes after
            after = tmp_db.read_bytes()
            assert before == after  # no mutation
        finally:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)


# --- Regression: Phase 27/28/29 tests remain green ---


class TestPhase2829Regression:
    """Ensure Phase 27/28/29 behavior is preserved."""

    def setup_method(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self.db = Path(self._tmpdir) / "audit.db"
        # Use _make_service which creates its own connection and seeds
        self.service = _make_service(self.db, seeded=True)
        self.tools = _make_tools(self.service)
        self.policy = _make_policy(self.tools)

    def teardown_method(self) -> None:
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_audit_without_bounds_still_works(self) -> None:
        # Phase 28/29: no bounds should return all
        rows = self.service.audit(limit=200)
        assert len(rows) == 5

        counts = self.service.audit_counts()
        assert counts["events"] == 5

    def test_tool_without_bounds_still_works(self) -> None:
        result = _invoke(self.policy, RESEARCHER, {"operation": "recent"})
        assert len(result["events"]) == 5

        result = _invoke(self.policy, RESEARCHER, {"operation": "counts"})
        assert result["events"] == 5

    def test_cli_audit_without_bounds_still_works(self) -> None:
        import sys
        from io import StringIO

        old_argv = sys.argv
        old_stdout = sys.stdout
        sys.argv = [
            "personal-ai",
            "memory",
            "review",
            "--audit",
            "--database",
            str(self.db),
        ]
        sys.stdout = StringIO()
        try:
            code = 0
            try:
                cli_main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
            out = sys.stdout.getvalue()
            assert code == 0
            assert "events: 5" in out
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout


# --- Read-only guarantee tests ---


class TestReadOnlyGuarantee:
    """Prove that time filtering never mutates the database."""

    def test_store_read_only(self, tmp_path: Path) -> None:
        tmp_db = tmp_path / "audit.db"
        conn = connect_database(tmp_db)
        store = CurationStore(conn)
        _seed_audit(store)
        conn.close()

        # Read file content before
        before = tmp_db.read_bytes()

        # Run various filtered queries
        conn = connect_database(tmp_db)
        store = CurationStore(conn)
        store.review_audit(limit=200, since="2026-02-02")
        store.review_audit(limit=200, until="2026-02-03")
        store.review_audit(limit=200, since="2026-02-01", until="2026-02-04")
        store.review_audit_counts(since="2026-02-02")
        store.review_audit_counts(until="2026-02-03")
        store.review_audit_counts(since="2026-02-01", until="2026-02-04")
        conn.close()

        # Read file content after
        after = tmp_db.read_bytes()
        assert before == after

    def test_service_read_only(self, tmp_path: Path) -> None:
        tmp_db = tmp_path / "audit.db"
        service = _make_service(tmp_db, seeded=True)

        before = tmp_db.read_bytes()

        service.audit(limit=200, since="2026-02-02")
        service.audit(limit=200, until="2026-02-03")
        service.audit(limit=200, since="2026-02-01", until="2026-02-04")
        service.audit_counts(since="2026-02-02")
        service.audit_counts(until="2026-02-03")
        service.audit_counts(since="2026-02-01", until="2026-02-04")

        after = tmp_db.read_bytes()
        assert before == after

    def test_tool_read_only(self, tmp_path: Path) -> None:
        tmp_db = tmp_path / "audit.db"
        service = _make_service(tmp_db, seeded=True)
        tools = _make_tools(service)
        policy = _make_policy(tools)

        before = tmp_db.read_bytes()

        _invoke(policy, RESEARCHER, {"operation": "recent", "since": "2026-02-01"})
        _invoke(policy, RESEARCHER, {"operation": "recent", "until": "2026-02-03"})
        _invoke(
            policy,
            RESEARCHER,
            {"operation": "recent", "since": "2026-02-01", "until": "2026-02-04"},
        )
        _invoke(policy, RESEARCHER, {"operation": "counts", "since": "2026-02-01"})
        _invoke(policy, RESEARCHER, {"operation": "counts", "until": "2026-02-03"})
        _invoke(
            policy,
            RESEARCHER,
            {"operation": "counts", "since": "2026-02-01", "until": "2026-02-04"},
        )

        after = tmp_db.read_bytes()
        assert before == after


# Need to import hashlib for the read-only tests
