"""Tests for ChatGPTConversationLoader."""

import json
from pathlib import Path

from personal_ai.sources.chatgpt import SOURCE_TYPE
from personal_ai.sources.chatgpt_loader import ChatGPTConversationLoader


def _make_node(
    node_id: str,
    parent: str | None = None,
    role: str = "user",
    content_type: str = "text",
    parts: list[object] | None = None,
    create_time: float | None = None,
    msg_id: str | None = None,
    attachments: list[dict] | None = None,
) -> dict:
    """Build a ChatGPT mapping node."""
    message = None
    if role is not None:
        msg: dict[str, object] = {
            "author": {"name": None, "role": role},
            "content": {"content_type": content_type, "parts": parts or ["Hello"]},
            "id": msg_id or f"msg-{node_id}",
        }
        if create_time is not None:
            msg["create_time"] = create_time
        if attachments:
            msg["metadata"] = {"attachments": attachments}
        message = msg
    return {"id": node_id, "parent": parent, "message": message}


def _write_shard(
    directory: Path,
    filename: str,
    conversations: list[dict],
) -> Path:
    """Write a ChatGPT shard file."""
    path = directory / filename
    path.write_text(json.dumps(conversations), encoding="utf-8")
    return path


def _simple_linear_conversation(
    conv_id: str = "test-conv-1",
    title: str = "Test Conversation",
    messages: list[tuple[str, str]] | None = None,
    create_time: float = 1693371594.897,
    current_node: str | None = None,
) -> dict:
    """Build a simple linear ChatGPT conversation (no branching)."""
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

    if current_node is None:
        current_node = node_ids[-1] if node_ids else "node-0"

    return {
        "conversation_id": conv_id,
        "title": title,
        "create_time": create_time,
        "update_time": create_time + len(messages),
        "current_node": current_node,
        "mapping": nodes,
        "default_model_slug": "gpt-4",
        "is_archived": False,
    }


class TestDiscoverShards:
    def test_finds_shard(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(),
            ],
        )
        loader = ChatGPTConversationLoader(tmp_path)
        shards = loader.discover_shards()
        assert len(shards) == 1

    def test_ignores_non_conversation_json(self, tmp_path: Path) -> None:
        (tmp_path / "user.json").write_text(json.dumps({"email": "test@test.com"}))
        (tmp_path / "conversations-000.json").write_text(
            json.dumps(
                [
                    _simple_linear_conversation(),
                ]
            )
        )
        loader = ChatGPTConversationLoader(tmp_path)
        shards = loader.discover_shards()
        assert len(shards) == 1

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
        loader = ChatGPTConversationLoader(tmp_path)
        shards = loader.discover_shards()
        assert len(shards) == 2

    def test_sorted_by_path(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-001.json",
            [
                _simple_linear_conversation(conv_id="c2"),
            ],
        )
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(conv_id="c1"),
            ],
        )
        loader = ChatGPTConversationLoader(tmp_path)
        shards = loader.discover_shards()
        names = [s.name for s in shards]
        assert names == ["conversations-000.json", "conversations-001.json"]

    def test_ignores_corrupt_json(self, tmp_path: Path) -> None:
        (tmp_path / "conversations-000.json").write_text("not json {{{")
        loader = ChatGPTConversationLoader(tmp_path)
        shards = loader.discover_shards()
        assert len(shards) == 0

    def test_ignores_non_list_json(self, tmp_path: Path) -> None:
        (tmp_path / "conversations-000.json").write_text(
            json.dumps({"data": "not a list"})
        )
        loader = ChatGPTConversationLoader(tmp_path)
        shards = loader.discover_shards()
        assert len(shards) == 0

    def test_ignores_empty_list(self, tmp_path: Path) -> None:
        (tmp_path / "conversations-000.json").write_text(json.dumps([]))
        loader = ChatGPTConversationLoader(tmp_path)
        shards = loader.discover_shards()
        assert len(shards) == 0


