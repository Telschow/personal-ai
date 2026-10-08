"""Phase 27: review adjudication transaction + audit-log hardening.

Verifies the two durable guarantees added on top of Phase 26's exception-only
human adjudication, without changing its security model, policy semantics,
provenance model, reconciliation behaviour, or single-write-path invariant:

* **atomic adjudication** — the whole decision (pending-row validation,
  deterministic policy re-evaluation, the policy-gated memory write,
  reconciliation, the audit event, and the pending -> terminal transition)
  commits in one explicit ``BEGIN IMMEDIATE`` ... ``COMMIT`` transaction on the
  shared SQLite connection. A failure before commit rolls everything back; a
  committed decision is durable. No ``approved``/no-memory or
  pending/duplicate-memory durable states are possible.
* **durable adjudication audit trail** — exactly one content-free terminal
  event per review obligation (approved/rejected/expired); repeated decisions
  are ``not_pending`` and add no further events.

Everything is hermetic: temp SQLite files and in-memory databases only — no
network, no Ollama, no real personal data.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from personal_ai.memory.curation import CurationStore
from personal_ai.memory.models import (
    MemoryCandidate,
    MemoryEvidenceRef,
    now_iso,
)
from personal_ai.memory.review import (
    MemoryReviewService,
    _AdjudicationConnection,
    _statement_hash,
)
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryStore

# ---------------------------------------------------------------------------
# helpers (mirror test_memory_review_service harness)
# ---------------------------------------------------------------------------


def _evidence(*message_ids: str) -> tuple[MemoryEvidenceRef, ...]:
    return tuple(
        MemoryEvidenceRef(
            source_type="chatgpt",
            source_id=f"conv-x-{mid}",
            source_document_id=mid,
            source_timestamp="2026-01-01T00:00:00+00:00",
        )
        for mid in message_ids
    )


def _candidate(
    statement: str, *, kind: str = "work", msg: str = "ev-001"
) -> MemoryCandidate:
    return MemoryCandidate(
        statement=statement,
        kind=kind,
        confidence=0.9,
        durability=0.8,
        relevance=0.8,
        specificity=0.7,
        recurrence=1,
        utility=0.7,
        temporal_scope="current",
        assertion_status="asserted",
        summary="",
        evidence=_evidence(msg),
    )


SENSITIVE = "The user earns a salary of 100000 USD per year"
SECRET = "The user token is sk_live_abcdefghijklmnopqrst"


def _enqueue(
    store: CurationStore,
    candidate: MemoryCandidate,
    *,
    category: str = "require_approval",
    reason: str = "keyword_sensitive",
) -> int:
    return store.enqueue_review(
        run_id="r27",
        unit_id="u27",
        source_type=candidate.evidence[0].source_type,
        kind=str(candidate.kind.value),
        temporal_scope=str(candidate.temporal_scope.value),
        confidence=candidate.confidence,
        importance=candidate.utility,
        statement=candidate.statement,
        candidate_json=json.dumps(candidate.to_dict(), sort_keys=True),
        evidence_json=json.dumps(
            [ref.to_dict() for ref in candidate.evidence], sort_keys=True
        ),
        created_at=now_iso(),
        category=category,
        reason=reason,
    )


class Harness:
    def __init__(self, path: Path | None = None) -> None:
        if path is not None:
            self.db_path = path / "review.db"
            self.raw = sqlite3.connect(str(self.db_path), check_same_thread=False)
        else:
            self.db_path = None
            self.raw = sqlite3.connect(":memory:", check_same_thread=False)
        self.proxy = _AdjudicationConnection(self.raw)
        self.curation = CurationStore(self.proxy)
        self.service = MemoryService(MemoryStore(self.proxy))
        self.review = MemoryReviewService(
            self.curation, self.service, connection=self.proxy
        )

    def close(self) -> None:
        self.raw.close()


# ---------------------------------------------------------------------------
# audit trail: exactly one content-free terminal event per obligation
# ---------------------------------------------------------------------------


def test_approve_records_single_audit_event_with_metadata(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    rid = _enqueue(h.curation, _candidate(SENSITIVE))
    try:
        result = h.review.approve(rid)
        assert result["outcome"] == "approved"
        events = h.curation.review_audit(review_id=rid)
        assert len(events) == 1
        ev = events[0]
        assert ev["action"] == "approve"
        assert ev["outcome"] == "approved"
        assert ev["review_id"] == rid
        assert ev["memory_id"] == result["memory_id"]
        assert ev["actor"] == "human"
        assert ev["statement_hash"] == _statement_hash(SENSITIVE)
        assert ev["statement_hash"] != SENSITIVE
        assert "salary" not in json.dumps(ev).lower()
        assert ev["created_at"]
    finally:
        h.close()


def test_reject_records_single_audit_event(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    rid = _enqueue(h.curation, _candidate(SENSITIVE))
    try:
        result = h.review.reject(rid, actor="alice")
        assert result["outcome"] == "rejected"
        events = h.curation.review_audit(review_id=rid)
        assert len(events) == 1
        assert events[0]["outcome"] == "rejected"
        assert events[0]["action"] == "reject"
        assert events[0]["actor"] == "alice"
        assert events[0]["memory_id"] is None
    finally:
        h.close()


def test_expired_due_to_revoked_policy_records_event(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    # Policy hard-rejects this candidate at decision time (a genuine secret), so
    # approval expires the row without writing a memory (no write, no storage).
    rid = _enqueue(h.curation, _candidate(SECRET))
    try:
        result = h.review.approve(rid)
        assert result["outcome"] == "expired"
        assert h.service.list() == ()
        events = h.curation.review_audit(review_id=rid)
        assert len(events) == 1
        assert events[0]["outcome"] == "expired"
        assert events[0]["memory_id"] is None
    finally:
        h.close()


def test_repeated_decision_is_not_pending_without_new_event(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    rid = _enqueue(h.curation, _candidate(SENSITIVE))
    try:
        first = h.review.approve(rid)
        assert first["outcome"] == "approved"
        second = h.review.approve(rid)
        assert second["outcome"] == "not_pending"
        assert len(h.curation.review_audit(review_id=rid)) == 1
        third = h.review.reject(rid)
        assert third["outcome"] == "not_pending"
        assert len(h.curation.review_audit(review_id=rid)) == 1
        # exactly one memory, no duplicates
        assert len(h.service.list()) == 1
    finally:
        h.close()


def test_not_found_records_no_audit_event(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    try:
        result = h.review.approve(999999)
        assert result["outcome"] == "not_found"
        assert h.curation.review_audit() == ()
    finally:
        h.close()


def test_audit_counts_are_aggregate_only(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    try:
        r1 = _enqueue(h.curation, _candidate(SENSITIVE, msg="ev-001"))
        r2 = _enqueue(h.curation, _candidate(SENSITIVE, msg="ev-002"))
        r3 = _enqueue(h.curation, _candidate(SECRET, msg="ev-003"))
        assert h.review.approve(r1)["outcome"] == "approved"
        assert h.review.reject(r2)["outcome"] == "rejected"
        assert h.review.approve(r3)["outcome"] == "expired"
        counts = h.curation.review_audit_counts()
        assert counts["total"] == 3
        assert counts["by_outcome"]["approved"] == 1
        assert counts["by_outcome"]["rejected"] == 1
        assert counts["by_outcome"]["expired"] == 1
    finally:
        h.close()


# ---------------------------------------------------------------------------
# atomicity: injected failure mid-decision rolls everything back
# ---------------------------------------------------------------------------


def test_crash_after_write_but_before_transition_rolls_back_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate the process dying right after the memory write: the memory
    write, the audit event, and the review transition must all be undone."""

    def boom(review_id, status, *, reviewed_at, note=""):
        raise RuntimeError("simulated crash before review transition")

    h = Harness(tmp_path)
    rid = _enqueue(h.curation, _candidate(SENSITIVE))
    # Reopen a second, independent connection over the same file to observe the
    # durable state independently of the in-flight (rolled-back) transaction.
    observer = sqlite3.connect(str(tmp_path / "review.db"))
    try:
        monkeypatch.setattr(h.curation, "set_review_status", boom)
        with pytest.raises(RuntimeError):
            h.review.approve(rid)
        # Nothing durable: row still pending, no memory, no audit event.
        row = h.curation.get_review(rid)
        assert str(row["status"]) == "pending"
        assert list(h.curation.review_audit(review_id=rid)) == []
        assert h.service.list() == ()
        # Independent connection agrees there is no durable memory.
        assert observer.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    finally:
        h.close()
        observer.close()


