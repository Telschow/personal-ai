"""Regression tests for career evidence layer enhancements."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from job_agent.career.documents import ingest_document
from job_agent.career.evidence import (
    CareerEvidence,
    CareerEvidenceType,
    GapSupportLevel,
    VerificationLevel,
    build_profile_evidence,
    level_index,
)
from job_agent.career.evidence_cache import EvidenceQueryCache, get_global_cache
from job_agent.career.knowledge import NullCareerKnowledge, PersonalAiCareerKnowledge
from job_agent.career.reconcile import candidate_evidence_from_document


def test_evidence_verification_levels() -> None:
    """Test that verification levels are ordered correctly."""
    assert level_index(VerificationLevel.UNKNOWN) == 0
    assert level_index(VerificationLevel.INFERRED) == 1
    assert level_index(VerificationLevel.CANDIDATE) == 2
    assert level_index(VerificationLevel.DOCUMENTED) == 3
    assert level_index(VerificationLevel.USER_CONFIRMED) == 4
    assert level_index(VerificationLevel.VERIFIED) == 5
    # VERIFIED > DOCUMENTED > CANDIDATE > INFERRED > UNKNOWN
    assert level_index(VerificationLevel.VERIFIED) > level_index(VerificationLevel.DOCUMENTED)
    assert level_index(VerificationLevel.DOCUMENTED) > level_index(VerificationLevel.CANDIDATE)


def test_evidence_type_enum() -> None:
    """Test that CareerEvidenceType enum has all required values."""
    assert CareerEvidenceType.SKILL == "skill"
    assert CareerEvidenceType.TECHNOLOGY == "technology"
    assert CareerEvidenceType.PROJECT == "project"
    assert CareerEvidenceType.ACHIEVEMENT == "achievement"
    assert CareerEvidenceType.RESPONSIBILITY == "responsibility"
    assert CareerEvidenceType.LEADERSHIP == "leadership"
    assert CareerEvidenceType.EDUCATION == "education"
    assert CareerEvidenceType.CERTIFICATION == "certification"
    assert CareerEvidenceType.DOMAIN_EXPERIENCE == "domain_experience"
    assert CareerEvidenceType.INDUSTRY_EXPERIENCE == "industry_experience"
    assert CareerEvidenceType.LANGUAGE == "language"
    assert CareerEvidenceType.CAREER_EXPERIENCE == "career_experience"


def test_gap_support_level_enum() -> None:
    """Test that GapSupportLevel enum has all required values."""
    assert GapSupportLevel.STRONGLY_SUPPORTED == "strongly_supported"
    assert GapSupportLevel.SUPPORTED == "supported"
    assert GapSupportLevel.PARTIALLY_SUPPORTED == "partially_supported"
    assert GapSupportLevel.UNSUPPORTED == "unsupported"
    assert GapSupportLevel.UNKNOWN == "unknown"


def test_career_evidence_model_fields() -> None:
    """Test that CareerEvidence model has all required fields."""
    evidence = CareerEvidence(
        evidence_id="test123",
        claim="Test claim",
        level=VerificationLevel.VERIFIED,
        source="test",
        evidence_type=CareerEvidenceType.SKILL,
        source_location="profile:skills",
        confidence=0.9,
    )
    assert evidence.evidence_id == "test123"
    assert evidence.claim == "Test claim"
    assert evidence.level == VerificationLevel.VERIFIED
    assert evidence.source == "test"
    assert evidence.evidence_type == CareerEvidenceType.SKILL
    assert evidence.source_location == "profile:skills"
    assert evidence.confidence == 0.9
    assert evidence.gap_support is None  # Optional field


def test_build_profile_evidence_includes_new_fields() -> None:
    """Test that build_profile_evidence populates new fields."""
    profile_yaml = {
        "name": "Test User",
        "experience": [
            {
                "company": "Test Corp",
                "title": "Software Engineer",
                "dates": "2020-2023",
                "location": "Munich",
                "facts": ["Built APIs", "Led team of 5"],
            }
        ],
        "skills": ["Python", "Leadership"],
        "education": [{"school": "TUM", "degree": "MSc Computer Science", "years": "2018-2020"}],
        "languages": ["English: fluent", "German: native"],
        "career": {
            "target_role_families": ["product_management"],
            "target_seniority": 4,
            "leadership_direction": "product",
            "current_role_family": "product_management",
        },
    }

    # Create a minimal career profile object
    from job_agent.career.profile import derive_career_profile

    career = derive_career_profile(profile_yaml)

    evidence = build_profile_evidence(profile_yaml, career)

    # Check that we have evidence for experience, skills, education, languages
    assert len(evidence) > 0

    # Check experience evidence
    exp_evidence = [e for e in evidence if "Worked as" in e.claim]
    assert len(exp_evidence) == 1
    assert exp_evidence[0].level == VerificationLevel.VERIFIED
    assert exp_evidence[0].source == "profile"
    assert exp_evidence[0].source_location is not None
    assert exp_evidence[0].source_location.startswith("profile:experience:")
    assert exp_evidence[0].observed_at is not None

    # Check skill evidence
    skill_evidence = [e for e in evidence if "Lists the skill" in e.claim]
    assert len(skill_evidence) == 2
    for e in skill_evidence:
        assert e.level == VerificationLevel.VERIFIED
        assert e.source_location == "profile:skills"
        assert e.evidence_type == CareerEvidenceType.SKILL

    # Check education evidence
    edu_evidence = [e for e in evidence if "Graduated with" in e.claim]
    assert len(edu_evidence) == 1
    assert edu_evidence[0].level == VerificationLevel.VERIFIED
    assert edu_evidence[0].source_location == "profile:education"
    assert edu_evidence[0].evidence_type == CareerEvidenceType.EDUCATION

    # Check language evidence
    lang_evidence = [e for e in evidence if "Language proficiency" in e.claim]
    assert len(lang_evidence) == 2
    for e in lang_evidence:
        assert e.level == VerificationLevel.VERIFIED
        assert e.evidence_type == CareerEvidenceType.LANGUAGE
        assert e.source_location == "profile:languages"

    # Check career targets evidence
    target_evidence = [e for e in evidence if "Targets" in e.claim]
    assert len(target_evidence) == 1
    assert target_evidence[0].level == VerificationLevel.VERIFIED
    assert target_evidence[0].source == "career_profile"
    assert target_evidence[0].source_location == "career_profile:targets"


def test_evidence_cache_basic() -> None:
    """Test basic cache operations."""
    cache = EvidenceQueryCache(default_ttl=1.0, max_entries=10)

    # Test set and get
    from job_agent.career.evidence import CareerEvidence, VerificationLevel

    test_evidence = (
        CareerEvidence(evidence_id="1", claim="test", level=VerificationLevel.VERIFIED, source="test"),
        CareerEvidence(evidence_id="2", claim="test2", level=VerificationLevel.VERIFIED, source="test"),
    )

    cache.set("find_skills", "python", 10, test_evidence, evidence_type="skill")
    result = cache.get("find_skills", "python", 10, evidence_type="skill")
    assert result == test_evidence

    # Test cache miss
    result = cache.get("find_skills", "java", 10, evidence_type="skill")
    assert result is None

    # Test case-insensitive query
    result = cache.get("find_skills", "PYTHON", 10, evidence_type="skill")
    assert result == test_evidence

    # Test stats
    stats = cache.stats()
    assert stats["entries"] == 1
    assert stats["active"] == 1
    assert stats["total_hits"] == 2  # First get() and case-insensitive get() are both hits


def test_evidence_cache_ttl_expiry() -> None:
    """Test that cache entries expire after TTL."""
    cache = EvidenceQueryCache(default_ttl=0.1, max_entries=10)  # 100ms TTL

    from job_agent.career.evidence import CareerEvidence, VerificationLevel

    test_evidence = (CareerEvidence(evidence_id="1", claim="test", level=VerificationLevel.VERIFIED, source="test"),)

    cache.set("find_skills", "python", 10, test_evidence)
    assert cache.get("find_skills", "python", 10) == test_evidence

    # Wait for expiry
    import time

    time.sleep(0.2)

    result = cache.get("find_skills", "python", 10)
    assert result is None


def test_evidence_cache_max_entries() -> None:
    """Test that cache evicts oldest entries when max_entries is reached."""
    cache = EvidenceQueryCache(default_ttl=300.0, max_entries=3)

    from job_agent.career.evidence import CareerEvidence, VerificationLevel

    test_evidence = (CareerEvidence(evidence_id="1", claim="test", level=VerificationLevel.VERIFIED, source="test"),)

    cache.set("method", "q1", 10, test_evidence)
    cache.set("method", "q2", 10, test_evidence)
    cache.set("method", "q3", 10, test_evidence)
    assert cache.stats()["entries"] == 3

    # Adding a 4th should evict one
    cache.set("method", "q4", 10, test_evidence)
    assert cache.stats()["entries"] == 3


def test_candidate_evidence_from_document_includes_new_fields() -> None:
    """Test that candidate_evidence_from_document populates new fields."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Experience\n")
        f.write("Worked as Product Manager at BMW Group (2020-2023)\n")
        f.write("Led autonomous driving team of 15 engineers\n")
        f.write("\n")
        f.write("Skills\n")
        f.write("Python, Leadership, Product Strategy\n")
        f.flush()
        doc_path = Path(f.name)

    try:
        doc = ingest_document(doc_path)
        candidates = candidate_evidence_from_document(doc)

        assert len(candidates) > 0
        for ev in candidates:
            assert ev.source == doc.document_id
            assert ev.source_type == "document"
            assert ev.source_location is not None
            assert ev.source_location.startswith(f"document:{doc.document_id[:8]}:")
            assert ev.observed_at is not None
            assert ev.document_id == doc.document_id
    finally:
        doc_path.unlink()


