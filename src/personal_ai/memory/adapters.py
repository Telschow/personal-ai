"""Slice 7: source adapters for durable full-corpus memory curation.

Curation adapters extend the Slice 6 runner (:mod:`personal_ai.memory.
curation`) to every already-ingested source without changing the write path:
each adapter discovers bounded, immutable :class:`CurationUnit` objects and
derives :class:`MemoryCandidate` instances that flow through the exact same
policy-gated curator as conversations (``MemoryPolicy`` →
``AutomaticMemoryCurator`` → ``propose_memory``  gate →
``MemoryService.apply_candidate``). Adapters never write SQLite and never
touch the memory tables directly.

Supported sources (priority order in the Slice 7 plan):

* **email** — corpus-level aggregation per normalized sender domain. Only
  recurring correspondents (enough emails across enough distinct months, and
  not a mass webmail provider) become units. Deterministic extraction proposes
  a recurring ``interest`` candidate from metadata alone (the domain only —
  never names, subjects, or bodies); LLM mode additionally shows the model a
  bounded window of representative emails and keeps its proposals behind the
  same deterministic guards.
* **financial** — scanned for counts only. Deterministic and LLM mode both
  propose zero candidates and never send financial content to the model.
  Sensitive financial information is never auto-remembered merely because it
  appears frequently.
* **document** (generic documents such as PDFs/notes, anything the document
  store holds that is not email/financial) — deterministic mode proposes
  nothing (metadata alone cannot safely assert facts); LLM mode shows the
  model bounded chunk windows of the document so that documents with strong
  personal-context signal can contribute proposals.
* **workout** — a single bounded unit reuses the deterministic, conservative
  ``extract_workout_routine`` (recurring ``habit`` only above session/month
  thresholds; never one memory per workout).
* **activity** — a single bounded unit reuses ``extract_activity_patterns``
  (recurring ``interest`` per normalized domain; never URLs, paths, or search
  queries).

Evidence stays provenance-only (stable identifiers and ISO timestamps), every
step is bounded (``max_records_per_source`` caps, ``max_messages``,
``max_prompt_chars``, ``max_candidates_per_unit``, ``max_retries``), the LLM
is a proposal generator only, and diagnostics remain aggregate-only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from personal_ai.documents.models import Document
from personal_ai.events.models import EVENT_TYPE_URL_VISIT, Event
from personal_ai.memory.conversations import _NEGATION, _SENSITIVE_FORM
from personal_ai.memory.corpus import (
    ACTIVITY,
    DEFAULT_MAX_RECORDS,
    EMAIL,
    FINANCIAL,
    MAX_EVIDENCE,
    WORKOUTS,
    extract_activity_patterns,
    extract_workout_routine,
)
from personal_ai.memory.curation import (
    CurationConfig,
    CurationExtractionMode,
    CurationUnit,
    UnitExtraction,
)
from personal_ai.memory.models import (
    AssertionStatus,
    MemoryCandidate,
    MemoryEvidenceRef,
    MemoryKind,
    TemporalScope,
)
from personal_ai.memory.proposals import (
    _SCORES,
    MAX_PROPOSALS_PER_BATCH,
    _apply_deterministic_overrides,
    _canonicalize_statement,
)
from personal_ai.memory.reconcile import normalize_text
from personal_ai.storage.chunks import ChunkStore
from personal_ai.storage.documents import DocumentStore
from personal_ai.storage.events import EventStore
from personal_ai.workouts.models import WorkoutSummary

# ---------------------------------------------------------------------------
# Source type constants and deterministic thresholds
# ---------------------------------------------------------------------------

GENERIC_DOCUMENT_SOURCE = "document"
CHROME_HISTORY_EVENT_SOURCE = "chrome_history"

_EMAIL_MIN_MESSAGES = 5
_EMAIL_MIN_DISTINCT_MONTHS = 2

# Mass webmail providers whose domains carry no durable information about a
# recurring correspondent ("someone at gmail.com" is noise, not memory).
_IGNORED_SENDER_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.de",
        "gmx.net",
        "gmx.com",
        "web.de",
        "t-online.de",
        "freenet.de",
        "mail.de",
        "mail.com",
        "fastmail.com",
        "zoho.com",
        "yandex.com",
        "yandex.ru",
    }
)

_MAX_EMAIL_DOCUMENTS = DEFAULT_MAX_RECORDS
_MAX_DOCUMENTS = DEFAULT_MAX_RECORDS
_MAX_WORKOUTS = DEFAULT_MAX_RECORDS
_MAX_EVENTS = DEFAULT_MAX_RECORDS
_BATCH = 500

DOCUMENT_EXTRACTOR_VERSION = "document-curation-v1"
WORKOUT_EXTRACTOR_VERSION = "workout-curation-v1"
ACTIVITY_EXTRACTOR_VERSION = "activity-curation-v1"


# ---------------------------------------------------------------------------
# Bounded units and contexts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EmailDomainContext:
    """Content-free aggregate context for one recurring email correspondent."""

    domain: str
    count: int
    months: int


@dataclass(frozen=True, slots=True)
class EmailWindow:
    """One bounded representative email shown to the model (or not at all)."""

    doc_id: str
    snippet: str
    iso_date: str


@dataclass(frozen=True, slots=True)
class FinancialCorpusContext:
    """Count-only context for the financial source (no content is read)."""

    count: int


@dataclass(frozen=True, slots=True)
class WorkoutCorpusContext:
    """Count-only context for the aggregated workout corpus unit."""

    count: int


@dataclass(frozen=True, slots=True)
class ActivityCorpusContext:
    """Count-only context for the aggregated activity corpus unit."""

    count: int


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_timestamp(value: object) -> str | None:
    """Return a valid ISO timestamp for provenance, or ``None``."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return None
    return text


