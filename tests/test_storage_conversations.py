"""Tests for ConversationStore SQLite persistence."""

import pytest

from personal_ai.documents.conversations import (
    Conversation,
    ConversationAttachment,
    ConversationMessage,
)
from personal_ai.storage import ConversationStore, connect_database


def _conv(
    conv_id: str = "conv-1",
    title: str = "Test Conversation",
    source_type: str = "gemini",
) -> Conversation:
    return Conversation(
        id=conv_id,
        title=title,
        source_type=source_type,
        created_at="2026-01-01T00:00:00+00:00",
        modified_at="2026-01-02T00:00:00+00:00",
        metadata={"key": "value"},
    )


def _msg(
    msg_id: str = "msg-1",
    conv_id: str = "conv-1",
    index: int = 0,
    role: str = "user",
    content: str = "Hello",
    speaker: str = "User",
    content_type: str = "text",
    timestamp: str | None = None,
    parent_message_id: str | None = None,
    is_active_branch: bool = True,
) -> ConversationMessage:
    return ConversationMessage(
        id=msg_id,
        conversation_id=conv_id,
        message_index=index,
        role=role,
        speaker=speaker,
        content_text=content,
        content_type=content_type,
        timestamp=timestamp,
        parent_message_id=parent_message_id,
        is_active_branch=is_active_branch,
        metadata={},
    )


def _att(
    att_id: str = "att-1",
    msg_id: str = "msg-1",
    filename: str = "document.pdf",
    mime_type: str = "application/pdf",
    size_bytes: int = 1024,
    blob_path: str = "",
) -> ConversationAttachment:
    return ConversationAttachment(
        id=att_id,
        message_id=msg_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        blob_path=blob_path,
        metadata={},
    )


class TestConversationStore:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_save_new_conversation(self) -> None:
        conv = _conv()
        assert self.store.save_conversation(conv) is True

    def test_get_conversation(self) -> None:
        conv = _conv()
        self.store.save_conversation(conv)
        retrieved = self.store.get_conversation("conv-1")
        assert retrieved is not None
        assert retrieved.id == "conv-1"
        assert retrieved.title == "Test Conversation"
        assert retrieved.source_type == "gemini"
        assert retrieved.metadata == {"key": "value"}

    def test_get_nonexistent_returns_none(self) -> None:
        assert self.store.get_conversation("nonexistent") is None

    def test_list_conversations(self) -> None:
        self.store.save_conversation(_conv("c1", "First"))
        self.store.save_conversation(_conv("c2", "Second"))
        result = self.store.list_conversations()
        assert len(result) == 2

    def test_list_by_source_type(self) -> None:
        self.store.save_conversation(_conv("c1", "First", source_type="gemini"))
        self.store.save_conversation(_conv("c2", "Second", source_type="chatgpt"))
        result = self.store.list_conversations(source_type="gemini")
        assert len(result) == 1
        assert result[0].id == "c1"

    def test_count_conversations(self) -> None:
        self.store.save_conversation(_conv("c1"))
        self.store.save_conversation(_conv("c2"))
        assert self.store.count_conversations() == 2

    def test_count_by_source_type(self) -> None:
        self.store.save_conversation(_conv("c1", source_type="gemini"))
        self.store.save_conversation(_conv("c2", source_type="chatgpt"))
        assert self.store.count_conversations(source_type="gemini") == 1


