"""Unit tests for the career application lifecycle state machine (M12-15).

Pure state transitions and field validation — no database, no network.
"""

from __future__ import annotations

import pytest

from job_agent.application import (
    ALL_STAGES,
    INTERVIEW_STAGES,
    Application,
    ApplicationError,
    InvalidApplicationTransition,
    UnknownStageError,
    check_transition,
    is_terminal,
    is_valid_interview_stage,
    is_valid_stage,
    normalize_company,
    normalize_job_title,
    valid_stages,
)


def test_stages_in_expected_order() -> None:
    assert valid_stages() == list(
        ("NOT_APPLIED", "APPLIED", "RESPONDED", "INTERVIEW", "OFFER", "HIRED", "REJECTED", "WITHDRAWN")
    )
    assert ALL_STAGES == ("NOT_APPLIED", "APPLIED", "RESPONDED", "INTERVIEW", "OFFER", "HIRED", "REJECTED", "WITHDRAWN")
    assert INTERVIEW_STAGES == (
        "first_round",
        "second_round",
        "technical",
        "final",
        "onsite",
        "meet_and_greet",
        "phone_screen",
        "take_home",
    )


def test_default_application_stage_is_not_applied() -> None:
    app = Application(job_id="j1")
    assert app.stage == "NOT_APPLIED"
    assert app.applied_at is None


def test_valid_stage_and_unknown_stage() -> None:
    assert is_valid_stage("APPLIED")
    assert not is_valid_stage("APPLIED ")  # exact casing required
    assert not is_valid_stage("NOPE")
    assert is_valid_interview_stage("second_round")
    assert not is_valid_interview_stage("bogus")


def test_check_transition_forward_legal() -> None:
    for target in ("APPLIED", "REJECTED", "WITHDRAWN"):
        check_transition("NOT_APPLIED", target)
    for target in ("RESPONDED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"):
        check_transition("APPLIED", target)
    for target in ("OFFER", "HIRED", "REJECTED", "WITHDRAWN"):
        check_transition("INTERVIEW", target)
    check_transition("OFFER", "HIRED")


def test_same_stage_update_is_legal() -> None:
    check_transition("APPLIED", "APPLIED")


def test_invalid_transition_rejected() -> None:
    with pytest.raises(InvalidApplicationTransition):
        check_transition("NOT_APPLIED", "RESPONDED")
    with pytest.raises(InvalidApplicationTransition):
        check_transition("APPLIED", "HIRED")
    with pytest.raises(InvalidApplicationTransition):
        check_transition("REJECTED", "WITHDRAWN")


def test_unknown_stage_errors() -> None:
    with pytest.raises(UnknownStageError):
        check_transition("OFFERED", "APPLIED")
    with pytest.raises(UnknownStageError):
        check_transition("APPLIED", "OFFERED")
    with pytest.raises(UnknownStageError):
        Application(job_id="j", stage="OFFERED")


def test_is_terminal() -> None:
    assert is_terminal("HIRED")
    assert is_terminal("REJECTED")
    assert is_terminal("WITHDRAWN")
    assert not is_terminal("APPLIED")
    assert not is_terminal("OFFER")


def test_enter_advances_and_stamps_timestamps() -> None:
    app = Application(job_id="j")
    applied = app.enter("APPLIED")
    assert applied.stage == "APPLIED"
    assert applied.applied_at is not None
    assert applied.responded_at is None

    responded = applied.enter("RESPONDED")
    assert responded.responded_at is not None
    assert responded.applied_at is not None  # preserved

    offered = responded.enter("OFFER")
    assert offered.offer_at is not None
    assert offered.closed_at is None  # offers stay open until accepted/rejected

    hired = offered.enter("HIRED")
    assert hired.closed_at is not None


def test_terminal_exits_stamp_closed_at() -> None:
    rejected = Application(job_id="j").enter("APPLIED").enter("REJECTED")
    assert rejected.closed_at is not None
    withdrawn = Application(job_id="j").enter("WITHDRAWN")
    assert withdrawn.closed_at is not None


def test_enter_preserves_identity_and_copies() -> None:
    original = Application(job_id="j")
    advanced = original.enter("APPLIED")
    assert original.stage == "NOT_APPLIED"  # immutability: original untouched
    assert advanced.job_id == "j"


def test_with_fields_updates_metadata_only() -> None:
    app = Application(job_id="j", stage="APPLIED", notes="n")
    updated = app.with_fields(notes="better", follow_up_at="2026-02-01T00:00:00Z")
    assert updated.notes == "better"
    assert updated.applied_at is None  # metadata update never progresses stage
    assert updated.stage == "APPLIED"
    assert updated.follow_up_at is not None
    assert updated.follow_up_at.tzinfo is not None  # normalized to tz-aware


def test_with_fields_interview_stage() -> None:
    app = Application(job_id="j", stage="INTERVIEW")
    updated = app.with_fields(interview_stage="second_round", interview_notes="went well")
    assert updated.interview_stage == "second_round"
    assert updated.interview_notes == "went well"
    with pytest.raises(ApplicationError):
        app.with_fields(interview_stage="bogus")


def test_with_fields_rejects_unknown_and_bad_dates() -> None:
    app = Application(job_id="j")
    with pytest.raises(ApplicationError):
        app.with_fields(unknown_field="x")
    with pytest.raises(ApplicationError):
        app.with_fields(follow_up_at="not-a-date")
    with pytest.raises(ApplicationError):
        app.with_fields(interview_date="bad")


def test_normalize_company_nfkc_and_whitespace() -> None:
    assert normalize_company("  Acme\u00a0 GmbH ") == "acme gmbh"
    assert normalize_company("Apple.Inc") == "apple.inc"
    assert normalize_company("") == ""


def test_normalize_job_title() -> None:
    assert normalize_job_title("  Senior   ML  ") == "senior ml"
    assert normalize_job_title("") == ""
