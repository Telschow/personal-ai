"""Application lifecycle state machine (M3 MVP).

A career application is a typed, deterministic lifecycle over a job. It is
independent from the aggregate ``jobs.user_status`` flag: the user may mark a
job SAVED while applying later, or move an application through its own
stages. The stage machine is pure and mechanical — no LLM, no policy, no
network — and is the single source of truth for valid transitions.

Stage order::

    NOT_APPLIED -> APPLIED -> RESPONDED -> INTERVIEW -> OFFER -> HIRED

plus the terminal exits ``REJECTED`` and ``WITHDRAWN`` (withdraw legal from
any non-terminal stage). ``OFFER -> HIRED`` requires no further input;
anything terminal is final (an approved offer, a rejection, or a withdrawal
is never silently re-opened).

Timestamps are stamped automatically when a stage is first entered
(``applied_at``, ``responded_at``, ``offer_at``, ``closed_at``); optional
interview and follow-up metadata is set explicitly.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

NON_TERMINAL_STAGES = ("NOT_APPLIED", "APPLIED", "RESPONDED", "INTERVIEW", "OFFER")
TERMINAL_STAGES = ("HIRED", "REJECTED", "WITHDRAWN")
ALL_STAGES = NON_TERMINAL_STAGES + TERMINAL_STAGES


class ApplicationError(ValueError):
    """A lifecycle or payload validation error."""


class UnknownStageError(ApplicationError):
    pass


class InvalidApplicationTransition(ApplicationError):
    """An attempt to move between two stages the machine forbids."""


# Forward adjacency: each stage lists the stages it may transition into.
_TRANSITIONS: dict[str, frozenset[str]] = {
    "NOT_APPLIED": frozenset({"APPLIED", "REJECTED", "WITHDRAWN"}),
    "APPLIED": frozenset({"RESPONDED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"}),
    "RESPONDED": frozenset({"INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"}),
    "INTERVIEW": frozenset({"OFFER", "HIRED", "REJECTED", "WITHDRAWN"}),
    "OFFER": frozenset({"HIRED", "REJECTED", "WITHDRAWN"}),
    "HIRED": frozenset(),
    "REJECTED": frozenset(),
    "WITHDRAWN": frozenset(),
}


def valid_stages() -> list[str]:
    return list(ALL_STAGES)


def is_valid_stage(stage: str) -> bool:
    return stage in ALL_STAGES


def check_transition(current: str, target: str) -> None:
    """Validate a ``current -> target`` transition; raises on violation.

    Same-stage updates are allowed (the target equals the current stage).
    """
    if not is_valid_stage(current):
        raise UnknownStageError(f"Unknown application stage: {current}")
    if not is_valid_stage(target):
        raise UnknownStageError(f"Unknown application stage: {target}")
    if current == target:
        return
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise InvalidApplicationTransition(f"Invalid application transition: {current} -> {target}")


def is_terminal(stage: str) -> bool:
    return stage in TERMINAL_STAGES


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_datetime(value: object) -> datetime:
    text = str(value).strip()
    try:
        normalized_text = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(normalized_text)
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    except ValueError as exc:
        raise ApplicationError(f"Invalid datetime: {text!r}") from exc


INTERVIEW_STAGES = (
    "first_round",
    "second_round",
    "technical",
    "final",
    "onsite",
    "meet_and_greet",
    "phone_screen",
    "take_home",
)


def is_valid_interview_stage(value: str) -> bool:
    return value in INTERVIEW_STAGES


@dataclass
class Application:
    """The durable, typed lifecycle record for one job application."""

    job_id: str
    stage: str = "NOT_APPLIED"
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    applied_at: datetime | None = None
    responded_at: datetime | None = None
    offer_at: datetime | None = None
    closed_at: datetime | None = None
    follow_up_at: datetime | None = None
    interview_stage: str | None = None
    interview_date: datetime | None = None
    interview_notes: str | None = None

    def __post_init__(self) -> None:
        if not is_valid_stage(self.stage):
            raise UnknownStageError(f"Unknown application stage: {self.stage}")
        if self.interview_stage is not None and not is_valid_interview_stage(self.interview_stage):
            raise ApplicationError(f"Unknown interview stage: {self.interview_stage}")

    def enter(self, new_stage: str, *, now: datetime | None = None) -> Application:
        """Return a copy advanced to ``new_stage`` with timestamps stamped."""
        check_transition(self.stage, new_stage)
        now = now or _now()
        updated = Application(
            job_id=self.job_id,
            stage=new_stage,
            notes=self.notes,
            created_at=self.created_at or now,
            updated_at=now,
            applied_at=self.applied_at,
            responded_at=self.responded_at,
            offer_at=self.offer_at,
            closed_at=self.closed_at,
            follow_up_at=self.follow_up_at,
            interview_stage=self.interview_stage,
            interview_date=self.interview_date,
            interview_notes=self.interview_notes,
        )
        if new_stage == "APPLIED" and updated.applied_at is None:
            updated.applied_at = now
        if new_stage == "RESPONDED" and updated.responded_at is None:
            updated.responded_at = now
        if new_stage == "OFFER" and updated.offer_at is None:
            updated.offer_at = now
        if is_terminal(new_stage) and updated.closed_at is None:
            updated.closed_at = now
        return updated

    def with_fields(self, **updates: object) -> Application:
        """Update optional metadata in place (no stage progression).

        ``follow_up_at`` and ``interview_date`` accept ISO-8601 strings which
        are normalized to timezone-aware UTC datetimes; an unparseable or
        ambiguous value is rejected deterministically (never stored raw).
        """
        allowed_keys = {"notes", "follow_up_at", "interview_stage", "interview_date", "interview_notes"}
        for key, _value in updates.items():
            if key not in allowed_keys:
                raise ApplicationError(f"Unsupported application field: {key}")
        parsed: dict[str, object] = {}
        for key, value in updates.items():
            if value is None:
                parsed[key] = None
                continue
            if key in ("follow_up_at", "interview_date"):
                parsed[key] = _parse_datetime(value)
            elif key == "interview_stage":
                if not is_valid_interview_stage(str(value)):
                    raise ApplicationError(f"Unknown interview stage: {value}")
                parsed[key] = str(value)
            else:
                parsed[key] = value
        now = _now()
        kwargs = {
            k: getattr(self, k)
            for k in (
                "job_id",
                "stage",
                "notes",
                "created_at",
                "updated_at",
                "applied_at",
                "responded_at",
                "offer_at",
                "closed_at",
                "follow_up_at",
                "interview_stage",
                "interview_date",
                "interview_notes",
            )
        }
        for key, value in parsed.items():
            kwargs[key] = value
        kwargs["updated_at"] = now
        if self.created_at is None:
            kwargs["created_at"] = now
        return Application(**kwargs)


def normalize_company(company: str) -> str:
    """Fold a company display name into a stable grouping key.

    NFKC + casefold + whitespace collapse; accents are preserved (Müller
    never folds into Muller), matching the identity conventions used across
    the repository. Pure and deterministic — never infers or renames.
    """
    folded = unicodedata.normalize("NFKC", company or "").casefold()
    return re.sub(r"\s+", " ", folded).strip()


_COMMON_TITLE_WORDS = frozenset(
    [
        "for",
        "at",
        "the",
        "an",
        "a",
        "and",
        "or",
        "der",
        "die",
        "das",
        "den",
        "mit",
        "in",
        "on",
        "von",
        "zu",
        "into",
        "-",
    ]
)


def normalize_job_title(title: str) -> str:
    """Fold a job title into a comparable grouping key (stopwords removed)."""
    words = [w for w in normalize_company(title).split() if w not in _COMMON_TITLE_WORDS]
    return " ".join(words)


__all__ = [
    "ALL_STAGES",
    "Application",
    "ApplicationError",
    "InvalidApplicationTransition",
    "NON_TERMINAL_STAGES",
    "TERMINAL_STAGES",
    "UnknownStageError",
    "check_transition",
    "is_terminal",
    "is_valid_interview_stage",
    "is_valid_stage",
    "normalize_company",
    "normalize_job_title",
    "valid_stages",
]