def test_committed_approval_is_durable_across_connection(tmp_path: Path) -> None:
    """After a clean commit, a fresh connection sees the memory, the approved
    row, and the audit event — proving the unit committed atomically."""
    h = Harness(tmp_path)
    rid = _enqueue(h.curation, _candidate(SENSITIVE))
    observer = sqlite3.connect(str(tmp_path / "review.db"))
    try:
        result = h.review.approve(rid)
        assert result["outcome"] == "approved"
        assert observer.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
        assert (
            observer.execute(
                "SELECT status FROM memory_curation_review WHERE id=?",
                (rid,),
            ).fetchone()[0]
            == "approved"
        )
        assert (
            observer.execute(
                "SELECT COUNT(*) FROM memory_review_audit WHERE review_id=?",
                (rid,),
            ).fetchone()[0]
            == 1
        )
    finally:
        h.close()
        observer.close()


def test_commit_failure_returns_conservative_error_no_approved(tmp_path: Path) -> None:
    """If the final COMMIT fails, approve must not report the decision as
    committed; it returns a conservative error outcome and writes nothing.

    The failing proxy is the *single* shared proxy used by both stores and the
    review service, so the intermediate commits are all deferred inside the one
    transaction and the failed COMMIT rolls everything back."""

    class FailingCommitRaw:
        def __init__(self, raw: sqlite3.Connection) -> None:
            self._raw = raw

        def execute(self, sql, params=()):
            if isinstance(sql, str) and "COMMIT" in sql.upper():
                raise sqlite3.OperationalError("disk I/O error")
            return self._raw.execute(sql, params)

        def commit(self) -> None:
            self._raw.commit()

        def rollback(self) -> None:
            self._raw.rollback()

        def executescript(self, script):
            return self._raw.executescript(script)

        def __getattr__(self, name):
            return getattr(self._raw, name)

    raw = sqlite3.connect(str(tmp_path / "commitfail.db"), check_same_thread=False)
    failing_raw = FailingCommitRaw(raw)  # type: ignore[arg-type]
    proxy = _AdjudicationConnection(failing_raw)  # type: ignore[arg-type]
    curation = CurationStore(proxy)
    service = MemoryService(MemoryStore(proxy))
    review = MemoryReviewService(curation, service, connection=proxy)
    rid = _enqueue(curation, _candidate(SENSITIVE))
    observer = sqlite3.connect(str(tmp_path / "commitfail.db"))
    try:
        result = review.approve(rid)
        assert result["outcome"] == "error"
        assert "commit failed" in result["reason"]
        # Nothing durable: no memory, row still pending, no audit event.
        assert service.list() == ()
        assert str(curation.get_review(rid)["status"]) == "pending"
        assert curation.review_audit(review_id=rid) == ()
        assert observer.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    finally:
        raw.close()
        observer.close()


