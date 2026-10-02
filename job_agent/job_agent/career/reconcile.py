"""CV evidence derivation + reconciliation into existing evidence.

Given a :class:`CareerDocument`, deterministic line-by-line fact extraction
yields ``CareerEvidence`` items at ``DOCUMENTED`` level (user-provided CV
documents are credible but not the canonical profile).  Reconciliation against
existing evidence is three-valued:

- **exact**: the same normalized fact already exists at a ``DOCUMENTED+`` level
  in the existing evidence set — keep the existing row, record the mapping.
- **new**: a compatible fact not yet present — persist as a new ``DOCUMENTED``
  row (idempotent across re-runs via ``evidence_id``).
- **conflict**: the same structured key (company + role title) appears in both
  the existing evidence and the document with differing text — record an
  explicit ``ReconciliationConflict``; never silently resolve.

No LLM involvement; no automatic promotion or demotion across levels; no
content logged.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from unicodedata import normalize

from pydantic import BaseModel, Field

from .documents import CareerDocument
from .evidence import (
    CareerEvidence,
    VerificationLevel,
    classify_categories,
    evidence_id,
    level_index,
)

DEFAULT_EVIDENCE_CONFIDENCE = 0.85

# Bounded extraction: cap per-document facts to avoid a noisy resume
# overwhelming the pool.
MAX_FACTS_PER_DOCUMENT = 200
_MAX_CLAIM_LENGTH = 300


# ──────────────────────────── normalized facts ────────────────────────────────


def _normalize(text: str) -> str:
    """NFKC + casefold + whitespace collapse — deterministic, ordering-safe."""
    folded = normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", folded).strip()


# ──────────────────────────── structured keys ──────────────────────────────────

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_DATE_RE = re.compile(r"(?:\d{4})\s*[-–—]\s*(?:\d{4}|present)", re.IGNORECASE)


def _structured_key(claim: str) -> tuple[str, str] | None:
    """Best-effort extraction of ``(company, role_title)`` from a factual line.

    Returns ``None`` for non-experience lines (skills, headings, bullet notes
    without an employer or role).
    """
    # Strip trailing date range before matching.
    stripped = re.sub(r"\s*\(?(?:\d{4})\s*[-–—]\s*(?:\d{4}|present)\)?\s*$", "", claim.strip())

    # "Product Owner at Nimbus Motors" form (with or without stripped date range)
    m = re.match(
        r"^(?P<title>[^,–—]{3,60}?)\s+at\s+(?P<company>[^,–—]{2,60})$",
        stripped,
    )
    if m:
        return (m.group("company").strip().casefold(), m.group("title").strip().casefold())

    # Comma-separated form: "Nimbus Motors, 2024-present, Product Owner"
    parts = [p.strip() for p in re.split(r"[,;–—]", claim) if p.strip()]
    if len(parts) >= 2 and _YEAR_RE.search(parts[1]):
        company = parts[0].strip().casefold()
        title = parts[-1].strip().casefold()
        if len(company) >= 2 and len(title) >= 2 and company != title:
            return (company, title)
    return None


# ──────────────────────────── fact line extraction ────────────────────────────

_NON_FACT = re.compile(
    r"^("
    r"cv|resume|curriculum vitae|"
    r"personal data|address|email|phone|mobile|linkedin|github|portfolio|"
    r"references|available on request|"
    r"date of birth|nationality|work permit|visa|notice period"
    r")\b",
    re.IGNORECASE,
)


def _is_fact_line(line: str, section_heading: str | None) -> bool:
    line = line.strip()
    if len(line) < 6:
        return False
    if line.startswith(("-", "•", "·", "✓", "●")):
        line = line[1:].strip()
    if _NON_FACT.match(line):
        return False
    return not (section_heading and _is_just_heading_repetition(line, section_heading))


def _is_just_heading_repetition(line: str, heading: str) -> bool:
    return line.strip().casefold().rstrip("s") == heading.strip().casefold().rstrip("s")


def _extract_fact_lines(
    doc: CareerDocument,
) -> list[tuple[str, str, int, str | None]]:
    """Yield ``(claim, section_name, position, section_heading)`` triples,
    skipping non-fact and duplicate lines."""
    seen: set[str] = set()
    out: list[tuple[str, str, int, str | None]] = []
    for sec in doc.sections:
        heading: str | None = sec.heading
        lines = [ln.strip() for ln in sec.text.splitlines() if ln.strip()] if sec.text else []
        for ln in lines:
            claim = ln[:_MAX_CLAIM_LENGTH]
            normalized = _normalize(claim)
            if normalized in seen:
                continue
            if not _is_fact_line(claim, heading):
                continue
            seen.add(normalized)
            out.append((claim, sec.section, sec.position, heading))
        if len(out) >= MAX_FACTS_PER_DOCUMENT:
            break
    return out[:MAX_FACTS_PER_DOCUMENT]


# ──────────────────────────── candidate derivation ────────────────────────────


def candidate_evidence_from_document(
    doc: CareerDocument,
    *,
    level: VerificationLevel = VerificationLevel.DOCUMENTED,
) -> list[CareerEvidence]:
    """Derive evidence candidates from a :class:`CareerDocument`.

    Every claim is deterministic, bounded, and attributed to the document's
    ``document_id`` (idempotent across re-runs for the same file).
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")
    out: list[CareerEvidence] = []
    for claim, section_name, position, section_heading in _extract_fact_lines(doc):
        eid = evidence_id(claim, doc.document_id)
        cats = set(classify_categories(claim))
        words = [w for w in re.findall(r"[a-zäöüßñ]{3,}", _normalize(claim)) if len(w) > 2]
        # Infer source location from section and position
        source_loc = f"document:{doc.document_id[:8]}:{section_name}:pos{position}"
        if section_heading:
            source_loc += f":heading={section_heading}"
        out.append(
            CareerEvidence(
                evidence_id=eid,
                claim=claim,
                level=level,
                source=doc.document_id,
                source_type="document",
                categories=list(cats),
                keywords=list(dict.fromkeys(words)),
                confidence=DEFAULT_EVIDENCE_CONFIDENCE,
                document_id=doc.document_id,
                normalized_fact=_normalize(claim),
                authority="cv_document",
                observed_at=now,
                source_location=source_loc,
                raw={
                    "section": section_name,
                    "heading": section_heading,
                    "position": position,
                    "filename": doc.filename,
                    "document_hash": doc.content_hash,
                },
            )
        )
    return out


