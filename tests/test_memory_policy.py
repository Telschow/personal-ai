"""Tests for the deterministic automatic memory policy.

The policy is a pure function of validated candidate metadata. These tests pin
the decision matrix: what auto-accepts, what defers, what escalates for human
approval, and what is always rejected. Secrets must never become durable
memory through any path.
"""

from __future__ import annotations

import pytest

from personal_ai.memory.models import (
    MemoryCandidate,
    MemoryEvidenceRef,
    MemoryValidationError,
)
from personal_ai.memory.policy import (
    MemoryDecision,
    MemoryPolicy,
    Sensitivity,
    SourceQuality,
)


def _evidence(source_type: str = "email", **overrides) -> MemoryEvidenceRef:
    base = {
        "source_type": source_type,
        "source_id": "doc-1",
        "source_document_id": None,
        "source_timestamp": "2026-01-05T10:00:00+00:00",
    }
    base.update(overrides)
    return MemoryEvidenceRef(**base)


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


def test_high_quality_asserted_candidate_is_accepted() -> None:
    decision = MemoryPolicy().evaluate(_candidate())
    assert decision.decision is MemoryDecision.ACCEPT
    assert decision.reason == "auto_accept_thresholds_met"
    assert decision.sensitivity is Sensitivity.ORDINARY


def test_conversation_and_direct_user_provenance_accept() -> None:
    for source_type in ("manual", "user", "chatgpt", "gemini", "conversation"):
        decision = MemoryPolicy().evaluate(
            _candidate(evidence=(_evidence(source_type=source_type),))
        )
        assert decision.decision is MemoryDecision.ACCEPT, source_type
        assert decision.source_quality in {
            SourceQuality.DIRECT_USER,
            SourceQuality.CONVERSATION,
        }


@pytest.mark.parametrize(
    "statement",
    [
        "BEGIN PRIVATE KEY----MIIEvg\n-----END PRIVATE KEY-----",
        "The token is sk_live_abcdefghijklmnopqrst",
        "key AKIAIOSFODNN7EXAMPLE is in the vault",
        "token ghp_0123456789abcdefghijklmnopqrstuvwxyz",
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjg",
    ],
)
def test_secret_content_is_always_rejected(statement: str) -> None:
    decision = MemoryPolicy().evaluate(_candidate(statement=statement))
    assert decision.decision is MemoryDecision.REJECT
    assert decision.reason == "secret_content"
    assert decision.sensitivity is Sensitivity.SECRET


def test_secret_conservative_keywords_reject() -> None:
    decision = MemoryPolicy().evaluate(
        _candidate(statement="Credentials are kept in a password manager.")
    )
    assert decision.decision is MemoryDecision.REJECT
    assert decision.reason == "secret_content"


@pytest.mark.parametrize(
    "statement",
    [
        "The user's salary is in the employment file.",
        "The user has a bank account ending in 1234.",
        "The user's phone number is not shared.",
    ],
)
def test_sensitive_content_requires_approval(statement: str) -> None:
    decision = MemoryPolicy().evaluate(_candidate(statement=statement))
    assert decision.decision is MemoryDecision.REQUIRE_APPROVAL
    assert decision.reason == "sensitive_content"
    assert decision.sensitivity is Sensitivity.SENSITIVE


@pytest.mark.parametrize(
    "statement",
    [
        "The user's SSN is 123-45-6789.",
        "The user recorded a medical condition last year.",
        "Card number 4111 1111 1111 1111 is on file.",
    ],
)
def test_highly_sensitive_content_requires_approval(statement: str) -> None:
    decision = MemoryPolicy().evaluate(_candidate(statement=statement))
    assert decision.decision is MemoryDecision.REQUIRE_APPROVAL
    assert decision.reason == "sensitive_content"
    assert decision.sensitivity is Sensitivity.HIGHLY_SENSITIVE


@pytest.mark.parametrize("status", ["hypothetical", "uncertain", "quoted"])
def test_non_asserted_statements_are_deferred(status: str) -> None:
    decision = MemoryPolicy().evaluate(
        _candidate(assertion_status=status, confidence=0.95)
    )
    assert decision.decision is MemoryDecision.DEFER
    assert decision.reason == "not_asserted"


def test_low_confidence_is_deferred() -> None:
    decision = MemoryPolicy().evaluate(_candidate(confidence=0.3))
    assert decision.decision is MemoryDecision.DEFER
    assert decision.reason == "low_confidence"


@pytest.mark.parametrize(
    "overrides",
    [
        {"durability": 0.4},
        {"relevance": 0.5},
    ],
)
def test_below_threshold_scores_defer(overrides: dict[str, object]) -> None:
    decision = MemoryPolicy().evaluate(_candidate(**overrides))
    assert decision.decision is MemoryDecision.DEFER
    assert decision.reason == "below_acceptance_threshold"


@pytest.mark.parametrize("source_type", ["event", "chrome_history", "youtube", ""])
def test_weak_or_missing_provenance_defers(source_type: str) -> None:
    evidence = () if not source_type else (_evidence(source_type=source_type),)
    decision = MemoryPolicy().evaluate(_candidate(evidence=evidence))
    assert decision.decision is MemoryDecision.DEFER
    assert decision.reason == "weak_source_quality"


def test_document_provenance_accepts() -> None:
    decision = MemoryPolicy().evaluate(
        _candidate(
            evidence=(
                _evidence(source_type="email"),
                _evidence(source_type="file", source_id="f"),
            )
        )
    )
    assert decision.decision is MemoryDecision.ACCEPT
    assert decision.source_quality is SourceQuality.PERSONAL_DOCUMENT


def test_summary_never_contains_statement() -> None:
    statement = "The user prefers local-first tools."
    decision = MemoryPolicy().evaluate(_candidate(statement=statement))
    summary = decision.summary()
    assert statement not in summary.values()
    assert summary == {
        "decision": "accept",
        "reason": "auto_accept_thresholds_met",
        "sensitivity": "ordinary",
        "source_quality": "personal_document",
    }


def test_policy_rejects_malformed_numeric_inputs() -> None:
    with pytest.raises(MemoryValidationError):
        _candidate(confidence="high")
