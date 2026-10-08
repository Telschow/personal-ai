"""Bounded LLM-assisted memory proposal extraction.

The LLM is a **candidate proposal generator only**. It may propose a
candidate statement, a memory kind, a temporal scope, an approximate
confidence, and the message identifiers it is using as evidence. It never
decides acceptance, rejection, deferral, approval requirements, sensitivity,
or conflicts — those decisions belong to the deterministic application code.

Data flow (identical write path to every other memory, one path only):

    bounded source window
        -> LLM proposal (strict JSON contract)
        -> validated MemoryProposal
        -> application-side MemoryCandidate construction
        -> existing MemoryPolicy
        -> existing AutomaticMemoryCurator
        -> PolicyEngine "propose_memory" gate
        -> MemoryService.apply_candidate
        -> reconciliation -> memory + memory_evidence + audit

The model cannot write, cannot bypass :class:`~personal_ai.agents.policy.
PolicyEngine`, cannot see the whole corpus at once (one bounded conversation
window per model call), and its output is strictly validated and bounded —
malformed structures fail the whole batch, invalid items are dropped, and a
failed model call yields zero candidates from that batch. Diagnostics are
aggregate-only; prompts and responses are never logged and candidate
statements never appear in reports.

Evidence stays provenance-only: the model may only reference message ids that
exist in the bounded window with which it was presented, and at least one
referenced message must be user-authored. The application maps those ids to
real :class:`MemoryEvidenceRef` instances.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from personal_ai.documents.conversations import (
    Conversation,
    ConversationMessage,
)
from personal_ai.memory.conversations import (
    _MODEL_DIRECT_PATTERNS,
    _NEGATION_PATTERNS,
    _RECURRING_PATTERNS,
    _REQUEST_STARTER_PATTERNS,
    _SENSITIVE_FORM,
    _YOU_TOWARD_PATTERNS,
    CONVERSATION_SOURCE_TYPES,
    MAX_STATEMENT_CHARS,
    ConversationLanguage,
    ConversationSourceError,
    _canonical_statement,
    _contains_quotation,
    _detect_language,
)
from personal_ai.memory.corpus import OutcomeTally
from personal_ai.memory.models import (
    AssertionStatus,
    MemoryCandidate,
    MemoryEvidenceRef,
    MemoryKind,
    TemporalScope,
)
from personal_ai.memory.policy import MemoryPolicy
from personal_ai.memory.reconcile import normalize_text
from personal_ai.storage.conversations import ConversationStore

# --- hard bounds (enforced in application code, never by prompt alone) -------
MAX_EVIDENCE_PER_PROPOSAL = 5
MAX_PROPOSALS_PER_BATCH = 5
MAX_RATIONALE_CHARS = 200
MAX_PROMPT_CHARS = 8_000
MAX_PROMPT_LINE_CHARS = 600
DEFAULT_MAX_MESSAGES = 40
DEFAULT_MAX_CONVERSATIONS = 10_000
DEFAULT_MAX_CANDIDATES_PER_UNIT = 5
DEFAULT_MAX_RETRIES = 1
DEFAULT_BATCH_SIZE = 500

# The kinds the LLM may propose for curated personal memory. Parsing accepts
# any existing ``MemoryKind`` member; this is the prompt-facing subset.
_PROPOSABLE_KINDS: tuple[str, ...] = (
    "identity",
    "biography",
    "relationship",
    "goal",
    "habit",
    "routine",
    "interest",
    "skill",
    "work",
    "education",
    "location_context",
    "communication_preference",
    "long_term_context",
    "personal_fact",
    "preference",
    "fact",
    "project_context",
    "important_event",
)

# --- deterministic overrides (application authority over temporal reasoning) -
_GOAL_TRIGGER = re.compile(
    r"\bthe user (?:wants|hopes|plans|aims|aspires|would like|is working toward|"
    r"works toward|is trying|is learning)"
    r"(?:\s+to)?\b|\b(?:goal|plan|ambition|target) (?:is|was)\b",
    re.IGNORECASE,
)
_RECURRING_TRIGGER = re.compile(
    r"\bevery (?:morning|evening|afternoon|day|week|weekend|night|month)\b"
    r"|\b(?:daily|weekly|monthly|regularly)\b",
    re.IGNORECASE,
)
_PAST_TRIGGER = re.compile(
    r"\bthe user (?:used\s+to|no\s+longer|previously|formerly|worked|studied|"
    r"graduated|lived|moved|was|were|had|learned|learnt)\b",
    re.IGNORECASE,
)

# Per-language trigger tables. The English (EN) entries ARE the original
# single-language regexes above — English behavior is byte-identical; DE/ES
# cover the same deterministic semantics for third-person canonical forms.
_LANG_GOAL_TRIGGERS: dict[ConversationLanguage, re.Pattern[str]] = {
    ConversationLanguage.EN: _GOAL_TRIGGER,
    ConversationLanguage.DE: re.compile(
        r"\bder nutzer (?:möchte|will|plane|plant|beabsichtigt|hat vor|"
        r"würde gerne|würde gern)\b"
        r"|\b(?:ziel|traum|plan|vorsatz|ambition|wunsch) (?:ist|war)\b",
        re.IGNORECASE,
    ),
    ConversationLanguage.ES: re.compile(
        r"\bel usuario (?:quiere|espera|planea|tiene la intención de|"
        r"tiene el objetivo de|le gustaría|está planeando)\b"
        r"|\b(?:meta|sueño|plan|objetivo|ambición) (?:es|era)\b",
        re.IGNORECASE,
    ),
}
_LANG_RECURRING_TRIGGERS: dict[ConversationLanguage, re.Pattern[str]] = {
    ConversationLanguage.EN: _RECURRING_TRIGGER,
    ConversationLanguage.DE: _RECURRING_PATTERNS[ConversationLanguage.DE],
    ConversationLanguage.ES: _RECURRING_PATTERNS[ConversationLanguage.ES],
}
_LANG_PAST_TRIGGERS: dict[ConversationLanguage, re.Pattern[str]] = {
    ConversationLanguage.EN: _PAST_TRIGGER,
    ConversationLanguage.DE: re.compile(
        r"\bder nutzer (?:wohnte|lebte|arbeitete|studierte|lernte|sprach|"
        r"hatte|war|zog|lief|joggte|trainierte|schwamm|hat (?:früher|damals))"
        r"\b|\b(?:früher|damals|ehemals|vor jahren|in der vergangenheit)\b",
        re.IGNORECASE,
    ),
    ConversationLanguage.ES: re.compile(
        r"\bel usuario (?:vivía|trabajaba|estudiaba|era|tenía|hablaba|"
        r"aprendía|estudió|trabajó|vivió|nació|solía|empezó)\b"
        r"|\b(?:antes|en el pasado|hace años|anteriormente)\b",
        re.IGNORECASE,
    ),
}

# Third-person user-reference markers per language, used to reject statements
# about arbitrary third parties (the model may never invent user facts).
_LANGUAGE_USER_MARKERS: dict[ConversationLanguage, str] = {
    ConversationLanguage.DE: "der nutzer",
    ConversationLanguage.ES: "el usuario",
    ConversationLanguage.EN: "the user",
}

# --- errors ------------------------------------------------------------------


class MemoryProposalError(ValueError):
    """A model proposal violates the strict output contract."""


class MalformedMemoryProposalError(ValueError):
    """Model output is structurally invalid; the whole batch is discarded."""


# --- strict model-output contract --------------------------------------------
#
# Deliberately smaller than ``MemoryCandidate``: durability/relevance/
# specificity/utility/recurrence/assertion_status are computed by the
# application; the model only proposes statement/kind/temporal/confidence and
# the evidence message ids it saw.


@dataclass(frozen=True, slots=True)
class MemoryProposal:
    """One validated proposal parsed from model output.

    ``evidence_message_ids`` must reference messages in the bounded window the
    model was shown. ``rationale`` is bounded and is never persisted or logged.
    """

    statement: str
    kind: MemoryKind | str
    temporal_scope: TemporalScope | str
    confidence: float
    evidence_message_ids: tuple[str, ...]
    rationale: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "statement", self.statement.strip())
        object.__setattr__(self, "kind", _coerce_kind(self.kind))
        object.__setattr__(
            self, "temporal_scope", _coerce_temporal(self.temporal_scope)
        )
        object.__setattr__(
            self, "evidence_message_ids", tuple(self.evidence_message_ids)
        )
        object.__setattr__(self, "rationale", self.rationale.strip())
        self.validate()

    def validate(self) -> None:
        if not self.statement:
            raise MemoryProposalError("proposal statement must not be empty")
        if len(self.statement) > MAX_STATEMENT_CHARS:
            raise MemoryProposalError(
                f"proposal statement exceeds {MAX_STATEMENT_CHARS} characters"
            )
        if self.temporal_scope is TemporalScope.UNKNOWN:
            raise MemoryProposalError(
                "temporal_scope must be current, historical, or recurring"
            )
        if isinstance(self.confidence, bool) or not math.isfinite(self.confidence):
            raise MemoryProposalError("proposal confidence must be a finite number")
        if not (0.0 <= self.confidence <= 1.0):
            raise MemoryProposalError("proposal confidence must be in [0.0, 1.0]")
        if not self.evidence_message_ids:
            raise MemoryProposalError("at least one evidence message id is required")
        if len(self.evidence_message_ids) > MAX_EVIDENCE_PER_PROPOSAL:
            raise MemoryProposalError(
                f"proposal exceeds {MAX_EVIDENCE_PER_PROPOSAL} evidence references"
            )
        if len(set(self.evidence_message_ids)) != len(self.evidence_message_ids):
            raise MemoryProposalError("duplicate evidence message id in proposal")
        if any(
            not isinstance(mid, str) or not mid for mid in self.evidence_message_ids
        ):
            raise MemoryProposalError("evidence message ids must be non-empty strings")
        if len(self.rationale) > MAX_RATIONALE_CHARS:
            raise MemoryProposalError(
                f"proposal rationale exceeds {MAX_RATIONALE_CHARS} characters"
            )


@dataclass(frozen=True, slots=True)
class MemoryProposalBatch:
    """One validated model response; ``dropped`` counts rejected items."""

    proposals: tuple[MemoryProposal, ...]
    dropped: int = 0

    def __post_init__(self) -> None:
        if len(self.proposals) > MAX_PROPOSALS_PER_BATCH:
            raise MemoryProposalError(
                f"batch exceeds {MAX_PROPOSALS_PER_BATCH} proposals"
            )


MEMORY_PROPOSAL_SCHEMA: dict[str, object] = {
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
                    "evidence_message_ids": {
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
                    "evidence_message_ids",
                ],
            },
        }
    },
    "required": ["proposals"],
}

_PROPOSAL_FIELDS = {
    "statement",
    "kind",
    "temporal_scope",
    "confidence",
    "evidence_message_ids",
    "rationale",
}


def _strip_json_fences(content: str) -> str:
    """Remove a wrapping ```json ... ``` block a model may emit anyway."""
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip().startswith("```"):
        lines.pop()
    return "\n".join(lines)


def parse_memory_proposal_batch(content: str) -> MemoryProposalBatch:
    """Parse and strictly validate one model response.

    Structural problems (invalid JSON, non-object payload, missing/unknown
    fields, wrong container types) raise :class:`MalformedMemoryProposalError`
    and discard the whole batch. Invalid individual proposals are skipped and
    counted as ``dropped`` — output is never repaired or guessed.
    """
    if not isinstance(content, str):
        msg = "model output must be a string"
        raise MalformedMemoryProposalError(msg)
    try:
        data = json.loads(_strip_json_fences(content))
    except ValueError as exc:
        msg = f"model returned non-JSON output ({len(content)} characters)"
        raise MalformedMemoryProposalError(msg) from exc

    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        msg = "model output must be an object with a 'proposals' array"
        raise MalformedMemoryProposalError(msg)

    proposals: list[MemoryProposal] = []
    dropped = 0
    for index, raw in enumerate(data["proposals"]):
        try:
            proposals.append(_parse_proposal(raw, index))
        except (MemoryProposalError, TypeError) as exc:
            dropped += 1
            del exc
    return MemoryProposalBatch(proposals=tuple(proposals), dropped=dropped)


def _parse_proposal(raw: object, index: int) -> MemoryProposal:
    if not isinstance(raw, dict):
        raise TypeError(f"proposal {index} is not an object")
    unknown = set(raw) - _PROPOSAL_FIELDS
    if unknown:
        msg = f"proposal {index} has unknown fields: {sorted(unknown)}"
        raise MemoryProposalError(msg)
    statement = raw.get("statement")
    kind = raw.get("kind")
    temporal = raw.get("temporal_scope")
    confidence = raw.get("confidence")
    evidence = raw.get("evidence_message_ids")
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
        raise TypeError(f"proposal {index}: evidence_message_ids must be a string list")
    if not isinstance(rationale, str):
        raise TypeError(f"proposal {index}: rationale must be a string")
    return MemoryProposal(
        statement=statement,
        kind=kind,
        temporal_scope=temporal,
        confidence=float(confidence),
        evidence_message_ids=tuple(evidence),
        rationale=rationale,
    )


def _coerce_kind(value: object) -> MemoryKind:
    if isinstance(value, MemoryKind):
        return value
    try:
        return MemoryKind(value)  # type: ignore[arg-type]
    except ValueError as exc:
        raise MemoryProposalError(f"invalid kind: {value!r}") from exc


def _coerce_temporal(value: object) -> TemporalScope:
    if isinstance(value, TemporalScope):
        return value
    try:
        return TemporalScope(value)  # type: ignore[arg-type]
    except ValueError as exc:
        raise MemoryProposalError(f"invalid temporal_scope: {value!r}") from exc


# --- deterministic application-side metadata ---------------------------------
#
# Model confidence is used as-is (it is one metadata input to the policy);
# every other score is fixed by the application so the model cannot self-rate
# its way to acceptance.

_SCORES: dict[MemoryKind, tuple[float, float, float, float]] = {
    MemoryKind.FACT: (0.7, 0.7, 0.6, 0.6),
    MemoryKind.PREFERENCE: (0.7, 0.8, 0.6, 0.7),
    MemoryKind.DECISION: (0.7, 0.7, 0.6, 0.6),
    MemoryKind.PROJECT_CONTEXT: (0.75, 0.8, 0.6, 0.7),
    MemoryKind.ENTITY: (0.7, 0.7, 0.6, 0.6),
    MemoryKind.SUMMARY: (0.6, 0.7, 0.5, 0.6),
    MemoryKind.INSTRUCTION: (0.7, 0.8, 0.6, 0.7),
    MemoryKind.IDENTITY: (0.9, 0.85, 0.7, 0.8),
    MemoryKind.BIOGRAPHY: (0.9, 0.8, 0.7, 0.75),
    MemoryKind.RELATIONSHIP: (0.8, 0.8, 0.6, 0.7),
    MemoryKind.GOAL: (0.6, 0.7, 0.6, 0.7),
    MemoryKind.HABIT: (0.7, 0.8, 0.6, 0.7),
    MemoryKind.ROUTINE: (0.7, 0.8, 0.6, 0.7),
    MemoryKind.INTEREST: (0.75, 0.8, 0.6, 0.7),
    MemoryKind.SKILL: (0.75, 0.8, 0.7, 0.75),
    MemoryKind.WORK: (0.7, 0.8, 0.7, 0.75),
    MemoryKind.EDUCATION: (0.75, 0.8, 0.7, 0.75),
    MemoryKind.LOCATION_CONTEXT: (0.8, 0.8, 0.7, 0.7),
    MemoryKind.IMPORTANT_EVENT: (0.7, 0.7, 0.6, 0.65),
    MemoryKind.LONG_TERM_CONTEXT: (0.8, 0.8, 0.7, 0.75),
    MemoryKind.COMMUNICATION_PREFERENCE: (0.8, 0.8, 0.7, 0.75),
    MemoryKind.PERSONAL_FACT: (0.8, 0.7, 0.6, 0.65),
}

_FIRST_USER_REGEX = re.compile(r"(?i)\bthe user(?:\b|'s)")


def _statement_language(text: str) -> ConversationLanguage:
    """Detect the language of a proposed statement or evidence message.

    Marker-first: a third-person user reference ("der nutzer", "el usuario",
    "the user") pins the language unambiguously. Otherwise the deterministic
    conversation-layer detector scores first-person trigger words, with an
    English fallback on UNKNOWN (both detectors are case- and accent-insensitive).
    """
    lowered = text.casefold()
    for lang, marker in _LANGUAGE_USER_MARKERS.items():
        if marker in lowered:
            return lang
    detected = _detect_language(text)
    if detected is ConversationLanguage.UNKNOWN:
        return ConversationLanguage.EN
    return detected


def _statement_is_negated(statement: str) -> bool:
    """True when a canonical statement is negated in its own language."""
    lang = _statement_language(statement)
    patterns = _NEGATION_PATTERNS.get(lang, _NEGATION_PATTERNS[ConversationLanguage.EN])
    return bool(patterns.search(statement))


def _canonicalize_statement(statement: str) -> str | None:
    """Normalize a proposed statement to the durable "The user ..." form.

    Statements already framed as "The user ..." (or the German/Spanish
    equivalents, since the LLM works in the source language) are kept;
    first-person forms are rewritten with the deterministic Slice 4
    canonicalizer; anything that does not ultimately reference the user is
    rejected (the model may not propose facts about arbitrary third parties as
    if they were user facts).
    """
    stripped = statement.strip()
    lang = _statement_language(stripped)
    candidate = _canonical_statement(stripped, lang) or ""
    if lang is ConversationLanguage.EN:
        if not _FIRST_USER_REGEX.search(candidate):
            return None
    elif _LANGUAGE_USER_MARKERS[lang] not in candidate.casefold():
        return None
    if candidate[:1].islower():
        return candidate[:1].upper() + candidate[1:]
    return candidate


def _apply_deterministic_overrides(
    statement: str, kind: MemoryKind, temporal_scope: TemporalScope
) -> tuple[str, MemoryKind, TemporalScope]:
    """Re-assert application authority over kind and temporal scope.

    Deterministic trigger phrases win over the model's labels: future plans
    are always ``goal``/``current``, recurring timeframes are ``recurring``,
    and past-tense declarations are ``historical``. Triggers are per-language;
    the English tables are identical to the original single-language ones.
    """
    lang = _statement_language(statement)
    if _LANG_GOAL_TRIGGERS[lang].search(statement):
        return statement, MemoryKind.GOAL, TemporalScope.CURRENT
    if _LANG_RECURRING_TRIGGERS[lang].search(statement):
        return statement, kind, TemporalScope.RECURRING
    if _LANG_PAST_TRIGGERS[lang].search(statement):
        return statement, kind, TemporalScope.HISTORICAL
    return statement, kind, temporal_scope


def _evidence_ref(
    conversation: Conversation, message: ConversationMessage
) -> MemoryEvidenceRef:
    stamp = (
        message.timestamp or conversation.modified_at or conversation.created_at or None
    )
    return MemoryEvidenceRef(
        source_type=conversation.source_type,
        source_id=conversation.id,
        source_document_id=message.id,
        source_timestamp=stamp,
    )


def _is_user_assertion(message: ConversationMessage) -> bool:
    """True when a user message states a fact rather than asks or directs.

    Questions, requests directed at the model, and model-directed wishes are
    never durable user facts on their own; a proposal whose only evidence is
    such text is not a user self-assertion.

    The gate is a conservative union across every supported language: a
    message that starts with a request imperative ("Erstelle ...", "Crea ...",
    "Please ...") or addresses the model ("du", "tú", "you", "ich will, dass
    du ...") is excluded regardless of which language the detector assigned
    to the message.
    """
    text = message.content_text.strip()
    if text.endswith("?"):
        return False
    for table in (
        _REQUEST_STARTER_PATTERNS,
        _MODEL_DIRECT_PATTERNS,
        _YOU_TOWARD_PATTERNS,
    ):
        if any(pattern.search(text) for pattern in table.values()):
            return False
    return True


def to_memory_candidate(
    proposal: MemoryProposal,
    conversation: Conversation,
    messages: Sequence[ConversationMessage],
) -> tuple[MemoryCandidate | None, str]:
    """Convert one validated proposal into a provenanced candidate.

    Returns ``(candidate, None)`` on success or ``(None, reason_code)`` when
    the proposal is safely dropped. The evidence ids the model referenced must
    exist in ``messages`` and include at least one user-authored message that
    is a genuine self-assertion (not a question or a request its own claim).
    """
    by_id = {message.id: message for message in messages}
    for message_id in proposal.evidence_message_ids:
        if message_id not in by_id:
            return None, "evidence_unknown"
    user_ids = [
        message_id
        for message_id in proposal.evidence_message_ids
        if (by_id[message_id].role or "").strip().lower() == "user"
    ]
    if not user_ids:
        return None, "evidence_not_user"
    if not any(_is_user_assertion(by_id[message_id]) for message_id in user_ids):
        return None, "evidence_not_claim"

    statement = _canonicalize_statement(proposal.statement)
    if statement is None:
        return None, "not_user_statement"
    if _contains_quotation(statement):
        return None, "quoted"
    if _SENSITIVE_FORM.search(statement):
        return None, "sensitive_form"
    if statement.endswith("?"):
        return None, "question"
    if _statement_is_negated(statement):
        return None, "negated"

    kind, temporal_scope = proposal.kind, proposal.temporal_scope
    statement, kind, temporal_scope = _apply_deterministic_overrides(
        statement, kind, temporal_scope
    )

    durability, relevance, specificity, utility = _SCORES[kind]
    recurrence = 2 if temporal_scope is TemporalScope.RECURRING else 1
    evidence = tuple(
        _evidence_ref(conversation, by_id[message_id])
        for message_id in proposal.evidence_message_ids
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


# --- prompt + bounded window --------------------------------------------------

SYSTEM_PROPOSAL_PROMPT = f"""\
You propose durable facts a personal AI should remember about its owner.
You receive ONE bounded conversation window (labeled by stable message ids).

Rules:
- Propose ONLY durable, likely-long-term facts, never one-off trivia,
  transient logistics, temporary errands, single purchases, individual
  links/URLs, generic opinions, routine conversational filler, or isolated
  events unless they represent a durable fact.
- Facts must come from the USER's own messages about themselves. Never turn
  assistant claims, quoted text, hypotheticals, or requests into facts about
  the user.
- Statement format: third person, starting with "The user", e.g.
  "The user works at Example Corp as a product manager."
- The statement must be about the user (never about other people, companies,
  or external documents).
- temporal_scope: "current" (still true today), "historical" (past),
  "recurring" (happens regularly), or "unknown" when unsure.
- kind: one of {", ".join(_PROPOSABLE_KINDS)}.
- confidence: a number 0.0-1.0 for how strongly the USER's own words support
  the fact.
- evidence_message_ids: the ids of the USER messages that support this fact
  (at least one). They must match ids exactly as shown, e.g. "[msg-123]".
- Emit at most 5 proposals. Prefer fewer high-value facts over many.
- Never propose statements containing emails, URLs, currency amounts, account
  or credential-shaped strings, or secrets. Never propose phone numbers,
  addresses, salaries, medical or financial details.
Respond with ONLY a JSON object of the form:
{{"proposals": [{{"statement": str, "kind": str, "temporal_scope": str,
"confidence": number, "evidence_message_ids": [str], "rationale": str}}]}}
No prose, no markdown fences.
"""


def _build_user_prompt(header: str, lines: Sequence[str]) -> str:
    transcript = "\n".join(lines)
    return (
        f"{header}\n\n"
        "Transcript (stable ids in brackets):\n"
        f"{transcript}\n\n"
        "Output ONLY the JSON object according to the system instructions."
    )


# --- model-calling extractor --------------------------------------------------


class MemoryProposalClient(Protocol):
    """The narrow model surface: the existing ``OllamaClient.chat`` shape."""

    def chat(self, messages: object, *, think: object, format: object) -> object: ...


@dataclass(frozen=True, slots=True)
class ProposalUnitResult:
    """Outcome of extracting proposals from one bounded source unit."""

    candidates: tuple[MemoryCandidate, ...]
    messages_scanned: int
    model_calls: int
    proposals_parsed: int
    proposals_dropped: int
    failed: bool = False
    failure_reason: str = ""


class LLMMemoryProposalExtractor:
    """Runs bounded LLM proposal extraction over one conversation window.

    This is the first concrete adapter for the generic proposal-extractor
    interface: a document/email adapter would implement the same
    ``extract(unit, records)`` shape with its own bounded window and
    evidence mapping, and feed the same policy-gated write path.
    """

    def __init__(
        self,
        client: MemoryProposalClient,
        *,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        max_prompt_chars: int = MAX_PROMPT_CHARS,
        max_candidates_per_unit: int = DEFAULT_MAX_CANDIDATES_PER_UNIT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._client = client
        self._max_messages = max_messages
        self._max_prompt_chars = max_prompt_chars
        self._max_candidates = max_candidates_per_unit
        self._max_retries = max_retries

    def extract(
        self,
        conversation: Conversation,
        messages: Sequence[ConversationMessage],
    ) -> ProposalUnitResult:
        from personal_ai.ollama_client import ChatMessage, OllamaError

        active = tuple(message for message in messages if message.is_active_branch)[
            : self._max_messages
        ]
        if not any(
            (message.role or "").strip().lower() == "user" for message in active
        ):
            return ProposalUnitResult(
                candidates=(),
                messages_scanned=len(active),
                model_calls=0,
                proposals_parsed=0,
                proposals_dropped=0,
            )

        lines, used = [], 0
        for message in active:
            role = (message.role or "user").strip().lower()
            label = "USER" if role == "user" else "ASSISTANT"
            content = " ".join(message.content_text.split())[:MAX_PROMPT_LINE_CHARS]
            line = f"[{message.id}] {label}: {content}"
            if used + len(line) + 1 > self._max_prompt_chars:
                break
            lines.append(line)
            used += len(line) + 1
        if not lines:
            return ProposalUnitResult(
                candidates=(),
                messages_scanned=len(active),
                model_calls=0,
                proposals_parsed=0,
                proposals_dropped=0,
            )

        header = (
            f"Conversation {conversation.id} (source: {conversation.source_type}, "
            f"title: {conversation.title[:120]})"
        )
        model_calls = 0
        failure_reason = ""
        for _attempt in range(self._max_retries + 1):
            model_calls += 1
            try:
                response = self._client.chat(
                    [
                        ChatMessage(role="system", content=SYSTEM_PROPOSAL_PROMPT),
                        ChatMessage(
                            role="user",
                            content=_build_user_prompt(header, lines),
                        ),
                    ],
                    think=False,
                    format=MEMORY_PROPOSAL_SCHEMA,
                )
                batch = parse_memory_proposal_batch(response.content)
            except OllamaError as exc:
                failure_reason = "ollama_error"
                del exc
                continue
            except MalformedMemoryProposalError as exc:
                failure_reason = "malformed_output"
                del exc
                continue
            return self._build_unit_result(conversation, active, batch, model_calls)

        return ProposalUnitResult(
            candidates=(),
            messages_scanned=len(active),
            model_calls=model_calls,
            proposals_parsed=0,
            proposals_dropped=0,
            failed=True,
            failure_reason=failure_reason or "model_error",
        )

    def _build_unit_result(
        self,
        conversation: Conversation,
        messages: tuple[ConversationMessage, ...],
        batch: MemoryProposalBatch,
        model_calls: int,
    ) -> ProposalUnitResult:
        candidates: list[MemoryCandidate] = []
        seen: set[tuple[str, str]] = set()
        for proposal in batch.proposals:
            candidate, _reason = to_memory_candidate(proposal, conversation, messages)
            if candidate is None:
                continue
            key = (candidate.kind.value, normalize_text(candidate.statement))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
        return ProposalUnitResult(
            candidates=tuple(candidates[: self._max_candidates]),
            messages_scanned=len(messages),
            model_calls=model_calls,
            proposals_parsed=len(batch.proposals),
            proposals_dropped=batch.dropped,
        )


# --- bounded ingestion over stored conversations ------------------------------


@dataclass(frozen=True, slots=True)
class LLMConversationMemoryReport:
    """Count-only outcome of one LLM-assisted conversation memory run."""

    source_type: str
    conversations_scanned: int
    messages_scanned: int
    model_batches: int
    failed_batches: int
    proposals_parsed: int
    proposals_dropped: int
    errors: tuple[tuple[str, int], ...]
    tally: OutcomeTally

    def summary(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "conversations_scanned": self.conversations_scanned,
            "messages_scanned": self.messages_scanned,
            "model_batches": self.model_batches,
            "failed_batches": self.failed_batches,
            "proposals_parsed": self.proposals_parsed,
            "proposals_dropped": self.proposals_dropped,
            **self.tally.to_dict(),
            "errors": dict(self.errors),
        }


class LLMConversationMemoryIngestor:
    """Runs bounded LLM proposal extraction through the policy-gated write path.

    Reads a bounded run of stored conversations for one source type, derives
    candidates with :class:`LLMMemoryProposalExtractor`, and routes every
    candidate through the policy-gated :class:`AutomaticMemoryCurator` — never
    touching SQL or the store directly, never logging prompts or responses.
    """

    def __init__(
        self,
        conversation_store: ConversationStore,
        memory_service: object,
        *,
        client: MemoryProposalClient | None = None,
        extractor: LLMMemoryProposalExtractor | None = None,
        policy: MemoryPolicy | None = None,
        max_conversations: int = DEFAULT_MAX_CONVERSATIONS,
        max_messages_per_conversation: int = DEFAULT_MAX_MESSAGES,
        max_candidates_per_conversation: int = DEFAULT_MAX_CANDIDATES_PER_UNIT,
        offset: int = 0,
        interactive_approver: Callable[[str, str, str], bool] | None = None,
        auto_approver: Callable[[str, str, str], bool] | None = None,
    ) -> None:
        # Imported lazily: pulling in the tools layer at module import time
        # would create a circular import through the chat tool registry.
        from personal_ai.tools.memory import AutomaticMemoryCurator

        if extractor is None:
            if client is None:
                raise MemoryProposalError(
                    "an LLMConversationMemoryIngestor requires either a model "
                    "client or a prebuilt extractor"
                )
            extractor = LLMMemoryProposalExtractor(
                client=client,
                max_messages=max_messages_per_conversation,
                max_candidates_per_unit=max_candidates_per_conversation,
            )
        self._store = conversation_store
        self._max_conversations = max(0, max_conversations)
        self._offset = max(0, offset)
        self._curator = AutomaticMemoryCurator(
            memory_service,
            policy or MemoryPolicy(),
            interactive_approver=interactive_approver,
            auto_approver=auto_approver,
        )
        self._extractor = extractor

    def ingest(self, source_type: str) -> LLMConversationMemoryReport:
        """Extract and gate LLM-proposed candidates for one source type."""
        if source_type not in CONVERSATION_SOURCE_TYPES:
            raise ConversationSourceError(
                f"unsupported conversation source: {source_type!r}"
            )
        tally = OutcomeTally()
        error_counts: dict[str, int] = {}
        conversations_scanned = 0
        messages_scanned = 0
        model_batches = 0
        failed_batches = 0
        proposals_parsed = 0
        proposals_dropped = 0

        while conversations_scanned < self._max_conversations:
            batch = self._store.list_conversations(
                source_type=source_type,
                limit=min(
                    DEFAULT_BATCH_SIZE,
                    self._max_conversations - conversations_scanned,
                ),
                offset=self._offset + conversations_scanned,
            )
            if not batch:
                break
            for conversation in batch:
                messages = self._store.list_messages(conversation.id)
                messages_scanned += len(messages)
                unit = self._extractor.extract(conversation, messages)
                model_batches += unit.model_calls
                proposals_parsed += unit.proposals_parsed
                proposals_dropped += unit.proposals_dropped
                if unit.failed:
                    failed_batches += 1
                    reason = unit.failure_reason or "model_error"
                    error_counts[reason] = error_counts.get(reason, 0) + 1
                for candidate in unit.candidates:
                    tally.add(self._curator.curate(candidate))
            conversations_scanned += len(batch)

        return LLMConversationMemoryReport(
            source_type=source_type,
            conversations_scanned=conversations_scanned,
            messages_scanned=messages_scanned,
            model_batches=model_batches,
            failed_batches=failed_batches,
            proposals_parsed=proposals_parsed,
            proposals_dropped=proposals_dropped,
            errors=tuple(sorted(error_counts.items())),
            tally=tally,
        )