def _iso_month(value: object) -> str | None:
    """Extract a ``YYYY-MM`` month key from an ISO-8601 timestamp."""
    text = str(value or "").strip()
    if len(text) >= 7 and text[:4].isdigit() and text[4] == "-" and text[5:7].isdigit():
        return text[:7]
    return None


# ---------------------------------------------------------------------------
# Email sender-domain extraction
# ---------------------------------------------------------------------------

_SENDER_DOMAIN_RE = re.compile(
    r"@([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)"
)


def _sender_domain(sender: object) -> str | None:
    """Return the normalized sender domain, or ``None``.

    Handles both bare addresses (``a@example.org``) and display-name forms
    (``Jane Doe <jane@example.org>``). Mass webmail providers are excluded.
    """
    text = str(sender or "").strip().lower()
    match = _SENDER_DOMAIN_RE.search(text)
    if match is None:
        return None
    domain = match.group(1)
    if domain in _IGNORED_SENDER_DOMAINS:
        return None
    return domain


def _domain_units(
    documents: Sequence[Document],
    config: CurationConfig,
    chunk_store: ChunkStore | None,
) -> tuple[CurationUnit, ...]:
    """Build one bounded unit per recurring sender domain, sorted by volume."""
    per_domain: dict[str, list[Document]] = {}
    for document in documents:
        domain = _sender_domain(document.metadata.get("sender"))
        if domain is not None:
            per_domain.setdefault(domain, []).append(document)

    stats: list[tuple[str, int, int, list[Document]]] = []
    for domain, docs in per_domain.items():
        ordered = sorted(docs, key=lambda d: (d.created_at, d.id))
        months = {
            month
            for month in (_iso_month(d.created_at) for d in ordered)
            if month is not None
        }
        count = len(ordered)
        if count < _EMAIL_MIN_MESSAGES or len(months) < _EMAIL_MIN_DISTINCT_MONTHS:
            continue
        representatives: list[Document] = []
        chosen: set[str] = set()
        for doc in ordered:
            month = _iso_month(doc.created_at)
            if month is not None and month not in chosen:
                representatives.append(doc)
                chosen.add(month)
        stats.append((domain, count, len(months), representatives))

    stats.sort(key=lambda item: (-item[1], item[0]))
    llm_requested = config.extraction is CurationExtractionMode.LLM
    units: list[CurationUnit] = []
    for index, (domain, count, months, representatives) in enumerate(
        stats[: config.limit]
    ):
        windows = _email_windows(
            representatives,
            chunk_store,
            with_content=llm_requested and chunk_store is not None,
            max_messages=config.max_messages,
            max_prompt_chars=config.max_prompt_chars,
        )
        version = _sha(
            "\x00".join(
                [domain, str(count), str(months)]
                + sorted(doc.id for doc in representatives)
            )
        )
        units.append(
            CurationUnit(
                unit_id=f"email-domain-{_sha(domain)[:12]}",
                index=index + 1,
                source_type=EMAIL,
                extraction=config.extraction,
                context=EmailDomainContext(domain=domain, count=count, months=months),
                records=windows,
                source_id=domain,
                source_version=version,
                signal=count,
                max_messages=config.max_messages,
                max_prompt_chars=config.max_prompt_chars,
                max_candidates_per_unit=config.max_candidates_per_unit,
                max_retries=config.max_retries,
            )
        )
    return tuple(units)