class TestMessageStore:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)
        # Messages require parent conversations (foreign key constraint)
        self.store.save_conversation(_conv("conv-1"))
        self.store.save_conversation(_conv("c1", "Conv C1"))
        self.store.save_conversation(_conv("c2", "Conv C2"))

    def teardown_method(self) -> None:
        self.connection.close()

    def test_save_new_message(self) -> None:
        msg = _msg()
        assert self.store.save_message(msg) is True

    def test_get_message(self) -> None:
        msg = _msg()
        self.store.save_message(msg)
        retrieved = self.store.get_message("msg-1")
        assert retrieved is not None
        assert retrieved.id == "msg-1"
        assert retrieved.conversation_id == "conv-1"
        assert retrieved.message_index == 0
        assert retrieved.role == "user"
        assert retrieved.content_text == "Hello"
        assert retrieved.speaker == "User"

    def test_list_messages_ordering(self) -> None:
        self.store.save_message(_msg("m3", index=2, content="Third"))
        self.store.save_message(_msg("m1", index=0, content="First"))
        self.store.save_message(_msg("m2", index=1, content="Second"))
        messages = self.store.list_messages("conv-1")
        assert [m.message_index for m in messages] == [0, 1, 2]
        assert [m.content_text for m in messages] == ["First", "Second", "Third"]

    def test_count_messages(self) -> None:
        self.store.save_message(_msg("m1"))
        self.store.save_message(_msg("m2"))
        assert self.store.count_messages("conv-1") == 2

    def test_count_all_messages(self) -> None:
        self.store.save_message(_msg("m1", conv_id="c1"))
        self.store.save_message(_msg("m2", conv_id="c2"))
        assert self.store.count_messages() == 2

    def test_message_timestamp_none(self) -> None:
        msg = ConversationMessage(
            id="m1",
            conversation_id="conv-1",
            message_index=0,
            role="user",
            content_text="Hi",
            timestamp=None,
        )
        self.store.save_message(msg)
        retrieved = self.store.get_message("m1")
        assert retrieved is not None
        assert retrieved.timestamp is None

    def test_message_parent_id_none(self) -> None:
        msg = ConversationMessage(
            id="m1",
            conversation_id="conv-1",
            message_index=0,
            role="user",
            content_text="Hi",
            parent_message_id=None,
        )
        self.store.save_message(msg)
        retrieved = self.store.get_message("m1")
        assert retrieved is not None
        assert retrieved.parent_message_id is None

    def test_message_is_active_branch(self) -> None:
        msg = ConversationMessage(
            id="m1",
            conversation_id="conv-1",
            message_index=0,
            role="user",
            content_text="Hi",
            is_active_branch=True,
        )
        self.store.save_message(msg)
        retrieved = self.store.get_message("m1")
        assert retrieved is not None
        assert retrieved.is_active_branch is True


class TestIdempotency:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_conversation_idempotent(self) -> None:
        conv = _conv()
        first = self.store.save_conversation(conv)
        second = self.store.save_conversation(conv)
        assert first is True
        assert second is False
        assert self.store.count_conversations() == 1

    def test_message_idempotent(self) -> None:
        self.store.save_conversation(_conv())
        msg = _msg()
        first = self.store.save_message(msg)
        second = self.store.save_message(msg)
        assert first is True
        assert second is False
        assert self.store.count_messages("conv-1") == 1

    def test_conversation_update_on_reinsert(self) -> None:
        conv = _conv(title="Original")
        self.store.save_conversation(conv)
        updated = _conv(title="Updated")
        self.store.save_conversation(updated)
        retrieved = self.store.get_conversation("conv-1")
        assert retrieved is not None
        assert retrieved.title == "Updated"

    def test_message_update_on_reinsert(self) -> None:
        self.store.save_conversation(_conv())
        msg = _msg(content="Original")
        self.store.save_message(msg)
        updated = _msg(content="Updated")
        self.store.save_message(updated)
        retrieved = self.store.get_message("msg-1")
        assert retrieved is not None
        assert retrieved.content_text == "Updated"

    def test_batch_save_idempotent(self) -> None:
        self.store.save_conversation(_conv())
        msgs = tuple(_msg(f"m{i}", index=i, content=f"Msg {i}") for i in range(5))
        first_count = self.store.save_messages(msgs)
        second_count = self.store.save_messages(msgs)
        assert first_count == 5
        assert second_count == 0
        assert self.store.count_messages("conv-1") == 5


