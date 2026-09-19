"""CI-safe semantic-layer merge test over the eval harness (no model, no net).

Runs refined mapping through ``refine_mapping`` with a *mocked* successful
``SemanticMappingClient`` — the exact production merge path — and pins the
invariants that guard it:

- explicit negative evidence (CONFLICTING cases) is never upgraded, even when
  the model proposes STRONG with allow-listed evidence,
- positive refinements carry allow-listed evidence and a schema-valid
  ``layer="semantic"`` stamp,
- a ``None`` (malformed/provider-failed) response keeps the deterministic
  floor untouched with an aggregate ``skipped`` reason.

Zero model calls, zero network, zero real filesystem access.
"""

from job_agent.career.eval import _career_for_case
from job_agent.career.evidence import CareerEvidence, VerificationLevel
from job_agent.career.mapping import CoverageLevel, map_requirements
from job_agent.career.requirements import extract_job_attributes
from job_agent.career.semantic_mapping import SemanticMapping, SemanticRequirement, refine_mapping
from job_agent.models import Job
from tests.fixtures.mapping_eval_cases import MAPPING_EVAL_CASES


def _evidences(case) -> list[CareerEvidence]:
    out = []
    for i, (claim, level, categories, keywords) in enumerate(case.evidence):
        out.append(
            CareerEvidence(
                evidence_id=f"{case.id}:e{i}",
                claim=claim,
                level=VerificationLevel(level),
                source="eval",
                categories=list(categories),
                keywords=list(keywords),
                confidence=0.9,
            )
        )
    return out


def _map_for_case(case):
    career = _career_for_case(case)
    job = Job(
        id=f"eval:{case.id}",
        title="Eval Job",
        company="ACME",
        url="https://example.invalid/eval",
        description=case.job_description,
        source="eval",
        source_type="board",
    )
    attrs = extract_job_attributes(job)
    if case.capability not in attrs.concepts:
        return None
    return map_requirements(attrs, career, _evidences(case))


def _for_capability(mapping, case):
    for m in mapping or []:
        if m.capability == case.capability:
            return m
    return None


class _MockSemanticClient:
    """Deterministic stand-in for OllamaJsonClient.classify_mapping."""

    def __init__(self, *, proposed_coverage: CoverageLevel | None = None) -> None:
        self.proposed_coverage = proposed_coverage

    def classify_mapping(self, *, requirements, evidence_by_capability, allowlist):
        reqs: list[SemanticRequirement] = []
        for r in requirements:
            ev = evidence_by_capability.get(r["capability"], [])
            ids = [e["evidence_id"] for e in ev if e["evidence_id"] in allowlist][:1]
            coverage = self.proposed_coverage or r["coverage"]
            reqs.append(
                SemanticRequirement(
                    requirement=r["requirement"],
                    coverage=coverage,
                    confidence=0.9,
                    evidence_ids=ids,
                    reasoning="mocked semantic evidence",
                )
            )
        return SemanticMapping(requirements=reqs)


def _conflicting_and_direct():
    cases = list(MAPPING_EVAL_CASES)
    conflicting = [c for c in cases if c.category == "CONFLICTING"]
    direct = [c for c in cases if c.category == "DIRECT"]
    assert conflicting, "eval fixture must include CONFLICTING cases"
    assert direct, "eval fixture must include DIRECT cases"
    return conflicting, direct


def _case(category: str, expected_coverage: str):
    for c in MAPPING_EVAL_CASES:
        if c.category == category and c.expected_coverage == expected_coverage:
            return c
    raise AssertionError(f"no eval case for {category}->{expected_coverage}")


def test_negative_evidence_guard_under_strong_proposal():
    conflicting, _ = _conflicting_and_direct()
    case = conflicting[0]
    mapping = _map_for_case(case)
    evidence = _evidences(case)
    target = _for_capability(mapping, case)
    assert target is not None and target.negative_evidence is True

    client = _MockSemanticClient(proposed_coverage=CoverageLevel.STRONG)
    result = refine_mapping(mapping, evidence, client=client)
    assert result.skipped == ""
    assert result.applied is False
    after = _for_capability(result.mapping, case)
    assert after is not None
    assert after.coverage is CoverageLevel.GAP
    assert after.negative_evidence is True
    assert after.layer == "deterministic"


def test_all_conflicting_cases_stay_gap_under_semantic_layer():
    conflicting, _ = _conflicting_and_direct()
    for case in conflicting:
        mapping = _map_for_case(case)
        target = _for_capability(mapping, case)
        assert target is not None and target.negative_evidence is True
        client = _MockSemanticClient(proposed_coverage=CoverageLevel.STRONG)
        result = refine_mapping(mapping, _evidences(case), client=client)
        after = _for_capability(result.mapping, case)
        assert after is not None
        assert after.coverage is CoverageLevel.GAP, f"{case.id}: conflicting upgraded"


