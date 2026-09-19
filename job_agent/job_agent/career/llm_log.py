"""Aggregate-only LLM call observability.

Records one :class:`LlmCallRecord` per LLM call (semantic mapping, narrative,
tailoring). The payload here is strictly operational metadata — call type,
model, prompt size, duration, outcome, retry count — never prompts, responses,
evidence text, or job content. Consumers (CLI) surface only counts and
durations, so latency and failure modes are diagnosable without leaking
personal data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class LlmOutcome(StrEnum):
    """Canonical per-call outcome; exactly one per record."""

    SUCCESS = "success"
    SCHEMA_INVALID = "schema_invalid"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    TRANSPORT_ERROR = "transport_error"


@dataclass(frozen=True)
class LlmCallRecord:
    """Operational metadata for one LLM call. No content allowed."""

    call_type: str  # "semantic" | "narrative" | "tailor"
    model: str
    duration_seconds: float
    outcome: LlmOutcome
    prompt_chars: int = 0
    retry_count: int = 0


@dataclass
class LlmCallLog:
    """Bounded, near-zero-overhead call log (default cap 256 records)."""

    records: list[LlmCallRecord] = field(default_factory=list)
    max_records: int = 256

    def add(self, record: LlmCallRecord) -> None:
        if self.max_records > 0:
            self.records.append(record)
            if len(self.records) > self.max_records:
                self.records.pop(0)

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

    def latest(self) -> LlmCallRecord | None:
        return self.records[-1] if self.records else None

    def outcome_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.records:
            counts[r.outcome.value] = counts.get(r.outcome.value, 0) + 1
        return counts

    def total_calls(self) -> int:
        return len(self.records)

    def total_duration_seconds(self) -> float:
        return round(sum(r.duration_seconds for r in self.records), 3)


__all__ = [
    "LlmCallLog",
    "LlmCallRecord",
    "LlmOutcome",
]