# ──────────────────────────── reconciliation ──────────────────────────────────


class ReconciliationConflict(BaseModel):
    """An explicit, never-auto-resolved conflict between an existing higher-
    level claim and a new document-derived claim sharing a structured key."""

    document_id: str
    existing_evidence_id: str
    existing_claim: str
    document_claim: str
    company: str = ""
    role_title: str = ""
    note: str = ""


class ReconcileResult(BaseModel):
    """Deterministic, idempotent reconciliation output."""

    evidence: list[CareerEvidence] = Field(default_factory=list)
    new_ids: list[str] = Field(default_factory=list)
    kept_existing_ids: list[str] = Field(default_factory=list)
    exact_matches: list[tuple[str, str]] = Field(default_factory=list)
    conflicts: list[ReconciliationConflict] = Field(default_factory=list)
    conflict_count: int = 0
    new_count: int = 0
    kept_count: int = 0


def _index_by_normalized(
    evidence: list[CareerEvidence],
) -> dict[str, list[CareerEvidence]]:
    idx: dict[str, list[CareerEvidence]] = {}
    for ev in evidence:
        key = ev.normalized_fact or _normalize(ev.claim)
        idx.setdefault(key, []).append(ev)
    return idx


def _index_by_key(
    evidence: list[CareerEvidence],
) -> dict[tuple[str, str], list[CareerEvidence]]:
    idx: dict[tuple[str, str], list[CareerEvidence]] = {}
    for ev in evidence:
        skey = _structured_key(ev.claim)
        if skey is not None:
            idx.setdefault(skey, []).append(ev)
    return idx


def reconcile_document(
    doc: CareerDocument,
    candidates: list[CareerEvidence],
    existing_evidence: list[CareerEvidence],
) -> ReconcileResult:
    """Reconcile document-derived candidates against existing evidence.

    Deterministic, idempotent. Never promotes or demotes evidence levels.
    Conflicts are explicit, never auto-resolved.
    """
    existing_by_norm = _index_by_normalized(existing_evidence)
    existing_by_key = _index_by_key(existing_evidence)
    existing_ids = {e.evidence_id for e in existing_evidence}

    result = ReconcileResult()

    for cand in candidates:
        norm = cand.normalized_fact or _normalize(cand.claim)

        # Re-run idempotency: same claim + same document_id → same evidence_id
        if cand.evidence_id in existing_ids:
            result.kept_existing_ids.append(cand.evidence_id)
            result.kept_count += 1
            continue

        norm_matches = existing_by_norm.get(norm, [])
        doc_level = level_index(cand.level)
        higher = [m for m in norm_matches if level_index(m.level) >= doc_level]

        if higher:
            best = max(higher, key=lambda e: level_index(e.level))
            result.exact_matches.append((best.evidence_id, cand.claim))
            result.kept_existing_ids.append(best.evidence_id)
            result.kept_count += 1
            continue

        skey = _structured_key(cand.claim)
        if skey is not None:
            same_key = [
                ev
                for ev in existing_by_key.get(skey, [])
                if ev.evidence_id not in {c.evidence_id for c in result.evidence}
            ]
            for existing_ev in same_key:
                existing_norm = existing_ev.normalized_fact or _normalize(existing_ev.claim)
                if existing_norm != norm:
                    result.conflicts.append(
                        ReconciliationConflict(
                            document_id=doc.document_id,
                            existing_evidence_id=existing_ev.evidence_id,
                            existing_claim=existing_ev.claim,
                            document_claim=cand.claim,
                            company=skey[0],
                            role_title=skey[1],
                            note=(
                                "same structured key (company, role) with differing content; keeping existing record"
                            ),
                        )
                    )
                    result.conflict_count += 1
                    break

        if not any(
            c.existing_evidence_id == cand.evidence_id or c.document_claim == cand.claim for c in result.conflicts
        ):
            result.evidence.append(cand)
            result.new_ids.append(cand.evidence_id)
            result.new_count += 1

    result.evidence = result.evidence[:MAX_FACTS_PER_DOCUMENT]
    result.new_ids = result.new_ids[:MAX_FACTS_PER_DOCUMENT]
    return result


__all__ = [
    "DEFAULT_EVIDENCE_CONFIDENCE",
    "MAX_FACTS_PER_DOCUMENT",
    "ReconcileResult",
    "ReconciliationConflict",
    "candidate_evidence_from_document",
    "reconcile_document",
]
