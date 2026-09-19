"""Career-track classifier tests — hermetic, deterministic."""

from __future__ import annotations

from job_agent.career_tracks import (
    DEFAULT_TRACKS,
    CareerTracksSettings,
    classify_track,
    match_tracks,
    normalize_seniority,
)

TRACK_IDS = {t.track_id for t in DEFAULT_TRACKS}


def test_twelve_default_tracks():
    assert len(DEFAULT_TRACKS) == 12
    assert {
        "product_management",
        "program_management",
        "engineering_leadership",
        "autonomous_driving",
        "ai_ml",
        "robotics",
        "deeptech",
        "defence_aerospace",
        "mobility",
        "energy_cleantech",
        "data_platform",
        "innovation_strategy",
    } == TRACK_IDS


def test_priorities_are_positive_and_ids_unique():
    priorities = [t.priority for t in DEFAULT_TRACKS]
    ids = [t.track_id for t in DEFAULT_TRACKS]
    assert all(p > 0 for p in priorities)
    assert len(ids) == len(set(ids)) and not any(not t.enabled for t in DEFAULT_TRACKS)


def test_classify_autonomous_driving():
    m = classify_track("Senior Engineering Manager - Autonomous Driving")
    assert m is not None
    assert m.track_id == "autonomous_driving"


def test_classify_product_management():
    m = classify_track("Principal Product Manager (Platform)")
    assert m is not None
    assert m.track_id == "product_management"


def test_classify_ai_ml():
    m = classify_track("Senior Machine Learning Engineer - LLM Platform")
    assert m.track_id == "ai_ml"


def test_classify_robotics():
    m = classify_track("Robotics Software Engineer - Motion Planning")
    assert m.track_id == "robotics"


def test_classify_unknown_returns_none():
    assert classify_track("Barista") is None


def test_match_tracks_returns_deterministic_multi():
    a = match_tracks("Senior Engineering Manager - Autonomous Driving")
    b = match_tracks("Senior Engineering Manager - Autonomous Driving")
    assert len(a) >= 1
    assert [m.track_id for m in a] == [m.track_id for m in b]


def test_resolved_defaults_to_all():
    assert len(CareerTracksSettings().resolved) == 12


def test_resolved_uses_configured():
    cfg = CareerTracksSettings(tracks=[DEFAULT_TRACKS[0]])
    assert cfg.resolved == [DEFAULT_TRACKS[0]]


def test_normalize_seniority_bands():
    assert normalize_seniority(6) == "executive"
    assert normalize_seniority(5) == "lead"
    assert normalize_seniority(4) == "staff"
    assert normalize_seniority(3) == "senior"
    assert normalize_seniority(2) == "mid"
    assert normalize_seniority(1) == "entry"
    assert normalize_seniority(0) == "unknown"
    assert normalize_seniority(None) == "unknown"