class TestAttachmentStore:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)
        self.store.save_conversation(_conv("conv-1"))
        self.store.save_message(_msg("msg-1", conv_id="conv-1", index=0))

    def teardown_method(self) -> None:
        self.connection.close()

    def test_save_new_attachment(self) -> None:
        att = _att()
        assert self.store.save_attachment(att) is True

    def test_get_attachments_for_conversation(self) -> None:
        self.store.save_attachment(_att("att-1", msg_id="msg-1", filename="a.pdf"))
        atts = self.store.list_attachments_for_conversation("conv-1")
        assert len(atts) == 1
        assert atts[0].filename == "a.pdf"

    def test_count_attachments(self) -> None:
        self.store.save_attachment(_att("att-1", msg_id="msg-1"))
        self.store.save_attachment(_att("att-2", msg_id="msg-1"))
        assert self.store.count_attachments("conv-1") == 2

    def test_count_all_attachments(self) -> None:
        self.store.save_conversation(_conv("conv-2"))
        self.store.save_message(_msg("msg-2", conv_id="conv-2", index=0))
        self.store.save_attachment(_att("att-1", msg_id="msg-1"))
        self.store.save_attachment(_att("att-2", msg_id="msg-2"))
        assert self.store.count_attachments() == 2

    def test_attachment_idempotent(self) -> None:
        att = _att()
        first = self.store.save_attachment(att)
        second = self.store.save_attachment(att)
        assert first is True
        assert second is False
        assert self.store.count_attachments("conv-1") == 1

    def test_attachment_update_on_reinsert(self) -> None:
        att = _att(filename="original.pdf")
        self.store.save_attachment(att)
        updated = _att(filename="updated.pdf")
        self.store.save_attachment(updated)
        atts = self.store.list_attachments_for_conversation("conv-1")
        assert atts[0].filename == "updated.pdf"

    def test_batch_save_attachments(self) -> None:
        atts = tuple(
            _att(f"att-{i}", msg_id="msg-1", filename=f"f{i}.pdf") for i in range(3)
        )
        count = self.store.save_attachments(atts)
        assert count == 3
        assert self.store.count_attachments("conv-1") == 3

    def test_attachments_ordered_by_message_index(self) -> None:
        self.store.save_message(_msg("msg-2", conv_id="conv-1", index=1))
        self.store.save_attachment(_att("att-2", msg_id="msg-2", filename="b.pdf"))
        self.store.save_attachment(_att("att-1", msg_id="msg-1", filename="a.pdf"))
        atts = self.store.list_attachments_for_conversation("conv-1")
        assert [a.filename for a in atts] == ["a.pdf", "b.pdf"]


class TestSearchWithThoughts:
    """Search should exclude thoughts and reasoning_recap by default."""

    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)
        self.store.save_conversation(_conv("conv-1", title="Test Conv"))

    def teardown_method(self) -> None:
        self.connection.close()

    def test_thoughts_excluded_from_search(self) -> None:
        self.store.save_message(_msg("m1", content="Hello there", content_type="text"))
        self.store.save_message(
            _msg("m2", content="Thinking about this", content_type="thoughts")
        )
        results = self.store.search("Thinking")
        assert len(results) == 0

    def test_reasoning_recap_excluded_from_search(self) -> None:
        self.store.save_message(_msg("m1", content="Real answer", content_type="text"))
        self.store.save_message(
            _msg("m2", content="Recap of reasoning", content_type="reasoning_recap")
        )
        results = self.store.search("Recap")
        assert len(results) == 0

    def test_text_messages_found(self) -> None:
        self.store.save_message(_msg("m1", content="Hello world"))
        results = self.store.search("Hello")
        assert len(results) == 1


