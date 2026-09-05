"""Deterministic, bounded conversation memory extraction.

Conversation exports are ingested into the :class:`ConversationStore` as
first-class entities. This module derives durable *identity-tier* memory
candidates from the user-authored messages of those stored conversations and
routes every candidate through the same policy-gated write path as corpus
extraction (``AutomaticMemoryCurator`` -> ``propose_memory`` gate ->
``MemoryService.apply_candidate``). It performs no raw SQL and no direct
memory writes.

Extraction is deterministic and conservative — no LLM, no assistant claims,
no invented facts:

* only ``user``-role messages on the active branch are considered
  (assistant/system/tool claims are never user facts);
* only explicit first-person self-assertions can yield a candidate;
* negation, questions, requests directed at the model, quoted content, and
  sensitive material (email addresses, phone numbers, currency amounts,
  credential-shaped strings, credential keywords) are skipped;
* temporal scope is derived from the trigger: present-tense declarations are
  ``current``, past-tense ones ``historical``, and recurring-timeframe
  declarations ``recurring``. "I want to do X" never becomes "the user does
  X" — a plan is represented as a ``goal``, never an achieved fact.

Evidence is provenance-only: stable identifiers and ISO timestamps, never
conversation content. Repeated facts across conversations accumulate evidence
on a single memory through the existing reconciler, and re-running on
unchanged conversations is idempotent.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from personal_ai.documents.conversations import (
    Conversation,
    ConversationMessage,
)
from personal_ai.memory.corpus import OutcomeTally
from personal_ai.memory.models import (
    AssertionStatus,
    MemoryCandidate,
    MemoryEvidenceRef,
    MemoryKind,
    TemporalScope,
)
from personal_ai.memory.policy import (
    _HIGHLY_SENSITIVE_KEYWORDS as _POLICY_HIGHLY_SENSITIVE_KEYWORDS,
)
from personal_ai.memory.policy import (
    _SENSITIVE_KEYWORDS as _POLICY_SENSITIVE_KEYWORDS,
)
from personal_ai.memory.policy import MemoryPolicy
from personal_ai.memory.reconcile import normalize_text
from personal_ai.storage.conversations import ConversationStore

# Bounds (deterministic, conservative).
MAX_STATEMENT_CHARS = 512
DEFAULT_MAX_CONVERSATIONS = 10_000
DEFAULT_MAX_MESSAGES_PER_CONVERSATION = 1_000
DEFAULT_MAX_CANDIDATES_PER_CONVERSATION = 10

# The evidence ``source_type`` strings this layer produces (recognized as
# ``conversation_statement`` provenance by ``MemoryPolicy``).
CONVERSATION_SOURCE_TYPES: frozenset[str] = frozenset({"chatgpt", "gemini"})

_FIRST_PERSON = re.compile(r"(?:\bi\b|\bmy\b|\bme\b|i[''][mdv])", re.IGNORECASE)

_NEGATION = re.compile(
    r"\b(?:no|not|never|hardly|scarcely"
    r"|don't|doesn't|didn't|isn't|aren't|wasn't|weren't"
    r"|can't|cannot|won't|wouldn't|shouldn't|couldn't|mustn't"
    r"|haven't|hasn't|hadn't|isn't)\b",
    re.IGNORECASE,
)

_REQUEST_STARTER = re.compile(
    r"^(?:"
    r"can you|could you|will you|would you|please|help me|give me|tell me|"
    r"show me|write|explain|describe|summarize|create|generate|draft|review|"
    r"fix|convert|translate|find|search|make|build|list|define|recommend|"
    r"suggest|compare|analyze"
    r")\b",
    re.IGNORECASE,
)

_MODEL_DIRECT = re.compile(
    r"\bi (?:want|'?d like|hope) (?:you|the (?:ai|assistant|model|chatbot))\b",
    re.IGNORECASE,
)

_YOU_TOWARD = re.compile(r"\byou\b", re.IGNORECASE)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"\b(?:https?://|www\.)", re.IGNORECASE)
_CURRENCY = re.compile(
    r"(?:[€£$]\s?\d|\d[\d.,]*\s?(?:€|£|\$|USD|EUR|dollars?|euros?))",
    re.IGNORECASE,
)
_CREDENTIAL_SHAPES = re.compile(
    r"\b\d{3}-\d{2}-\d{4}\b"
    r"|\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"
    r"|\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"
    r"|\b(?:sk_live|sk_test)_[A-Za-z0-9]{16,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
)
_SENSITIVE_FORM_SOURCE = (
    f"{_EMAIL.pattern}|{_URL.pattern}|{_CURRENCY.pattern}|{_CREDENTIAL_SHAPES.pattern}"
)
_SENSITIVE_FORM = re.compile(_SENSITIVE_FORM_SOURCE, re.IGNORECASE)

# Content never worth proposing regardless of the policy's own classification:
# privacy-sensitive keyword material is skipped at extraction so it can never
# even surface as a "require approval" candidate.
_SENSITIVE_KEYWORDS: frozenset[str] = frozenset(
    _POLICY_SENSITIVE_KEYWORDS
    | _POLICY_HIGHLY_SENSITIVE_KEYWORDS
    | {
        "salary",
        "credentials",
        "password",
        "passphrase",
        "api key",
        "apikey",
        "access token",
        "secret key",
        "private key",
        "session token",
        "seed phrase",
        "security answer",
    }
)


def _sentence_split(text: str) -> tuple[str, ...]:
    """Split message text into candidate sentences.

    Splits on sentence-ending punctuation followed by whitespace (conservative:
    abbreviations and decimals stay intact). Long messages are bounded by the
    per-conversation message cap the caller applies.
    """
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return tuple(p.strip() for p in parts if p.strip())


@dataclass(frozen=True, slots=True)
class _Rule:
    """One deterministic extraction rule: kind, temporal scope, and triggers."""

    kind: MemoryKind
    temporal: TemporalScope
    patterns: tuple[re.Pattern[str], ...]
    confidence: float = 0.85
    durability: float = 0.8
    relevance: float = 0.8
    specificity: float = 0.6
    utility: float = 0.7
    recurrence: int = 1


# First matching rule wins. Rules are ordered so the most specific signal
# (routines, identity, biography) is checked before broader categories.
_RECURRING_TIME = re.compile(
    r"\bevery (?:morning|evening|afternoon|day|week|weekend|night|month)\b"
    r"|\b(?:daily|weekly|monthly|regularly)\b",
    re.IGNORECASE,
)
_JOB_TITLES = (
    r"software engineer|data scientist|product manager|project manager|"
    r"engineer|developer|designer|analyst|manager|consultant|researcher|"
    r"scientist|teacher|professor|nurse|doctor|lawyer|architect|accountant|"
    r"marketer|student|technician|administrator|director|founder|writer|"
    r"editor|photographer|musician|athlete|trainer|chef|operator"
)
_INSTRUMENTS = (
    r"guitar|piano|violin|viola|cello|drums|bass|ukulele|flute|saxophone|"
    r"trumpet|clarinet|accordion|keyboard"
)
_SPORTS = (
    r"football|soccer|basketball|tennis|badminton|hockey|cricket|rugby|golf|"
    r"volleyball|swimming|running|cycling|hiking|climbing|skiing|surfing|diving"
)

_RULES: tuple[_Rule, ...] = (
    _Rule(
        MemoryKind.HABIT,
        TemporalScope.RECURRING,
        (_RECURRING_TIME,),
        recurrence=3,
        confidence=0.85,
        durability=0.8,
        relevance=0.8,
    ),
    _Rule(
        MemoryKind.IDENTITY,
        TemporalScope.CURRENT,
        (re.compile(r"\bmy name (?:is|'s|is called)\b", re.IGNORECASE),),
        confidence=0.9,
        durability=0.9,
        relevance=0.85,
        utility=0.8,
    ),
    _Rule(
        MemoryKind.BIOGRAPHY,
        TemporalScope.HISTORICAL,
        (re.compile(r"\bi (?:was born|grew ?up)\b", re.IGNORECASE),),
    ),
    _Rule(
        MemoryKind.WORK,
        TemporalScope.HISTORICAL,
        (
            re.compile(
                r"\bi (?:used to work|worked|used to be|was) (?:as|at|for|in)\b",
                re.IGNORECASE,
            ),
        ),
        durability=0.7,
        relevance=0.7,
    ),
    _Rule(
        MemoryKind.WORK,
        TemporalScope.CURRENT,
        (
            re.compile(r"\bi work (?:as|at|for|in)\b", re.IGNORECASE),
            re.compile(rf"\bi'?m a (?:{_JOB_TITLES})\b", re.IGNORECASE),
            re.compile(r"\bmy (?:job|work)\b", re.IGNORECASE),
        ),
    ),
    _Rule(
        MemoryKind.EDUCATION,
        TemporalScope.HISTORICAL,
        (
            re.compile(
                r"\bi (?:graduated|majored|attended|studied) (?:from|in|at)\b",
                re.IGNORECASE,
            ),
        ),
        durability=0.7,
        relevance=0.7,
    ),
    _Rule(
        MemoryKind.EDUCATION,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi (?:study|am studying|'m studying|attend)\b",
                re.IGNORECASE,
            ),
            re.compile(r"\bi go to (?:school|college|university)\b", re.IGNORECASE),
            re.compile(
                r"\bi'?m a (?:student|freshman|sophomore|junior|senior|"
                r"grad (?:student|school student))\b",
                re.IGNORECASE,
            ),
        ),
    ),
    _Rule(
        MemoryKind.LOCATION_CONTEXT,
        TemporalScope.HISTORICAL,
        (
            re.compile(
                r"\bi (?:moved|relocated) (?:to|in|near)\b|\bi lived in\b"
                r"|\bi used to live\b|\bi was based in\b",
                re.IGNORECASE,
            ),
        ),
        durability=0.7,
        relevance=0.7,
    ),
    _Rule(
        MemoryKind.LOCATION_CONTEXT,
        TemporalScope.CURRENT,
        (
            re.compile(r"\bi (?:live|work from) in\b", re.IGNORECASE),
            re.compile(r"\bi'?m based in\b|\bi'?m from\b", re.IGNORECASE),
            re.compile(
                r"\bmy (?:hometown|city|neighborhood|house|home) is\b", re.IGNORECASE
            ),
        ),
    ),
    _Rule(
        MemoryKind.GOAL,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi (?:want|hope|plan|intend|aspire) to\b|\bi'?d like to\b"
                r"|\bmy (?:goal|dream|plan|ambition|target) (?:is|'s)\b",
                re.IGNORECASE,
            ),
        ),
        confidence=0.8,
        durability=0.6,
        relevance=0.7,
    ),
    _Rule(
        MemoryKind.SKILL,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi (?:speak|am fluent in|'m fluent in|can speak)\b", re.IGNORECASE
            ),
            re.compile(
                rf"\bi (?:play|can play) (?:(?:the|an?)\s+)?(?:{_INSTRUMENTS})\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\bi (?:code|program) in\b|\bi (?:can|know how to) (?:code|program)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\bi'?m (?:learning|teaching myself|practicing|taking a course in)\b",
                re.IGNORECASE,
            ),
        ),
        confidence=0.8,
        durability=0.75,
        relevance=0.8,
    ),
    _Rule(
        MemoryKind.INTEREST,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi (?:like|love|enjoy) (?!it\b|that\b|this\b|them\b)\b",
                re.IGNORECASE,
            ),
            re.compile(r"\bi'?m (?:into|a fan of)\b", re.IGNORECASE),
            re.compile(
                r"\bmy (?:favorite|favourite) (?:hobby|sport|team|band|artist|movie|film|show|"
                r"book|author|genre|food|cuisine|activity|game|podcast|place)\b",
                re.IGNORECASE,
            ),
            re.compile(rf"\bi (?:play|watch) (?:{_SPORTS})\b", re.IGNORECASE),
        ),
        confidence=0.8,
        durability=0.75,
        relevance=0.8,
    ),
    _Rule(
        MemoryKind.PREFERENCE,
        TemporalScope.CURRENT,
        (
            re.compile(r"\bi (?:prefer|'?d rather|would rather)\b", re.IGNORECASE),
            re.compile(r"\bmy (?:preference|preferred)\b", re.IGNORECASE),
        ),
        confidence=0.8,
        durability=0.7,
        relevance=0.8,
    ),
    _Rule(
        MemoryKind.COMMUNICATION_PREFERENCE,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi (?:prefer|like) to (?:communicate|be (?:reached|contacted))\b"
                r"|\bi (?:prefer|like) (?:email|text|phone|video|calls?)\b",
                re.IGNORECASE,
            ),
        ),
        confidence=0.8,
        durability=0.7,
        relevance=0.8,
    ),
    _Rule(
        MemoryKind.RELATIONSHIP,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bmy (?:wife|husband|partner|spouse|fiancee|fiance|girlfriend|boyfriend|"
                r"mom|mum|mother|dad|father|brother|sister|son|daughter|parent|parents|"
                r"children|kids|family|grandmother|grandfather|aunt|uncle|cousin|boss|"
                r"manager|teammate|colleague)\b",
                re.IGNORECASE,
            ),
        ),
    ),
    _Rule(
        MemoryKind.PERSONAL_FACT,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi(?:'?ve| have) (?:a|an|the|my) (?:dog|cat|pet|house|apartment|flat|"
                r"car|bike|motorcycle|boat|garden)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\bi (?:own|drive|ride|use) (?:a|an|my) [a-z]{2,}\b", re.IGNORECASE
            ),
            re.compile(
                r"\b(?:i'?m married|i(?:'?ve)? have been married)\b", re.IGNORECASE
            ),
        ),
        confidence=0.8,
        durability=0.8,
        relevance=0.7,
    ),
)

_VERB_FORMS: dict[str, str] = {
    "am": "is",
    "live": "lives",
    "work": "works",
    "study": "studies",
    "like": "likes",
    "love": "loves",
    "enjoy": "enjoys",
    "want": "wants",
    "hope": "hopes",
    "plan": "plans",
    "intend": "intends",
    "aspire": "aspires",
    "prefer": "prefers",
    "speak": "speaks",
    "play": "plays",
    "code": "codes",
    "program": "programs",
    "have": "has",
    "own": "owns",
    "use": "uses",
    "drive": "drives",
    "ride": "rides",
    "go": "goes",
    "watch": "watches",
    "read": "reads",
    "exercise": "exercises",
    "run": "runs",
    "jog": "jogs",
    "swim": "swims",
    "meditate": "meditates",
    "practice": "practices",
    "train": "trains",
    "cook": "cooks",
    "wake": "wakes",
    "moved": "moved",
    "lived": "lived",
    "worked": "worked",
    "studied": "studied",
    "graduated": "graduated",
    "attended": "attended",
    "attend": "attends",
    "was": "was",
    "used": "used",
    "based": "is based",
}

_PREFIX_FORMS: tuple[tuple[str, str], ...] = (
    ("i'm", "the user is"),
    ("i am", "the user is"),
    ("i'd", "the user would"),
    ("i've", "the user has"),
)


def _canonical_statement(sentence: str) -> str:
    """Rewrite a first-person user sentence as a third-person fact.

    Deterministic, bounded transformation: leading ``I'm/I am/I'd/I've/I``
    forms become "the user ...", ``my`` becomes "the user's", and the
    sentence-initial verb is conjugated for the singular subject. Unmapped
    verbs pass through unchanged (conservative: never invented).
    """
    text = re.sub(r"\s+", " ", sentence.strip())
    call_me = re.match(r"(?i)^call me\s+(\w+)(.*)$", text)
    if call_me:
        return f"The user goes by {call_me.group(1)}{call_me.group(2)}".strip()

    lowered = text.lower()
    for prefix, replacement in _PREFIX_FORMS:
        if lowered.startswith(prefix):
            return _capitalize(replacement + text[len(prefix) :]).strip()

    if lowered.startswith("i ") or lowered == "i":
        rest = text[2:].lstrip()
        if rest:
            verb, sep, tail = rest.partition(" ")
            conjugated = _VERB_FORMS.get(verb.lower(), verb)
            return _capitalize(f"the user {conjugated}{sep}{tail}").strip()

    return _capitalize(re.sub(r"\bmy\b", "the user's", text, flags=re.IGNORECASE))


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _evidence_ref(
    conversation: Conversation, message: ConversationMessage
) -> MemoryEvidenceRef:
    """Provenance-only evidence: identifiers and an ISO timestamp."""
    stamp = (
        message.timestamp or conversation.modified_at or conversation.created_at or None
    )
    return MemoryEvidenceRef(
        source_type=conversation.source_type,
        source_id=conversation.id,
        source_document_id=message.id,
        source_timestamp=stamp,
    )


class ConversationMemoryExtractor:
    """Deterministic extraction of identity-tier memory candidates.

    Purely in-memory: takes a ``Conversation`` and its messages and returns
    validated ``MemoryCandidate`` instances. Never writes anything.
    """

    def __init__(
        self,
        *,
        max_messages: int = DEFAULT_MAX_MESSAGES_PER_CONVERSATION,
        max_candidates: int = DEFAULT_MAX_CANDIDATES_PER_CONVERSATION,
    ) -> None:
        self._max_messages = max_messages
        self._max_candidates = max_candidates

    def extract(
        self,
        conversation: Conversation,
        messages: Sequence[ConversationMessage],
    ) -> tuple[MemoryCandidate, ...]:
        """Return bounded candidates from active-branch user messages."""
        if conversation.source_type not in CONVERSATION_SOURCE_TYPES:
            return ()
        candidates: list[MemoryCandidate] = []
        seen: set[tuple[str, str]] = set()
        for message in messages[: self._max_messages]:
            if not message.is_active_branch:
                continue
            if (message.role or "").strip().lower() != "user":
                continue
            for sentence in _sentence_split(message.content_text):
                candidate = self._candidate_for(conversation, message, sentence)
                if candidate is None:
                    continue
                key = (candidate.kind.value, normalize_text(candidate.statement))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(candidate)
        return tuple(candidates[: self._max_candidates])

    def _candidate_for(
        self,
        conversation: Conversation,
        message: ConversationMessage,
        sentence: str,
    ) -> MemoryCandidate | None:
        raw = sentence
        if len(raw) > MAX_STATEMENT_CHARS:
            return None
        if raw.endswith("?"):
            return None
        if not _FIRST_PERSON.search(raw):
            return None
        if _NEGATION.search(raw):
            return None
        if _YOU_TOWARD.search(raw):
            return None
        if '"' in raw or "``" in raw:
            return None
        if _SENSITIVE_FORM.search(raw):
            return None
        lowered = raw.lower()
        if any(keyword in lowered for keyword in _SENSITIVE_KEYWORDS):
            return None
        if _REQUEST_STARTER.search(raw):
            return None
        if _MODEL_DIRECT.search(raw):
            return None

        for rule in _RULES:
            if any(pattern.search(raw) for pattern in rule.patterns):
                statement = _canonical_statement(raw)
                if not statement or len(statement) > MAX_STATEMENT_CHARS:
                    return None
                return MemoryCandidate(
                    statement=statement,
                    kind=rule.kind,
                    confidence=rule.confidence,
                    durability=rule.durability,
                    relevance=rule.relevance,
                    specificity=rule.specificity,
                    recurrence=rule.recurrence,
                    utility=rule.utility,
                    temporal_scope=rule.temporal,
                    assertion_status=AssertionStatus.ASSERTED,
                    evidence=(_evidence_ref(conversation, message),),
                )
        return None


class ConversationSourceError(ValueError):
    """Raised for unsupported conversation source types."""


@dataclass(frozen=True, slots=True)
class ConversationMemoryReport:
    """Count-only outcome of a conversation memory extraction run.

    Deliberately content-free: totals, candidate/decision counts, and write
    statuses — never statements, evidence identifiers, or conversation text.
    """

    source_type: str
    conversations_scanned: int
    user_messages_scanned: int
    tally: OutcomeTally
    evidence_rows_added: int

    def summary(self) -> dict[str, object]:
        """Count-only view of the run (no content or identifiers)."""
        return {
            "source_type": self.source_type,
            "conversations_scanned": self.conversations_scanned,
            "user_messages_scanned": self.user_messages_scanned,
            "evidence_rows_added": self.evidence_rows_added,
            **self.tally.to_dict(),
        }


class ConversationMemoryIngestor:
    """Runs bounded conversation memory extraction through the write gate.

    Reads a bounded run of stored conversations for one source type, derives
    candidates with the deterministic extractor, and routes every candidate
    through the policy-gated ``AutomaticMemoryCurator`` — never touching SQL
    or the store directly.
    """

    def __init__(
        self,
        conversation_store: ConversationStore,
        memory_service: object,
        *,
        policy: MemoryPolicy | None = None,
        max_conversations: int = DEFAULT_MAX_CONVERSATIONS,
        max_messages_per_conversation: int = DEFAULT_MAX_MESSAGES_PER_CONVERSATION,
        max_candidates_per_conversation: int = DEFAULT_MAX_CANDIDATES_PER_CONVERSATION,
        interactive_approver: Callable[[str, str, str], bool] | None = None,
        auto_approver: Callable[[str, str, str], bool] | None = None,
        extractor: ConversationMemoryExtractor | None = None,
    ) -> None:
        # Imported lazily: pulling in the tools layer at module import time
        # would create a circular import through the chat tool registry.
        from personal_ai.tools.memory import AutomaticMemoryCurator

        self._store = conversation_store
        self._max_conversations = max_conversations
        self._curator = AutomaticMemoryCurator(
            memory_service,
            policy or MemoryPolicy(),
            interactive_approver=interactive_approver,
            auto_approver=auto_approver,
        )
        self._extractor = extractor or ConversationMemoryExtractor(
            max_messages=max_messages_per_conversation,
            max_candidates=max_candidates_per_conversation,
        )

    def ingest(self, source_type: str) -> ConversationMemoryReport:
        """Extract and gate candidates for one conversation source type."""
        if source_type not in CONVERSATION_SOURCE_TYPES:
            raise ConversationSourceError(
                f"unsupported conversation source: {source_type!r}"
            )
        conversations = self._store.list_conversations(
            source_type=source_type,
            limit=self._max_conversations,
        )
        tally = OutcomeTally()
        user_messages = 0
        evidence_rows = 0
        for conversation in conversations:
            messages = self._store.list_messages(conversation.id)
            candidates = self._extractor.extract(conversation, messages)
            user_messages += sum(
                1
                for message in messages
                if (message.role or "").strip().lower() == "user"
            )
            for candidate in candidates:
                outcome = self._curator.curate(candidate)
                tally.add(outcome)
                if outcome.get("applied"):
                    evidence_rows += int(outcome.get("evidence_added", 0) or 0)
        return ConversationMemoryReport(
            source_type=source_type,
            conversations_scanned=len(conversations),
            user_messages_scanned=user_messages,
            tally=tally,
            evidence_rows_added=evidence_rows,
        )
