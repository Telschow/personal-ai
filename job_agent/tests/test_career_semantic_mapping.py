"""Semantic mapping: bounded LLM refinement over the deterministic floor.

Every refinement is mechanically guarded: positive re-classifications must
cite allow-listed evidence, negative evidence is never upgraded, malformed
output degrades to the deterministic floor, and the LLM proposes only.
"""

from __future__ import annotations

import json

from job_agent.career.evidence import CareerEvidence, VerificationLevel
from job_agent.career.mapping import CoverageLevel, RequirementMap
from job_agent.career.semantic_mapping import (
    MAX_EVIDENCE_PER_CONCEPT,
    MAX_REASONING_CHARS,
    SemanticMapping,
    SemanticMapResult,
    SemanticRequirement,
    parse_semantic_mapping,
    refine_mapping,
)


def _ev(eid: str, claim: str) -> CareerEvidence:
    return CareerEvidence(
        evidence_id=eid,
        claim=claim,
        level=VerificationLevel.DOCUMENTED,
        source="doc-a",
        source_type="cv_document",
        categories=["ai", "technical"],
        keywords=["autonomous driving"],
        confidence=0.8,
    )


def _det(requirement: str, capability: str, coverage: CoverageLevel = CoverageLevel.GAP) -> RequirementMap:
    return RequirementMap(
        requirement=requirement,
        capability=capability,
        coverage=coverage,
        confidence=0.3,
        evidence_ids=[],
        note="no evidence",
    )