def test_database_level_crash_atomicity(tmp_path: Path) -> None:
    """Raw SQLite-level check that BEGIN IMMEDIATE ... (no commit) does not
    leak partial adjudication rows to other connections — the foundation of the
    crash guarantee (SQLite rolls the transaction back on recovery)."""
    db = tmp_path / "db.sqlite"
    conn_a = sqlite3.connect(str(db))
    conn_b = sqlite3.connect(str(db))
    try:
        conn_a.execute("CREATE TABLE t (x TEXT)")
        conn_a.execute("INSERT INTO t VALUES (?)", ("seed",))
        conn_a.commit()
        conn_b.execute("BEGIN IMMEDIATE")
        conn_b.execute("INSERT INTO t VALUES (?)", ("partial",))
        # Simulate a die before COMMIT: another connection must not see it.
        assert conn_a.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
        # A process crash would leave the journal to roll this back; an explicit
        # rollback models that recovery.
        conn_b.execute("ROLLBACK")
        assert conn_a.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    finally:
        conn_a.close()
        conn_b.close()


# ---------------------------------------------------------------------------
# concurrency: one approval across separate shared connections/instances
# ---------------------------------------------------------------------------


def test_two_connections_concurrent_approval_single_write(tmp_path: Path) -> None:
    """Two independent review services over the same DB file, each on its own
    connection, approve the same pending row concurrently. Exactly one wins and
    writes one memory + one audit event; the other observes ``not_pending`` and
    writes nothing."""
    db = tmp_path / "conc.db"
    c1 = sqlite3.connect(str(db), check_same_thread=False)
    c2 = sqlite3.connect(str(db), check_same_thread=False)
    p1 = _AdjudicationConnection(c1)
    p2 = _AdjudicationConnection(c2)
    curation1 = CurationStore(p1)
    service1 = MemoryService(MemoryStore(p1))
    review1 = MemoryReviewService(curation1, service1, connection=p1)
    curation2 = CurationStore(p2)
    service2 = MemoryService(MemoryStore(p2))
    review2 = MemoryReviewService(curation2, service2, connection=p2)
    rid = _enqueue(curation1, _candidate(SENSITIVE))
    # Both services share the same file; let them act on the same row id.
    barrier = threading.Barrier(2)
    results: list[dict[str, object]] = []

    def run(review, out):
        barrier.wait()
        out.append(review.approve(rid, actor="t"))

    t1 = threading.Thread(target=run, args=(review1, results))
    t2 = threading.Thread(target=run, args=(review2, results))
    t1.start()
    t2.start()
    t1.join(10)
    t2.join(10)
    outcomes = sorted(str(r.get("outcome")) for r in results)
    assert outcomes == ["approved", "not_pending"]
    # Single durable memory + single audit event.
    c3 = sqlite3.connect(str(db))
    try:
        assert c3.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
        assert (
            c3.execute(
                "SELECT COUNT(*) FROM memory_review_audit WHERE review_id=?",
                (rid,),
            ).fetchone()[0]
            == 1
        )
        assert (
            c3.execute(
                "SELECT status FROM memory_curation_review WHERE id=?", (rid,)
            ).fetchone()[0]
            == "approved"
        )
    finally:
        c1.close()
        c2.close()
        c3.close()
