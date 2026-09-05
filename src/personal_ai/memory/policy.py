"""Deterministic automatic memory policy.

The policy decides whether a :class:`MemoryCandidate` may become durable
memory *automatically* (no human interaction), must be deferred, must be
rejected outright, or may proceed only with explicit human approval.

The policy is deliberately **not** an LLM judge. It is a pure, deterministic
function of validated candidate metadata:

* sensitivity classification (secret content is always rejected);
* assertion quality (only asserted statements can auto-accept);
* provenance quality (behavioral/inferred evidence is not strong enough);
* numeric thresholds on confidence, durability, and relevance.

Model-generated candidate metadata is treated as untrusted input: it is
validated when the candidate is constructed, and the policy never trusts the
model's own claim that a statement is safe to store. Policy decisions carry
stable machine-readable ``reason`` codes so callers can log/audit the outcome
without echoing candidate content.

The policy performs no writes. The caller is responsible for routing an
accepted candidate through the policy engine's ``propose_memory`` gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from personal_ai.memory.models import (
    AssertionStatus,
    MemoryCandidate,
    MemoryEvidenceRef,
)

# Numeric acceptance thresholds (deterministic, conservative).
ACCEPT_MIN_CONFIDENCE = 0.7
ACCEPT_MIN_DURABILITY = 0.6
ACCEPT_MIN_RELEVANCE = 0.6


class Sensitivity(Enum):
    """How sensitive the candidate statement is."""

    ORDINARY = "ordinary"
    SENSITIVE = "sensitive"
    HIGHLY_SENSITIVE = "highly_sensitive"
    SECRET = "secret"


class MemoryDecision(Enum):
    """The policy decision for a candidate."""

    ACCEPT = "accept"
    REJECT = "reject"
    DEFER = "defer"
    REQUIRE_APPROVAL = "require_approval"


class SourceQuality(Enum):
    """Strength of the provenance backing a candidate.

    Ordered strongest to weakest: a direct user statement, a transcript of
    the user (conversation export), a personal document, observed behavior,
    and finally inference with no direct supporting provenance.
    """

    DIRECT_USER = "direct_user"
    CONVERSATION = "conversation_statement"
    PERSONAL_DOCUMENT = "personal_document"
    BEHAVIORAL = "behavioral_evidence"
    INFERRED = "inferred_pattern"


@dataclass(frozen=True, slots=True)
class MemoryPolicyDecision:
    """Outcome of evaluating one candidate against the policy."""

    decision: MemoryDecision
    reason: str
    sensitivity: Sensitivity
    source_quality: SourceQuality
    confidence: float = 0.0

    @property
    def accepted(self) -> bool:
        return self.decision is MemoryDecision.ACCEPT

    def summary(self) -> dict[str, str]:
        """Identifiers only — never candidate content. Safe for audit logs."""
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "sensitivity": self.sensitivity.value,
            "source_quality": self.source_quality.value,
        }


# Secret detection. Matches actual credential-looking content — not prose that
# merely mentions the words — so the checks are keyword- and regex-driven and
# never depend on model judgment.
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN[^-]*PRIVATE KEY-----"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b"),
)
_SECRET_KEYWORDS = frozenset(
    {
        "password",
        "passphrase",
        "credentials",
        "api key",
        "apikey",
        "access token",
        "secret key",
        "private key",
        "session token",
        "security answer",
        "seed phrase",
    }
)

# Highly sensitive content that at minimum must never auto-accept.
_HIGHLY_SENSITIVE_PATTERNS = (
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # US SSN
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),  # IBAN
    re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"),  # card number
    re.compile(
        r"\b\d{5,}(?:[- ][0-9]\d{3})?\b(?=[\s,]*(?:ssn|passport))", re.IGNORECASE
    ),
)
_HIGHLY_SENSITIVE_KEYWORDS = frozenset(
    {
        "social security",
        "passport number",
        "national insurance",
        "medical condition",
        "diagnosis",
        "medical treatment",
        "medication",
        "health record",
    }
)

# Sensitive content that requires human approval before becoming durable.
_SENSITIVE_KEYWORDS = frozenset(
    {
        "salary",
        "bank account",
        "account number",
        "routing number",
        "credit card",
        "net worth",
        "tax record",
        "home address",
        "phone number",
        "date of birth",
        "financial situation",
    }
)


class MemoryPolicy:
    """Pure, deterministic policy over validated memory candidates."""

    def evaluate(self, candidate: MemoryCandidate) -> MemoryPolicyDecision:
        """Classify a candidate; performs no writes and never trusts the model."""
        sensitivity = classify_sensitivity(candidate)
        source_quality = classify_source_quality(candidate)

        if sensitivity is Sensitivity.SECRET:
            return MemoryPolicyDecision(
                MemoryDecision.REJECT,
                "secret_content",
                sensitivity,
                source_quality,
                candidate.confidence,
            )
        if candidate.assertion_status is not AssertionStatus.ASSERTED:
            return MemoryPolicyDecision(
                MemoryDecision.DEFER,
                "not_asserted",
                sensitivity,
                source_quality,
                candidate.confidence,
            )
        if sensitivity in (Sensitivity.SENSITIVE, Sensitivity.HIGHLY_SENSITIVE):
            return MemoryPolicyDecision(
                MemoryDecision.REQUIRE_APPROVAL,
                "sensitive_content",
                sensitivity,
                source_quality,
                candidate.confidence,
            )
        if source_quality in (SourceQuality.BEHAVIORAL, SourceQuality.INFERRED):
            return MemoryPolicyDecision(
                MemoryDecision.DEFER,
                "weak_source_quality",
                sensitivity,
                source_quality,
                candidate.confidence,
            )
        if candidate.confidence < ACCEPT_MIN_CONFIDENCE:
            return MemoryPolicyDecision(
                MemoryDecision.DEFER,
                "low_confidence",
                sensitivity,
                source_quality,
                candidate.confidence,
            )
        if (
            candidate.durability < ACCEPT_MIN_DURABILITY
            or candidate.relevance < ACCEPT_MIN_RELEVANCE
        ):
            return MemoryPolicyDecision(
                MemoryDecision.DEFER,
                "below_acceptance_threshold",
                sensitivity,
                source_quality,
                candidate.confidence,
            )
        return MemoryPolicyDecision(
            MemoryDecision.ACCEPT,
            "auto_accept_thresholds_met",
            sensitivity,
            source_quality,
            candidate.confidence,
        )


def classify_sensitivity(candidate: MemoryCandidate) -> Sensitivity:
    """Deterministic, conservative sensitivity classification.

    ``secret`` content (actual credentials) is hard-rejected; nothing about
    the statement or its provenance can lower a classification, so the check
    is safe under untrusted candidate metadata.
    """
    text = candidate.statement.lower()
    if _matches(candidate.statement, _SECRET_PATTERNS, _SECRET_KEYWORDS, text):
        return Sensitivity.SECRET
    if _matches(
        candidate.statement,
        _HIGHLY_SENSITIVE_PATTERNS,
        _HIGHLY_SENSITIVE_KEYWORDS,
        text,
    ):
        return Sensitivity.HIGHLY_SENSITIVE
    if _matches(candidate.statement, (), _SENSITIVE_KEYWORDS, text):
        return Sensitivity.SENSITIVE
    return Sensitivity.ORDINARY


def classify_source_quality(candidate: MemoryCandidate) -> SourceQuality:
    """Best available provenance quality across the candidate's evidence.

    The strongest single evidence reference determines the quality; a
    candidate with no evidence at all is classified as inferred and can never
    auto-accept (provenance is mandatory for durable memory).
    """
    best = SourceQuality.INFERRED
    rank = {
        SourceQuality.DIRECT_USER: 4,
        SourceQuality.CONVERSATION: 3,
        SourceQuality.PERSONAL_DOCUMENT: 2,
        SourceQuality.BEHAVIORAL: 1,
        SourceQuality.INFERRED: 0,
    }
    for ref in candidate.evidence:
        quality = _evidence_source_quality(ref)
        if rank[quality] > rank[best]:
            best = quality
    return best


def _evidence_source_quality(ref: MemoryEvidenceRef) -> SourceQuality:
    source = (ref.source_type or "").strip().lower()
    if source in {"user", "manual", "manual_memory", "cli"}:
        return SourceQuality.DIRECT_USER
    if source in {"chatgpt", "gemini", "conversation", "chat", "transcript"}:
        return SourceQuality.CONVERSATION
    if source in {
        "email",
        "document",
        "pdf",
        "file",
        "financial",
        "google_keep",
        "notebooklm",
        "md",
        "txt",
        "excel",
        "xlsx",
        "markdown",
    }:
        return SourceQuality.PERSONAL_DOCUMENT
    if source in {
        "event",
        "chrome_history",
        "youtube",
        "activity",
        "workout",
        "behavior",
    }:
        return SourceQuality.BEHAVIORAL
    return SourceQuality.INFERRED


def _matches(
    raw: str,
    patterns: tuple[re.Pattern[str], ...],
    keywords: frozenset[str],
    lower: str,
) -> bool:
    for pattern in patterns:
        if pattern.search(raw):
            return True
    return any(keyword in lower for keyword in keywords)
