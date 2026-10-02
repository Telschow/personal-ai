"""Career profile derivation: provenance, defaults, determinism."""

from job_agent.career.profile import ROLE_FAMILIES, CareerProfile, derive_career_profile


def _prof(**overrides):
    data = {
        "name": "Jane Doe",
        "languages": ["English: native"],
        "education": [{"school": "Example University", "degree": "MSc", "years": "2016-2019"}],
        "experience": [
            {"company": "ACME", "title": "Product Owner - AI", "dates": "2024-present"},
            {"company": "Beta", "title": "Development Engineer", "dates": "2021-2024"},
        ],
        "skills": ["Product Management", "ML"],
    }
    data.update(overrides)
    return data


def test_default_target_families_include_current():
    career = derive_career_profile(_prof())
    assert career.current_role_family == "product_management"
    assert career.target_role_families[0] == "product_management"
    assert "product_leadership" in career.target_role_families


def test_explicit_targets_are_kept_and_current_inserted_first():
    career = derive_career_profile(_prof(career={"target_role_families": ["program_leadership"]}))
    assert career.target_role_families[0] == "product_management"
    assert "program_leadership" in career.target_role_families
    assert "target_role_families" not in career.inferred_fields


def test_inferred_targets_are_tracked():
    career = derive_career_profile(_prof())
    assert "target_role_families" in career.inferred_fields


def test_leadership_direction_inferred_from_latest_title():
    career = derive_career_profile(_prof())
    assert career.leadership_direction == "product"
    career2 = derive_career_profile(
        _prof(experience=[{"company": "X", "title": "Engineering Manager", "dates": "2024-present"}])
    )
    assert career2.leadership_direction == "technical"


def test_inferred_fields_tracked_for_all_defaults():
    career = derive_career_profile(_prof())
    assert "ai_exposure" in career.inferred_fields
    assert "willingness_to_relocate" in career.inferred_fields


def test_explicit_values_remove_from_inferred():
    career = derive_career_profile(_prof(career={"ai_exposure": 9, "willingness_to_relocate": False}))
    assert "ai_exposure" not in career.inferred_fields
    assert "willingness_to_relocate" not in career.inferred_fields
    assert career.ai_exposure == 9
    assert career.willingness_to_relocate is False


def test_constraints_back_stated_values():
    career = derive_career_profile(
        _prof(constraints={"family_compatibility_important": False, "willing_to_relocate": False})
    )
    assert career.family_compatibility_important is False
    assert career.willingness_to_relocate is False


def test_empty_profile_is_safe():
    career = derive_career_profile({})
    assert career.name == ""
    assert career.target_role_families == ["product_management", "product_leadership"]


def test_all_families_valid():
    for family in ROLE_FAMILIES:
        CareerProfile(target_role_families=[family])


def test_industry_domains_default():
    career = derive_career_profile(_prof())
    assert "automotive" in career.industry_domains


def test_deterministic():
    assert derive_career_profile(_prof()) == derive_career_profile(_prof())
