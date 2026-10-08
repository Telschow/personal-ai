"""Tests for GeminiConversationLoader."""

import json
from pathlib import Path

from personal_ai.sources.gemini import SOURCE_TYPE
from personal_ai.sources.gemini_loader import (
    GeminiConversationLoader,
)


def _write_conversation(
    directory: Path,
    filename: str,
    *,
    conv_id: str = "abc123def456",
    title: str = "Test Conversation",
    messages: list[dict[str, str]] | None = None,
    created_at: str = "2026-01-01T00:00:00.000Z",
    last_message_at: str = "2026-01-01T00:00:00.000Z",
    url: str = "",
) -> Path:
    if messages is None:
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
    data: dict[str, object] = {
        "id": conv_id,
        "title": title,
        "messages": messages,
        "messageCount": len(messages),
        "createdAt": created_at,
        "lastMessageAt": last_message_at,
    }
    if url:
        data["url"] = url
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestDiscoverFiles:
    def test_finds_conversation_json(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_My Chat_abc123.json")
        loader = GeminiConversationLoader(tmp_path)
        files = loader.discover_files()
        assert len(files) == 1

    def test_ignores_md_twins(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_My Chat_abc123.json")
        (tmp_path / "20260101_My Chat_abc123.md").write_text("# render")
        loader = GeminiConversationLoader(tmp_path)
        files = loader.discover_files()
        assert len(files) == 1

    def test_ignores_aggregate_files(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "_all_conversations.json")
        _write_conversation(tmp_path, "_urls_index.json")
        loader = GeminiConversationLoader(tmp_path)
        files = loader.discover_files()
        assert len(files) == 0

    def test_ignores_non_conversation_json(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text(
            json.dumps({"data": "not a conversation"})
        )
        loader = GeminiConversationLoader(tmp_path)
        files = loader.discover_files()
        assert len(files) == 0

    def test_ignores_manifest_with_conversations_key(self, tmp_path: Path) -> None:
        # Manifests have "conversations" but not top-level "messages"
        data = {"exported_at": "2026-01-01", "conversations": []}
        (tmp_path / "gemini-export-2026-07-25.json").write_text(json.dumps(data))
        loader = GeminiConversationLoader(tmp_path)
        files = loader.discover_files()
        assert len(files) == 0

    def test_multiple_conversations(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_Chat A_aaa.json")
        _write_conversation(tmp_path, "20260102_Chat B_bbb.json")
        _write_conversation(tmp_path, "20260103_Chat C_ccc.json")
        loader = GeminiConversationLoader(tmp_path)
        files = loader.discover_files()
        assert len(files) == 3

    def test_sorted_by_path(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260103_Chat C_ccc.json")
        _write_conversation(tmp_path, "20260101_Chat A_aaa.json")
        _write_conversation(tmp_path, "20260102_Chat B_bbb.json")
        loader = GeminiConversationLoader(tmp_path)
        files = loader.discover_files()
        names = [f.name for f in files]
        assert names == sorted(names)

    def test_ignores_corrupt_json(self, tmp_path: Path) -> None:
        (tmp_path / "20260101_Bad_bad.json").write_text("not json {{{")
        loader = GeminiConversationLoader(tmp_path)
        files = loader.discover_files()
        assert len(files) == 0


class TestLoadFile:
    def test_basic_conversation(self, tmp_path: Path) -> None:
        path = _write_conversation(
            tmp_path,
            "20260101_Test_abc.json",
            conv_id="abc123",
            title="My Test",
        )
        loader = GeminiConversationLoader(tmp_path)
        conv, msgs = loader.load_file(path)

        assert conv.source_type == SOURCE_TYPE
        assert conv.title == "My Test"
        assert conv.created_at == "2026-01-01T00:00:00+00:00"
        assert len(msgs) == 2

    def test_message_roles(self, tmp_path: Path) -> None:
        path = _write_conversation(
            tmp_path,
            "20260101_Test_abc.json",
            messages=[
                {"role": "user", "content": "Question"},
                {"role": "assistant", "content": "Answer"},
            ],
        )
        loader = GeminiConversationLoader(tmp_path)
        _, msgs = loader.load_file(path)

        assert msgs[0].role == "user"
        assert msgs[0].speaker == "User"
        assert msgs[1].role == "assistant"
        assert msgs[1].speaker == "Gemini"

    def test_message_ordering(self, tmp_path: Path) -> None:
        messages = [{"role": "user", "content": f"Message {i}"} for i in range(10)]
        path = _write_conversation(
            tmp_path,
            "20260101_Test_abc.json",
            messages=messages,
        )
        loader = GeminiConversationLoader(tmp_path)
        _, msgs = loader.load_file(path)

        assert len(msgs) == 10
        for i, msg in enumerate(msgs):
            assert msg.message_index == i
            assert msg.content_text == f"Message {i}"

    def test_text_preservation(self, tmp_path: Path) -> None:
        text = "This is a detailed message with\nnewlines and special chars: <>&"
        path = _write_conversation(
            tmp_path,
            "20260101_Test_abc.json",
            messages=[{"role": "user", "content": text}],
        )
        loader = GeminiConversationLoader(tmp_path)
        _, msgs = loader.load_file(path)
        assert msgs[0].content_text == text

    def test_conversation_metadata(self, tmp_path: Path) -> None:
        path = _write_conversation(
            tmp_path,
            "20260101_Test_abc.json",
            url="https://gemini.google.com/app/abc123",
        )
        loader = GeminiConversationLoader(tmp_path)
        conv, _ = loader.load_file(path)

        assert conv.metadata["raw_id"] is not None
        assert conv.metadata["message_count"] == 2
        assert conv.metadata["url"] == "https://gemini.google.com/app/abc123"
        assert conv.metadata["filename"] == "20260101_Test_abc.json"

    def test_per_message_timestamp_none(self, tmp_path: Path) -> None:
        path = _write_conversation(tmp_path, "20260101_Test_abc.json")
        loader = GeminiConversationLoader(tmp_path)
        _, msgs = loader.load_file(path)
        for msg in msgs:
            assert msg.timestamp is None

    def test_deterministic_message_ids(self, tmp_path: Path) -> None:
        path = _write_conversation(tmp_path, "20260101_Test_abc.json")
        loader = GeminiConversationLoader(tmp_path)
        _, msgs1 = loader.load_file(path)
        _, msgs2 = loader.load_file(path)

        assert [m.id for m in msgs1] == [m.id for m in msgs2]

    def test_deterministic_conversation_id(self, tmp_path: Path) -> None:
        path = _write_conversation(tmp_path, "20260101_Test_abc.json", conv_id="xyz")
        loader = GeminiConversationLoader(tmp_path)
        conv1, _ = loader.load_file(path)
        conv2, _ = loader.load_file(path)
        assert conv1.id == conv2.id

    def test_missing_title_fallback_to_stem(self, tmp_path: Path) -> None:
        data = {
            "id": "abc",
            "messages": [{"role": "user", "content": "Hi"}],
            "messageCount": 1,
            "createdAt": "2026-01-01T00:00:00.000Z",
        }
        path = tmp_path / "20260101_Fallback_stem_abc.json"
        path.write_text(json.dumps(data))
        loader = GeminiConversationLoader(tmp_path)
        conv, _ = loader.load_file(path)
        assert conv.title == "20260101_Fallback_stem_abc"

    def test_parent_message_id_none(self, tmp_path: Path) -> None:
        path = _write_conversation(tmp_path, "20260101_Test_abc.json")
        loader = GeminiConversationLoader(tmp_path)
        _, msgs = loader.load_file(path)
        for msg in msgs:
            assert msg.parent_message_id is None

    def test_is_active_branch_true(self, tmp_path: Path) -> None:
        path = _write_conversation(tmp_path, "20260101_Test_abc.json")
        loader = GeminiConversationLoader(tmp_path)
        _, msgs = loader.load_file(path)
        for msg in msgs:
            assert msg.is_active_branch is True

    def test_content_type_text(self, tmp_path: Path) -> None:
        path = _write_conversation(tmp_path, "20260101_Test_abc.json")
        loader = GeminiConversationLoader(tmp_path)
        _, msgs = loader.load_file(path)
        for msg in msgs:
            assert msg.content_type == "text"


class TestLoadAll:
    def test_loads_all_conversations(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_A_aaa.json", title="Chat A")
        _write_conversation(tmp_path, "20260102_B_bbb.json", title="Chat B")
        _write_conversation(tmp_path, "20260103_C_ccc.json", title="Chat C")

        loader = GeminiConversationLoader(tmp_path)
        results = loader.load_all()
        assert len(results) == 3

    def test_sorted_by_path(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260103_C_ccc.json", title="C")
        _write_conversation(tmp_path, "20260101_A_aaa.json", title="A")
        _write_conversation(tmp_path, "20260102_B_bbb.json", title="B")

        loader = GeminiConversationLoader(tmp_path)
        results = loader.load_all()
        titles = [conv.title for conv, _ in results]
        assert titles == ["A", "B", "C"]
