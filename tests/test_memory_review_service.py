"""Phase 26: review-queue adjudication correctness.

Exercises the exception-only human review boundary end to end:
``pending -> approve/reject`` and the invariants that make adjudication safe
(one canonical write path, policy re-evaluation at decision time, secrets
never approvable, language-agnostic behavior, provenance preserved,
idempotent decisions, serialized concurrency).

Everything is hermetic: temp SQLite files and in-memory databases only —
no network, no Ollama, no real personal data.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from personal_ai.memory.curation import CurationStore
from personal_ai.memory.models import (
    MemoryCandidate,
    MemoryEvidenceRef,
    now_iso,
)
from personal_ai.memory.policy import MemoryPolicy, Sensitivity
from personal_ai.memory.review import (
    MemoryReviewService,
    _AdjudicationConnection,
)
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryStore
from personal_ai.storage import connect_database


def _evidence(
    *message_ids: str,
    source_type: str = "chatgpt",
    source_id: str = "conv-x",
    timestamp: str = "2026-01-01T00:00:00+00:00",
) -> tuple[MemoryEvidenceRef, ...]:
    return tuple(
        MemoryEvidenceRef(
            source_type=source_type,
            source_id=f"{source_id}-{message_id}"
            if source_id == "conv-x"
            else source_id,
            source_document_id=message_id,
            source_timestamp=timestamp,
        )
        for message_id in message_ids
    )


def _candidate(
    statement: str,
    *,
    kind: str = "work",
    confidence: float = 0.9,
    durability: float = 0.8,
    relevance: float = 0.8,
    evidence: tuple[MemoryEvidenceRef, ...] = (),
) -> MemoryCandidate:
    return MemoryCandidate(
        statement=statement,
        kind=kind,
        confidence=confidence,
        durability=durability,
        relevance=relevance,
        specificity=0.7,
        recurrence=1,
        utility=0.7,
        temporal_scope="current",
        assertion_status="asserted",
        summary="",
        evidence=evidence,
    )


def _enqueue(
    store: CurationStore,
    candidate: MemoryCandidate,
    *,
    category: str,
    reason: str,
    run_id: str = "r26",
    unit_id: str = "u26",
    created_at: str | None = None,
) -> int:
    return store.enqueue_review(
        run_id=run_id,
        unit_id=unit_id,
        source_type=candidate.evidence[0].source_type
        if candidate.evidence
        else "chatgpt",
        kind=str(candidate.kind.value),
        temporal_scope=str(candidate.temporal_scope.value),
        confidence=candidate.confidence,
        importance=candidate.utility,
        statement=candidate.statement,
        candidate_json=json.dumps(candidate.to_dict(), sort_keys=True),
        evidence_json=json.dumps(
            [ref.to_dict() for ref in candidate.evidence], sort_keys=True
        ),
        created_at=created_at or now_iso(),
        category=category,
        reason=reason,
    )


@dataclass(frozen=True)
class Harness:
    connection: sqlite3.Connection
    proxy: _AdjudicationConnection
    curation: CurationStore
    service: MemoryService
    review: MemoryReviewService

    def close(self) -> None:
        self.connection.close()


def _harness(tmp_path: Path | None = None, *, shared: bool = True) -> Harness:
    if tmp_path is not None:
        connection = sqlite3.connect(
            str(tmp_path / "review.db"), check_same_thread=False
        )
    elif shared:
        connection = sqlite3.connect(":memory:", check_same_thread=False)
    else:
        connection = connect_database(":memory:")
    proxy = _AdjudicationConnection(connection)
    curation = CurationStore(proxy)
    service = MemoryService(MemoryStore(proxy))
    review = MemoryReviewService(curation, service, connection=proxy)
    return Harness(connection, proxy, curation, service, review)


SENSITIVE_EN = "The user earns a salary of 100000 USD per year"
SENSITIVE_DE = "Der Nutzer verdient ein Gehalt von 80000 Euro im Jahr"
SENSITIVE_ES = "El usuario tiene un salario de 60000 euros al año"

SAFE_EN = "The user works as a software engineer"
SAFE_DE = "Der Nutzer arbeitet als Softwareentwickler"
SAFE_ES = "El usuario trabaja como ingeniero de software"


# ---------------------------------------------------------------------------
# approve / reject basics
# ---------------------------------------------------------------------------


def test_approve_sensitive_writes_exactly_one_memory(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    rid = _enqueue(
        harness.curation,
        _candidate(SENSITIVE_EN, evidence=_evidence("en-msg-001")),
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        result = harness.review.approve(rid)
        assert result["outcome"] == "approved"
        assert result["category"] == "require_approval"
        memories = harness.service.list()
        assert len(memories) == 1
        assert memories[0].content == SENSITIVE_EN
        row = harness.curation.get_review(rid)
        assert row["status"] == "approved"  # type: ignore[index]
        assert harness.curation.review_counts()["by_status"].get("pending", 0) == 0
    finally:
        harness.close()


def test_approve_is_idempotent_no_duplicate_memory(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    rid = _enqueue(
        harness.curation,
        _candidate(SENSITIVE_EN, evidence=_evidence("en-msg-001")),
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        first = harness.review.approve(rid)
        second = harness.review.approve(rid)
        assert first["outcome"] == "approved"
        assert second["outcome"] == "not_pending"
        assert second["status"] == "approved"
        assert len(harness.service.list()) == 1
        assert len(harness.service.evidence_for(first["memory_id"])) == 1  # type: ignore[arg-type]
        assert len(harness.curation.list_review()) == 1
    finally:
        harness.close()


def test_reject_writes_no_memory_and_is_terminal(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    rid = _enqueue(
        harness.curation,
        _candidate(SENSITIVE_EN, evidence=_evidence("en-msg-001")),
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        first = harness.review.reject(rid, note="not relevant")
        second = harness.review.reject(rid)
        assert first["outcome"] == "rejected"
        assert second["outcome"] == "not_pending"
        assert second["status"] == "rejected"
        assert harness.service.counts()["active"] == 0
        row = harness.curation.get_review(rid)
        assert row["status"] == "rejected"  # type: ignore[index]
        assert row["review_note"] == "not relevant"  # type: ignore[index]
    finally:
        harness.close()


def test_missing_review_id_is_explicit() -> None:
    harness = _harness()
    try:
        assert harness.review.approve(999)["outcome"] == "not_found"
        assert harness.review.reject(999)["outcome"] == "not_found"
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# state machine: reruns never reopen terminal rows
# ---------------------------------------------------------------------------


def test_rerun_after_approve_does_not_reopen_or_duplicate(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    candidate = _candidate(SENSITIVE_EN, evidence=_evidence("en-msg-001"))
    rid = _enqueue(
        harness.curation,
        candidate,
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        harness.review.approve(rid)
        # A rerun of curation re-encounters the same obligation.
        created, again = harness.curation.enqueue_review_if_missing(
            run_id="r26",
            unit_id="u26",
            source_type="chatgpt",
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
            category="require_approval",
            reason="keyword_sensitive",
        )
        assert created is False
        assert again == rid
        assert len(harness.curation.list_review()) == 1
        assert harness.curation.get_review(rid)["status"] == "approved"  # type: ignore[index]
        assert len(harness.service.list()) == 1
    finally:
        harness.close()


def test_rerun_after_reject_does_not_reopen_or_write(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    candidate = _candidate(SENSITIVE_EN, evidence=_evidence("en-msg-001"))
    rid = _enqueue(
        harness.curation,
        candidate,
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        harness.review.reject(rid)
        created, again = harness.curation.enqueue_review_if_missing(
            run_id="r26",
            unit_id="u26",
            source_type="chatgpt",
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
            category="require_approval",
            reason="keyword_sensitive",
        )
        assert created is False
        assert again == rid
        assert harness.curation.get_review(rid)["status"] == "rejected"  # type: ignore[index]
        assert harness.service.counts()["active"] == 0
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# policy remains authoritative at approval time
# ---------------------------------------------------------------------------


def test_secret_can_never_be_approved_even_from_a_forged_row(tmp_path: Path) -> None:
    # A secret must not become a pending obligation through the legitimate
    # pipeline (policy rejects before the queue). But even if a row for a
    # secret candidate is forged directly into the store, approval must not
    # resurrect it: policy re-evaluation expires the row and writes nothing.
    harness = _harness(tmp_path)
    forged = _candidate(
        "My password is hunter2secret", evidence=_evidence("sec-msg-001")
    )
    assert harness.review._policy.evaluate(forged).decision.value == "reject"
    rid = _enqueue(
        harness.curation,
        forged,
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        decision = harness.review._policy.evaluate(forged)
        assert decision.sensitivity is Sensitivity.SECRET
        result = harness.review.approve(rid)
        assert result["outcome"] == "expired"
        assert harness.service.counts()["active"] == 0
        assert harness.curation.get_review(rid)["status"] == "expired"  # type: ignore[index]
    finally:
        harness.close()


def test_tampered_candidate_cannot_bypass_policy_at_approval(tmp_path: Path) -> None:
    # The row keyed as "require_approval" held a safe candidate when queued;
    # someone changed the stored candidate to secret content. Approval must
    # not trust "previously reviewed" — the policy is re-evaluated on the
    # reconstructed candidate and the row expires with no write.
    harness = _harness(tmp_path)
    safe = _candidate(SENSITIVE_EN, evidence=_evidence("en-msg-001"))
    rid = _enqueue(
        harness.curation,
        safe,
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        tampered = _candidate(
            "My API key is sk_live_1234567890abcdef123456",
            evidence=_evidence("evil-msg-001"),
        )
        harness.connection.execute(
            "UPDATE memory_curation_review SET candidate_json = ? WHERE id = ?",
            (json.dumps(tampered.to_dict(), sort_keys=True), rid),
        )
        harness.connection.commit()
        result = harness.review.approve(rid)
        assert result["outcome"] == "expired"
        assert result["reason"] == "secret_content"
        assert harness.service.counts()["active"] == 0
        assert harness.curation.get_review(rid)["status"] == "expired"  # type: ignore[index]
    finally:
        harness.close()


def test_deferred_candidate_expires_without_storage(tmp_path: Path) -> None:
    # A candidate that now only reaches DEFER (e.g. provenance no longer
    # strong enough) is not written; the row expires as a recoverable state.
    harness = _harness(tmp_path)
    weak = _candidate(
        SAFE_EN,
        evidence=(),
        confidence=0.3,
    )
    rid = _enqueue(
        harness.curation,
        weak,
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        result = harness.review.approve(rid)
        assert result["outcome"] == "expired"
        assert result["decision"] == "defer"
        assert result["reason"] == "weak_source_quality"
        assert harness.service.counts()["active"] == 0
        assert harness.curation.get_review(rid)["status"] == "expired"  # type: ignore[index]
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# secrets never reach the queue through the real pipeline
# ---------------------------------------------------------------------------


def test_secret_documents_pipeline_never_creates_review_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personal_ai.documents.conversations import (
        Conversation,
        ConversationMessage,
    )
    from personal_ai.memory.curation import (
        ConversationCurationAdapter,
        MemoryCurationRunner,
    )

    connection = connect_database(tmp_path / "seed.db")
    conv = Conversation(
        id="conv-sec",
        title="Secret chat",
        source_type="chatgpt",
        created_at="2026-01-01T00:00:00+00:00",
        modified_at="2026-01-01T00:00:00+00:00",
        metadata={},
    )
    message = ConversationMessage(
        id="m-sec",
        conversation_id="conv-sec",
        message_index=0,
        role="user",
        speaker="User",
        content_text="My password is hunter2secret",
        content_type="text",
        timestamp=None,
        is_active_branch=True,
        metadata={},
    )
    from personal_ai.storage.conversations import ConversationStore

    cstore = ConversationStore(connection)
    cstore.save_conversation(conv)
    cstore.save_messages((message,))
    harness = _harness(tmp_path, shared=False)
    runner = MemoryCurationRunner(
        store=harness.curation,
        adapter=ConversationCurationAdapter(cstore, client=None),
        memory_service=harness.service,
        policy=MemoryPolicy(),
    )
    try:
        report = runner.run(
            __import__(
                "personal_ai.memory.curation", fromlist=["CurationConfig"]
            ).CurationConfig(source_type="chatgpt", limit=10)
        )
        assert report.counters.tally.candidates == 0
        assert report.counters.tally.rejected == 0
        assert report.counters.review_queued == 0
        assert harness.service.counts()["active"] == 0
        assert harness.curation.list_review() == ()
    finally:
        harness.close()
        connection.close()


# ---------------------------------------------------------------------------
# multilingual lifecycle: statements preserved, evidence untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "message_id"),
    [
        (SAFE_EN, "en-msg-001"),
        (SAFE_DE, "de-msg-001"),
        (SAFE_ES, "es-msg-001"),
    ],
)
def test_multilingual_evidence_survives_approval_unchanged(
    tmp_path: Path, statement: str, message_id: str
) -> None:
    harness = _harness(tmp_path)
    candidate = _candidate(statement, evidence=_evidence(message_id))
    rid = _enqueue(
        harness.curation,
        candidate,
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        result = harness.review.approve(rid)
        assert result["outcome"] == "approved"
        memories = harness.service.list()
        assert len(memories) == 1
        assert memories[0].content == statement
        evidence = harness.service.evidence_for(memories[0].memory_id)
        assert [row["source_document_id"] for row in evidence] == [message_id]
        # No translation, no fabricated ids — the original reference is the
        # only evidence record.
        assert len(evidence) == 1
    finally:
        harness.close()


@pytest.mark.parametrize(
    ("statement",),
    [(SENSITIVE_EN,), (SENSITIVE_DE,), (SENSITIVE_ES,)],
)
def test_multilingual_sensitive_requires_approval_then_writes(
    tmp_path: Path, statement: str
) -> None:
    harness = _harness(tmp_path)
    candidate = _candidate(statement)
    policy = MemoryPolicy()
    assert policy.evaluate(candidate).decision.value == "require_approval"
    rid = _enqueue(
        harness.curation,
        candidate,
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        assert harness.service.counts()["active"] == 0
        result = harness.review.approve(rid)
        assert result["outcome"] == "approved"
        memories = harness.service.list()
        assert len(memories) == 1
        assert memories[0].content == statement
        # No per-language write path: the same approve API drove all three.
    finally:
        harness.close()


def test_highly_sensitive_requires_approval_then_writes(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    candidate = _candidate("The user has a medical diagnosis of diabetes")
    policy = MemoryPolicy()
    assert policy.evaluate(candidate).decision.value == "require_approval"
    rid = _enqueue(
        harness.curation,
        candidate,
        category="require_approval",
        reason="sensitive_content",
    )
    try:
        assert harness.service.counts()["active"] == 0
        result = harness.review.approve(rid)
        assert result["outcome"] == "approved"
        assert len(harness.service.list()) == 1
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# provenance matrix across the adjudication lifecycle
# ---------------------------------------------------------------------------


def test_provenance_matrix_distinct_obligations_and_terminal_decisions(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    statement = SENSITIVE_EN
    try:
        # Same statement + same evidence: one obligation.
        a = _enqueue(
            harness.curation,
            _candidate(statement, evidence=_evidence("m-a")),
            category="require_approval",
            reason="keyword_sensitive",
        )
        a2 = _enqueue(
            harness.curation,
            _candidate(statement, evidence=_evidence("m-a")),
            category="require_approval",
            reason="keyword_sensitive",
        )
        assert a2 == a
        # Same statement + different evidence: distinct obligations.
        b = _enqueue(
            harness.curation,
            _candidate(statement, evidence=_evidence("m-b")),
            category="require_approval",
            reason="keyword_sensitive",
        )
        assert b != a
        # Same statement + same evidence + different category: distinct.
        c = _enqueue(
            harness.curation,
            _candidate(statement, evidence=_evidence("m-a")),
            category="conflict",
            reason="ambiguous_related_fact",
        )
        assert c != a and c != b
        assert len(harness.curation.list_review()) == 3

        # Approve one, reject another, expire the third by tampering.
        approved = harness.review.approve(a)
        assert approved["outcome"] == "approved"
        rejected = harness.review.reject(b)
        assert rejected["outcome"] == "rejected"
        harness.connection.execute(
            "UPDATE memory_curation_review SET candidate_json = ? WHERE id = ?",
            (
                json.dumps(
                    _candidate(
                        "My API key is sk_live_1234567890abcdef123456",
                        evidence=_evidence("m-c"),
                    ).to_dict(),
                    sort_keys=True,
                ),
                c,
            ),
        )
        harness.connection.commit()
        expired = harness.review.approve(c)
        assert expired["outcome"] == "expired"

        # Reruns deduplicate against all three terminal rows; none reopens.
        # Each rerun reproduces the exact obligation tuple (category/reason
        # and evidence) of the row it must match.
        reruns = {
            a: ("require_approval", "keyword_sensitive", _evidence("m-a")),
            b: ("require_approval", "keyword_sensitive", _evidence("m-b")),
            # Row c was queued as a conflict with evidence m-a; only its
            # candidate_json (not evidence_json) was later tampered with, so
            # the dedup key reproduces the original obligation tuple (m-a).
            c: ("conflict", "ambiguous_related_fact", _evidence("m-a")),
        }
        for rid, (category, reason, evidence) in reruns.items():
            created, again = harness.curation.enqueue_review_if_missing(
                run_id="r26",
                unit_id="u26",
                source_type="chatgpt",
                kind="work",
                temporal_scope="current",
                confidence=0.9,
                importance=0.7,
                statement=statement,
                candidate_json="{}",
                evidence_json=json.dumps(
                    [ref.to_dict() for ref in evidence], sort_keys=True
                ),
                created_at=now_iso(),
                category=category,
                reason=reason,
            )
            assert created is False
            assert again == rid
        assert len(harness.curation.list_review()) == 3
        assert harness.curation.review_counts()["by_status"] == {
            "approved": 1,
            "rejected": 1,
            "expired": 1,
        }
        # Only the approved obligation produced a memory.
        assert len(harness.service.list()) == 1
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# reconciliation is never bypassed by approval
# ---------------------------------------------------------------------------


def test_approval_merges_duplicate_evidence_into_one_memory(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        # The first fact is already stored (evidence m-a).
        seed = _candidate(SAFE_EN, evidence=_evidence("m-a"))
        applied = harness.service.apply_candidate(seed)
        assert applied["status"] == "created"
        assert len(harness.service.list()) == 1

        # A second obligation with the same statement and different evidence
        # is approved; reconciliation must merge, not duplicate.
        rid = _enqueue(
            harness.curation,
            _candidate(SAFE_EN, evidence=_evidence("m-b")),
            category="require_approval",
            reason="keyword_sensitive",
        )
        result = harness.review.approve(rid)
        assert result["outcome"] == "approved"
        assert result["write_status"] == "updated"
        memories = harness.service.list()
        assert len(memories) == 1
        evidence = harness.service.evidence_for(memories[0].memory_id)
        assert {row["source_document_id"] for row in evidence} == {"m-a", "m-b"}
        assert len(evidence) == 2
    finally:
        harness.close()


def test_approval_of_conflict_stores_alongside_never_over(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        harness.service.apply_candidate(
            _candidate("The user works at BCG", evidence=_evidence("m-1"))
        )
        conflicting = _candidate("The user works at Google", evidence=_evidence("m-2"))
        with pytest.raises(Exception, match="ambiguous_related_fact|conflict"):
            harness.service.apply_candidate(conflicting)
        rid = _enqueue(
            harness.curation,
            conflicting,
            category="conflict",
            reason="ambiguous_related_fact",
        )
        result = harness.review.approve(rid)
        assert result["outcome"] == "approved"
        assert result["category"] == "conflict"
        memories = harness.service.list()
        assert len(memories) == 2
        contents = {m.content for m in memories}
        # Both survive; the existing record was not overwritten or superseded.
        assert contents == {"The user works at BCG", "The user works at Google"}
        assert all(m.status.value == "active" for m in memories)
        for memory in memories:
            evidence = harness.service.evidence_for(memory.memory_id)
            assert len(evidence) == 1
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# LLM-style candidates follow exactly the same review workflow
# ---------------------------------------------------------------------------


def test_llm_style_candidate_uses_the_same_adjudication(tmp_path: Path) -> None:
    from personal_ai.memory.models import MemoryCandidate as Candidate

    harness = _harness(tmp_path)
    # An LLM proposal payload (what the proposal layer emits) round-tripped
    # through the canonical candidate boundary.
    proposal = {
        "statement": "The user's salary is 120k",
        "kind": "personal_fact",
        "temporal_scope": "current",
        "confidence": 0.9,
        "durability": 0.8,
        "relevance": 0.8,
        "specificity": 0.7,
        "recurrence": 1,
        "utility": 0.7,
        "assertion_status": "asserted",
        "summary": "",
        "evidence": [
            {
                "source_type": "chatgpt",
                "source_id": "conv-llm",
                "source_document_id": "m-llm-001",
                "source_timestamp": "2026-01-02T00:00:00+00:00",
            }
        ],
    }
    candidate = Candidate.from_dict(proposal)
    assert MemoryPolicy().evaluate(candidate).decision.value == "require_approval"
    rid = _enqueue(
        harness.curation,
        candidate,
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        result = harness.review.approve(rid)
        assert result["outcome"] == "approved"
        memories = harness.service.list()
        assert len(memories) == 1
        evidence = harness.service.evidence_for(memories[0].memory_id)
        assert [row["source_document_id"] for row in evidence] == ["m-llm-001"]
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------


def test_concurrent_approval_exactly_one_authorization_and_write(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    rid = _enqueue(
        harness.curation,
        _candidate(SENSITIVE_EN, evidence=_evidence("en-msg-001")),
        category="require_approval",
        reason="keyword_sensitive",
    )
    outcomes: list[str] = []
    barrier = threading.Barrier(3)
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        result = harness.review.approve(rid)
        with lock:
            outcomes.append(str(result.get("outcome")))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    try:
        assert sorted(outcomes) == ["approved", "not_pending"]
        assert len(harness.service.list()) == 1
        row = harness.curation.get_review(rid)
        assert row["status"] == "approved"  # type: ignore[index]
        assert harness.curation.review_counts()["by_status"].get("pending", 0) == 0
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# privacy-safe output
# ---------------------------------------------------------------------------


def test_adjudication_surfaces_are_privacy_safe(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    rid = _enqueue(
        harness.curation,
        _candidate(SENSITIVE_EN, evidence=_evidence("en-msg-001")),
        category="require_approval",
        reason="keyword_sensitive",
    )
    try:
        counts = harness.review.pending_counts()
        assert counts["pending"] == 1
        approved = harness.review.approve(rid)
        flat = json.dumps(approved)
        assert SENSITIVE_EN not in flat
        assert "salary" not in flat
        rejected_flat = json.dumps(harness.review.reject(rid))
        assert SENSITIVE_EN not in rejected_flat
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# security matrix (EN / DE / ES papered over the deterministic policy)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        (SAFE_EN, "accept"),
        (SAFE_DE, "accept"),
        (SAFE_ES, "accept"),
        ("My password is hunter2secret", "reject"),
        ("Mein Passwort ist geheim123", "reject"),
        ("Mi contraseña es secreta123", "reject"),
        ("The user has a medical diagnosis of diabetes", "require_approval"),
        ("Der Nutzer hat eine medizinische Diagnose", "require_approval"),
        ("El usuario tiene un diagnóstico médico", "require_approval"),
        (SENSITIVE_EN, "require_approval"),
        (SENSITIVE_DE, "require_approval"),
        (SENSITIVE_ES, "require_approval"),
    ],
)
def test_security_matrix_deterministic_policy(statement: str, expected: str) -> None:
    decision = MemoryPolicy().evaluate(_candidate(statement, evidence=_evidence("m-1")))
    assert decision.decision.value == expected