class TestSearchTimestampAndBranch:
    """Search results should include timestamp and is_active_branch."""

    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)
        self.store.save_conversation(_conv("conv-1", title="Test Conv"))

    def teardown_method(self) -> None:
        self.connection.close()

    def test_timestamp_in_search_result(self) -> None:
        self.store.save_message(
            _msg("m1", content="Hello", timestamp="2026-01-01T12:00:00+00:00")
        )
        results = self.store.search("Hello")
        assert len(results) == 1
        assert results[0].timestamp == "2026-01-01T12:00:00+00:00"

    def test_is_active_branch_in_search_result(self) -> None:
        self.store.save_message(_msg("m1", content="Active msg", is_active_branch=True))
        self.store.save_message(
            _msg("m2", content="Inactive msg", is_active_branch=False)
        )
        results = self.store.search("Active")
        assert len(results) == 1
        assert results[0].is_active_branch is True

    def test_inactive_message_not_in_search(self) -> None:
        """Inactive branch messages are excluded from search."""
        self.store.save_message(_msg("m1", content="Hello", is_active_branch=False))
        results = self.store.search("Hello")
        assert len(results) == 0

    def test_active_message_in_search(self) -> None:
        self.store.save_message(_msg("m1", content="Hello", is_active_branch=True))
        results = self.store.search("Hello")
        assert len(results) == 1
        assert results[0].is_active_branch is True


class TestCountMessagesActiveOnly:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)
        self.store.save_conversation(_conv("conv-1"))

    def teardown_method(self) -> None:
        self.connection.close()

    def test_count_active_only(self) -> None:
        self.store.save_message(_msg("m1", is_active_branch=True))
        self.store.save_message(_msg("m2", is_active_branch=False))
        assert self.store.count_messages("conv-1", active_only=True) == 1
        assert self.store.count_messages("conv-1", active_only=False) == 2

    def test_count_all_active_only(self) -> None:
        self.store.save_conversation(_conv("conv-2"))
        self.store.save_message(_msg("m1", conv_id="conv-1", is_active_branch=True))
        self.store.save_message(_msg("m2", conv_id="conv-2", is_active_branch=False))
        assert self.store.count_messages(active_only=True) == 1
        assert self.store.count_messages(active_only=False) == 2


class TestCountMessagesByContentType:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)
        self.store.save_conversation(_conv("conv-1", source_type="chatgpt"))
        self.store.save_conversation(_conv("conv-2", source_type="gemini"))

    def teardown_method(self) -> None:
        self.connection.close()

    def test_count_by_content_type(self) -> None:
        self.store.save_message(_msg("m1", conv_id="conv-1", content_type="text"))
        self.store.save_message(_msg("m2", conv_id="conv-1", content_type="thoughts"))
        self.store.save_message(_msg("m3", conv_id="conv-2", content_type="text"))
        assert self.store.count_messages_by_content_type("thoughts") == 1
        assert (
            self.store.count_messages_by_content_type("thoughts", source_type="chatgpt")
            == 1
        )
        assert (
            self.store.count_messages_by_content_type("thoughts", source_type="gemini")
            == 0
        )


