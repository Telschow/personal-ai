"""Tests for ChatGPT conversation ingestion orchestration."""

import json
from pathlib import Path

from personal_ai.conversation_ingestion import (
    ingest_chatgpt_conversations,
    ingest_gemini_conversations,
)
from personal_ai.storage import ConversationStore, connect_database


def _make_node(
    node_id: str,
    parent: str | None = None,
    role: str = "user",
    parts: list[object] | None = None,
    create_time: float | None = None,
    msg_id: str | None = None,
) -> dict:
    message = None
    if role is not None:
        msg: dict[str, object] = {
            "author": {"name": None, "role": role},
            "content": {"content_type": "text", "parts": parts or ["Hello"]},
            "id": msg_id or f"msg-{node_id}",
        }
        if create_time is not None:
            msg["create_time"] = create_time
        message = msg
    return {"id": node_id, "parent": parent, "message": message}


def _simple_linear_conversation(
    conv_id: str = "test-conv-1",
    title: str = "Test Conversation",
    messages: list[tuple[str, str]] | None = None,
    create_time: float = 1693371594.897,
) -> dict:
    if messages is None:
        messages = [("user", "Hello"), ("assistant", "Hi there!")]

    nodes = {}
    prev_id: str | None = None
    node_ids: list[str] = []

    for i, (role, text) in enumerate(messages):
        node_id = f"node-{i}"
        node_ids.append(node_id)
        nodes[node_id] = _make_node(
            node_id,
            parent=prev_id,
            role=role,
            parts=[text],
            create_time=create_time + i,
            msg_id=f"msg-{conv_id}-{i}",
        )
        prev_id = node_id

    return {
        "conversation_id": conv_id,
        "title": title,
        "create_time": create_time,
        "update_time": create_time + len(messages),
        "current_node": node_ids[-1] if node_ids else "node-0",
        "mapping": nodes,
        "default_model_slug": "gpt-4",
        "is_archived": False,
    }


def _write_shard(directory: Path, filename: str, conversations: list[dict]) -> Path:
    path = directory / filename
    path.write_text(json.dumps(conversations), encoding="utf-8")
    return path


