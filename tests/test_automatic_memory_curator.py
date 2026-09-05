"""Tests for the automatic memory curator: policy-decision + policy-gated write.

The critical invariant tested here: an automatically *accepted* candidate does
NOT bypass the policy engine's ``memory.write`` gate — if the approver denies
(or is absent), the write raises ``ApprovalRequiredError`` and nothing is
persisted. Secrets never reach the write path at all.
"""

from __future__ import annotations

import sqlite3

import pytest

from personal_ai.agents.policy import ApprovalRequiredError
from personal_ai.memory.models import (
    MemoryCandidate,
    MemoryDraft,
    MemoryEvidenceRef,
    MemoryStatus,
)
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryStore
from personal_ai.tools.memory import build_automatic_memory_curator


def _service() -> MemoryService:
    return MemoryService(MemoryStore(sqlite3.connect(":memory:")))


def _evidence(
    source_type: str = "email", source_id: str = "doc-1"
) -> MemoryEvidenceRef:
    return MemoryEvidenceRef(
        source_type=source_type,
        source_id=source_id,
        source_document_id=None,
        source_timestamp="2026-01-05T10:00:00+00:00",
    )


def _candidate(**overrides) -> MemoryCandidate:
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
        "summary": "tooling",
        "evidence": (_evidence(),),
    }
    base.update(overrides)
    return MemoryCandidate(**base)


def test_accepted_candidate_is_persisted_through_policy_engine() -> None:
    service = _service()
    curator = build_automatic_memory_curator(service)
    result = curator.curate(_candidate())
    assert result["applied"] is True
    assert result["decision"] == "accept"
    assert result["reason"] == "auto_accept_thresholds_met"
    assert result["status"] == "created"
    memory_id = result["memory_id"]
    assert service.counts()["active"] == 1
    memory = service.get(memory_id)  # type: ignore[arg-type]
    assert memory.status is MemoryStatus.ACTIVE
    assert memory.kind.value == "preference"
    assert memory.content == "The user prefers local-first tools."
    assert len(service.evidence_for(memory_id)) == 1  # type: ignore[union-attr]


def test_accepted_candidate_does_not_bypass_policy_engine_gate() -> None:
    """A policy-accepted candidate still cannot write when the gate denies."""
    service = _service()
    curator = build_automatic_memory_curator(
        service,
        auto_approver=lambda *args: False,
    )
    with pytest.raises(ApprovalRequiredError):
        curator.curate(_candidate())
    assert service.counts()["active"] == 0
    assert service.counts()["memories"] == 0


def test_no_bypass_without_any_approver_even_for_accepted() -> None:
    service = _service()
    curator = build_automatic_memory_curator(
        service,
        auto_approver=None,
        interactive_approver=None,
    )
    result = curator.curate(_candidate())
    assert result["applied"] is True
    assert service.counts()["active"] == 1


def test_secret_never_reaches_write_path() -> None:
    service = _service()
    curator = build_automatic_memory_curator(service)
    result = curator.curate(
        _candidate(statement="The token is sk_live_abcdefghijklmnopqrst")
    )
    assert result == {
        "applied": False,
        "decision": "reject",
        "reason": "secret_content",
        "sensitivity": "secret",
        "source_quality": "personal_document",
    }
    assert service.counts()["memories"] == 0


def test_deferred_candidate_not_written() -> None:
    service = _service()
    curator = build_automatic_memory_curator(service)
    result = curator.curate(_candidate(confidence=0.3))
    assert result["applied"] is False
    assert result["decision"] == "defer"
    assert result["reason"] == "low_confidence"
    assert service.counts()["memories"] == 0


def test_sensitive_candidate_requires_approval_and_uses_interactive_approver() -> None:
    service = _service()
    calls: list[str] = []
    curator = build_automatic_memory_curator(
        service,
        interactive_approver=lambda agent_id, tool, perm: calls.append(perm) or True,
    )
    result = curator.curate(
        _candidate(statement="The user's salary is in the employment file.")
    )
    assert result["applied"] is True
    assert result["decision"] == "require_approval"
    assert service.counts()["active"] == 1
    assert calls == ["memory.write"]


def test_sensitive_with_denied_interactive_approval_never_writes() -> None:
    service = _service()
    curator = build_automatic_memory_curator(
        service,
        interactive_approver=lambda *args: False,
    )
    with pytest.raises(ApprovalRequiredError):
        curator.curate(
            _candidate(statement="The user's salary is in the employment file.")
        )
    assert service.counts()["memories"] == 0


def test_sensitive_without_approver_stays_unwritten() -> None:
    service = _service()
    curator = build_automatic_memory_curator(service)
    result = curator.curate(
        _candidate(statement="The user's salary is in the employment file.")
    )
    assert result["applied"] is False
    assert result["decision"] == "require_approval"
    assert service.counts()["memories"] == 0


def test_results_never_echo_statement_text() -> None:
    service = _service()
    curator = build_automatic_memory_curator(service)
    result = curator.curate(_candidate(statement="Secret project QUANTUM-LOOM."))
    blob = str(result)
    assert "QUANTUM-LOOM" not in blob


def test_accepted_supersede_path_goes_through_curator() -> None:
    service = _service()
    service.create(
        MemoryDraft(
            kind="preference",
            content="The user's preferred name is Alex.",
            source_type="user",
            source_id="seed",
            confidence=0.5,
        )
    )
    curator = build_automatic_memory_curator(service)
    result = curator.curate(
        _candidate(
            statement="The user's preferred name is Atlas.",
            confidence=0.95,
            evidence=(_evidence(source_type="chatgpt", source_id="convo-9"),),
        )
    )
    assert result["applied"] is True
    assert result["status"] == "created"
    assert service.counts()["active"] == 1
    assert service.counts()["superseded"] == 1
    hits = service.search("preferred name")
    assert [h.memory.content for h in hits] == ["The user's preferred name is Atlas."]
