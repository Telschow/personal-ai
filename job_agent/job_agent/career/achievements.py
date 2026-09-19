"""Deterministic achievement derivation from evidence claims.

An "achievement" is an evidence-backed, impactful framing: metric-bearing or
action-verb-led factual lines. Derivation is lexical and bounded — it never
invents metrics or scope, it only *selects and reorders* the user's own
statements.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from .evidence import CareerEvidence, level_index

_ACTION_VERBS = (
    "led",
    "led cross-functional",
    "built",
    "designed",
    "launched",
    "drove",
    "delivered",
    "established",
    "created",
    "improved",
    "reduced",
    "increased",
    "managed",
    "spearheaded",
    "scaled",
    "implemented",
    "developed",
    "coordinated",
    "owned",
    "introduced",
    "set up",
)

_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_START_VERB_RE = re.compile(rf"^({'|'.join(_ACTION_VERBS)})\b", re.IGNORECASE)


class Achievement(BaseModel):
    text: str
    evidence_id: str
    level: str
    number_driven: bool = False
    source_type: str | None = None


def _looks_achievement(claim: str) -> bool:
    return bool(_NUMBER_RE.search(claim)) or bool(_START_VERB_RE.search(claim))


def derive_achievements(
    evidence: list[CareerEvidence],
    *,
    limit: int = 8,
) -> list[Achievement]:
    """Rank evidence lines that read as achievements; deterministic ordering."""
    pool = [e for e in evidence if _looks_achievement(e.claim)]
    pool.sort(
        key=lambda e: (
            level_index(e.level),
            bool(_NUMBER_RE.search(e.claim)),
            e.confidence,
            e.evidence_id,
        ),
        reverse=True,
    )
    out: list[Achievement] = []
    seen: set[str] = set()
    for e in pool[: limit * 2]:
        text = e.claim.rstrip(".")
        key = text.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Achievement(
                text=text[:300],
                evidence_id=e.evidence_id,
                level=e.level.value,
                number_driven=bool(_NUMBER_RE.search(text)),
                source_type=e.source_type,
            )
        )
        if len(out) >= limit:
            break
    return out


__all__ = ["Achievement", "derive_achievements"]