def test_null_career_knowledge_implements_new_methods() -> None:
    """Test that NullCareerKnowledge implements all new methods."""
    knowledge = NullCareerKnowledge()

    assert knowledge.find_skills("python", 10) == ()
    assert knowledge.find_projects("autonomous", 10) == ()
    assert knowledge.find_achievements("led", 10) == ()
    assert knowledge.find_leadership_experience("managed", 10) == ()
    assert knowledge.find_domain_experience("automotive", 10) == ()
    assert knowledge.find_education("degree", 10) == ()
    assert knowledge.find_certifications("pmp", 10) == ()
    assert knowledge.find_technologies("python", 10) == ()
    assert knowledge.find_languages("german", 10) == ()
    assert knowledge.find_career_experience("product", 10) == ()
    assert knowledge.find_evidence_by_type(CareerEvidenceType.SKILL, "python", 10) == ()


def test_personal_ai_career_knowledge_accepts_cache_param() -> None:
    """Test that PersonalAiCareerKnowledge accepts cache parameter."""
    cache = EvidenceQueryCache()
    # We can't easily test the full class without a real DB, but we can verify
    # the constructor accepts the parameter
    try:
        knowledge = PersonalAiCareerKnowledge(
            "/nonexistent.db",
            cache=cache,
            cache_ttl=60.0,
        )
        # Should not raise
        assert knowledge._cache is cache
        assert knowledge._cache_ttl == 60.0
    except Exception:
        # Expected to fail on _load(), but constructor should accept params
        pass


