"""Entity-level claim validation tests.

Verifies that each entity type (employer, job_title, dates, team_size,
scope, technology, credential, award/publication) is deterministically
extracted and checked against evidence text.  Unverifiable entities
(award/publication signals) surface as UNKNOWN, never silently SUPPORTED.
"""

from __future__ import annotations

import pytest

from job_agent.career.evidence import CareerEvidence, VerificationLevel
from job_agent.career.validation import extract_entities, validate_claim

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ev(cid: str, claim: str, level: VerificationLevel = VerificationLevel.DOCUMENTED) -> CareerEvidence:
    return CareerEvidence(evidence_id=cid, claim=claim, level=level, source="doc-a")


# ---------------------------------------------------------------------------
# Employer entity
# ---------------------------------------------------------------------------


class TestEmployerEntity:
    def test_employer_in_evidence(self) -> None:
        claim = "Product Owner for Autonomous Driving at BMW Group"
        evidence = [_ev("e1", "Worked as Product Owner at BMW Group (2024-present)")]
        v = validate_claim(claim, evidence)
        assert v.problem is None
        assert v.matched_evidence_ids == ["e1"]

    def test_employer_not_in_evidence(self) -> None:
        # "at" + capitalized = employer; "tesla" not in evidence → entity_unmatched
        claim = "Led AI strategy implementation at Tesla"
        evidence = [_ev("e1", "Led AI strategy implementation at BMW Group")]
        v = validate_claim(claim, evidence)
        assert v.problem == "entity_unmatched"
        assert any(e.entity == "employer" and e.value == "Tesla" for e in v.unmatched_entities)

    def test_employer_single_capitalized_word(self) -> None:
        claim = "Deployed infrastructure at TUM"
        evidence = [_ev("e1", "Deployed infrastructure at TUM (2015-2018)")]
        v = validate_claim(claim, evidence)
        assert v.problem is None

    def test_at_without_capital_not_employer(self) -> None:
        claim = "Worked at a startup"
        evidence = [_ev("e1", "Worked at a startup")]
        v = validate_claim(claim, evidence)
        assert not any(e.entity == "employer" for e in v.unmatched_entities)


# ---------------------------------------------------------------------------
# Job title entity (as <Role> marker only)
# ---------------------------------------------------------------------------


class TestJobTitleEntity:
    def test_title_as_marker(self) -> None:
        claim = "Worked as Product Owner at BMW Group"
        evidence = [_ev("e1", "Worked as Product Owner at BMW Group (2024-present)")]
        v = validate_claim(claim, evidence)
        assert v.problem is None

    def test_title_fabricated(self) -> None:
        # "Worked as <Capitalized Role> on ..." — title is "Chief AI Officer"
        claim = "Worked as Chief AI Officer on the BMW Group account"
        evidence = [_ev("e1", "Worked as Product Owner on the BMW Group account")]
        v = validate_claim(claim, evidence)
        assert v.problem == "entity_unmatched"
        assert any(e.entity == "job_title" and e.value == "Chief AI Officer" for e in v.unmatched_entities)

    def test_no_title_without_as_marker(self) -> None:
        claim = "Product Owner for Autonomous Driving at BMW Group"
        evidence = [_ev("e1", "Worked as Product Owner at BMW Group (2024-present)")]
        v = validate_claim(claim, evidence)
        assert v.problem is None
        assert not any(e.entity == "job_title" for e in v.unmatched_entities)


# ---------------------------------------------------------------------------
# Dates entity (numeric check fires first — these verify numeric_unmatched)
# ---------------------------------------------------------------------------


class TestDatesEntity:
    def test_date_range_in_evidence(self) -> None:
        claim = "Managed team (2020-2023)"
        evidence = [_ev("e1", "Managed team of 5 engineers (2020-2023)")]
        v = validate_claim(claim, evidence)
        assert v.problem is None

    def test_date_range_not_in_evidence_triggers_numeric(self) -> None:
        # Numbers 2020, 2023 not in evidence → numeric_unmatched (correct precedence)
        claim = "Managed team (2020-2023)"
        evidence = [_ev("e1", "Managed team of 5 engineers (2015-2018)")]
        v = validate_claim(claim, evidence)
        assert v.problem == "numeric_unmatched"

    def test_single_year_in_evidence(self) -> None:
        claim = "Joined in 2024"
        evidence = [_ev("e1", "Joined in 2024")]
        v = validate_claim(claim, evidence)
        assert v.problem is None

    def test_single_year_not_in_evidence_triggers_numeric(self) -> None:
        claim = "Joined in 2024"
        evidence = [_ev("e1", "Joined in 2023")]
        v = validate_claim(claim, evidence)
        assert v.problem == "numeric_unmatched"