def _email_windows(
    representatives: Sequence[Document],
    chunk_store: ChunkStore | None,
    *,
    with_content: bool,
    max_messages: int,
    max_prompt_chars: int,
) -> tuple[EmailWindow, ...]:
    """Build bounded email windows (never full emails).

    With ``with_content=False`` (deterministic runs) the windows carry
    provenance only — doc id and ISO date — so deterministic curation never
    reads message bodies or subjects from chunk storage.
    """
    selected = representatives[:max_messages]
    budget = max_prompt_chars // max(1, len(selected)) if with_content else 0
    windows: list[EmailWindow] = []
    for doc in selected:
        snippet = ""
        if with_content and chunk_store is not None:
            subject = str(doc.metadata.get("subject") or "").strip()
            chunks = chunk_store.list_for_document(doc.id)
            body = " ".join(chunk.text for chunk in chunks)
            snippet = " ".join((subject + " " + body).split())[:budget]
        windows.append(
            EmailWindow(
                doc_id=doc.id,
                snippet=snippet,
                iso_date=doc.created_at or "",
            )
        )
    return tuple(windows)


def _email_evidence(
    windows: Sequence[EmailWindow], domain: str
) -> tuple[MemoryEvidenceRef, ...]:
    """Id-only evidence references, at most one per distinct month."""
    refs: list[MemoryEvidenceRef] = []
    seen_months: set[str] = set()
    for window in windows:
        month = _iso_month(window.iso_date)
        if month is not None and month in seen_months:
            continue
        if month is not None:
            seen_months.add(month)
        refs.append(
            MemoryEvidenceRef(
                source_type=EMAIL,
                source_id=domain,
                source_document_id=window.doc_id,
                source_timestamp=_safe_timestamp(window.iso_date),
            )
        )
        if len(refs) >= MAX_EVIDENCE:
            break
    return tuple(refs)


def _email_domain_candidate(
    context: EmailDomainContext, evidence: tuple[MemoryEvidenceRef, ...]
) -> MemoryCandidate:
    """Deterministic candidate for a recurring email correspondent."""
    return MemoryCandidate(
        statement=(
            f"The user regularly corresponds by email with contacts at "
            f"{context.domain}."
        ),
        kind=MemoryKind.INTEREST,
        confidence=min(
            0.85, 0.55 + 0.03 * context.months + 0.001 * min(context.count, 100)
        ),
        durability=min(0.75, 0.45 + 0.03 * context.months),
        relevance=0.7,
        specificity=0.6,
        recurrence=context.months,
        utility=0.5,
        temporal_scope=TemporalScope.RECURRING,
        assertion_status=AssertionStatus.ASSERTED,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Bounded LLM document proposals
# ---------------------------------------------------------------------------

DOCUMENT_PROPOSAL_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "kind": {"type": "string"},
                    "temporal_scope": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence_document_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "rationale": {"type": "string"},
                },
                "required": [
                    "statement",
                    "kind",
                    "temporal_scope",
                    "confidence",
                    "evidence_document_ids",
                ],
            },
        }
    },
    "required": ["proposals"],
}

DOCUMENT_PROPOSAL_PROMPT = """\
You propose durable facts a personal AI should remember about its owner,
from ONE bounded document window (labeled by stable document ids).

Rules:
- Propose ONLY durable, likely-long-term facts about the document owner.
  Never propose facts about third parties, companies, or the documents
  themselves.
- Statement format: third person, starting with "The user", e.g.
  "The user works at BCG as a product manager."
- temporal_scope: "current" (still true today), "historical" (past),
  "recurring" (happens regularly), or "unknown" when unsure.
- kind: one of work, education, goal, project_context, identity,
  relationship, location_context, habit, routine, interest, skill,
  preference, decision, personal_fact, important_event, long_term_context.
- confidence: a number 0.0-1.0 for how strongly the window supports the fact.
- evidence_document_ids: the ids of the documents that support this fact
  (at least one). They must match the ids shown in brackets, e.g.
  "[docs/0001.pdf]".
- Emit at most 5 proposals. Prefer fewer high-value facts over many.
- Never propose statements containing emails, URLs, currency amounts,
  account or credential-shaped strings, secrets, phone numbers, addresses,
  salaries, medical or financial details.
Respond with ONLY a JSON object of the form:
{"proposals": [{"statement": str, "kind": str, "temporal_scope": str,
"confidence": number, "evidence_document_ids": [str], "rationale": str}]}
No prose, no markdown fences.
"""

DOCUMENT_PROPOSAL_PROMPT_VERSION = _sha(DOCUMENT_PROPOSAL_PROMPT)[:12]

_DOCUMENT_PROPOSAL_FIELDS = {
    "statement",
    "kind",
    "temporal_scope",
    "confidence",
    "evidence_document_ids",
    "rationale",
}


class DocumentProposalError(ValueError):
    """Invalid or dropped document proposal."""


class MalformedDocumentProposalError(ValueError):
    """Malformed model output for a document proposal batch."""


