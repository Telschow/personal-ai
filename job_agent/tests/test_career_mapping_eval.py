"""Hermetic evaluation of the deterministic requirement-mapping layer.

Runs the labeled eval set (tests/fixtures/mapping_eval_cases.py) against
``map_requirements`` with zero model calls, zero network, and zero filesystem
access outside the fixture — the before/after measurement for Slice 3.6's
gap-detection accuracy fix.

The suite pins the post-fix accuracy (must be 0.8864 or better) and the specific
backend/data-engineering regression the fix was written for, so an accidental
regression back to TRANSFERABLE-without-hint fails loudly.
"""

from job_agent.career.eval import (
    _career_for_case,
    confusion_matrix,
    evaluate_mapping_set,
)
from job_agent.career.evidence import CareerEvidence, VerificationLevel
from job_agent.career.mapping import CoverageLevel, map_requirements
from job_agent.career.requirements import extract_job_attributes
from job_agent.models import Job
from tests.fixtures.mapping_eval_cases import MAPPING_EVAL_CASES

# The deterministic layer must stay accurate on the labeled set. The pre-fix
# measurement (adjacency on category labels alone, uncapped confidence) scored
# 0.8864 here; the floor is that number so a regression back to the inflated
# TRANSFERABLE semantics fails loudly.
MIN_EVAL_ACCURACY = 0.8864

# Reuse the eval module's deterministic base profile so this regression test is
# independent of the real profile.yaml.
_EMPLOYED_CAREER = _career_for_case(MAPPING_EVAL_CASES[0])


def _build_evidence(items):
    result = []
    for i, (claim, level, categories, keywords) in enumerate(items):
        result.append(
            CareerEvidence(
                evidence_id=f"reg:e{i}",
                claim=claim,
                level=VerificationLevel(level),
                source="eval",
                categories=list(categories),
                keywords=list(keywords),
                confidence=0.9,
            )
        )
    return result


def test_eval_set_is_44_cases_across_all_categories():
    assert len(MAPPING_EVAL_CASES) >= 40
    categories = {c.category for c in MAPPING_EVAL_CASES}
    for expected in ("DIRECT", "GAP", "UNKNOWN", "CONFLICTING"):
        assert expected in categories
    assert all(c.id and c.capability for c in MAPPING_EVAL_CASES)
    assert len({c.id for c in MAPPING_EVAL_CASES}) == len(MAPPING_EVAL_CASES)


def test_eval_accuracy_meets_floor():
    report = evaluate_mapping_set(MAPPING_EVAL_CASES)
    assert report.total == len(MAPPING_EVAL_CASES) >= 40
    assert report.accuracy >= MIN_EVAL_ACCURACY, (
        f"deterministic mapping accuracy {report.accuracy} < floor {MIN_EVAL_ACCURACY}; failures={report.failures}"
    )


def test_confusion_matrix_is_bounded_and_square():
    report = evaluate_mapping_set(MAPPING_EVAL_CASES)
    matrix = confusion_matrix(report)
    assert len(matrix) >= 3
    assert all(len(row) == len(matrix) for row in matrix)
    assert sum(sum(row) for row in matrix) == report.total


def test_all_failures_reference_known_cases():
    report = evaluate_mapping_set(MAPPING_EVAL_CASES)
    known_ids = {c.id for c in MAPPING_EVAL_CASES}
    for cid, _expected, _got in report.failures:
        assert cid in known_ids, f"failure references unknown case {cid}"


def _poor_fixture_mappings(evidence):
    """Run the exact Slice 3.5 poor fixture job through map_requirements."""
    job = Job(
        id="eval:poor",
        title="Junior Backend Engineer",
        company="ACME",
        url="https://example.invalid/eval/poor",
        description=(
            "Junior Backend Engineer: build REST APIs and microservices, "
            "data engineering with ETL pipelines, 0-2 years experience"
        ),
        source="eval",
        source_type="board",
    )
    attrs = extract_job_attributes(job)
    return {m.capability: m for m in map_requirements(attrs, _EMPLOYED_CAREER, evidence)}


def test_backend_and_data_without_hint_stay_gap():
    # The Slice 3.6 regression: generic Development Engineer claims whose
    # categories are merely "technical"/"engineering" are NOT backend or data
    # engineering evidence. Pre-fix they inflated backend_engineering to
    # TRANSFERABLE conf=1.0 and data_engineering into gaps+transferable in
    # fit.analyze_fit (the contradiction). Post-fix they must stay GAP.
    evidence = _build_evidence(
        (
            ("Worked as Development Engineer - Product Design", "verified", ("engineering", "technical"), ()),
            ("Specialist Engineer - Autonomous Driving experience", "documented", ("technical", "ai"), ()),
        )
    )
    maps = _poor_fixture_mappings(evidence)
    for concept in ("backend_engineering", "data_engineering"):
        m = maps.get(concept)
        assert m is not None, f"expected a mapping for {concept}"
        assert m.coverage is CoverageLevel.GAP, f"{concept} without a concept hint must be GAP, got {m.coverage}"
        assert m.negative_evidence is False
        assert m.confidence <= 0.3, f"{concept} GAP confidence too high: {m.confidence}"


def test_backend_with_direct_hint_is_not_gap():
    evidence = _build_evidence(
        (("Built REST APIs and deployed them to Kubernetes", "documented", ("technical",), ("rest api", "kubernetes")),)
    )
    maps = _poor_fixture_mappings(evidence)
    m = maps["backend_engineering"]
    assert m.coverage in (CoverageLevel.STRONG, CoverageLevel.PARTIAL)