class TestSearchTemporalBounds:
    """Search creates_after/created_before bounds on message timestamps.

    Bounds are inclusive ISO-8601 values applied to ``cm.timestamp`` with the
    same validation and semantics as document temporal filtering.
    """

    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)
        self.store.save_conversation(_conv("conv-1", title="Test Conv"))
        self.store.save_message(
            _msg(
                "m1",
                index=0,
                content="BCG early",
                timestamp="2026-01-15T00:00:00+00:00",
            )
        )
        self.store.save_message(
            _msg(
                "m2", index=1, content="BCG late", timestamp="2026-02-15T00:00:00+00:00"
            )
        )

    def teardown_method(self) -> None:
        self.connection.close()

    def test_created_after_includes_exact_boundary(self) -> None:
        results = self.store.search("BCG", created_after="2026-01-15T00:00:00+00:00")
        assert [r.content_text for r in results] == ["BCG early", "BCG late"]

    def test_created_after_excludes_earlier(self) -> None:
        results = self.store.search("BCG", created_after="2026-02-01T00:00:00+00:00")
        assert [r.content_text for r in results] == ["BCG late"]

    def test_created_before_includes_exact_boundary(self) -> None:
        results = self.store.search("BCG", created_before="2026-02-15T00:00:00+00:00")
        assert [r.content_text for r in results] == ["BCG early", "BCG late"]

    def test_created_before_excludes_later(self) -> None:
        results = self.store.search("BCG", created_before="2026-02-01T00:00:00+00:00")
        assert [r.content_text for r in results] == ["BCG early"]

    def test_both_bounds_restrict_range(self) -> None:
        results = self.store.search(
            "BCG",
            created_after="2026-01-20T00:00:00+00:00",
            created_before="2026-02-20T00:00:00+00:00",
        )
        assert [r.content_text for r in results] == ["BCG late"]

    def test_no_bounds_returns_all(self) -> None:
        results = self.store.search("BCG")
        assert [r.content_text for r in results] == ["BCG early", "BCG late"]

    def test_no_timestamp_never_matches_bounded_window(self) -> None:
        self.store.save_message(_msg("m3", index=2, content="BCG untimed"))
        results = self.store.search("BCG", created_after="2026-01-01T00:00:00+00:00")
        assert [r.content_text for r in results] == ["BCG early", "BCG late"]

    def test_malformed_timestamp_bound_rejected(self) -> None:
        with pytest.raises(ValueError, match="ISO-8601"):
            self.store.search("BCG", created_after="not-a-timestamp")
        with pytest.raises(ValueError, match="ISO-8601"):
            self.store.search("BCG", created_before="not-a-timestamp")

    def test_inverted_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be later"):
            self.store.search(
                "BCG",
                created_after="2026-02-15T00:00:00+00:00",
                created_before="2026-01-15T00:00:00+00:00",
            )

    def test_mixed_offset_bounds_rejected(self) -> None:
        with pytest.raises(ValueError, match="mix offset-naive"):
            self.store.search(
                "BCG",
                created_after="2026-01-15T00:00:00",
                created_before="2026-02-15T00:00:00+00:00",
            )

    def test_results_deterministic(self) -> None:
        first = self.store.search("BCG", created_after="2026-01-01T00:00:00+00:00")
        second = self.store.search("BCG", created_after="2026-01-01T00:00:00+00:00")
        assert first == second


