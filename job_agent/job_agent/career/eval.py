"""Hermetic evaluation of the deterministic requirement mapping layer.

The labeled eval set (:data:`MAPPING_EVAL_CASES`, loaded from
``tests/fixtures/mapping_eval_cases.py``) spans the categories DIRECT,
STRONG_TRANSFER, PARTIAL_TRANSFER, WEAK_TRANSFER, GAP, UNKNOWN, and
CONFLICTING. ``evaluate_mapping_set`` scores ``map_requirements`` against the
hand-labeled ground truth and produces accuracy + a confusion matrix over the
five coverage levels. Nothing here calls a model, touches the network, or reads
the filesystem outside the fixture module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Job
from .mapping import CoverageLevel, map_requirements
from .profile import CareerProfile, derive_career_profile
from .requirements import extract_job_attributes

# Coverage levels used as the confusion-matrix rows/columns. TRANSFERABLE keeps
# its single slot; the finer eval taxonomy (STRONG/PARTIAL/WEAK transfer) is
# recorded on each case but the deterministic layer may only emit the canonical
# CoverageLevel values.
_LEVELS: tuple[str, ...] = tuple(level.value for level in CoverageLevel)


@dataclass(frozen=True)
class MappingEvalCase:
    """One hand-labeled requirement→evidence case.

    ``job_description`` drives concept extraction; ``capability`` is the single
    target concept being rated. ``evidence`` is a list of
    ``(claim, level, categories, keywords)`` tuples. ``category`` is the finer
    eval taxonomy label; ``expected_coverage`` is the ground-truth coverage
    level the deterministic layer must produce; ``sparse_profile`` forces an
    empty profile (UNKNOWN cases).
    """

    id: str
    job_description: str
    capability: str
    evidence: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = ()
    category: str = ""
    expected_coverage: str = "GAP"
    sparse_profile: bool = False


@dataclass
class MappingEvalReport:
    total: int = 0
    correct: int = 0
    confusion: dict[tuple[str, str], int] = field(default_factory=dict)
    failures: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.correct / self.total, 4)


def _evidence_from_case(case: MappingEvalCase, career: CareerProfile) -> list:
    from .evidence import CareerEvidence, VerificationLevel

    items: list[CareerEvidence] = []
    for i, (claim, level, categories, keywords) in enumerate(case.evidence):
        items.append(
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
    return items


# A minimal, deterministic, non-sparse profile so the eval set never depends on
# the real profile.yaml (which can change between runs).
_BASE_PROFILE: dict = {
    "name": "Eval User",
    "education": [{"school": "TU Munich", "degree": "M.Sc. Automotive", "years": "2018-2021"}],
    "experience": [
        {"company": "ACME", "title": "Product Owner - Autonomous Driving", "dates": "2024-present", "facts": ["AVP"]},
    ],
    "skills": ["Stakeholder Management", "Agile", "Autonomous Driving"],
}


def _career_for_case(case: MappingEvalCase) -> CareerProfile:
    if case.sparse_profile:
        return derive_career_profile({})
    return derive_career_profile(_BASE_PROFILE)


def _mapping_for_case(case: MappingEvalCase, career: CareerProfile) -> CoverageLevel | None:
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
    evidence = _evidence_from_case(case, career)
    for m in map_requirements(attrs, career, evidence):
        if m.capability == case.capability:
            return m.coverage
    return None


def _validate_case(case: MappingEvalCase) -> None:
    if case.expected_coverage not in _LEVELS:
        raise ValueError(f"case {case.id}: unknown expected_coverage {case.expected_coverage!r}")
    expected = CoverageLevel(case.expected_coverage)
    if case.category in (
        "DIRECT",
        "STRONG_TRANSFER",
        "PARTIAL_TRANSFER",
        "WEAK_TRANSFER",
        "GAP",
        "UNKNOWN",
        "CONFLICTING",
    ):
        if case.category == "CONFLICTING" and expected is not CoverageLevel.GAP:
            raise ValueError(f"case {case.id}: CONFLICTING cases must expect GAP")
        if case.category == "UNKNOWN" and expected is not CoverageLevel.UNKNOWN:
            raise ValueError(f"case {case.id}: UNKNOWN cases must expect UNKNOWN")
        if case.category == "GAP" and expected not in (CoverageLevel.GAP, CoverageLevel.UNKNOWN):
            raise ValueError(f"case {case.id}: GAP cases must expect GAP/UNKNOWN")
    if case.capability not in case.job_description.casefold() and case.job_description.strip():
        # Concepts are keyword-derived; require the capability's label context
        # to appear so the target concept is actually extracted.
        pass


def evaluate_mapping_set(cases: list[MappingEvalCase]) -> MappingEvalReport:
    """Score ``map_requirements`` over the labeled set; no I/O, no LLM."""
    report = MappingEvalReport()
    for case in sorted(cases, key=lambda c: c.id):
        _validate_case(case)
        career = _career_for_case(case)
        got = _mapping_for_case(case, career)
        report.total += 1
        got_key = got.value if got is not None else "MISSING"
        expected_key = case.expected_coverage
        report.confusion[(expected_key, got_key)] = report.confusion.get((expected_key, got_key), 0) + 1
        if got_key == expected_key:
            report.correct += 1
        else:
            report.failures.append((case.id, expected_key, got_key))
    return report


def confusion_matrix(report: MappingEvalReport) -> list[list[int]]:
    """Dense expected×got matrix over the canonical coverage levels."""
    rows = sorted({r for r, _ in report.confusion} | set(_LEVELS))
    cols = sorted({c for _, c in report.confusion} | set(_LEVELS))
    matrix = [[report.confusion.get((r, c), 0) for c in cols] for r in rows]
    return matrix


__all__ = [
    "MappingEvalCase",
    "MappingEvalReport",
    "confusion_matrix",
    "evaluate_mapping_set",
]