def _write_gemini_conversation(
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


class TestIngestChatGPTConversations:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ConversationStore(self.connection)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_basic_ingestion(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(),
            ],
        )
        summary = ingest_chatgpt_conversations(tmp_path, self.store)
        assert summary.shards_discovered == 1
        assert summary.conversations_discovered == 1
        assert summary.conversations_stored == 1
        assert summary.messages_stored == 2

    def test_multiple_shards(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(conv_id="c1"),
            ],
        )
        _write_shard(
            tmp_path,
            "conversations-001.json",
            [
                _simple_linear_conversation(conv_id="c2"),
            ],
        )
        summary = ingest_chatgpt_conversations(tmp_path, self.store)
        assert summary.shards_discovered == 2
        assert summary.conversations_discovered == 2
        assert summary.conversations_stored == 2
        assert summary.messages_stored == 4

    def test_multiple_conversations_per_shard(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(conv_id="c1"),
                _simple_linear_conversation(conv_id="c2"),
                _simple_linear_conversation(conv_id="c3"),
            ],
        )
        summary = ingest_chatgpt_conversations(tmp_path, self.store)
        assert summary.conversations_discovered == 3
        assert summary.conversations_stored == 3
        assert summary.messages_stored == 6

    def test_idempotent(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(conv_id="c1"),
                _simple_linear_conversation(conv_id="c2"),
            ],
        )
        first = ingest_chatgpt_conversations(tmp_path, self.store)
        second = ingest_chatgpt_conversations(tmp_path, self.store)
        assert first.conversations_stored == 2
        assert second.conversations_stored == 0
        assert second.messages_stored == 0
        assert self.store.count_conversations() == 2
        assert self.store.count_messages() == 4

    def test_messages_persisted_correctly(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(
                    messages=[
                        ("user", "What is AI?"),
                        ("assistant", "AI is artificial intelligence."),
                    ],
                ),
            ],
        )
        ingest_chatgpt_conversations(tmp_path, self.store)
        convs = self.store.list_conversations()
        assert len(convs) == 1
        msgs = self.store.list_messages(convs[0].id)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content_text == "What is AI?"
        assert msgs[1].role == "assistant"
        assert msgs[1].content_text == "AI is artificial intelligence."

    def test_timestamps_preserved(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(create_time=1693371594.897),
            ],
        )
        ingest_chatgpt_conversations(tmp_path, self.store)
        convs = self.store.list_conversations()
        msgs = self.store.list_messages(convs[0].id)
        for msg in msgs:
            assert msg.timestamp is not None
            assert "+00:00" in msg.timestamp

    def test_branching_messages_preserved(self, tmp_path: Path) -> None:
        nodes = {
            "root": _make_node("root", parent=None, role="user", parts=["Q"]),
            "a": _make_node("a", parent="root", role="assistant", parts=["A1"]),
            "b": _make_node("b", parent="root", role="assistant", parts=["A2"]),
        }
        conv = {
            "conversation_id": "branch-conv",
            "title": "Branch Test",
            "create_time": 1693371594.0,
            "current_node": "a",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        ingest_chatgpt_conversations(tmp_path, self.store)
        convs = self.store.list_conversations()
        msgs = self.store.list_messages(convs[0].id, include_inactive=True)
        assert len(msgs) == 3
        active = [m for m in msgs if m.is_active_branch]
        inactive = [m for m in msgs if not m.is_active_branch]
        assert len(active) == 2
        assert len(inactive) == 1

    def test_attachments_stored(self, tmp_path: Path) -> None:
        nodes = {
            "root": _make_node(
                "root",
                parent=None,
                role="user",
                parts=["File"],
            ),
            "resp": _make_node("resp", parent="root", role="assistant", parts=["OK"]),
        }
        nodes["root"]["message"]["metadata"] = {
            "attachments": [
                {
                    "id": "file-abc",
                    "name": "doc.pdf",
                    "mime_type": "application/pdf",
                    "size": 1024,
                },
            ]
        }
        conv = {
            "conversation_id": "att-conv",
            "title": "Att Test",
            "create_time": 1693371594.0,
            "current_node": "resp",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        summary = ingest_chatgpt_conversations(tmp_path, self.store)
        assert summary.attachments_stored == 1
        atts = self.store.list_attachments_for_conversation(
            self.store.list_conversations()[0].id
        )
        assert len(atts) == 1
        assert atts[0].filename == "doc.pdf"

    def test_empty_directory(self, tmp_path: Path) -> None:
        summary = ingest_chatgpt_conversations(tmp_path, self.store)
        assert summary.conversations_discovered == 0
        assert summary.conversations_stored == 0
        assert summary.messages_stored == 0
        assert summary.attachments_stored == 0

    def test_summary_source_type(self, tmp_path: Path) -> None:
        summary = ingest_chatgpt_conversations(tmp_path, self.store)
        assert summary.source_type == "chatgpt"

    def test_mixed_sources_do_not_interfere(self, tmp_path: Path) -> None:
        """Gemini and ChatGPT ingestion into the same store work independently."""
        # Gemini
        _write_gemini_conversation(
            tmp_path, "20260101_GemChat_aaa.json", title="Gemini Chat"
        )
        ingest_gemini_conversations(tmp_path, self.store)

        # ChatGPT
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(conv_id="gpt-c1", title="GPT Chat"),
            ],
        )
        ingest_chatgpt_conversations(tmp_path, self.store)

        assert self.store.count_conversations(source_type="gemini") == 1
        assert self.store.count_conversations(source_type="chatgpt") == 1
        assert self.store.count_conversations() == 2