# ---------------------------------------------------------------------------
# Team size entity
# ---------------------------------------------------------------------------


class TestTeamSizeEntity:
    def test_team_size_in_evidence(self) -> None:
        claim = "Led a team of 5 engineers"
        evidence = [_ev("e1", "Led a team of 5 engineers at BMW Group")]
        v = validate_claim(claim, evidence)
        assert v.problem is None

    def test_team_size_not_in_evidence(self) -> None:
        # Numbers 10 not in evidence → numeric_unmatched (correct: team size differs)
        claim = "Led a team of 10 engineers"
        evidence = [_ev("e1", "Led a team of 5 engineers at BMW Group")]
        v = validate_claim(claim, evidence)
        assert v.problem == "numeric_unmatched"

    def test_team_of_pattern(self) -> None:
        claim = "Managed group of 3 people"
        evidence = [_ev("e1", "Managed group of 3 people")]
        v = validate_claim(claim, evidence)
        assert v.problem is None


# ---------------------------------------------------------------------------
# Scope entity
# ---------------------------------------------------------------------------


class TestScopeEntity:
    def test_scope_in_evidence(self) -> None:
        claim = "Delivered global rollout across regions"
        evidence = [_ev("e1", "Delivered global rollout across 5 regions")]
        v = validate_claim(claim, evidence)
        assert v.problem is None

    def test_scope_not_in_evidence(self) -> None:
        claim = "Delivered global rollout across regions"
        evidence = [_ev("e1", "Delivered local rollout across regions")]
        v = validate_claim(claim, evidence)
        assert v.problem == "entity_unmatched"
        assert any(e.entity == "scope" and e.value == "global" for e in v.unmatched_entities)


# ---------------------------------------------------------------------------
# Technology entity
# ---------------------------------------------------------------------------


class TestTechnologyEntity:
    def test_technology_in_evidence(self) -> None:
        claim = "Built Python ETL pipelines at BMW"
        evidence = [_ev("e1", "Built Python ETL pipelines at BMW Group")]
        v = validate_claim(claim, evidence)
        assert v.problem is None

    def test_technology_not_in_evidence(self) -> None:
        claim = "Deployed Kubernetes clusters across data"
        evidence = [_ev("e1", "Deployed Docker containers across data")]
        v = validate_claim(claim, evidence)
        assert v.problem == "entity_unmatched"
        assert any(e.entity == "technology" and e.value == "kubernetes" for e in v.unmatched_entities)

    def test_technology_with_slash(self) -> None:
        claim = "Implemented CI/CD pipelines using Jenkins"
        evidence = [_ev("e1", "Implemented CI/CD pipelines using Jenkins")]
        v = validate_claim(claim, evidence)
        assert v.problem is None


# ---------------------------------------------------------------------------
# Credential entity
# ---------------------------------------------------------------------------


class TestCredentialEntity:
    def test_credential_in_evidence(self) -> None:
        claim = "MSc Data Science from TU Munich"
        evidence = [_ev("e1", "MSc Data Science from TU Munich (2015)")]
        v = validate_claim(claim, evidence)
        assert v.problem is None

    def test_credential_not_in_evidence(self) -> None:
        # Shared tokens: certified/scrum/master → passes token overlap; PMP misses
        claim = "PMP certified scrum master"
        evidence = [_ev("e1", "Certified Scrum Master with PSM")]
        v = validate_claim(claim, evidence)
        assert v.problem == "entity_unmatched"
        assert any(e.entity == "credential" and "PMP" in e.value for e in v.unmatched_entities)


# ---------------------------------------------------------------------------
# Unverified signals (award / publication)
# ---------------------------------------------------------------------------


class TestUnverifiedSignals:
    def test_award_signal_is_unknown(self) -> None:
        claim = "Won the ACM Best Paper Award at CHI"
        evidence = [_ev("e1", "Won the ACM Best Paper Award at CHI 2023")]
        v = validate_claim(claim, evidence)
        assert v.problem == "entity_unknown"
        assert any(e.entity == "award" and e.status == "unknown" for e in v.unknown_entities)

    def test_publication_signal_is_unknown(self) -> None:
        claim = "Published peer-reviewed paper in NeurIPS"
        evidence = [_ev("e1", "Published peer-reviewed paper in NeurIPS 2022")]
        v = validate_claim(claim, evidence)
        assert v.problem == "entity_unknown"
        assert any(e.entity == "publication" and e.status == "unknown" for e in v.unknown_entities)

    def test_no_unverified_signal_when_clean(self) -> None:
        claim = "Built Python ETL pipelines at BMW Group"
        evidence = [_ev("e1", "Built Python ETL pipelines at BMW Group")]
        v = validate_claim(claim, evidence)
        assert v.problem is None
        assert v.unknown_entities == []