class TestLoadShard:
    def test_basic_linear(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(conv_id="c1", title="Test Chat"),
            ],
        )
        loader = ChatGPTConversationLoader(tmp_path)
        results = loader.load_shard(tmp_path / "conversations-000.json")
        assert len(results) == 1
        conv, msgs, attachments = results[0]
        assert conv.title == "Test Chat"
        assert conv.source_type == SOURCE_TYPE
        assert len(msgs) == 2
        assert len(attachments) == 0

    def test_message_roles(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(
                    messages=[("user", "Q"), ("assistant", "A")],
                ),
            ],
        )
        loader = ChatGPTConversationLoader(tmp_path)
        results = loader.load_shard(tmp_path / "conversations-000.json")
        assert len(results) == 1
        _, msgs, _ = results[0]
        assert msgs[0].role == "user"
        assert msgs[0].speaker == "User"
        assert msgs[1].role == "assistant"
        assert msgs[1].speaker == "ChatGPT"

    def test_message_content(self, tmp_path: Path) -> None:
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
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        assert msgs[0].content_text == "What is AI?"
        assert msgs[1].content_text == "AI is artificial intelligence."

    def test_timestamps_converted(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(create_time=1693371594.897),
            ],
        )
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        for msg in msgs:
            assert msg.timestamp is not None
            assert "+00:00" in msg.timestamp

    def test_message_ids_use_original_uuid(self, tmp_path: Path) -> None:
        conv = _simple_linear_conversation()
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        # Message IDs should be the original msg_id from the node
        assert msgs[0].id == "msg-test-conv-1-0"
        assert msgs[1].id == "msg-test-conv-1-1"

    def test_deterministic_message_index(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(
                    messages=[("user", f"Msg {i}") for i in range(5)]
                ),
            ],
        )
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs1, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        _, msgs2, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        assert [m.message_index for m in msgs1] == [m.message_index for m in msgs2]

    def test_empty_conversation_returns_none(self, tmp_path: Path) -> None:
        """Conversation with no message-bearing nodes returns None."""
        conv = {
            "conversation_id": "empty-conv",
            "title": "Empty",
            "create_time": 1693371594.0,
            "current_node": "root",
            "mapping": {
                "root": _make_node("root", parent=None, role=None),
            },
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        results = loader.load_shard(tmp_path / "conversations-000.json")
        assert len(results) == 0

    def test_archived_conversation_metadata(self, tmp_path: Path) -> None:
        conv = _simple_linear_conversation()
        conv["is_archived"] = True
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        convs, _, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        assert convs.metadata["is_archived"] is True

    def test_model_slug_in_metadata(self, tmp_path: Path) -> None:
        conv = _simple_linear_conversation()
        conv["default_model_slug"] = "o3-mini"
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        convs, _, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        assert convs.metadata["default_model_slug"] == "o3-mini"


class TestBranching:
    def test_linear_all_active(self, tmp_path: Path) -> None:
        """Linear conversation: all messages are on the active branch."""
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(),
            ],
        )
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        assert all(m.is_active_branch for m in msgs)

    def test_branching_basic(self, tmp_path: Path) -> None:
        """Two branches off the same parent: only one is active."""
        nodes = {
            "root": _make_node("root", parent=None, role="user", parts=["Q1"]),
            "resp-a": _make_node(
                "resp-a", parent="root", role="assistant", parts=["A1"]
            ),
            "resp-b": _make_node(
                "resp-b", parent="root", role="assistant", parts=["A2 (edited)"]
            ),
        }
        conv = {
            "conversation_id": "branch-conv",
            "title": "Branch Test",
            "create_time": 1693371594.0,
            "current_node": "resp-a",  # Active branch: root -> resp-a
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        assert len(msgs) == 3

        # root and resp-a are active, resp-b is inactive
        active = [m for m in msgs if m.is_active_branch]
        inactive = [m for m in msgs if not m.is_active_branch]
        assert len(active) == 2
        assert len(inactive) == 1
        assert inactive[0].content_text == "A2 (edited)"

    def test_branching_active_path_first(self, tmp_path: Path) -> None:
        """Active branch messages have lower message_index than inactive."""
        nodes = {
            "root": _make_node("root", parent=None, role="user", parts=["Q"]),
            "resp-a": _make_node(
                "resp-a", parent="root", role="assistant", parts=["A1"]
            ),
            "resp-b": _make_node(
                "resp-b", parent="root", role="assistant", parts=["A2"]
            ),
        }
        conv = {
            "conversation_id": "branch-order",
            "title": "Order Test",
            "create_time": 1693371594.0,
            "current_node": "resp-a",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]

        active_msgs = [m for m in msgs if m.is_active_branch]
        inactive_msgs = [m for m in msgs if not m.is_active_branch]

        # Active branch: root (0), resp-a (1)
        # Inactive: resp-b (2)
        assert active_msgs[0].message_index == 0
        assert active_msgs[1].message_index == 1
        assert inactive_msgs[0].message_index == 2

    def test_parent_message_id_set(self, tmp_path: Path) -> None:
        """Non-root messages have parent_message_id set."""
        nodes = {
            "root": _make_node("root", parent=None, role="user", parts=["Q"]),
            "child": _make_node("child", parent="root", role="assistant", parts=["A"]),
        }
        conv = {
            "conversation_id": "parent-test",
            "title": "Parent Test",
            "create_time": 1693371594.0,
            "current_node": "child",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]

        root_msg = next(m for m in msgs if m.content_text == "Q")
        child_msg = next(m for m in msgs if m.content_text == "A")
        assert root_msg.parent_message_id is None
        assert child_msg.parent_message_id == "root"

    def test_deep_branching(self, tmp_path: Path) -> None:
        """Three-level tree: root -> A -> B, with C branching from A."""
        nodes = {
            "root": _make_node("root", parent=None, role="user", parts=["Q1"]),
            "a": _make_node("a", parent="root", role="assistant", parts=["A1"]),
            "b": _make_node("b", parent="a", role="user", parts=["Q2"]),
            "c": _make_node("c", parent="a", role="assistant", parts=["A2 (alt)"]),
        }
        conv = {
            "conversation_id": "deep-branch",
            "title": "Deep Branch",
            "create_time": 1693371594.0,
            "current_node": "b",  # Active: root -> a -> b
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]

        active = [m for m in msgs if m.is_active_branch]
        inactive = [m for m in msgs if not m.is_active_branch]
        assert len(active) == 3
        assert len(inactive) == 1
        assert inactive[0].content_text == "A2 (alt)"

    def test_deterministic_branching(self, tmp_path: Path) -> None:
        """Same conversation always produces same ordering."""
        nodes = {
            "root": _make_node("root", parent=None, role="user", parts=["Q"]),
            "a": _make_node("a", parent="root", role="assistant", parts=["A1"]),
            "b": _make_node("b", parent="root", role="assistant", parts=["A2"]),
        }
        conv = {
            "conversation_id": "det-branch",
            "title": "Det Test",
            "create_time": 1693371594.0,
            "current_node": "a",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs1, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        _, msgs2, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        assert [
            (m.message_index, m.content_text, m.is_active_branch) for m in msgs1
        ] == [(m.message_index, m.content_text, m.is_active_branch) for m in msgs2]


class TestThoughts:
    def test_thoughts_stored_with_content_type(self, tmp_path: Path) -> None:
        """Thoughts messages are stored with content_type='thoughts'."""
        nodes = {
            "root": _make_node("root", parent=None, role="user", parts=["Q"]),
            "thought": _make_node(
                "thought",
                parent="root",
                role="assistant",
                content_type="thoughts",
                parts=[],  # parts ignored for thoughts
            ),
            "resp": _make_node("resp", parent="thought", role="assistant", parts=["A"]),
        }
        # Override thought node's content to have thoughts array
        nodes["thought"]["message"]["content"] = {
            "content_type": "thoughts",
            "thoughts": [
                {"summary": [{"type": "summary", "text": "Thinking about this..."}]}
            ],
        }
        conv = {
            "conversation_id": "thought-conv",
            "title": "Thought Test",
            "create_time": 1693371594.0,
            "current_node": "resp",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]

        thought_msg = next(m for m in msgs if m.content_type == "thoughts")
        assert thought_msg.content_text == "Thinking about this..."
        assert thought_msg.role == "assistant"

    def test_reasoning_recap_stored(self, tmp_path: Path) -> None:
        nodes = {
            "root": _make_node("root", parent=None, role="user", parts=["Q"]),
            "recap": _make_node(
                "recap",
                parent="root",
                role="assistant",
                content_type="reasoning_recap",
                parts=[],
            ),
            "resp": _make_node("resp", parent="recap", role="assistant", parts=["A"]),
        }
        nodes["recap"]["message"]["content"] = {
            "content_type": "reasoning_recap",
            "parts": ["Recap text here"],
        }
        conv = {
            "conversation_id": "recap-conv",
            "title": "Recap Test",
            "create_time": 1693371594.0,
            "current_node": "resp",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]

        recap_msg = next(m for m in msgs if m.content_type == "reasoning_recap")
        assert recap_msg.content_text == "Recap text here"


class TestAttachments:
    def test_attachments_collected(self, tmp_path: Path) -> None:
        nodes = {
            "root": _make_node(
                "root",
                parent=None,
                role="user",
                parts=["Look at this"],
                attachments=[
                    {
                        "id": "file-abc",
                        "name": "document.pdf",
                        "mime_type": "application/pdf",
                        "size": 1024,
                    },
                ],
            ),
            "resp": _make_node(
                "resp", parent="root", role="assistant", parts=["I see it"]
            ),
        }
        conv = {
            "conversation_id": "att-conv",
            "title": "Attachment Test",
            "create_time": 1693371594.0,
            "current_node": "resp",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, _, attachments = loader.load_shard(tmp_path / "conversations-000.json")[0]

        assert len(attachments) == 1
        att = attachments[0]
        assert att.filename == "document.pdf"
        assert att.mime_type == "application/pdf"
        assert att.size_bytes == 1024
        assert att.message_id == "msg-root"

    def test_multiple_attachments(self, tmp_path: Path) -> None:
        nodes = {
            "root": _make_node(
                "root",
                parent=None,
                role="user",
                parts=["Files"],
                attachments=[
                    {
                        "id": "f1",
                        "name": "a.pdf",
                        "mime_type": "application/pdf",
                        "size": 100,
                    },
                    {
                        "id": "f2",
                        "name": "b.png",
                        "mime_type": "image/png",
                        "size": 200,
                    },
                ],
            ),
            "resp": _make_node("resp", parent="root", role="assistant", parts=["OK"]),
        }
        conv = {
            "conversation_id": "multi-att",
            "title": "Multi Attach",
            "create_time": 1693371594.0,
            "current_node": "resp",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, _, attachments = loader.load_shard(tmp_path / "conversations-000.json")[0]
        assert len(attachments) == 2
        filenames = sorted(a.filename for a in attachments)
        assert filenames == ["a.pdf", "b.png"]

    def test_attachment_count_in_metadata(self, tmp_path: Path) -> None:
        nodes = {
            "root": _make_node(
                "root",
                parent=None,
                role="user",
                parts=["File"],
                attachments=[
                    {
                        "id": "f1",
                        "name": "doc.pdf",
                        "mime_type": "application/pdf",
                        "size": 50,
                    }
                ],
            ),
            "resp": _make_node("resp", parent="root", role="assistant", parts=["OK"]),
        }
        conv = {
            "conversation_id": "att-meta",
            "title": "Att Meta",
            "create_time": 1693371594.0,
            "current_node": "resp",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        convs, _, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        assert convs.metadata["attachment_count"] == 1


class TestMultimodal:
    def test_multimodal_text_parts(self, tmp_path: Path) -> None:
        """Multimodal text content (list of strings) is joined."""
        nodes = {
            "root": _make_node(
                "root",
                parent=None,
                role="user",
                parts=["What is in this image?"],
            ),
            "resp": _make_node(
                "resp",
                parent="root",
                role="assistant",
                parts=["The image shows a chart"],
            ),
        }
        conv = {
            "conversation_id": "mm-conv",
            "title": "Multimodal",
            "create_time": 1693371594.0,
            "current_node": "resp",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        assert msgs[0].content_text == "What is in this image?"
        assert msgs[1].content_text == "The image shows a chart"

    def test_audio_transcription_included(self, tmp_path: Path) -> None:
        nodes = {
            "root": _make_node("root", parent=None, role="user", parts=["Q"]),
            "resp": _make_node(
                "resp",
                parent="root",
                role="assistant",
                parts=[
                    {
                        "content_type": "audio_transcription",
                        "text": "Transcribed voice message",
                    },
                ],
            ),
        }
        conv = {
            "conversation_id": "audio-conv",
            "title": "Audio",
            "create_time": 1693371594.0,
            "current_node": "resp",
            "mapping": nodes,
        }
        _write_shard(tmp_path, "conversations-000.json", [conv])
        loader = ChatGPTConversationLoader(tmp_path)
        _, msgs, _ = loader.load_shard(tmp_path / "conversations-000.json")[0]
        assert msgs[1].content_text == "Transcribed voice message"


class TestLoadAll:
    def test_loads_from_multiple_shards(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(conv_id="c1", title="Chat 1"),
            ],
        )
        _write_shard(
            tmp_path,
            "conversations-001.json",
            [
                _simple_linear_conversation(conv_id="c2", title="Chat 2"),
            ],
        )
        loader = ChatGPTConversationLoader(tmp_path)
        results = loader.load_all()
        assert len(results) == 2
        titles = sorted(r[0].title for r in results)
        assert titles == ["Chat 1", "Chat 2"]

    def test_multiple_conversations_per_shard(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(conv_id="c1", title="Chat 1"),
                _simple_linear_conversation(conv_id="c2", title="Chat 2"),
                _simple_linear_conversation(conv_id="c3", title="Chat 3"),
            ],
        )
        loader = ChatGPTConversationLoader(tmp_path)
        results = loader.load_all()
        assert len(results) == 3

    def test_empty_directory(self, tmp_path: Path) -> None:
        loader = ChatGPTConversationLoader(tmp_path)
        results = loader.load_all()
        assert results == []

    def test_conversation_ids_unique(self, tmp_path: Path) -> None:
        _write_shard(
            tmp_path,
            "conversations-000.json",
            [
                _simple_linear_conversation(conv_id="c1"),
                _simple_linear_conversation(conv_id="c2"),
            ],
        )
        loader = ChatGPTConversationLoader(tmp_path)
        results = loader.load_all()
        ids = [r[0].id for r in results]
        assert len(set(ids)) == 2
