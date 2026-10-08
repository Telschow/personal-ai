"""Minimal deterministic verifier.

"LLM generated an answer" is not treated as success. The verifier checks a
task's structured result against deterministic requirements:

* required evidence exists (when the task demands it),
* a policy denial did not occur for the task,
* the output summary is non-empty and internally consistent.

An independent verifier model can be added later behind the same verdict
contract; this deterministic version establishes the boundary now and is
fully testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass

from personal_ai.execution.events import EventType
from personal_ai.execution.models import Task


class VerificationError(Exception):
    """Base class for verification failures."""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Deterministic verdict for a task."""

    passed: bool
    task_id: str
    reasons: tuple[str, ...] = ()
    outcome: str = ""  # "pass" | "fail"


class DeterministicVerifier:
    """Checks a task's structured result against deterministic requirements."""

    def __init__(
        self,
        require_evidence_for: tuple[str, ...] = ("corpus-research", "research"),
    ) -> None:
        self._require_evidence_for = require_evidence_for

    def verify(
        self, task: Task, task_events: tuple[object, ...] = ()
    ) -> VerificationResult:
        """Produce a deterministic pass/fail verdict for ``task``."""
        reasons: list[str] = []

        if task.skill in self._require_evidence_for and len(task.evidence) == 0:
            reasons.append("required evidence is missing")

        denied = any(
            event.event_type == EventType.TOOL_DENIED and event.task_id == task.task_id
            for event in task_events
        )
        if denied:
            reasons.append("a policy denial was recorded")

        summary = task.outputs.get("summary", "")
        if not isinstance(summary, str) or not summary.strip():
            reasons.append("output summary is empty")

        if reasons:
            return VerificationResult(
                passed=False,
                task_id=task.task_id,
                reasons=tuple(reasons),
                outcome="fail",
            )
        return VerificationResult(
            passed=True,
            task_id=task.task_id,
            outcome="pass",
        )
