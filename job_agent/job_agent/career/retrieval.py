"""Retrieval plan + rank / dedup / compact for career evidence.

Retrieval queries are **templates over structured concepts**, never raw JD
text. The ranker deduplicates on ``evidence_id`` and promotes by trust,
then compaction squeezes the result to a bounded character window for the
fit calculator and the optional LLM narrative.
"""

from __future__ import annotations

import contextlib
from typing import Any

from .evidence import build_profile_evidence, rank_evidence
from .knowledge import CareerKnowledge
from .profile import CareerProfile
from .requirements import CONCEPT_TERMS, StructuredJobAttributes

MAX_QUERIES = 6
MAX_CORPUS_CHARS = 6000
DEFAULT_MAX_CORPUS_EVIDENCE = 40
DEFAULT_MAX_MEMORY_EVIDENCE = 20

# template list: each uses structured attributes, never raw JD tokens
_QUERY_TEMPLATES: tuple[str, ...] = (
    "{name}'s evidence of experience in {concept}",
    "{name}'s skills in {concept}",
)

# concept -> human-readable display label (for positioning text)
CONCEPT_LABELS: dict[str, str] = {
    "ai_systems": "AI systems",
    "product_strategy": "product strategy",
    "stakeholder_management": "stakeholder management",
    "team_leadership": "team leadership",
    "program_management": "program management",
    "systems_engineering": "systems engineering",
    "safety_critical": "safety-critical development",
    "autonomous_driving": "autonomous driving",
    "backend_engineering": "backend engineering",
    "embedded_engineering": "embedded engineering",
    "data_engineering": "data engineering",
    "ai_tooling": "AI tooling",
    "user_research": "user research",
    "gtm": "go-to-market",
    "regulation": "regulation",
}


def build_retrieval_plan(
    career: CareerProfile,
    attrs: StructuredJobAttributes,
) -> list[str]:
    """Bounded, deterministic queries drawn from structured attributes.

    No raw JD text is ever interpolated; every template is one of the
    fixed literals. Queries are capped at ``MAX_QUERIES``.
    """
    name = career.name or "the user"
    concept_pool = list(dict.fromkeys(attrs.concepts))[:8]
    queries: list[str] = []
    for concept in concept_pool:
        if len(queries) >= MAX_QUERIES:
            break
        label = CONCEPT_LABELS.get(concept, concept.replace("_", " "))
        template = _QUERY_TEMPLATES[len(queries) % len(_QUERY_TEMPLATES)]
        q = template.format(name=name, concept=label)
        queries.append(q[:120])
    return queries


def concept_coverage(
    evidence: list,
    concepts: list[str],
) -> dict[str, list]:
    """Group evidence by which concepts they can support, deterministic.

    A piece of evidence supports a concept when the concept's keyword terms
    appear in the evidence claim/keywords/categories. The result is capped
    per concept (max 4 items) and per concept dedupes on ``evidence_id``.
    """
    mapping: dict[str, list] = {c: [] for c in concepts}
    seen: dict[str, set[str]] = {c: set() for c in concepts}
    for ev in evidence:
        haystack = f"{ev.claim} {' '.join(ev.keywords)} {' '.join(ev.categories)}".casefold()
        for concept in concepts:
            terms = CONCEPT_TERMS.get(concept, (concept.replace("_", " "),))
            if any(term in haystack for term in terms) and ev.evidence_id not in seen[concept]:
                mapping[concept].append(ev)
                seen[concept].add(ev.evidence_id)
    return {k: v[:4] for k, v in mapping.items()}


def collect_evidence(
    profile_yaml: dict,
    career: CareerProfile,
    knowledge: CareerKnowledge,
    plan: list[str],
    attrs: StructuredJobAttributes,
    *,
    max_corpus: int = DEFAULT_MAX_CORPUS_EVIDENCE,
    max_memory: int = DEFAULT_MAX_MEMORY_EVIDENCE,
) -> list:
    """Merge profile evidence with retrieval results, rank, and cap.

    Returns an already-ranked, deduped list bounded by
    ``max_corpus + max_memory + profile_evidence``.
    """
    profile = build_profile_evidence(profile_yaml, career)
    mem: list = []
    corp: list = []
    if knowledge.health():
        for q in plan[:MAX_QUERIES]:
            with contextlib.suppress(Exception):
                mem.extend(knowledge.memory_search(q, limit=5))
            with contextlib.suppress(Exception):
                corp.extend(knowledge.corpus_search(q, limit=8))
    mem = rank_evidence(mem, limit=max_memory)
    corp = rank_evidence(corp, limit=max_corpus)
    combined = profile + mem + corp
    return rank_evidence(combined, limit=len(profile) + max_memory + max_corpus)


def compact_evidence(
    evidence: list,
    max_chars: int = MAX_CORPUS_CHARS,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Bounded, safe payload for the LLM or log.

    Returns ``(items, evidence_ids)`` where each item is a compact dict
    containing only provenance and categorical fields (no raw text). Total
    string length of all ``claim`` fields is capped at ``max_chars``.
    """
    items: list[dict[str, Any]] = []
    ids: list[str] = []
    budget = max_chars
    for ev in evidence:
        claim = ev.claim
        if budget <= 0:
            break
        if len(claim) > budget:
            claim = claim[: budget - 12] + "…[-truncated]"
        items.append(
            {
                "evidence_id": ev.evidence_id,
                "claim": claim,
                "level": ev.level.value,
                "source": ev.source,
                "categories": ev.categories,
                "keywords": ev.keywords,
                "confidence": round(ev.confidence, 2),
            }
        )
        ids.append(ev.evidence_id)
        budget -= len(claim)
    return items, ids