# ---------------------------------------------------------------------------
# Entity extraction (deterministic unit tests)
# ---------------------------------------------------------------------------


class TestExtractEntities:
    def test_employer_extraction(self) -> None:
        verifiable, _ = extract_entities("Led AI strategy at BMW Group")
        assert any(e.entity == "employer" and e.value == "BMW Group" for e in verifiable)

    def test_title_extraction(self) -> None:
        verifiable, _ = extract_entities("Worked as Product Owner at BMW Group")
        assert any(e.entity == "job_title" and e.value == "Product Owner" for e in verifiable)

    def test_date_range_extraction(self) -> None:
        verifiable, _ = extract_entities("Managed team (2020-2023)")
        assert any(e.entity == "dates" and e.value == "2020-2023" for e in verifiable)

    def test_single_date_extraction(self) -> None:
        verifiable, _ = extract_entities("Joined in 2024")
        assert any(e.entity == "dates" and e.value == "2024" for e in verifiable)

    def test_team_size_extraction(self) -> None:
        verifiable, _ = extract_entities("Led a team of 10 engineers")
        assert any(e.entity == "team_size" and "10" in e.value for e in verifiable)

    def test_scope_extraction(self) -> None:
        verifiable, _ = extract_entities("Delivered global rollout")
        assert any(e.entity == "scope" and e.value == "global" for e in verifiable)

    def test_technology_extraction(self) -> None:
        verifiable, _ = extract_entities("Built Python ETL pipelines")
        assert any(e.entity == "technology" and e.value == "python" for e in verifiable)

    def test_credential_extraction(self) -> None:
        verifiable, _ = extract_entities("MSc Data Science from TU Munich")
        assert any(e.entity == "credential" and "MSc" in e.value for e in verifiable)

    def test_award_signal_extraction(self) -> None:
        _, unverified = extract_entities("Won the ACM Best Paper Award")
        assert any(e.entity == "award" and e.status == "unknown" for e in unverified)

    def test_publication_signal_extraction(self) -> None:
        _, unverified = extract_entities("Published peer-reviewed paper in NeurIPS")
        assert any(e.entity == "publication" and e.status == "unknown" for e in unverified)

    def test_no_entities(self) -> None:
        verifiable, unverified = extract_entities("Led design of an Automated Valet Parking feature")
        assert verifiable == []
        assert unverified == []


# ---------------------------------------------------------------------------
# Problem precedence
# ---------------------------------------------------------------------------


class TestProblemPrecedence:
    def test_numeric_beats_entity(self) -> None:
        """Numbers are checked first; if unmatched, numeric_unmatched wins."""
        claim = "Managed a team of 999 engineers"
        evidence = [_ev("e1", "Managed a team of 5 engineers")]
        v = validate_claim(claim, evidence)
        assert v.problem == "numeric_unmatched"

    def test_no_evidence_beats_entity(self) -> None:
        """If no evidence matches at all, no_evidence wins over entities."""
        claim = "Deployed Kafka clusters at Netflix"
        evidence = [_ev("e1", "Built Docker containers at AWS")]
        v = validate_claim(claim, evidence)
        assert v.problem == "no_evidence"

    def test_entity_unmatched_when_evidence_matches_but_entity_fabricated(self) -> None:
        """Evidence supports some claims but a specific entity is fabricated."""
        claim = "Led AI strategy implementation at Tesla"
        evidence = [_ev("e1", "Led AI strategy implementation at BMW Group")]
        v = validate_claim(claim, evidence)
        assert v.problem == "entity_unmatched"


# ---------------------------------------------------------------------------
# Backward compatibility (existing claim patterns must remain problem=None)
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    EXISTING_CLAIMS_AND_EVIDENCE = [
        (
            "Product Owner for Autonomous Driving at BMW Group",
            ["Worked as Product Owner - Autonomous Driving at BMW Group (2024-present)"],
        ),
        (
            "Led design of an Automated Valet Parking feature",
            ["Led the design of an Automated Valet Parking feature"],
        ),
        (
            "Reduced reporting time by 30 percent",
            ["Reduced reporting time by 30 percent through automated data pipelines"],
        ),
    ]

    @pytest.mark.parametrize("claim,evidence_claims", EXISTING_CLAIMS_AND_EVIDENCE)
    def test_existing_claims_stay_supported(self, claim: str, evidence_claims: list[str]) -> None:
        evidence = [_ev(f"e{i}", c) for i, c in enumerate(evidence_claims)]
        v = validate_claim(claim, evidence)
        assert v.problem is None, f"Claim {claim!r} should be supported, got problem={v.problem}"