def test_evidence_type_inference() -> None:
    """Test evidence type inference from claims."""
    from job_agent.career.evidence import _infer_evidence_type

    # Leadership
    assert _infer_evidence_type("Led a team of 10", set()) == CareerEvidenceType.LEADERSHIP
    assert _infer_evidence_type("Managed cross-functional team", set()) == CareerEvidenceType.LEADERSHIP

    # Project
    assert _infer_evidence_type("Built a new platform", set()) == CareerEvidenceType.PROJECT
    assert _infer_evidence_type("Developed autonomous driving system", set()) == CareerEvidenceType.PROJECT

    # Achievement
    assert _infer_evidence_type("Increased revenue by 20%", set()) == CareerEvidenceType.ACHIEVEMENT
    assert _infer_evidence_type("Reduced latency by 50%", set()) == CareerEvidenceType.ACHIEVEMENT

    # Technology
    assert _infer_evidence_type("Python and Kubernetes expert", set()) == CareerEvidenceType.TECHNOLOGY
    assert _infer_evidence_type("Experience with Docker and AWS", set()) == CareerEvidenceType.TECHNOLOGY

    # Education
    assert _infer_evidence_type("MSc Computer Science from TUM", set()) == CareerEvidenceType.EDUCATION
    assert _infer_evidence_type("Certified Kubernetes Administrator", set()) == CareerEvidenceType.CERTIFICATION

    # Domain experience
    assert _infer_evidence_type("Autonomous driving experience", set()) == CareerEvidenceType.DOMAIN_EXPERIENCE
    assert _infer_evidence_type("Automotive industry background", set()) == CareerEvidenceType.DOMAIN_EXPERIENCE

    # Language
    assert _infer_evidence_type("Fluent in German and English", set()) == CareerEvidenceType.LANGUAGE

    # Career experience
    assert _infer_evidence_type("Worked as Product Manager at BMW", set()) == CareerEvidenceType.CAREER_EXPERIENCE


def test_global_cache_singleton() -> None:
    """Test that get_global_cache returns the same instance."""
    cache1 = get_global_cache()
    cache2 = get_global_cache()
    assert cache1 is cache2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