@dataclass(frozen=True, slots=True)
class DocumentProposal:
    """One strictly-validated document proposal (reference ids only)."""

    statement: str
    kind: str
    temporal_scope: str
    confidence: float
    evidence_document_ids: tuple[str, ...]
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_document_ids:
            raise DocumentProposalError("evidence_document_ids must not be empty")
        if len(self.evidence_document_ids) > MAX_EVIDENCE:
            raise DocumentProposalError(
                f"no more than {MAX_EVIDENCE} evidence document ids"
            )
        if len(set(self.evidence_document_ids)) != len(self.evidence_document_ids):
            raise DocumentProposalError("evidence_document_ids must be unique")
        if any(not doc_id for doc_id in self.evidence_document_ids):
            raise DocumentProposalError("evidence_document_ids must be non-empty")


@dataclass(frozen=True, slots=True)
class DocumentProposalBatch:
    """Strictly parsed batch; invalid items are dropped and counted."""

    proposals: tuple[DocumentProposal, ...]
    dropped: int

    def __post_init__(self) -> None:
        if len(self.proposals) > MAX_PROPOSALS_PER_BATCH:
            raise MalformedDocumentProposalError(
                f"batch exceeds {MAX_PROPOSALS_PER_BATCH} proposals"
            )


def _strip_json_fences(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip().startswith("```"):
        lines.pop()
    return "\n".join(lines)


def parse_document_proposal_batch(content: str) -> DocumentProposalBatch:
    """Parse and strictly validate one model response for a document window."""
    if not isinstance(content, str):
        raise MalformedDocumentProposalError("model output must be a string")
    try:
        data = json.loads(_strip_json_fences(content))
    except ValueError as exc:
        raise MalformedDocumentProposalError("model returned non-JSON output") from exc
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        raise MalformedDocumentProposalError(
            "model output must be an object with a 'proposals' array"
        )
    proposals: list[DocumentProposal] = []
    dropped = 0
    for index, raw in enumerate(data["proposals"]):
        try:
            proposals.append(_parse_document_proposal(raw, index))
        except (DocumentProposalError, TypeError) as exc:
            dropped += 1
            del exc
    return DocumentProposalBatch(proposals=tuple(proposals), dropped=dropped)


def _parse_document_proposal(raw: object, index: int) -> DocumentProposal:
    if not isinstance(raw, dict):
        raise TypeError(f"proposal {index} is not an object")
    unknown = set(raw) - _DOCUMENT_PROPOSAL_FIELDS
    if unknown:
        raise DocumentProposalError(f"proposal {index} has unknown fields")
    statement = raw.get("statement")
    kind = raw.get("kind")
    temporal = raw.get("temporal_scope")
    confidence = raw.get("confidence")
    evidence = raw.get("evidence_document_ids")
    rationale = raw.get("rationale", "")
    if not isinstance(statement, str):
        raise TypeError(f"proposal {index}: statement must be a string")
    if not isinstance(kind, str):
        raise TypeError(f"proposal {index}: kind must be a string")
    if not isinstance(temporal, str):
        raise TypeError(f"proposal {index}: temporal_scope must be a string")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise TypeError(f"proposal {index}: confidence must be a number")
    if not isinstance(evidence, list) or not all(
        isinstance(item, str) for item in evidence
    ):
        raise TypeError(
            f"proposal {index}: evidence_document_ids must be a string list"
        )
    if not isinstance(rationale, str):
        raise TypeError(f"proposal {index}: rationale must be a string")
    return DocumentProposal(
        statement=statement,
        kind=kind,
        temporal_scope=temporal,
        confidence=float(confidence),
        evidence_document_ids=tuple(evidence),
        rationale=rationale,
    )


def to_document_candidate(
    proposal: DocumentProposal,
    *,
    source_type: str,
    source_id: str,
    allowed_ids: frozenset[str],
    id_timestamps: Mapping[str, str],
) -> tuple[MemoryCandidate | None, str]:
    """Convert one validated proposal into a provenanced candidate.

    The referenced document ids must exist in the bounded window. Statements
    must canonicalize to a "The user ..." form; sensitive forms, questions,
    and negations are dropped deterministically.
    """
    if not proposal.evidence_document_ids:
        return None, "evidence_missing"
    if any(doc_id not in allowed_ids for doc_id in proposal.evidence_document_ids):
        return None, "evidence_unknown"

    statement = _canonicalize_statement(proposal.statement)
    if statement is None:
        return None, "not_user_statement"
    if _SENSITIVE_FORM.search(statement):
        return None, "sensitive_form"
    if statement.endswith("?"):
        return None, "question"
    if _NEGATION.search(statement):
        return None, "negated"

    try:
        kind = (
            proposal.kind
            if isinstance(proposal.kind, MemoryKind)
            else MemoryKind(proposal.kind)
        )
        temporal_scope = (
            proposal.temporal_scope
            if isinstance(proposal.temporal_scope, TemporalScope)
            else TemporalScope(proposal.temporal_scope)
        )
    except ValueError:
        return None, "unknown_label"
    if temporal_scope is TemporalScope.UNKNOWN:
        return None, "unknown_temporal"

    statement, kind, temporal_scope = _apply_deterministic_overrides(
        statement, kind, temporal_scope
    )

    durability, relevance, specificity, utility = _SCORES[kind]
    recurrence = 2 if temporal_scope is TemporalScope.RECURRING else 1
    evidence = tuple(
        MemoryEvidenceRef(
            source_type=source_type,
            source_id=source_id,
            source_document_id=doc_id,
            source_timestamp=_safe_timestamp(id_timestamps.get(doc_id)),
        )
        for doc_id in proposal.evidence_document_ids
    )
    candidate = MemoryCandidate(
        statement=statement.strip(),
        kind=kind,
        confidence=proposal.confidence,
        durability=durability,
        relevance=relevance,
        specificity=specificity,
        recurrence=recurrence,
        utility=utility,
        temporal_scope=temporal_scope,
        assertion_status=AssertionStatus.ASSERTED,
        evidence=evidence,
    )
    return candidate, None


@dataclass(frozen=True, slots=True)
class DocumentUnitResult:
    """Outcome of extracting proposals from one bounded document unit."""

    candidates: tuple[MemoryCandidate, ...]
    records_scanned: int
    model_calls: int
    proposals_parsed: int
    proposals_dropped: int
    failed: bool = False
    failure_reason: str = ""


class DocumentProposalExtractor:
    """Runs bounded LLM proposal extraction over one document window.

    Mirrors the conversation proposal extractor's strict contract: one bounded
    window per model call, malformed output retries at most ``max_retries``
    times, invalid items are dropped, and a persistent failure yields zero
    candidates for that unit.
    """

    def __init__(
        self,
        client: object,
        *,
        max_messages: int,
        max_prompt_chars: int,
        max_candidates_per_unit: int,
        max_retries: int,
    ) -> None:
        self._client = client
        self._max_messages = max_messages
        self._max_prompt_chars = max_prompt_chars
        self._max_candidates = max_candidates_per_unit
        self._max_retries = max_retries

    def extract(
        self,
        *,
        source_type: str,
        source_id: str,
        allowed_ids: frozenset[str],
        id_timestamps: Mapping[str, str],
        lines: Sequence[str],
        header: str,
    ) -> DocumentUnitResult:
        from personal_ai.ollama_client import ChatMessage, OllamaError

        bounded, used = [], 0
        for line in lines:
            line = " ".join(line.split())[: self._max_prompt_chars]
            if used + len(line) + 1 > self._max_prompt_chars:
                break
            bounded.append(line)
            used += len(line) + 1
        if not bounded:
            return DocumentUnitResult(
                candidates=(),
                records_scanned=len(lines),
                model_calls=0,
                proposals_parsed=0,
                proposals_dropped=0,
            )

        prompt = (
            f"{header}\n\nDocument window (stable ids in brackets):\n"
            f"{chr(10).join(bounded)}\n\n"
            "Output ONLY the JSON object according to the system instructions."
        )
        model_calls = 0
        failure_reason = ""
        for _attempt in range(self._max_retries + 1):
            model_calls += 1
            try:
                response = self._client.chat(
                    [
                        ChatMessage(role="system", content=DOCUMENT_PROPOSAL_PROMPT),
                        ChatMessage(role="user", content=prompt),
                    ],
                    think=False,
                    format=DOCUMENT_PROPOSAL_SCHEMA,
                )
                batch = parse_document_proposal_batch(response.content)
            except OllamaError as exc:
                failure_reason = "ollama_error"
                del exc
                continue
            except MalformedDocumentProposalError as exc:
                failure_reason = "malformed_output"
                del exc
                continue
            return self._build_result(
                batch,
                source_type=source_type,
                source_id=source_id,
                allowed_ids=allowed_ids,
                id_timestamps=id_timestamps,
                records_scanned=len(lines),
                model_calls=model_calls,
            )
        return DocumentUnitResult(
            candidates=(),
            records_scanned=len(lines),
            model_calls=model_calls,
            proposals_parsed=0,
            proposals_dropped=0,
            failed=True,
            failure_reason=failure_reason or "model_error",
        )

    def _build_result(
        self,
        batch: DocumentProposalBatch,
        *,
        source_type: str,
        source_id: str,
        allowed_ids: frozenset[str],
        id_timestamps: Mapping[str, str],
        records_scanned: int,
        model_calls: int,
    ) -> DocumentUnitResult:
        candidates: list[MemoryCandidate] = []
        seen: set[tuple[str, str]] = set()
        for proposal in batch.proposals:
            candidate, _reason = to_document_candidate(
                proposal,
                source_type=source_type,
                source_id=source_id,
                allowed_ids=allowed_ids,
                id_timestamps=id_timestamps,
            )
            if candidate is None:
                continue
            key = (candidate.kind.value, normalize_text(candidate.statement))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
        return DocumentUnitResult(
            candidates=tuple(candidates[: self._max_candidates]),
            records_scanned=records_scanned,
            model_calls=model_calls,
            proposals_parsed=len(batch.proposals),
            proposals_dropped=batch.dropped,
        )


# ---------------------------------------------------------------------------
# Document curation adapter (email / financial / generic documents)
# ---------------------------------------------------------------------------


class DocumentCurationAdapter:
    """Source adapter over the document store (email, financial, documents).

    Deterministic extraction works from bounded metadata reads alone; LLM
    extraction requires a model client *and* chunk text and is used only for
    the sources that can contribute bounded windows (email, generic
    documents). Financial content is never sent to the model.
    """

    def __init__(
        self,
        document_store: DocumentStore,
        chunk_store: ChunkStore | None = None,
        *,
        client: object | None = None,
    ) -> None:
        self._documents = document_store
        self._chunks = chunk_store
        self._client = client
        self.llm_enabled = client is not None and chunk_store is not None

    def supports(self, source_type: str) -> bool:
        return source_type in {EMAIL, FINANCIAL, GENERIC_DOCUMENT_SOURCE}

    def version(self, extraction: CurationExtractionMode) -> tuple[str, str]:
        if extraction is CurationExtractionMode.LLM:
            return DOCUMENT_EXTRACTOR_VERSION, DOCUMENT_PROPOSAL_PROMPT_VERSION
        return DOCUMENT_EXTRACTOR_VERSION, ""

    def discover(self, config: CurationConfig) -> tuple[CurationUnit, ...]:
        if config.source_type == EMAIL:
            return self._discover_email(config)
        if config.source_type == FINANCIAL:
            return self._discover_financial(config)
        return self._discover_documents(config)

    # ---- email -----------------------------------------------------------

    def _discover_email(self, config: CurationConfig) -> tuple[CurationUnit, ...]:
        documents = self._page_documents(source_type=EMAIL, limit=_MAX_EMAIL_DOCUMENTS)
        return _domain_units(documents, config, self._chunks)

    def _process_email(self, unit: CurationUnit) -> UnitExtraction:
        assert isinstance(unit.context, EmailDomainContext)
        windows = tuple(unit.records)  # type: ignore[arg-type]
        evidence = _email_evidence(windows, unit.context.domain)
        candidates: list[MemoryCandidate] = [
            _email_domain_candidate(unit.context, evidence)
        ]
        proposals_parsed = 0
        proposals_dropped = 0
        model_calls = 0
        failed = False
        failure_reason = ""
        if (
            unit.extraction is CurationExtractionMode.LLM
            and self._client is not None
            and self._chunks is not None
            and windows
        ):
            extractor = DocumentProposalExtractor(
                self._client,
                max_messages=unit.max_messages,
                max_prompt_chars=unit.max_prompt_chars,
                max_candidates_per_unit=unit.max_candidates_per_unit,
                max_retries=unit.max_retries,
            )
            result = extractor.extract(
                source_type=EMAIL,
                source_id=unit.context.domain,
                allowed_ids=frozenset(window.doc_id for window in windows),
                id_timestamps={window.doc_id: window.iso_date for window in windows},
                lines=[f"[{window.doc_id}] {window.snippet}" for window in windows],
                header=(
                    f"Recurring email correspondence with contacts at "
                    f"{unit.context.domain} "
                    f"({unit.context.count} emails across "
                    f"{unit.context.months} months)."
                ),
            )
            candidates.extend(result.candidates)
            proposals_parsed = result.proposals_parsed
            proposals_dropped = result.proposals_dropped
            model_calls = result.model_calls
            failed = result.failed
            failure_reason = result.failure_reason
        if failed:
            return UnitExtraction(
                candidates=(),
                messages_scanned=unit.context.count,
                model_calls=model_calls,
                failed=True,
                failure_reason=failure_reason or "model_error",
            )
        return UnitExtraction(
            candidates=tuple(candidates),
            messages_scanned=unit.context.count,
            model_calls=model_calls,
            proposals_parsed=proposals_parsed,
            proposals_dropped=proposals_dropped,
        )

    # ---- financial (counts only, never content) ---------------------------

    def _discover_financial(self, config: CurationConfig) -> tuple[CurationUnit, ...]:
        count = 0
        offset = 0
        while count < _MAX_DOCUMENTS:
            batch = self._documents.list_documents(
                source_type=FINANCIAL, limit=_BATCH, offset=offset
            )
            if not batch:
                break
            count += len(batch)
            if len(batch) < _BATCH:
                break
            offset += len(batch)
        return (
            CurationUnit(
                unit_id="financial-corpus-v1",
                index=1,
                source_type=FINANCIAL,
                extraction=config.extraction,
                context=FinancialCorpusContext(count=count),
                records=(),
                source_id="financial-corpus",
                source_version="financial-corpus-v1",
                signal=0,
                max_messages=config.max_messages,
                max_prompt_chars=config.max_prompt_chars,
                max_candidates_per_unit=config.max_candidates_per_unit,
                max_retries=config.max_retries,
            ),
        )

    def _process_financial(self, unit: CurationUnit) -> UnitExtraction:
        assert isinstance(unit.context, FinancialCorpusContext)
        return UnitExtraction(
            candidates=(),
            messages_scanned=unit.context.count,
            model_calls=0,
        )

    # ---- generic documents ------------------------------------------------

    def _discover_documents(self, config: CurationConfig) -> tuple[CurationUnit, ...]:
        units: list[CurationUnit] = []
        llm_requested = config.extraction is CurationExtractionMode.LLM
        offset = config.offset
        while len(units) < config.limit:
            batch = self._documents.list_documents(
                limit=config.batch_size, offset=offset
            )
            if not batch:
                break
            for document in batch:
                if document.source_type in {EMAIL, FINANCIAL}:
                    continue
                records = (
                    self._bounded_chunk_text(document, config) if llm_requested else ()
                )
                signal = sum(len(record) for record in records)
                units.append(
                    CurationUnit(
                        unit_id=f"doc:{document.id}",
                        index=len(units) + 1,
                        source_type=GENERIC_DOCUMENT_SOURCE,
                        extraction=config.extraction,
                        context=document,
                        records=records,
                        source_id=document.id,
                        source_version=document.content_hash,
                        signal=signal,
                        max_messages=config.max_messages,
                        max_prompt_chars=config.max_prompt_chars,
                        max_candidates_per_unit=config.max_candidates_per_unit,
                        max_retries=config.max_retries,
                    )
                )
                if len(units) >= config.limit:
                    break
            if len(batch) < config.batch_size:
                break
            offset += len(batch)
        return tuple(units)

    def _bounded_chunk_text(
        self, document: Document, config: CurationConfig
    ) -> tuple[str, ...]:
        if self._chunks is None:
            return ()
        chunks = self._chunks.list_for_document(document.id)[: config.max_messages]
        budget = max(1, config.max_prompt_chars // max(1, len(chunks)))
        records: list[str] = []
        for chunk in chunks:
            text = " ".join(chunk.text.split())[:budget]
            if text:
                records.append(text)
        return tuple(records)

    def _process_document(self, unit: CurationUnit) -> UnitExtraction:
        assert isinstance(unit.context, Document)
        document = unit.context
        records_scanned = len(unit.records)
        if (
            unit.extraction is CurationExtractionMode.LLM
            and self._client is not None
            and unit.records
        ):
            extractor = DocumentProposalExtractor(
                self._client,
                max_messages=unit.max_messages,
                max_prompt_chars=unit.max_prompt_chars,
                max_candidates_per_unit=unit.max_candidates_per_unit,
                max_retries=unit.max_retries,
            )
            result = extractor.extract(
                source_type=document.source_type,
                source_id=document.id,
                allowed_ids=frozenset({document.id}),
                id_timestamps={document.id: document.created_at or ""},
                lines=[f"[{document.id}] {record}" for record in unit.records],
                header=f"Document {document.id} (source: {document.source_type}).",
            )
            return UnitExtraction(
                candidates=result.candidates,
                messages_scanned=records_scanned,
                model_calls=result.model_calls,
                proposals_parsed=result.proposals_parsed,
                proposals_dropped=result.proposals_dropped,
                failed=result.failed,
                failure_reason=result.failure_reason,
            )
        return UnitExtraction(
            candidates=(),
            messages_scanned=records_scanned,
            model_calls=0,
        )

    # ---- shared paging -----------------------------------------------------

    def _page_documents(self, *, source_type: str, limit: int) -> list[Document]:
        documents: list[Document] = []
        offset = 0
        while len(documents) < limit:
            batch = self._documents.list_documents(
                source_type=source_type, limit=_BATCH, offset=offset
            )
            if not batch:
                break
            documents.extend(batch)
            if len(batch) < _BATCH:
                break
            offset += len(batch)
        return documents[:limit]

    # ---- dispatch -----------------------------------------------------------

    def process(self, unit: CurationUnit) -> UnitExtraction:
        if unit.source_type == EMAIL:
            return self._process_email(unit)
        if unit.source_type == FINANCIAL:
            return self._process_financial(unit)
        return self._process_document(unit)


# ---------------------------------------------------------------------------
# Workout curation adapter (deterministic, reuse corpus extractor)
# ---------------------------------------------------------------------------


class WorkoutQuerySource(Protocol):  # pragma: no cover
    """Narrow read-only surface over the workout store."""

    def list_workouts(self, *, limit: int | None = None) -> list[WorkoutSummary]: ...


class WorkoutCurationAdapter:
    """Deterministic-only adapter reusing the corpus routine extractor.

    A single bounded unit aggregates the workout corpus; the conserved slice
    thresholds (minimum sessions across minimum distinct months) decide
    whether a generic recurring ``habit`` candidate is proposed. Workout
    provenance is behavioral and the policy defers it — the point is that
    evidence accumulates through the same idempotent pipeline.
    """

    def __init__(self, query: WorkoutQuerySource) -> None:
        self._query = query
        self.llm_enabled = False

    def supports(self, source_type: str) -> bool:
        return source_type == WORKOUTS

    def version(self, extraction: CurationExtractionMode) -> tuple[str, str]:
        return WORKOUT_EXTRACTOR_VERSION, ""

    def discover(self, config: CurationConfig) -> tuple[CurationUnit, ...]:
        records = self._query.list_workouts(limit=_MAX_WORKOUTS) or []
        return (
            CurationUnit(
                unit_id="workout-corpus-v1",
                index=1,
                source_type=WORKOUTS,
                extraction=config.extraction,
                context=WorkoutCorpusContext(count=len(records)),
                records=tuple(records),
                source_id="workout-corpus",
                source_version="workout-corpus-v1",
                signal=len(records),
                max_messages=config.max_messages,
                max_prompt_chars=config.max_prompt_chars,
                max_candidates_per_unit=config.max_candidates_per_unit,
                max_retries=config.max_retries,
            ),
        )

    def process(self, unit: CurationUnit) -> UnitExtraction:
        assert isinstance(unit.context, WorkoutCorpusContext)
        candidates = extract_workout_routine(list(unit.records))  # type: ignore[arg-type]
        return UnitExtraction(
            candidates=candidates,
            messages_scanned=unit.context.count,
            model_calls=0,
        )


# ---------------------------------------------------------------------------
# Activity curation adapter (deterministic, reuse domain aggregate rules)
# ---------------------------------------------------------------------------


class EventCurationAdapter:
    """Deterministic-only adapter reusing the corpus domain aggregate rules.

    A single bounded unit aggregates URL-visit events; only normalized domains
    with sustained, repeated visits (and no obviously sensitive/operational
    name) can yield ``interest`` candidates. Never a URL, path, search query,
    or account identifier.
    """

    def __init__(self, event_store: EventStore) -> None:
        self._events = event_store
        self.llm_enabled = False

    def supports(self, source_type: str) -> bool:
        return source_type in {ACTIVITY, CHROME_HISTORY_EVENT_SOURCE}

    def version(self, extraction: CurationExtractionMode) -> tuple[str, str]:
        return ACTIVITY_EXTRACTOR_VERSION, ""

    def discover(self, config: CurationConfig) -> tuple[CurationUnit, ...]:
        records = tuple(
            self._events.list_events(
                source=CHROME_HISTORY_EVENT_SOURCE,
                event_type=EVENT_TYPE_URL_VISIT,
                limit=_MAX_EVENTS,
                offset=config.offset,
            )
        )
        context = ActivityCorpusContext(count=len(records))
        return (
            CurationUnit(
                unit_id="activity-corpus-v1",
                index=1,
                source_type=ACTIVITY,
                extraction=config.extraction,
                context=context,
                records=records,
                source_id="activity-corpus",
                source_version="activity-corpus-v1",
                signal=len(records),
                max_messages=config.max_messages,
                max_prompt_chars=config.max_prompt_chars,
                max_candidates_per_unit=config.max_candidates_per_unit,
                max_retries=config.max_retries,
            ),
        )

    def process(self, unit: CurationUnit) -> UnitExtraction:
        assert isinstance(unit.context, ActivityCorpusContext)
        events = tuple(event for event in unit.records if isinstance(event, Event))
        candidates = extract_activity_patterns(
            list(events), max_candidates=unit.max_candidates_per_unit
        )
        return UnitExtraction(
            candidates=candidates,
            messages_scanned=unit.context.count,
            model_calls=0,
        )


__all__: tuple[str, ...] = (
    "ACTIVITY_EXTRACTOR_VERSION",
    "CHROME_HISTORY_EVENT_SOURCE",
    "DOCUMENT_EXTRACTOR_VERSION",
    "DOCUMENT_PROPOSAL_PROMPT",
    "DOCUMENT_PROPOSAL_PROMPT_VERSION",
    "DOCUMENT_PROPOSAL_SCHEMA",
    "GENERIC_DOCUMENT_SOURCE",
    "WORKOUT_EXTRACTOR_VERSION",
    "ActivityCorpusContext",
    "DocumentCurationAdapter",
    "DocumentProposal",
    "DocumentProposalBatch",
    "DocumentProposalError",
    "DocumentProposalExtractor",
    "DocumentUnitResult",
    "EmailDomainContext",
    "EmailWindow",
    "EventCurationAdapter",
    "FinancialCorpusContext",
    "MalformedDocumentProposalError",
    "WorkoutCorpusContext",
    "WorkoutCurationAdapter",
    "to_document_candidate",
)
