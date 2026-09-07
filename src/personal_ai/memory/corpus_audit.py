"""Bounded, aggregate-only real-corpus audit for deterministic memory extraction.

The audit validates the multilingual (EN/DE/ES), Unicode-safe deterministic
conversation-memory stack (Phases 20/21) against a deliberately bounded sample
of *raw conversation exports* (ChatGPT/Gemini). It never opens a production
database and never performs memory writes on anything other than an optional
scratch SQLite file; the main metrics are computed purely in memory from the
export files through the existing loaders, extractor, and policy.

Privacy contract
----------------
The audit is aggregate-only. It never prints or returns message content,
statements, evidence identifiers, titles, filenames, prompts, or secret/sensitive
values. All metrics are counts and category tallies; provenance is validated as
valid/invalid counts. The only corpus-derived program state is the in-memory
sample used to compute those counts (deleted when the process exits).

Sampling boundary
-----------------
``limit`` bounds the number of conversations per source (default 25, hard-capped
at ``MAX_CONVERSATIONS_PER_SOURCE``) and ``max_messages`` bounds messages per
conversation. Discovery is sorted and deterministic, so the same export always
yields the same sample.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import regex

from personal_ai.documents.conversations import (
    Conversation,
    ConversationMessage,
)
from personal_ai.memory.conversations import (
    CONVERSATION_SOURCE_TYPES,
    ConversationLanguage,
    ConversationMemoryExtractor,
    ConversationMemoryIngestor,
    _detect_language,
    _sentence_split,
)
from personal_ai.memory.models import MemoryCandidate
from personal_ai.memory.retriever import tokenize
from personal_ai.memory.tokenizer import strip_variation_selectors
from personal_ai.sources.chatgpt_loader import ChatGPTConversationLoader
from personal_ai.sources.gemini_loader import GeminiConversationLoader

MAX_CONVERSATIONS_PER_SOURCE = 25
DEFAULT_AUDIT_LIMIT = MAX_CONVERSATIONS_PER_SOURCE
DEFAULT_AUDIT_MAX_MESSAGES = 1_000

# Phase 21A measurement: Spanish pro-drop first-person verbs currently missing
# from the deterministic ES trigger set. We only *count* occurrences; we never
# implement the fix here.
_PHASE21A_ES_VERBS = ("hago", "juego", "corro", "nado", "cocino")
_PHASE21A_RE = re.compile(
    r"^\s*(?:" + "|".join(_PHASE21A_ES_VERBS) + r")\b",
    re.IGNORECASE,
)

_NON_ASCII_RE = re.compile(r"[^\x00-\x7f]")

# Emoji-codepoint detection for zero-token classification (aggregate-only).
# VS are already stripped before classification; ZWJ and emoji modifiers are
# emoji companions and never make an otherwise-emoji message "non-lexical".
_EMOJI_CHAR_RE = regex.compile(r"\p{Emoji}", regex.UNICODE)
_EMOJI_MODIFIER_START = 0x1F3FB
_EMOJI_MODIFIER_END = 0x1F3FF
_ZWJ = "\u200d"

# Zero-token category names (content-free, fixed vocabulary).
ZERO_TOKEN_CATEGORIES = (
    "emoji_only",
    "symbol_only",
    "punctuation_only",
    "symbol_punctuation",
    "whitespace_only",
    "format_or_other",
    "lexical_unicode",
    "unknown",
)


def _is_emoji_char(character: str) -> bool:
    """True for emoji codepoints and their ZWJ/modifier companions."""
    if _EMOJI_CHAR_RE.fullmatch(character):
        return True
    point = ord(character)
    return character == _ZWJ or _EMOJI_MODIFIER_START <= point <= _EMOJI_MODIFIER_END


def _classify_zero_token(content: str) -> str:
    """Classify a zero-token user message into a content-free bucket.

    The message already tokenized to ZERO tokens, so by construction it contains
    no Unicode letter/number/mark material (post VS-strip). ``lexical_unicode``
    would be a genuine tokenization defect; every other bucket is legitimate
    non-lexical input (emoji/symbol/punctuation/whitespace/format).
    """
    cleaned = strip_variation_selectors(content)
    kinds: set[str] = set()
    for character in cleaned:
        if character.isspace():
            continue
        if _is_emoji_char(character):
            kinds.add("emoji")
            continue
        major, _ = unicodedata.category(character)
        if major in ("L", "M", "N"):
            kinds.add("lexical")
        elif major == "S":
            kinds.add("symbol")
        elif major == "P":
            kinds.add("punct")
        elif major == "C":
            kinds.add("format")
        else:
            kinds.add("other")

    if "lexical" in kinds:
        return "lexical_unicode"
    if not kinds or kinds == {"other"}:
        return "whitespace_only" if not kinds else "unknown"
    if kinds == {"emoji"}:
        return "emoji_only"
    if kinds == {"symbol"}:
        return "symbol_only"
    if kinds == {"punct"}:
        return "punctuation_only"
    if kinds == {"format"}:
        return "format_or_other"
    if kinds <= {"emoji", "symbol", "punct", "format"}:
        return "symbol_punctuation"
    return "unknown"


@dataclass(frozen=True, slots=True)
class _SampleUnit:
    """One bounded conversation and its capped messages."""

    conversation: Conversation
    messages: tuple[ConversationMessage, ...]


@dataclass(frozen=True, slots=True)
class AuditLanguageStats:
    """Message- and sentence-level language tallies (content-free)."""

    languages: dict[str, int] = field(default_factory=dict)
    mixed_language_messages: int = 0
    unknown_messages: int = 0


@dataclass(frozen=True, slots=True)
class AuditExtractionStats:
    """Candidate-level extraction tallies."""

    candidates: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    by_language: dict[str, int] = field(default_factory=dict)
    recurring: int = 0
    temporal: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuditSecurityStats:
    """Deterministic policy outcomes over the real candidates."""

    decisions: dict[str, int] = field(default_factory=dict)
    sensitivity: dict[str, int] = field(default_factory=dict)
    secret_rejected: int = 0
    require_approval: int = 0


@dataclass(frozen=True, slots=True)
class AuditProvenanceStats:
    """Evidence-reference validity counts."""

    evidence_valid: int = 0
    evidence_invalid: int = 0


@dataclass(frozen=True, slots=True)
class AuditUnicodeStats:
    """Unicode/tokenization health tallies.

    ``zero_token_messages`` counts messages that tokenize to ZERO tokens overall
    (lexically empty input: emoji/symbol/punctuation/whitespace/format). It is
    *not* "no non-ASCII token": non-ASCII letters that NFKC/casefold fold to
    ASCII forms (fullwidth/mathematical-alphanumeric letters, ``ß`` -> ``ss``,
    ...) still produce normal ASCII tokens and are counted by
    ``ascii_folded_messages`` — lexical material was fully preserved, just
    normalized.
    """

    non_ascii_messages: int = 0
    messages_with_umlauts_or_accents: int = 0
    tokens_total: int = 0
    unicode_tokens: int = 0
    zero_token_messages: int = 0
    zero_token_by_category: dict[str, int] = field(default_factory=dict)
    ascii_folded_messages: int = 0


@dataclass(frozen=True, slots=True)
class AuditPhase21AStats:
    """Occurrence counts for the Phase 21A ES trigger-gap verbs."""

    verb_sentences: int = 0
    detected_es: int = 0
    language_unknown: int = 0


@dataclass(frozen=True, slots=True)
class AuditIdempotencyStats:
    """Two-pass memory-store comparison on a scratch database."""

    conversations_seeded: int = 0
    messages_seeded: int = 0
    memories_pass1: int = 0
    memories_pass2: int = 0
    evidence_pass1: int = 0
    evidence_pass2: int = 0
    memories_growth: int = 0
    evidence_growth: int = 0

    @property
    def idempotent(self) -> bool:
        return self.memories_growth == 0 and self.evidence_growth == 0


@dataclass(frozen=True, slots=True)
class CorpusAuditReport:
    """Aggregate-only outcome of a bounded real-corpus audit.

    Every field is a count or a category tally — never content, never
    identifiers. Suitable for CLI printing and JSON emission.
    """

    source_type: str
    export_path: str
    conversations_available: int
    conversations_sampled: int
    messages_sampled: int
    user_messages: int
    languages: AuditLanguageStats
    extraction: AuditExtractionStats
    skip_reasons: dict[str, int]
    security: AuditSecurityStats
    provenance: AuditProvenanceStats
    unicode: AuditUnicodeStats
    phase21a: AuditPhase21AStats
    unicode_errors: list[str]
    model_calls: int = 0
    llm_proposal_layer: str = "not_exercised"
    idempotency: AuditIdempotencyStats | None = None

    def summary(self) -> dict[str, object]:
        """Content-free view for CLI/JSON output."""
        return {
            "source_type": self.source_type,
            "export_path": self.export_path,
            "conversations_available": self.conversations_available,
            "conversations_sampled": self.conversations_sampled,
            "messages_sampled": self.messages_sampled,
            "user_messages": self.user_messages,
            "languages": dict(self.languages.languages),
            "mixed_language_messages": self.languages.mixed_language_messages,
            "unknown_messages": self.languages.unknown_messages,
            "candidates": self.extraction.candidates,
            "candidates_by_kind": dict(self.extraction.by_kind),
            "candidates_by_language": dict(self.extraction.by_language),
            "recurring_candidates": self.extraction.recurring,
            "temporal": dict(self.extraction.temporal),
            "skip_reasons": dict(self.skip_reasons),
            "policy_decisions": dict(self.security.decisions),
            "sensitivity": dict(self.security.sensitivity),
            "secret_rejected": self.security.secret_rejected,
            "require_approval": self.security.require_approval,
            "evidence_valid": self.provenance.evidence_valid,
            "evidence_invalid": self.provenance.evidence_invalid,
            "non_ascii_messages": self.unicode.non_ascii_messages,
            "umlaut_or_accent_messages": self.unicode.messages_with_umlauts_or_accents,
            "tokens_total": self.unicode.tokens_total,
            "unicode_tokens": self.unicode.unicode_tokens,
            "zero_token_messages": self.unicode.zero_token_messages,
            "zero_token_by_category": dict(self.unicode.zero_token_by_category),
            "ascii_folded_messages": self.unicode.ascii_folded_messages,
            "phase21a_verb_sentences": self.phase21a.verb_sentences,
            "phase21a_detected_es": self.phase21a.detected_es,
            "phase21a_language_unknown": self.phase21a.language_unknown,
            "unicode_errors": list(self.unicode_errors),
            "model_calls": self.model_calls,
            "llm_proposal_layer": self.llm_proposal_layer,
            "idempotency": (
                None
                if self.idempotency is None
                else {
                    "conversations_seeded": self.idempotency.conversations_seeded,
                    "messages_seeded": self.idempotency.messages_seeded,
                    "memories_pass1": self.idempotency.memories_pass1,
                    "memories_pass2": self.idempotency.memories_pass2,
                    "evidence_pass1": self.idempotency.evidence_pass1,
                    "evidence_pass2": self.idempotency.evidence_pass2,
                    "memories_growth": self.idempotency.memories_growth,
                    "evidence_growth": self.idempotency.evidence_growth,
                    "idempotent": self.idempotency.idempotent,
                }
            ),
        }


def _discover_chatgpt_sample(
    loader: ChatGPTConversationLoader,
    limit: int,
    max_messages: int,
) -> tuple[list[_SampleUnit], int]:
    """Return ``(bounded_sample, conversations_available)`` for ChatGPT.

    Discovery order is deterministic (sorted shard paths); conversations are
    taken in shard order up to ``limit``, and each conversation's messages are
    capped. ``conversations_available`` is the full corpus count (all shards),
    counted without materializing per-message rows.
    """
    shards = loader.discover_shards()
    sample: list[_SampleUnit] = []
    for shard_path in shards:
        if len(sample) >= limit:
            break
        for conv, msgs, _atts in loader.load_shard(shard_path):
            if len(sample) < limit:
                sample.append(_SampleUnit(conv, msgs[:max_messages]))
    return sample, _count_chatgpt_conversations(loader)


def _discover_gemini_sample(
    loader: GeminiConversationLoader,
    limit: int,
    max_messages: int,
) -> tuple[list[_SampleUnit], int]:
    """Return ``(bounded_sample, conversations_available)`` for Gemini."""
    files = loader.discover_files()
    sample: list[_SampleUnit] = []
    for path in files[:limit]:
        conv, msgs = loader.load_file(path)
        sample.append(_SampleUnit(conv, msgs[:max_messages]))
    return sample, len(files)


def _count_chatgpt_conversations(loader: ChatGPTConversationLoader) -> int:
    """Count conversations across all discovered shards (shard list lengths)."""
    total = 0
    for shard_path in loader.discover_shards():
        data = json.loads(shard_path.read_bytes())
        if isinstance(data, list):
            total += len(data)
    return total


def _audit_languages(
    units: list[_SampleUnit],
) -> tuple[AuditLanguageStats, dict[str, ConversationMessage]]:
    """Tally message-level languages and map message ids to user messages."""
    languages: dict[str, int] = {}
    mixed = 0
    unknown = 0
    messages_by_id: dict[str, ConversationMessage] = {}
    for unit in units:
        for message in unit.messages:
            if (message.role or "").strip().lower() != "user":
                continue
            messages_by_id[message.id] = message
            content = message.content_text or ""
            sentence_langs = {
                _detect_language(sentence)
                for sentence in _sentence_split(content)
                if sentence.strip()
            }
            real = {
                lang
                for lang in sentence_langs
                if lang is not ConversationLanguage.UNKNOWN
            }
            if len(real) > 1:
                languages["mixed"] = languages.get("mixed", 0) + 1
                mixed += 1
            elif len(real) == 1:
                key = next(iter(real)).value
                languages[key] = languages.get(key, 0) + 1
            else:
                languages["unknown"] = languages.get("unknown", 0) + 1
                unknown += 1
    return AuditLanguageStats(
        languages=languages, mixed_language_messages=mixed, unknown_messages=unknown
    ), messages_by_id


def _classify_candidate_language(
    candidate: MemoryCandidate,
    messages_by_id: dict[str, ConversationMessage],
) -> str:
    """Best-effort language of a candidate from its first evidence message."""
    for ref in candidate.evidence:
        message = messages_by_id.get(ref.source_document_id or "")
        if message is not None:
            lang = _detect_language(message.content_text or "")
            if lang is not ConversationLanguage.UNKNOWN:
                return lang.value
            return "unknown"
    return "unknown"


def _extract_candidates(
    units: list[_SampleUnit], max_messages: int
) -> tuple[list[MemoryCandidate], dict[str, int]]:
    """Run the deterministic extractor once over the sample.

    Returns ``(candidates, skip_reasons)``; ``skip_reasons`` is a single
    content-free counter merged across all conversations.
    """
    extractor = ConversationMemoryExtractor(max_messages=max_messages)
    candidates: list[MemoryCandidate] = []
    skip_reasons: dict[str, int] = {}
    for unit in units:
        reasons: dict[str, int] = {}
        extracted = extractor.extract(
            unit.conversation, unit.messages, skip_reasons=reasons
        )
        for reason, count in reasons.items():
            skip_reasons[reason] = skip_reasons.get(reason, 0) + count
        candidates.extend(extracted)
    return candidates, skip_reasons


def _audit_extraction(
    candidates: list[MemoryCandidate],
    messages_by_id: dict[str, ConversationMessage],
) -> AuditExtractionStats:
    """Tally candidate kinds, temporal scopes, and approximated languages."""
    by_kind: dict[str, int] = {}
    by_language: dict[str, int] = {}
    temporal: dict[str, int] = {}
    recurring = 0
    for candidate in candidates:
        by_kind[candidate.kind.value] = by_kind.get(candidate.kind.value, 0) + 1
        temporal[candidate.temporal_scope.value] = (
            temporal.get(candidate.temporal_scope.value, 0) + 1
        )
        if candidate.recurrence > 1:
            recurring += 1
        lang = _classify_candidate_language(candidate, messages_by_id)
        by_language[lang] = by_language.get(lang, 0) + 1
    return AuditExtractionStats(
        candidates=len(candidates),
        by_kind=by_kind,
        by_language=by_language,
        recurring=recurring,
        temporal=temporal,
    )


def _audit_security(candidates: Sequence[MemoryCandidate]) -> AuditSecurityStats:
    """Evaluate deterministic policy over the extracted candidates (no writes)."""
    from personal_ai.memory.policy import MemoryDecision, MemoryPolicy, Sensitivity

    decisions: dict[str, int] = {}
    sensitivity: dict[str, int] = {}
    secret_rejected = 0
    require_approval = 0
    policy = MemoryPolicy()
    for candidate in candidates:
        decision = policy.evaluate(candidate)
        decisions[decision.decision.value] = (
            decisions.get(decision.decision.value, 0) + 1
        )
        sensitivity[decision.sensitivity.value] = (
            sensitivity.get(decision.sensitivity.value, 0) + 1
        )
        if (
            decision.decision is MemoryDecision.REJECT
            and decision.sensitivity is Sensitivity.SECRET
        ):
            secret_rejected += 1
        if decision.decision is MemoryDecision.REQUIRE_APPROVAL:
            require_approval += 1
    return AuditSecurityStats(
        decisions=decisions,
        sensitivity=sensitivity,
        secret_rejected=secret_rejected,
        require_approval=require_approval,
    )


def _audit_provenance(
    candidates: Sequence[MemoryCandidate],
    messages_by_id: dict[str, ConversationMessage],
    source_type: str,
) -> AuditProvenanceStats:
    """Validate that every evidence reference points into the sample."""
    evidence_valid = 0
    evidence_invalid = 0
    for candidate in candidates:
        for ref in candidate.evidence:
            message = messages_by_id.get(ref.source_document_id or "")
            valid = (
                message is not None
                and ref.source_type == source_type
                and (message.role or "").strip().lower() == "user"
            )
            if valid:
                evidence_valid += 1
            else:
                evidence_invalid += 1
    return AuditProvenanceStats(
        evidence_valid=evidence_valid, evidence_invalid=evidence_invalid
    )


def _audit_unicode(units: list[_SampleUnit]) -> tuple[AuditUnicodeStats, list[str]]:
    """Measure Unicode handling: non-ASCII, umlauts, zero-token gaps.

    ``zero_token_messages`` counts messages that tokenize to zero tokens at all.
    They are classified into content-free buckets so the audit can distinguish
    "unexpected zero-token *lexical* messages" (a defect) from legitimate
    emoji/symbol/punctuation-only input (expected). Only the lexical bucket is
    surfaced as an error; the others are reported as counts.

    Messages whose non-ASCII letters are NFKC/casefold-folded to ASCII forms
    (fullwidth/mathematical-alphanumeric letters, ``ß`` -> ``ss``) still
    tokenize normally and are counted as ``ascii_folded_messages`` — fully
    escaped: no error, no token loss, by design of the shared normalizer.
    """
    non_ascii = 0
    accent_messages = 0
    tokens_total = 0
    unicode_tokens = 0
    zero_token = 0
    ascii_folded = 0
    zero_token_by_category: dict[str, int] = {}
    for unit in units:
        for message in unit.messages:
            if (message.role or "").strip().lower() != "user":
                continue
            content = message.content_text or ""
            if _NON_ASCII_RE.search(content):
                non_ascii += 1
            tokens = tokenize(content)
            tokens_total += len(tokens)
            count_unicode = sum(1 for tok in tokens if _NON_ASCII_RE.search(tok))
            unicode_tokens += count_unicode
            if count_unicode:
                accent_messages += 1
            elif _NON_ASCII_RE.search(content):
                if not tokens:
                    zero_token += 1
                    category = _classify_zero_token(content)
                    zero_token_by_category[category] = (
                        zero_token_by_category.get(category, 0) + 1
                    )
                else:
                    ascii_folded += 1
    lexical_zero = zero_token_by_category.get("lexical_unicode", 0)
    errors: list[str] = []
    if lexical_zero:
        errors.append(f"zero_token_lexical_messages={lexical_zero}")
    return (
        AuditUnicodeStats(
            non_ascii_messages=non_ascii,
            messages_with_umlauts_or_accents=accent_messages,
            tokens_total=tokens_total,
            unicode_tokens=unicode_tokens,
            zero_token_messages=zero_token,
            zero_token_by_category=zero_token_by_category,
            ascii_folded_messages=ascii_folded,
        ),
        errors,
    )


def _audit_phase21a(units: list[_SampleUnit]) -> AuditPhase21AStats:
    """Count the ES pro-drop verb gap without implementing the fix."""
    verb_sentences = 0
    detected_es = 0
    language_unknown = 0
    for unit in units:
        for message in unit.messages:
            if (message.role or "").strip().lower() != "user":
                continue
            for sentence in _sentence_split(message.content_text or ""):
                if not _PHASE21A_RE.search(sentence):
                    continue
                verb_sentences += 1
                lang = _detect_language(sentence)
                if lang is ConversationLanguage.ES:
                    detected_es += 1
                elif lang is ConversationLanguage.UNKNOWN:
                    language_unknown += 1
    return AuditPhase21AStats(
        verb_sentences=verb_sentences,
        detected_es=detected_es,
        language_unknown=language_unknown,
    )


def _seed_and_run_idempotency(
    source_type: str,
    units: list[_SampleUnit],
    scratch_database: Path,
    max_messages: int,
) -> AuditIdempotencyStats:
    """Two-pass run over the same bounded sample on a scratch database.

    Seeds the sample into a scratch ``ConversationStore`` and runs the existing
    ``ConversationMemoryIngestor`` (the exact production write path through the
    automatic policy gate) twice, then compares memory-store counts. The second
    pass must produce zero memory/evidence growth to be idempotent.
    """
    from personal_ai.memory.service import MemoryService
    from personal_ai.memory.store import open_memory_store
    from personal_ai.storage.conversations import ConversationStore
    from personal_ai.storage.documents import connect_database

    connection = connect_database(scratch_database)
    memory_connection = None
    try:
        conversation_store = ConversationStore(connection)
        seeded_convs = 0
        seeded_msgs = 0
        for unit in units:
            conversation_store.save_conversation(unit.conversation)
            for message in unit.messages:
                conversation_store.save_message(message)
                seeded_msgs += 1
            seeded_convs += 1

        memory_connection, memory_store = open_memory_store(scratch_database)
        service = MemoryService(memory_store)
        ingestor = ConversationMemoryIngestor(
            conversation_store,
            service,
            max_conversations=seeded_convs,
            max_messages_per_conversation=max_messages,
        )
        ingestor.ingest(source_type)
        pass1 = memory_store.statistics()
        ingestor.ingest(source_type)
        pass2 = memory_store.statistics()

        pass1_evidence = int(pass1.get("evidence", 0))
        pass2_evidence = int(pass2.get("evidence", 0))
        pass1_memories = int(pass1["by_status"].get("memories", 0))
        pass2_memories = int(pass2["by_status"].get("memories", 0))
        return AuditIdempotencyStats(
            conversations_seeded=seeded_convs,
            messages_seeded=seeded_msgs,
            memories_pass1=pass1_memories,
            memories_pass2=pass2_memories,
            evidence_pass1=pass1_evidence,
            evidence_pass2=pass2_evidence,
            memories_growth=pass2_memories - pass1_memories,
            evidence_growth=pass2_evidence - pass1_evidence,
        )
    finally:
        if memory_connection is not None:
            memory_connection.close()
        connection.close()


def run_corpus_audit(
    source_type: str,
    export_path: str | Path,
    *,
    limit: int = DEFAULT_AUDIT_LIMIT,
    max_messages: int = DEFAULT_AUDIT_MAX_MESSAGES,
    scratch_database: str | Path | None = None,
) -> CorpusAuditReport:
    """Run a bounded, aggregate-only audit over raw conversation exports.

    Only the raw export files are read; the production database is never
    opened. ``scratch_database`` (optional) is used only for the two-pass
    idempotency check and may point at any disposable file.
    """
    path = Path(export_path)
    if not path.is_dir():
        raise NotADirectoryError(f"export path is not a directory: {path}")
    if source_type not in CONVERSATION_SOURCE_TYPES:
        raise ValueError(f"unsupported conversation source: {source_type!r}")
    if not 1 <= limit <= MAX_CONVERSATIONS_PER_SOURCE:
        raise ValueError(f"limit must be in [1, {MAX_CONVERSATIONS_PER_SOURCE}]")
    if max_messages < 1:
        raise ValueError("max_messages must be >= 1")

    if source_type == "chatgpt":
        loader = ChatGPTConversationLoader(path)
        units, available = _discover_chatgpt_sample(loader, limit, max_messages)
    else:
        loader = GeminiConversationLoader(path)
        units, available = _discover_gemini_sample(loader, limit, max_messages)

    languages, messages_by_id = _audit_languages(units)
    candidates, skip_reasons = _extract_candidates(units, max_messages)
    extraction = _audit_extraction(candidates, messages_by_id)
    security = _audit_security(candidates)
    provenance = _audit_provenance(candidates, messages_by_id, source_type)
    unicode_stats, unicode_errors = _audit_unicode(units)
    phase21a = _audit_phase21a(units)

    total_messages = sum(len(unit.messages) for unit in units)
    user_messages = sum(
        1
        for unit in units
        for message in unit.messages
        if (message.role or "").strip().lower() == "user"
    )

    idempotency_report: AuditIdempotencyStats | None = None
    if scratch_database is not None:
        idempotency_report = _seed_and_run_idempotency(
            source_type, units, Path(scratch_database), max_messages
        )

    return CorpusAuditReport(
        source_type=source_type,
        export_path=str(path),
        conversations_available=available,
        conversations_sampled=len(units),
        messages_sampled=total_messages,
        user_messages=user_messages,
        languages=languages,
        extraction=extraction,
        skip_reasons=skip_reasons,
        security=security,
        provenance=provenance,
        unicode=unicode_stats,
        phase21a=phase21a,
        unicode_errors=unicode_errors,
        model_calls=0,
        idempotency=idempotency_report,
    )
