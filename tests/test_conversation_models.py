"""Tests for conversation and message domain models."""

from personal_ai.documents.conversations import (
    Conversation,
    ConversationAttachment,
    ConversationMessage,
    compute_attachment_id,
    compute_conversation_id,
    compute_message_id,
)


class TestComputeConversationId:
    def test_deterministic(self) -> None:
        a = compute_conversation_id("gemini", "path/to/file.json", "abc123")
        b = compute_conversation_id("gemini", "path/to/file.json", "abc123")
        assert a == b

    def test_different_inputs_differ(self) -> None:
        a = compute_conversation_id("gemini", "file1.json", "abc123")
        b = compute_conversation_id("gemini", "file2.json", "abc123")
        assert a != b

    def test_different_source_types_differ(self) -> None:
        a = compute_conversation_id("gemini", "file.json", "abc123")
        b = compute_conversation_id("chatgpt", "file.json", "abc123")
        assert a != b

    def test_produces_hex_digest(self) -> None:
        result = compute_conversation_id("gemini", "key", "id")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestComputeMessageId:
    def test_deterministic(self) -> None:
        a = compute_message_id("conv-123", 0)
        b = compute_message_id("conv-123", 0)
        assert a == b

    def test_different_indices_differ(self) -> None:
        a = compute_message_id("conv-123", 0)
        b = compute_message_id("conv-123", 1)
        assert a != b

    def test_different_conversations_differ(self) -> None:
        a = compute_message_id("conv-1", 0)
        b = compute_message_id("conv-2", 0)
        assert a != b

    def test_produces_hex_digest(self) -> None:
        result = compute_message_id("conv", 5)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestConversation:
    def test_frozen(self) -> None:
        conv = Conversation(
            id="c1",
            title="Test",
            source_type="gemini",
            created_at="2026-01-01T00:00:00+00:00",
            modified_at="2026-01-01T00:00:00+00:00",
        )
        try:
            conv.title = "Changed"
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_metadata_default_empty(self) -> None:
        conv = Conversation(
            id="c1",
            title="Test",
            source_type="gemini",
            created_at="",
            modified_at="",
        )
        assert conv.metadata == {}


class TestConversationMessage:
    def test_defaults(self) -> None:
        msg = ConversationMessage(
            id="m1",
            conversation_id="c1",
            message_index=0,
            role="user",
            content_text="Hello",
        )
        assert msg.speaker == ""
        assert msg.content_type == "text"
        assert msg.timestamp is None
        assert msg.parent_message_id is None
        assert msg.is_active_branch is True
        assert msg.metadata == {}

    def test_frozen(self) -> None:
        msg = ConversationMessage(
            id="m1",
            conversation_id="c1",
            message_index=0,
            role="user",
            content_text="Hello",
        )
        try:
            msg.role = "changed"
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestComputeAttachmentId:
    def test_deterministic(self) -> None:
        a = compute_attachment_id("msg-1", 0)
        b = compute_attachment_id("msg-1", 0)
        assert a == b

    def test_different_indices_differ(self) -> None:
        a = compute_attachment_id("msg-1", 0)
        b = compute_attachment_id("msg-1", 1)
        assert a != b

    def test_different_messages_differ(self) -> None:
        a = compute_attachment_id("msg-1", 0)
        b = compute_attachment_id("msg-2", 0)
        assert a != b

    def test_produces_hex_digest(self) -> None:
        result = compute_attachment_id("msg-1", 0)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestConversationAttachment:
    def test_defaults(self) -> None:
        att = ConversationAttachment(
            id="att-1",
            message_id="msg-1",
            filename="document.pdf",
            mime_type="application/pdf",
            size_bytes=1024,
        )
        assert att.blob_path == ""
        assert att.metadata == {}

    def test_frozen(self) -> None:
        att = ConversationAttachment(
            id="att-1",
            message_id="msg-1",
            filename="document.pdf",
            mime_type="application/pdf",
            size_bytes=1024,
        )
        try:
            att.filename = "changed.pdf"
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_with_blob_path(self) -> None:
        att = ConversationAttachment(
            id="att-1",
            message_id="msg-1",
            filename="image.png",
            mime_type="image/png",
            size_bytes=2048,
            blob_path="/path/to/blob.dat",
        )
        assert att.blob_path == "/path/to/blob.dat"

    def test_with_metadata(self) -> None:
        att = ConversationAttachment(
            id="att-1",
            message_id="msg-1",
            filename="doc.pdf",
            mime_type="application/pdf",
            size_bytes=512,
            metadata={"raw_id": "file-abc"},
        )
        assert att.metadata["raw_id"] == "file-abc"
