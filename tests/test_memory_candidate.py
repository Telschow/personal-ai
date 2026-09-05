"""Tests for the bounded memory candidate model.

Candidates are model-generated metadata and therefore untrusted input: the
tests pin the validation surface (statement bounds, numeric bounds, evidence
bounds, enum rejection) and the canonical dict round-trip used across the
``propose_memory`` tool boundary.
"""

from __future__ import annotations

import math

import pytest

from personal_ai.memory.models import (
    AssertionStatus,
    MemoryCandidate,
    MemoryEvidenceRef,
    MemoryKind,
    MemoryValidationError,
    TemporalScope,
)


def _evidence(**overrides) -> MemoryEvidenceRef:
    base = {
        "source_type": "email",
        "source_id": "doc-123",
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


def test_statement_is_stripped_and_min_nonempty() -> None:
    candidate = _candidate(statement="  hello world  ")
    assert candidate.statement == "hello world"
    with pytest.raises(MemoryValidationError, match="empty"):
        _candidate(statement="   ")
    with pytest.raises(MemoryValidationError, match="empty"):
        _candidate(statement="")


def test_statement_length_is_bounded() -> None:
    long_statement = "x" * 513
    with pytest.raises(MemoryValidationError, match="512"):
        _candidate(statement=long_statement)
    assert _candidate(statement="x" * 512).statement == "x" * 512


@pytest.mark.parametrize(
    "field", ["confidence", "durability", "relevance", "specificity", "utility"]
)
def test_numeric_fields_reject_out_of_bounds(field: str) -> None:
    for bad in (-0.1, 1.1, math.nan, math.inf, -math.inf):
        with pytest.raises(MemoryValidationError, match=field):
            _candidate(**{field: bad})
    with pytest.raises(MemoryValidationError, match=field):
        _candidate(**{field: True})


@pytest.mark.parametrize("recurrence", [0, -3, 2.5, True, "two"])
def test_recurrence_must_be_positive_int(recurrence: object) -> None:
    with pytest.raises(MemoryValidationError, match="recurrence"):
        _candidate(recurrence=recurrence)


def test_evidence_count_is_bounded() -> None:
    refs = tuple(_evidence(source_id=f"doc-{i}") for i in range(6))
    with pytest.raises(MemoryValidationError, match="5"):
        _candidate(evidence=refs)


def test_duplicate_evidence_in_candidate_is_rejected() -> None:
    ref = _evidence()
    with pytest.raises(MemoryValidationError, match="duplicate"):
        _candidate(evidence=(ref, ref))


def test_evidence_requires_source_type_and_id() -> None:
    with pytest.raises(MemoryValidationError, match="source_type"):
        _candidate(evidence=(_evidence(source_type=" "),))
    with pytest.raises(MemoryValidationError, match="source_id"):
        _candidate(evidence=(_evidence(source_id=""),))


def test_evidence_timestamp_is_validated() -> None:
    with pytest.raises(MemoryValidationError, match="timestamp"):
        _candidate(evidence=(_evidence(source_timestamp="yesterday"),))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("kind", "not-a-kind"),
        ("temporal_scope", "imaginary-time"),
        ("assertion_status", "vague"),
    ],
)
def test_unknown_enum_values_are_rejected(field: str, bad: object) -> None:
    with pytest.raises(MemoryValidationError, match="invalid"):
        _candidate(**{field: bad})


def test_enum_fields_are_normalized_to_members() -> None:
    candidate = _candidate(
        kind="goal",
        temporal_scope="recurring",
        assertion_status="uncertain",
    )
    assert candidate.kind is MemoryKind.GOAL
    assert candidate.temporal_scope is TemporalScope.RECURRING
    assert candidate.assertion_status is AssertionStatus.UNCERTAIN


def test_to_dict_roundtrip_via_from_dict() -> None:
    candidate = _candidate(
        kind="identity",
        temporal_scope="historical",
        evidence=(
            _evidence(),
            _evidence(source_type="chatgpt", source_id="convo-7"),
        ),
    )
    rebuilt = MemoryCandidate.from_dict(candidate.to_dict())
    assert rebuilt == candidate
    assert rebuilt.evidence == candidate.evidence