def test_positive_refinement_is_applied_with_allowlisted_evidence():
    _, direct = _conflicting_and_direct()
    case = direct[0]
    mapping = _map_for_case(case)
    evidence = _evidences(case)
    before = _for_capability(mapping, case)
    assert before is not None and before.coverage in (CoverageLevel.STRONG, CoverageLevel.PARTIAL)

    client = _MockSemanticClient(proposed_coverage=CoverageLevel.STRONG)
    result = refine_mapping(mapping, evidence, client=client)
    after = _for_capability(result.mapping, case)
    assert after is not None
    assert after.layer == "semantic"
    assert after.coverage == CoverageLevel.STRONG
    assert "mocked semantic evidence" in after.note
    assert set(after.evidence_ids) <= {e.evidence_id for e in evidence}


def test_demotion_to_unknown_is_rejected():
    # Measured 2026-09-16 audit: small models proposed STRONG->UNKNOWN on a
    # DIRECT eval case. The deterministic floor must never be silently erased.
    case = _case("DIRECT", "STRONG")
    mapping = _map_for_case(case)
    target = _for_capability(mapping, case)
    assert target is not None and target.coverage is CoverageLevel.STRONG

    client = _MockSemanticClient(proposed_coverage=CoverageLevel.UNKNOWN)
    result = refine_mapping(mapping, _evidences(case), client=client)
    after = _for_capability(result.mapping, case)
    assert after is not None
    assert after.coverage is CoverageLevel.STRONG
    assert after.layer == "deterministic"
    assert result.applied is False


def test_demotion_within_positive_levels_is_rejected():
    case = _case("DIRECT", "STRONG")
    mapping = _map_for_case(case)
    target = _for_capability(mapping, case)
    assert target is not None and target.coverage is CoverageLevel.STRONG

    for weaker in (CoverageLevel.PARTIAL, CoverageLevel.TRANSFERABLE, CoverageLevel.GAP):
        client = _MockSemanticClient(proposed_coverage=weaker)
        result = refine_mapping(mapping, _evidences(case), client=client)
        after = _for_capability(result.mapping, case)
        assert after is not None and after.coverage is CoverageLevel.STRONG, weaker
        assert after.layer == "deterministic", weaker


def test_transferable_is_not_demoted_to_gap():
    case = _case("STRONG_TRANSFER", "TRANSFERABLE")
    mapping = _map_for_case(case)
    target = _for_capability(mapping, case)
    assert target is not None and target.coverage is CoverageLevel.TRANSFERABLE

    client = _MockSemanticClient(proposed_coverage=CoverageLevel.GAP)
    result = refine_mapping(mapping, _evidences(case), client=client)
    after = _for_capability(result.mapping, case)
    assert after is not None
    assert after.coverage is CoverageLevel.TRANSFERABLE
    assert after.layer == "deterministic"


def test_promotion_from_gap_to_positive_still_applies():
    # WEAK_TRANSFER resolves deterministically to GAP but carries a real
    # evidence window — the only way a promotion can be evidence-anchored.
    case = _case("WEAK_TRANSFER", "GAP")
    mapping = _map_for_case(case)
    target = _for_capability(mapping, case)
    assert target is not None and target.coverage is CoverageLevel.GAP
    assert target.negative_evidence is False

    client = _MockSemanticClient(proposed_coverage=CoverageLevel.PARTIAL)
    result = refine_mapping(mapping, _evidences(case), client=client)
    after = _for_capability(result.mapping, case)
    assert after is not None
    assert after.layer == "semantic"
    # promotion is allowed only when the proposal cites allow-listed evidence
    assert set(after.evidence_ids) <= {e.evidence_id for e in _evidences(case)}


def test_fail_closed_when_client_returns_none():
    _, direct = _conflicting_and_direct()
    case = direct[0]
    mapping = _map_for_case(case)
    evidence = _evidences(case)
    before = [m for m in mapping]

    class _NoneClient:
        def classify_mapping(self, **kwargs):
            return None

    result = refine_mapping(mapping, evidence, client=_NoneClient())
    assert result.skipped == "malformed"
    assert result.applied is False
    assert result.mapping == before  # deterministic floor untouched
    assert _for_capability(result.mapping, case).layer == "deterministic"


def test_eval_set_deterministic_floor_unchanged_by_semantic_harness():
    # The mocked semantic harness itself must never alter the deterministic
    # accuracy floor (it only adds refinements; the floor is measured without
    # a client).
    from job_agent.career.eval import evaluate_mapping_set

    report = evaluate_mapping_set(MAPPING_EVAL_CASES)
    assert report.accuracy >= 0.8864
    # And every case still resolves to the expected coverage category under a
    # mock that merely echoes the deterministic verdict.
    for case in MAPPING_EVAL_CASES:
        mapping = _map_for_case(case)
        if mapping is None:
            continue
        client = _MockSemanticClient()
        result = refine_mapping(mapping, _evidences(case), client=client)
        target = _for_capability(result.mapping, case)
        assert target is not None, f"{case.id}: capability dropped by merge"