class _FakeClient:
    """Deterministic fake: returns the provided mapping or raises."""

    def __init__(self, result, *, raise_exc: bool = False):
        self.result = result
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def classify_mapping(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc:
            raise RuntimeError("provider boom")
        return self.result


# ---------------------------------------------------------------------------
# parse_semantic_mapping
# ---------------------------------------------------------------------------


class TestParseSemanticMapping:
    def test_valid_batch(self) -> None:
        raw = json.dumps(
            {
                "version": "semantic-mapping-v1",
                "requirements": [
                    {
                        "requirement": "Agentic workflows",
                        "coverage": "STRONG",
                        "confidence": 0.8,
                        "evidence_ids": ["ev-a"],
                        "reasoning": "Experienced in agentic tooling.",
                    }
                ],
            }
        )
        m = parse_semantic_mapping(raw, {"ev-a"})
        assert m is not None
        assert m.requirements[0].coverage is CoverageLevel.STRONG
        assert m.requirements[0].evidence_ids == ["ev-a"]

    def test_positive_without_allowlisted_evidence_dropped(self) -> None:
        raw = json.dumps(
            {
                "requirements": [
                    {
                        "requirement": "Kubernetes platform",
                        "coverage": "STRONG",
                        "evidence_ids": ["ev-fake"],
                        "reasoning": "Seems relevant.",
                    }
                ]
            }
        )
        m = parse_semantic_mapping(raw, {"ev-real"})
        assert m is not None
        assert m.requirements == []

    def test_positive_with_no_ids_dropped(self) -> None:
        raw = json.dumps(
            {"requirements": [{"requirement": "Kubernetes", "coverage": "TRANSFERABLE", "reasoning": "Trust me."}]}
        )
        m = parse_semantic_mapping(raw, set())
        assert m is not None
        assert m.requirements == []

    def test_gap_without_ids_kept(self) -> None:
        raw = json.dumps(
            {"requirements": [{"requirement": "Unknown field", "coverage": "GAP", "reasoning": "Missing."}]}
        )
        m = parse_semantic_mapping(raw, set())
        assert m is not None
        assert len(m.requirements) == 1
        assert m.requirements[0].coverage is CoverageLevel.GAP

    def test_lowercase_coverage_coerced(self) -> None:
        raw = json.dumps(
            {
                "requirements": [
                    {
                        "requirement": "Agentic loop",
                        "coverage": "partial",
                        "evidence_ids": ["ev-a"],
                        "reasoning": "Some coverage.",
                    }
                ]
            }
        )
        m = parse_semantic_mapping(raw, {"ev-a"})
        assert m is not None
        assert m.requirements[0].coverage is CoverageLevel.PARTIAL

    def test_malformed_returns_none(self) -> None:
        assert parse_semantic_mapping("not json", set()) is None
        assert parse_semantic_mapping("[]", set()) is None
        assert parse_semantic_mapping('{"requirements": "nope"}', set()) is None

    def test_invalid_coverage_dropped(self) -> None:
        raw = json.dumps(
            {
                "requirements": [
                    {
                        "requirement": "Thing",
                        "coverage": "SUPER-STRONG",
                        "evidence_ids": ["ev-a"],
                        "reasoning": "..",
                    }
                ]
            }
        )
        m = parse_semantic_mapping(raw, {"ev-a"})
        assert m is not None
        assert m.requirements == []


# ---------------------------------------------------------------------------
# refine_mapping
# ---------------------------------------------------------------------------


class TestRefineMapping:
    def test_no_client_disables(self) -> None:
        mapping = [_det("Agentic workflows", "ai_tooling")]
        result = refine_mapping(mapping, [_ev("ev-a", "Built agentic tooling for BMW")], client=None)
        assert result.applied is False
        assert result.skipped == "disabled"
        assert result.mapping == mapping

    def test_empty_mapping_no_input(self) -> None:
        result = refine_mapping([], [], client=_FakeClient(None))
        assert result.applied is False
        assert result.skipped == "no_input"

    def test_provider_error_falls_back(self) -> None:
        mapping = [_det("Agentic workflows", "ai_tooling")]
        client = _FakeClient(None, raise_exc=True)
        result = refine_mapping(mapping, [_ev("ev-a", "Built agentic tooling for BMW")], client=client)
        assert result.applied is False
        assert result.skipped == "provider_error"
        assert result.mapping == mapping
        assert client.calls  # provider really was called

    def test_malformed_returns_none_falls_back(self) -> None:
        mapping = [_det("Agentic workflows", "ai_tooling")]
        result = refine_mapping(mapping, [_ev("ev-a", "Built agentic tooling for BMW")], client=_FakeClient(None))
        assert result.applied is False
        assert result.skipped == "malformed"

    def test_positive_upgrade_applies(self) -> None:
        evidence = [_ev("ev-a", "Built agentic tooling and LLM agents for BMW Group")]
        mapping = [_det("Agentic workflows", "ai_tooling")]
        proposal = SemanticMapping(
            requirements=[
                SemanticRequirement(
                    requirement="Agentic workflows",
                    coverage="STRONG",
                    confidence=0.85,
                    evidence_ids=["ev-a"],
                    reasoning="Candidate built agentic LLM tooling.",
                )
            ]
        )
        result = refine_mapping(mapping, evidence, client=_FakeClient(proposal))
        assert result.applied is True
        refined = result.mapping[0]
        assert refined.layer == "semantic"
        assert refined.coverage is CoverageLevel.STRONG
        assert refined.evidence_ids == ["ev-a"]
        assert "agentic" in refined.reasoning

    def test_negative_evidence_never_upgraded(self) -> None:
        evidence = [_ev("ev-a", "No functional safety experience")]
        mapping = [
            RequirementMap(
                requirement="Functional safety (ISO 26262)",
                capability="safety_critical",
                coverage=CoverageLevel.GAP,
                confidence=0.2,
                evidence_ids=["ev-a"],
                negative_evidence=True,
                note="explicit negative evidence",
            )
        ]
        proposal = SemanticMapping(
            requirements=[
                SemanticRequirement(
                    requirement="Functional safety (ISO 26262)",
                    coverage="PARTIAL",
                    confidence=0.7,
                    evidence_ids=["ev-a"],
                    reasoning="Has some exposure.",
                )
            ]
        )
        result = refine_mapping(mapping, evidence, client=_FakeClient(proposal))
        assert result.applied is False
        assert result.mapping == mapping  # untouched
        assert mapping[0].layer == "deterministic"

    def test_positive_proposal_without_ids_ignored(self) -> None:
        evidence = [_ev("ev-a", "Built agentic tooling for BMW")]
        mapping = [_det("Agentic workflows", "ai_tooling")]
        proposal = SemanticMapping(
            requirements=[
                SemanticRequirement(
                    requirement="Agentic workflows",
                    coverage="PARTIAL",
                    confidence=0.6,
                    evidence_ids=[],
                    reasoning="No anchor.",
                )
            ]
        )
        result = refine_mapping(mapping, evidence, client=_FakeClient(proposal))
        assert result.applied is False
        assert result.mapping == mapping

    def test_unknown_requirement_ignored(self) -> None:
        evidence = [_ev("ev-a", "Built agentic tooling for BMW")]
        mapping = [_det("Agentic workflows", "ai_tooling")]
        proposal = SemanticMapping(
            requirements=[
                SemanticRequirement(
                    requirement="Something entirely different",
                    coverage="STRONG",
                    confidence=0.9,
                    evidence_ids=["ev-a"],
                    reasoning="Irrelevant.",
                )
            ]
        )
        result = refine_mapping(mapping, evidence, client=_FakeClient(proposal))
        assert result.applied is False
        assert result.mapping == mapping

    def test_window_is_bounded(self) -> None:
        evidence = [
            _ev(f"ev-{i}", f"Past role built agentic tooling at company {i} with LLM agents") for i in range(20)
        ]
        mapping = [_det("Agentic workflows", "ai_tooling")]
        client = _FakeClient(None)
        refine_mapping(mapping, evidence, client=client)
        windows = client.calls[0]["evidence_by_capability"]
        assert len(windows["ai_tooling"]) <= MAX_EVIDENCE_PER_CONCEPT

    def test_proposal_calls_carry_allowlist(self) -> None:
        evidence = [_ev("ev-a", "Built agentic tooling for BMW")]
        mapping = [_det("Agentic workflows", "ai_tooling")]
        client = _FakeClient(None)
        refine_mapping(mapping, evidence, client=client)
        call = client.calls[0]
        assert call["allowlist"] == ["ev-a"]
        assert call["requirements"][0]["capability"] == "ai_tooling"
        assert call["requirements"][0]["negative_evidence"] is False

    def test_reasoning_truncated(self) -> None:
        evidence = [_ev("ev-a", "Built agentic tooling for BMW")]
        mapping = [_det("Agentic workflows", "ai_tooling")]
        long = "x" * (MAX_REASONING_CHARS + 50)
        proposal = SemanticMapping(
            requirements=[
                SemanticRequirement(
                    requirement="Agentic workflows",
                    coverage="STRONG",
                    confidence=0.8,
                    evidence_ids=["ev-a"],
                    reasoning=long,
                )
            ]
        )
        result = refine_mapping(mapping, evidence, client=_FakeClient(proposal))
        assert len(result.mapping[0].reasoning) <= MAX_REASONING_CHARS

    def test_semantic_result_contract(self) -> None:
        assert SemanticMapResult(mapping=[], applied=False).skipped == ""
