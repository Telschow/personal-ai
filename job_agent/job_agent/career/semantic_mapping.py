"""Semantic requirement mapping: a bounded LLM refinement over the
deterministic mapping floor.

The deterministic :mod:`career.mapping` floor already produces a
coverage/evidence mapping for every job concept. This module lets an LLM
*propose* refinements to that mapping — upgrading STRONG/PARTIAL/
TRANSFERABLE/GAP verdicts and suggesting additional evidence — subject to
hard, mechanical constraints:

- every positive re-classification (STRONG/PARTIAL/TRANSFERABLE) must cite
  at least one allow-listed evidence id,
- explicit negative evidence (``negative_evidence=True``) is never upgraded,
- a proposal never *downgrades* the deterministic coverage (e.g.
  STRONG -> UNKNOWN is rejected — same-level and upward refinements only;
  measured 2026-09-16: small models proposed exactly this demotion on a
  DIRECT eval case, qwen3.5:9b did not),
- every accepted refinement is stamped with ``layer="semantic"`` and a
  human-grounded ``reasoning`` string,
- parse errors degrade to ``skipped`` (deterministic floor stands),
- the LLM proposes only; it never decides, writes, or approves.

``OllamaJsonClient.classify_mapping`` (in :mod:`career.llm`) is the
production backend; this module stays provider-independent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from .evidence import CareerEvidence
from .mapping import CoverageLevel, RequirementMap
from .requirements import CONCEPT_TERMS

SEMANTIC_SCHEMA_VERSION = "semantic-mapping-v1"
MAX_SEMANTIC_CONCEPTS = 15
MAX_EVIDENCE_PER_CONCEPT = 6
MAX_REASONING_CHARS = 300
_MAX_EVIDENCE_IDS = 8

_POSITIVE_LEVELS = frozenset({CoverageLevel.STRONG, CoverageLevel.PARTIAL, CoverageLevel.TRANSFERABLE})

# Monotone rank used by ``is_proposal_applicable``: a proposal may hold or
# raise the deterministic coverage, never lower it. STRONG > PARTIAL >
# TRANSFERABLE > GAP > UNKNOWN. The exact numeric values are only compared
# relative to each other.
_COVERAGE_RANK = {
    CoverageLevel.STRONG: 4,
    CoverageLevel.PARTIAL: 3,
    CoverageLevel.TRANSFERABLE: 2,
    CoverageLevel.GAP: 1,
    CoverageLevel.UNKNOWN: 0,
}

_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{2,}")


class SemanticRequirement(BaseModel):
    """One proposed semantic refinement for a requirement."""

    requirement: str
    coverage: CoverageLevel
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    evidence_ids: list[str] = Field(default_factory=list)
    reasoning: str = Field(default="", max_length=MAX_REASONING_CHARS)

    @field_validator("coverage", mode="before")
    @classmethod
    def _coerce_coverage(cls, value: Any) -> CoverageLevel:
        if isinstance(value, CoverageLevel):
            return value
        try:
            return CoverageLevel(str(value).upper())
        except ValueError:
            raise ValueError(f"invalid coverage: {value!r}") from None

    @field_validator("requirement", mode="before")
    @classmethod
    def _require_text(cls, value: Any) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("requirement must be non-empty")
        return text

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _coerce_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("evidence_ids must be a list")
        return [str(v).strip() for v in value if str(v).strip()][:_MAX_EVIDENCE_IDS]

    @field_validator("reasoning", mode="before")
    @classmethod
    def _truncate_reasoning(cls, value: Any) -> str:
        return str(value).strip()[:MAX_REASONING_CHARS]


class SemanticMapping(BaseModel):
    """Validated batch of semantic refinements from the model."""

    version: str = SEMANTIC_SCHEMA_VERSION
    requirements: list[SemanticRequirement] = Field(default_factory=list)


class SemanticMappingClient(Protocol):
    """Any backend that can classify a bounded requirement→evidence set."""

    def classify_mapping(
        self,
        *,
        requirements: list[dict[str, Any]],
        evidence_by_capability: dict[str, list[dict[str, Any]]],
        allowlist: list[str],
    ) -> SemanticMapping | None: ...


def parse_semantic_mapping(raw: str, allowlist: set[str]) -> SemanticMapping | None:
    """Parse and validate the model output, dropping out-of-allowlist ids.

    Mechanical guards (never an LLM decision):

    - responses referencing no allow-listed evidence for positive coverage
      are dropped,
    - malformed output returns ``None`` (fail closed; deterministic floor
      stands).
    """
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        version = str(data.get("version") or SEMANTIC_SCHEMA_VERSION)
        raw_reqs = data.get("requirements")
        if not isinstance(raw_reqs, list):
            return None
    except Exception:  # noqa: BLE001
        return None

    kept: list[SemanticRequirement] = []
    for item in raw_reqs:
        if not isinstance(item, dict):
            continue
        try:
            req = SemanticRequirement.model_validate(item)
        except Exception:  # noqa: BLE001 - invalid items are dropped, never guessed
            continue
        allowed = [eid for eid in req.evidence_ids if eid in allowlist]
        if req.coverage in _POSITIVE_LEVELS and not allowed:
            # A positive re-classification without a verifiable evidence
            # anchor is unverifiable; drop it, never trust it.
            continue
        kept.append(req.model_copy(update={"evidence_ids": allowed}))
    return SemanticMapping(version=version, requirements=kept)


def _overlap_score(ev: CareerEvidence, concept: str) -> int:
    """Rank evidence for a concept window by term overlap."""
    terms = CONCEPT_TERMS.get(concept, (concept.replace("_", " "),))
    hay = " ".join(s for s in (ev.claim, *(ev.keywords or []), *(ev.categories or [])) if s)
    hay_l = hay.casefold()
    score = sum(1 for t in terms if t in hay_l)
    # token-level bonus for tighter lexical overlap
    ev_tokens = set(_TOKEN_RE.findall(hay_l))
    for t in terms:
        ev_tokens.update(_TOKEN_RE.findall(t))
    claim_tokens = set(_TOKEN_RE.findall(ev.claim.casefold()))
    score += len(claim_tokens & ev_tokens)
    return score


def _evidence_windows(
    mapping: list[RequirementMap],
    evidence: list[CareerEvidence],
) -> dict[str, list[CareerEvidence]]:
    """Bounded per-capability evidence window (referenced ≤2 + ranked pool)."""
    by_id = {e.evidence_id: e for e in evidence}
    windows: dict[str, list[CareerEvidence]] = {}
    for m in mapping[:MAX_SEMANTIC_CONCEPTS]:
        referenced = [by_id[eid] for eid in m.evidence_ids if eid in by_id][:2]
        pool = [e for e in evidence if e.evidence_id not in m.evidence_ids and _overlap_score(e, m.capability) > 0]
        pool.sort(key=lambda e: _overlap_score(e, m.capability), reverse=True)
        windows[m.capability] = (referenced + pool)[:MAX_EVIDENCE_PER_CONCEPT]
    return windows


def _compact_evidence(evs: list[CareerEvidence]) -> list[dict[str, Any]]:
    return [{"evidence_id": e.evidence_id, "claim": e.claim[:600], "level": e.level.value} for e in evs]


@dataclass(frozen=True)
class SemanticMapResult:
    """Result of a semantic refinement attempt.

    ``mapping`` is always the final mapping (refined or the untouched floor);
    ``applied`` is True when at least one refinement was accepted;
    ``skipped`` documents why no refinement happened
    ("" | "disabled" | "no_input" | "provider_error" | "malformed").
    """

    mapping: list[RequirementMap]
    applied: bool
    skipped: str = ""


def refine_mapping(
    mapping: list[RequirementMap],
    evidence: list[CareerEvidence],
    *,
    client: SemanticMappingClient | None,
) -> SemanticMapResult:
    """Refine the deterministic mapping with semantic proposals (or keep it).

    Provider-neutral. On any failure the deterministic floor is returned
    untouched with an aggregate-only ``skipped`` reason — never a partial
    ``layer="semantic"`` claim based on unverified output.
    """
    if client is None:
        return SemanticMapResult(mapping=mapping, applied=False, skipped="disabled")
    if not mapping:
        return SemanticMapResult(mapping=mapping, applied=False, skipped="no_input")

    allowlist = sorted({e.evidence_id for e in evidence})
    windows = _evidence_windows(mapping, evidence)
    if not windows:
        return SemanticMapResult(mapping=mapping, applied=False, skipped="no_input")

    try:
        semantic = client.classify_mapping(
            requirements=[
                {
                    "requirement": m.requirement,
                    "capability": m.capability,
                    "coverage": m.coverage.value,
                    "confidence": m.confidence,
                    "evidence_ids": m.evidence_ids[:4],
                    "negative_evidence": m.negative_evidence,
                }
                for m in mapping[:MAX_SEMANTIC_CONCEPTS]
            ],
            evidence_by_capability={cap: _compact_evidence(evs) for cap, evs in windows.items()},
            allowlist=allowlist,
        )
    except Exception:  # noqa: BLE001 - a failed proposal must not kill tailoring
        return SemanticMapResult(mapping=mapping, applied=False, skipped="provider_error")

    if semantic is None:
        return SemanticMapResult(mapping=mapping, applied=False, skipped="malformed")

    refined = _merge(mapping, semantic.requirements)
    applied = advanced_or_reevidenced(mapping, refined)
    return SemanticMapResult(mapping=refined, applied=applied)


def _merge(
    mapping: list[RequirementMap],
    proposals: list[SemanticRequirement],
) -> list[RequirementMap]:
    """Apply accepted semantic proposals to the deterministic mapping.

    A proposal is accepted only when ALL of: it targets a known
    requirement text, the deterministic verdict is not explicit negative
    evidence, and (for positive coverage) it carries allow-listed evidence
    ids. Accepted refinements get ``layer="semantic"`` and the model's
    ``reasoning``.
    """
    out: list[RequirementMap] = []
    for m in mapping:
        prop = next((p for p in proposals if p.requirement == m.requirement), None)
        if is_proposal_applicable(m, prop) and prop is not None:
            reasoning = prop.reasoning.strip()[:MAX_REASONING_CHARS]
            out.append(
                m.model_copy(
                    update={
                        "coverage": prop.coverage,
                        "confidence": round(float(prop.confidence), 3),
                        "evidence_ids": sorted(set(prop.evidence_ids))[:4],
                        "layer": "semantic",
                        "reasoning": reasoning,
                        "note": f"semantic refinement (v1): {reasoning}",
                    }
                )
            )
        else:
            out.append(m)
    return out


def is_proposal_applicable(
    deterministic: RequirementMap,
    prop: SemanticRequirement | None,
) -> bool:
    """Whether a semantic proposal may replace a deterministic verdict."""
    if prop is None:
        return False
    if deterministic.negative_evidence:
        return False  # explicit negative evidence is never upgraded
    if _COVERAGE_RANK[deterministic.coverage] > _COVERAGE_RANK[prop.coverage]:
        # A proposal never downgrades the deterministic coverage: same-level
        # and upward refinements only. Without this guard a noisy model can
        # turn a verified STRONG verdict into UNKNOWN and silently erase the
        # deterministic floor (measured on small models for a DIRECT eval
        # case; qwen3.5:9b did not do this).
        return False
    if prop.coverage in _POSITIVE_LEVELS and not prop.evidence_ids:  # noqa: SIM103
        return False  # positive re-classification must cite evidence
    return True


def advanced_or_reevidenced(
    before: list[RequirementMap],
    after: list[RequirementMap],
) -> bool:
    """True if any coverage upgraded, or any mapping changed its evidence
    set or reasoning."""
    return any(
        (x.coverage, x.layer, x.evidence_ids, x.reasoning) != (y.coverage, y.layer, y.evidence_ids, y.reasoning)
        for x, y in zip(before, after)
    )


__all__ = [
    "SEMANTIC_SCHEMA_VERSION",
    "MAX_SEMANTIC_CONCEPTS",
    "MAX_EVIDENCE_PER_CONCEPT",
    "MAX_REASONING_CHARS",
    "SemanticMapping",
    "SemanticMappingClient",
    "SemanticMapResult",
    "parse_semantic_mapping",
    "refine_mapping",
]