class TestSearchMultiTerm:
    """Multi-term conversation retrieval with OR (match-any-term) semantics.

    Model-generated queries are often descriptive multi-word phrases; a
    message should match when it contains *any* meaningful term rather than
    the whole phrase contiguously. Messages matching more terms rank higher.
    """

    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)
        self._add("m-bcg", "BCG case prep")
        self._add("m-career", "Career growth plan")
        self._add("m-interview", "Interview tips and strategy")
        self._add("m-none", "Cooking a simple dinner")

    def teardown_method(self) -> None:
        self.connection.close()

    def _add(self, msg_id: str, content: str, **kwargs) -> None:
        conv_id = f"conv-{msg_id}"
        self.store.save_conversation(_conv(conv_id, title="Neutral"))
        self.store.save_message(
            _msg(msg_id, conv_id=conv_id, content=content, **kwargs)
        )

    def _texts(self, query: str, **kwargs) -> list[str]:
        return [r.content_text for r in self.store.search(query, **kwargs)]

    def test_single_term_compatibility(self) -> None:
        texts = self._texts("career")
        assert texts == ["Career growth plan"]

    def test_multi_term_query_retrieves_any_match(self) -> None:
        texts = self._texts("BCG career interview")
        assert "BCG case prep" in texts
        assert "Career growth plan" in texts
        assert "Interview tips and strategy" in texts
        assert "Cooking a simple dinner" not in texts

    def test_partial_term_isolation(self) -> None:
        # A multi-term query requires no single term -- a message matching
        # just "BCG" is enough even when McKinsey/Bain/consulting are absent.
        texts = self._texts("BCG McKinsey Bain consulting")
        assert "BCG case prep" in texts

    def test_more_terms_rank_higher(self) -> None:
        results = self.store.search("BCG interview prep")
        m_bcg = next(r for r in results if r.message_id == "m-bcg")
        m_interview = next(r for r in results if r.message_id == "m-interview")
        # m-bcg matches "BCG" and "prep"; m-interview matches "interview" only.
        assert m_bcg.score > m_interview.score

    def test_case_insensitive(self) -> None:
        texts = self._texts("bCg CaReEr")
        assert "BCG case prep" in texts
        assert "Career growth plan" in texts

    def test_title_match_contributes(self) -> None:
        # A term appearing in the conversation title is a valid match.
        self.store.save_conversation(_conv("conv-title", title="BCG Careers Overview"))
        self.store.save_message(
            _msg("m-title", conv_id="conv-title", content="Unrelated body text")
        )
        texts = self._texts("BCG")
        assert "Unrelated body text" in texts

    def test_speaker_match_contributes(self) -> None:
        self._add("m-speaker", "Some generic content", speaker="Interviewer")
        texts = self._texts("Interviewer")
        assert "Some generic content" in texts

    def test_special_characters_are_literal(self) -> None:
        self._add("m-pct", "Scored 100% on test")
        self._add("m-pct2", "Scored 100x on test")
        self._add("m-undo", "best_notes")
        self._add("m-undo2", "bestXnotes")
        self._add("m-slash", "path/to/file")

        assert self._texts("100%") == ["Scored 100% on test"]
        assert self._texts("best_notes") == ["best_notes"]
        assert self._texts("path/to/file") == ["path/to/file"]

    def test_empty_and_whitespace_query(self) -> None:
        assert self.store.search("") == ()
        assert self.store.search("   ") == ()
        assert self.store.search("-- --") == ()

    def test_punctuation_only_terms_dropped(self) -> None:
        # "career - . , interview" keeps only the meaningful words.
        texts = self._texts("career - . , interview")
        assert "Career growth plan" in texts
        assert "Interview tips and strategy" in texts

    def test_duplicate_terms_collapsed(self) -> None:
        texts = self._texts("career career career")
        assert texts == ["Career growth plan"]

    def test_created_after_with_multi_term(self) -> None:
        self._add(
            "m-late",
            "BCG late interview",
            timestamp="2026-02-20T00:00:00+00:00",
        )
        texts = self._texts(
            "BCG McKinsey consulting",
            created_after="2026-02-01T00:00:00+00:00",
        )
        assert texts == ["BCG late interview"]

    def test_created_before_with_multi_term(self) -> None:
        texts = self._texts(
            "BCG McKinsey consulting",
            created_before="2026-01-01T00:00:00+00:00",
        )
        # Untimed messages never match a bounded window; none qualify here.
        assert texts == []

    def test_both_bounds_restrict_multi_term(self) -> None:
        self._add(
            "m-window",
            "BCG window interview",
            timestamp="2026-03-15T00:00:00+00:00",
        )
        texts = self._texts(
            "BCG McKinsey consulting",
            created_after="2026-03-01T00:00:00+00:00",
            created_before="2026-03-31T00:00:00+00:00",
        )
        assert texts == ["BCG window interview"]

    def test_null_timestamp_not_in_bounded_multi_term(self) -> None:
        texts = self._texts(
            "BCG McKinsey consulting",
            created_after="2026-01-01T00:00:00+00:00",
        )
        # None of the untimed fixture messages may match a bounded window.
        assert texts == []

    def test_multi_term_deterministic(self) -> None:
        first = self.store.search("BCG career interview")
        second = self.store.search("BCG career interview")
        assert first == second

    def test_limit_respected(self) -> None:
        results = self.store.search("BCG career interview", limit=2)
        assert len(results) <= 2
