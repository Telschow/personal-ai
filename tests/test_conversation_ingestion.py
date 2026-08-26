"""Tests for Gemini conversation ingestion orchestration."""

import json
from pathlib import Path

from personal_ai.conversation_ingestion import (
    ingest_gemini_conversations,
)
from personal_ai.storage import ConversationStore, connect_database


def _write_conversation(
    directory: Path,
    filename: str,
    *,
    conv_id: str = "abc123",
    title: str = "Test",
    messages: list[dict[str, str]] | None = None,
) -> Path:
    if messages is None:
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
    data = {
        "id": conv_id,
        "title": title,
        "messages": messages,
        "messageCount": len(messages),
        "createdAt": "2026-01-01T00:00:00.000Z",
        "lastMessageAt": "2026-01-01T00:00:00.000Z",
    }
    path = directory / filename
    path.write_text(json.dumps(data))
    return path


class TestIngestGeminiConversations:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_basic_ingestion(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_Chat_aaa.json")
        summary = ingest_gemini_conversations(tmp_path, self.store)
        assert summary.conversations_discovered == 1
        assert summary.conversations_stored == 1
        assert summary.messages_stored == 2

    def test_multiple_conversations(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_A_aaa.json")
        _write_conversation(tmp_path, "20260102_B_bbb.json")
        _write_conversation(tmp_path, "20260103_C_ccc.json")
        summary = ingest_gemini_conversations(tmp_path, self.store)
        assert summary.conversations_discovered == 3
        assert summary.conversations_stored == 3
        assert summary.messages_stored == 6

    def test_md_files_counted(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_A_aaa.json")
        (tmp_path / "20260101_A_aaa.md").write_text("# render")
        (tmp_path / "20260101_B_bbb.md").write_text("# render 2")
        summary = ingest_gemini_conversations(tmp_path, self.store)
        assert summary.md_files_skipped == 2

    def test_aggregate_files_counted(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_A_aaa.json")
        _write_conversation(tmp_path, "_all_conversations.json")
        _write_conversation(tmp_path, "_urls_index.json")
        summary = ingest_gemini_conversations(tmp_path, self.store)
        assert summary.aggregate_files_skipped == 2
        assert summary.conversations_discovered == 1

    def test_idempotent(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_A_aaa.json")
        _write_conversation(tmp_path, "20260102_B_bbb.json")
        first = ingest_gemini_conversations(tmp_path, self.store)
        second = ingest_gemini_conversations(tmp_path, self.store)
        assert first.conversations_stored == 2
        assert second.conversations_stored == 0
        assert second.messages_stored == 0
        assert self.store.count_conversations() == 2
        assert self.store.count_messages() == 4

    def test_messages_persisted_correctly(self, tmp_path: Path) -> None:
        _write_conversation(
            tmp_path,
            "20260101_A_aaa.json",
            messages=[
                {"role": "user", "content": "What is AI?"},
                {"role": "assistant", "content": "AI is artificial intelligence."},
            ],
        )
        ingest_gemini_conversations(tmp_path, self.store)
        convs = self.store.list_conversations()
        assert len(convs) == 1
        msgs = self.store.list_messages(convs[0].id)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content_text == "What is AI?"
        assert msgs[1].role == "assistant"
        assert msgs[1].content_text == "AI is artificial intelligence."

    def test_summary_source_type(self, tmp_path: Path) -> None:
        summary = ingest_gemini_conversations(tmp_path, self.store)
        assert summary.source_type == "gemini"

    def test_empty_directory(self, tmp_path: Path) -> None:
        summary = ingest_gemini_conversations(tmp_path, self.store)
        assert summary.conversations_discovered == 0
        assert summary.conversations_stored == 0
        assert summary.messages_stored == 0
