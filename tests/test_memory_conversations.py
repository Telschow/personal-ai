"""Tests for deterministic conversation memory extraction.

Coverage focuses on the conservative user-authored extraction rules (only
first-person self-assertions on the active branch), on the guarantees that
negation/questions/requests/sensitive material never surface as candidates,
on temporal/kind mapping, on provenance-only evidence, and on the fact that
every candidate still travels through the policy-gated curator: accepted
candidates persist exactly once (idempotent) and deferred/rejected candidates
never write.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from personal_ai.agents.policy import ApprovalRequiredError
from personal_ai.documents.conversations import (
    Conversation,
    ConversationMessage,
)
from personal_ai.memory.conversations import (
    CONVERSATION_SOURCE_TYPES,
    ConversationMemoryExtractor,
    ConversationMemoryIngestor,
    ConversationSourceError,
)
from personal_ai.memory.models import (
    MemoryEvidenceRef,
    MemoryKind,
    MemoryStatus,
    TemporalScope,
)
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryStore
from personal_ai.storage import ConversationStore, connect_database


def _service() -> MemoryService:
    return MemoryService(MemoryStore(sqlite3.connect(":memory:")))


def _conv(
    conv_id: str = "conv-1",
    *,
    source_type: str = "chatgpt",
    title: str = "Test",
    created_at: str = "2026-01-01T00:00:00+00:00",
    modified_at: str = "2026-01-02T00:00:00+00:00",
) -> Conversation:
    return Conversation(
        id=conv_id,
        title=title,
        source_type=source_type,
        created_at=created_at,
        modified_at=modified_at,
        metadata={},
    )


def _msg(
    msg_id: str,
    content: str,
    *,
    role: str = "user",
    index: int = 0,
    timestamp: str | None = None,
    is_active_branch: bool = True,
    conv_id: str = "conv-1",
) -> ConversationMessage:
    return ConversationMessage(
        id=msg_id,
        conversation_id=conv_id,
        message_index=index,
        role=role,
        speaker=role.capitalize(),
        content_text=content,
        content_type="text",
        timestamp=timestamp,
        is_active_branch=is_active_branch,
        metadata={},
    )


def _extract(
    conversation: Conversation,
    messages: tuple[ConversationMessage, ...],
) -> tuple:
    return ConversationMemoryExtractor().extract(conversation, messages)


# ---- extraction: kinds and temporal scope ----------------------------------


def test_work_current() -> None:
    (candidate,) = _extract(_conv(), (_msg("m1", "I work at BCG"),))
    assert candidate.statement == "The user works at BCG"
    assert candidate.kind is MemoryKind.WORK
    assert candidate.temporal_scope is TemporalScope.CURRENT


def test_work_historical() -> None:
    (candidate,) = _extract(_conv(), (_msg("m1", "I used to work at McKinsey"),))
    assert candidate.kind is MemoryKind.WORK
    assert candidate.temporal_scope is TemporalScope.HISTORICAL


def test_goal_never_becomes_achieved_fact() -> None:
    (candidate,) = _extract(
        _conv(), (_msg("m1", "I want to run a marathon next year"),)
    )
    assert candidate.statement == "The user wants to run a marathon next year"
    assert candidate.kind is MemoryKind.GOAL
    assert candidate.temporal_scope is TemporalScope.CURRENT


def test_habit_regular_timeframe() -> None:
    (candidate,) = _extract(_conv(), (_msg("m1", "I exercise every morning"),))
    assert candidate.statement == "The user exercises every morning"
    assert candidate.kind is MemoryKind.HABIT
    assert candidate.temporal_scope is TemporalScope.RECURRING
    assert candidate.recurrence == 3


def test_identity_name() -> None:
    (candidate,) = _extract(_conv(), (_msg("m1", "My name is John"),))
    assert candidate.statement == "The user's name is John"
    assert candidate.kind is MemoryKind.IDENTITY
    assert candidate.temporal_scope is TemporalScope.CURRENT


def test_biography() -> None:
    (candidate,) = _extract(_conv(), (_msg("m1", "I was born in London"),))
    assert candidate.statement == "The user was born in London"
    assert candidate.kind is MemoryKind.BIOGRAPHY
    assert candidate.temporal_scope is TemporalScope.HISTORICAL


def test_education() -> None:
    (candidate,) = _extract(_conv(), (_msg("m1", "I graduated from MIT"),))
    assert candidate.kind is MemoryKind.EDUCATION
    assert candidate.temporal_scope is TemporalScope.HISTORICAL


def test_location_current_and_historical() -> None:
    current = _extract(_conv(), (_msg("m1", "I live in Berlin"),))[0]
    historical = _extract(_conv(), (_msg("m1", "I moved to Berlin"),))[0]
    assert current.kind is MemoryKind.LOCATION_CONTEXT
    assert current.temporal_scope is TemporalScope.CURRENT
    assert historical.temporal_scope is TemporalScope.HISTORICAL


def test_skill_learning() -> None:
    (candidate,) = _extract(_conv(), (_msg("m1", "I'm learning Portuguese"),))
    assert candidate.statement == "The user is learning Portuguese"
    assert candidate.kind is MemoryKind.SKILL


def test_interest() -> None:
    (candidate,) = _extract(_conv(), (_msg("m1", "I like cooking"),))
    assert candidate.statement == "The user likes cooking"
    assert candidate.kind is MemoryKind.INTEREST


def test_preference() -> None:
    (candidate,) = _extract(_conv(), (_msg("m1", "I prefer async meetings"),))
    assert candidate.statement == "The user prefers async meetings"
    assert candidate.kind is MemoryKind.PREFERENCE


def test_relationship() -> None:
    (candidate,) = _extract(_conv(), (_msg("m1", "My wife is a doctor"),))
    assert candidate.statement == "The user's wife is a doctor"
    assert candidate.kind is MemoryKind.RELATIONSHIP


def test_personal_fact_ownership() -> None:
    (candidate,) = _extract(_conv(), (_msg("m1", "I have a dog"),))
    assert candidate.statement == "The user has a dog"
    assert candidate.kind is MemoryKind.PERSONAL_FACT


def test_gemini_source_type_is_supported() -> None:
    conversation = _conv(source_type="gemini")
    (candidate,) = _extract(conversation, (_msg("m1", "I live in Lisbon"),))
    assert candidate.kind is MemoryKind.LOCATION_CONTEXT
    assert candidate.evidence[0].source_type == "gemini"


def test_conversation_source_types_are_exactly_chatgpt_and_gemini() -> None:
    assert CONVERSATION_SOURCE_TYPES == frozenset({"chatgpt", "gemini"})


def test_unsupported_source_type_yields_nothing() -> None:
    conversation = _conv(source_type="email")
    assert _extract(conversation, (_msg("m1", "I live in Lisbon"),)) == ()


# ---- extraction: exclusions -------------------------------------------------


def test_assistant_messages_never_yield_candidates() -> None:
    conversation = _conv()
    messages = (_msg("m1", "I work at BCG", role="assistant"),)
    assert _extract(conversation, messages) == ()


def test_inactive_branch_messages_are_skipped() -> None:
    conversation = _conv()
    messages = (_msg("m1", "I live in Paris", is_active_branch=False),)
    assert _extract(conversation, messages) == ()


def test_non_first_person_is_ignored() -> None:
    conversation = _conv()
    messages = (_msg("m1", "BCG is a great consulting firm"),)
    assert _extract(conversation, messages) == ()


def test_negation_is_never_a_fact() -> None:
    for sentence in (
        "I don't work at BCG",
        "I do not work at BCG",
        "I did not graduate from MIT",
        "I never lived in Berlin",
        "I can't swim",
    ):
        assert _extract(_conv(), (_msg("m1", sentence),)) == (), sentence


def test_questions_are_skipped() -> None:
    conversation = _conv()
    messages = (_msg("m1", "Do I work at BCG?"),)
    assert _extract(conversation, messages) == ()


def test_requests_directed_at_the_model_are_skipped() -> None:
    for sentence in (
        "Write a poem about my dog",
        "Can you summarize my resume?",
        "Tell me what my strengths are",
        "I want you to review my resume",
    ):
        assert _extract(_conv(), (_msg("m1", sentence),)) == (), sentence


def test_statements_turned_toward_you_are_skipped() -> None:
    conversation = _conv()
    messages = (_msg("m1", "I work at BCG with you"),)
    assert _extract(conversation, messages) == ()


def test_quoted_content_is_skipped() -> None:
    conversation = _conv()
    messages = (_msg("m1", 'My boss said "I work at BCG"'),)
    assert _extract(conversation, messages) == ()


def test_sensitive_keywords_are_never_proposed() -> None:
    conversation = _conv()
    messages = (_msg("m1", "My salary is huge"),)
    assert _extract(conversation, messages) == ()


def test_sensitive_forms_are_never_proposed() -> None:
    for sentence in (
        "I earn €5000 a month",
        "My email is me@example.com",
        "My site is https://example.com",
        "My card number is 1234 5678 9012 3456",
    ):
        assert _extract(_conv(), (_msg("m1", sentence),)) == (), sentence


def test_credential_shaped_sentinels_are_never_proposed() -> None:
    for sentence in (
        "My key is sk_live_1234567890abcdef",
        "My aws key is AKIA1234567890ABCDEF",
    ):
        assert _extract(_conv(), (_msg("m1", sentence),)) == (), sentence


# ---- extraction: statement bounds and deduplication -------------------------


def test_overlong_statements_are_skipped() -> None:
    conversation = _conv()
    message = _msg("m1", "I want to " + "word " * 200)
    assert _extract(conversation, (message,)) == ()


def test_repeated_fact_within_conversation_collapses() -> None:
    conversation = _conv()
    messages = (
        _msg("m1", "I work at BCG.", index=0),
        _msg("m2", "I work at BCG", index=1),
    )
    candidates = _extract(conversation, messages)
    assert len(candidates) == 1
    assert candidates[0].evidence[0].source_document_id == "m1"


def test_max_candidates_bounds() -> None:
    extractor = ConversationMemoryExtractor(max_candidates=1)
    messages = tuple(
        _msg(f"m{i}", sentence, index=i)
        for i, sentence in enumerate(
            (
                "I work at BCG",
                "My name is John",
                "I live in London",
                "I like cooking",
            )
        )
    )
    candidates = extractor.extract(_conv(), messages)
    assert len(candidates) == 1


def test_max_messages_bounds() -> None:
    extractor = ConversationMemoryExtractor(max_messages=1)
    conversation = _conv()
    messages = (
        _msg("m1", "I work at BCG", index=0),
        _msg("m2", "My name is John", index=1),
    )
    candidates = extractor.extract(conversation, messages)
    assert [c.statement for c in candidates] == ["The user works at BCG"]


def test_empty_and_whitespace_messages_yield_nothing() -> None:
    conversation = _conv()
    messages = (_msg("m1", "   "), _msg("m2", "", index=1))
    assert _extract(conversation, messages) == ()


# ---- extraction: provenance-only evidence -----------------------------------


def test_evidence_points_at_the_message_without_content() -> None:
    conversation = _conv()
    message = _msg("msg-42", "I work at BCG", timestamp="2026-01-15T10:30:00+00:00")
    (candidate,) = _extract(conversation, (message,))

    (ref,) = candidate.evidence
    assert isinstance(ref, MemoryEvidenceRef)
    assert ref.source_type == "chatgpt"
    assert ref.source_id == "conv-1"
    assert ref.source_document_id == "msg-42"
    assert ref.source_timestamp == "2026-01-15T10:30:00+00:00"

    evidence_dict = ref.to_dict()
    assert set(evidence_dict) == {
        "source_type",
        "source_id",
        "source_document_id",
        "source_timestamp",
    }

    picked = ref
    assert "BCG" not in str(picked.to_dict().values())


def test_gemini_evidence_falls_back_to_conversation_timestamp() -> None:
    conversation = _conv(
        source_type="gemini",
        created_at="2026-01-03T00:00:00+00:00",
        modified_at="2026-01-04T00:00:00+00:00",
    )
    message = _msg("m1", "I live in Lisbon")  # Gemini timestamps are None
    (candidate,) = _extract(conversation, (message,))
    assert candidate.evidence[0].source_timestamp == "2026-01-04T00:00:00+00:00"
    assert not any(
        "Lisbon" in str(value)
        for ref in candidate.evidence
        for value in ref.to_dict().values()
    )


# ---- ingestor orchestration --------------------------------------------------


def _store(*conversations):
    connection = connect_database(":memory:")
    store = ConversationStore(connection)
    for conv, messages in conversations:
        store.save_conversation(conv)
        store.save_messages(messages)
    return connection, store


def _chatgpt_work_pair():
    return _conv(), (
        _msg("m1", "I work at BCG", index=0),
        _msg("m2", "Can you draft a report?", role="assistant", index=1),
    )


def test_ingestor_rejects_unsupported_source() -> None:
    connection, store = _store(_chatgpt_work_pair())
    try:
        ingestor = ConversationMemoryIngestor(store, _service())
        with pytest.raises(ConversationSourceError):
            ingestor.ingest("email")
    finally:
        connection.close()


def test_ingestor_applies_accepted_candidate_and_is_idempotent() -> None:
    service = _service()
    connection, store = _store(_chatgpt_work_pair())
    try:
        ingestor = ConversationMemoryIngestor(store, service)

        first = ingestor.ingest("chatgpt")
        second = ingestor.ingest("chatgpt")

        assert first.conversations_scanned == 1
        assert first.user_messages_scanned == 1
        assert first.tally.candidates == 1
        assert first.tally.accepted == 1
        assert first.tally.writes == 1
        assert first.tally.deferred == 0
        assert first.tally.rejected == 0
        assert first.evidence_rows_added == 1

        assert second.tally.writes == 1
        assert second.tally.created == 0
        assert second.tally.updated == 1
        assert second.evidence_rows_added == 0

        assert service.counts()["active"] == 1
        memory = service.list()[0]
        assert memory.status is MemoryStatus.ACTIVE
        assert memory.content == "The user works at BCG"
        assert len(service.evidence_for(memory.memory_id)) == 1
    finally:
        connection.close()


def test_run_scans_only_the_requested_source_type() -> None:
    service = _service()
    gemini_conv = _conv("conv-g", source_type="gemini")
    chatgpt_conv, _ = _chatgpt_work_pair()
    connection, store = _store(
        (gemini_conv, (_msg("m-g", "I live in Lisbon", conv_id="conv-g"),)),
        (chatgpt_conv, (_msg("m1", "I work at BCG"),)),
    )
    try:
        ingestor = ConversationMemoryIngestor(store, service)

        chatgpt_report = ingestor.ingest("chatgpt")
        gemini_report = ingestor.ingest("gemini")

        assert chatgpt_report.conversations_scanned == 1
        assert gemini_report.conversations_scanned == 1
        assert service.counts()["active"] == 2
        statements = {memory.content for memory in service.list()}
        assert statements == {
            "The user works at BCG",
            "The user lives in Lisbon",
        }
    finally:
        connection.close()


def test_denied_gate_raises_and_never_writes() -> None:
    service = _service()
    connection, store = _store(_chatgpt_work_pair())
    try:
        ingestor = ConversationMemoryIngestor(
            store,
            service,
            auto_approver=lambda *args: False,
        )
        with pytest.raises(ApprovalRequiredError):
            ingestor.ingest("chatgpt")
        assert service.counts()["memories"] == 0
    finally:
        connection.close()


def test_report_is_aggregate_only_and_content_free() -> None:
    service = _service()
    connection, store = _store(_chatgpt_work_pair())
    try:
        report = ConversationMemoryIngestor(store, service).ingest("chatgpt")
    finally:
        connection.close()

    rendered = json.dumps(report.summary())
    assert "render" not in rendered  # nothing content-shaped is serialized
    assert "statement" not in rendered
    assert "BCG" not in rendered
    assert report.summary()["source_type"] == "chatgpt"
    assert report.summary()["conversations_scanned"] == 1


def test_ingestor_reads_are_bounded() -> None:
    service = _service()
    conversations = [
        (
            _conv(f"conv-{index}"),
            (_msg(f"m-{index}", "I work at BCG", conv_id=f"conv-{index}"),),
        )
        for index in range(12)
    ]
    connection, store = _store(*conversations)
    try:
        ingestor = ConversationMemoryIngestor(store, service, max_conversations=5)
        report = ingestor.ingest("chatgpt")
        assert report.conversations_scanned == 5
    finally:
        connection.close()
