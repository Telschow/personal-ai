"""Tests for the ChatGPT conversation source adapter."""

import json
import sqlite3
from pathlib import Path

import pytest

from personal_ai.documents.classifier import DocumentKind, classify_document
from personal_ai.documents.extractor import extract_text
from personal_ai.documents.models import compute_content_hash
from personal_ai.documents.structured import StructuredExtraction
from personal_ai.ingestion import DocumentIngestor
from personal_ai.orchestration import ingest_source
from personal_ai.sources.base import SourceAdapter
from personal_ai.sources.chatgpt import (
    ChatGPTParseError,
    ChatGPTSourceAdapter,
    EmptyConversationError,
    PathOutsideExportError,
    SourceNotFoundError,
    UnsupportedConversationError,
    build_conversation_record,
    compose_conversation_text,
    is_conversation_list,
    parse_shard,
)
from personal_ai.sources.models import SourceRecord
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)

CONVERSATION_ID = "aa11bb22-cc33-44dd-55ee-ff6677889900"


def node(node_id: str, parent: str | None, message: dict | None = None) -> dict:
    return {"id": node_id, "message": message, "parent": parent}


def text_message(role: str, text: str, create_time: float | None = 1000.0) -> dict:
    message: dict = {
        "author": {"role": role, "name": None, "metadata": {}},
        "content": {"content_type": "text", "parts": [text]},
    }
    if create_time is not None:
        message["create_time"] = create_time
    return message


def conversation(
    *,
    mapping: dict[str, dict] | None = None,
    current_node: str = "leaf",
    title: str = "Sample Chat",
    **overrides: object,
) -> dict[str, object]:
    if mapping is None:
        mapping = {
            "root": node("root", None),
            "mid": node("mid", "root", text_message("user", "hello there")),
            "leaf": node("leaf", "mid", text_message("assistant", "greetings")),
        }
    base: dict[str, object] = {
        "conversation_id": CONVERSATION_ID,
        "id": CONVERSATION_ID,
        "title": title,
        "create_time": 1753300000.5,
        "update_time": 1753300600.75,
        "current_node": current_node,
        "mapping": mapping,
        "is_archived": False,
        "default_model_slug": "gpt-4o",
    }
    base.update(overrides)
    return base


def record_for(
    conv: dict[str, object], source_key: str | None = None, *, shard=None
) -> SourceRecord:
    key = source_key or f"{conv['conversation_id']}.json"
    return build_conversation_record(conv, key, shard=shard)


def text_of(conv: dict[str, object]) -> str:
    payload = record_for(conv).payload
    assert payload is not None
    return payload.decode("utf-8")


def shard_bytes(convs: list[dict[str, object]]) -> bytes:
    return json.dumps(convs).encode("utf-8")


class FakeStructuredExtractor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract(self, extraction) -> StructuredExtraction:
        self.calls.append(extraction.document_id)
        return StructuredExtraction(document_id=extraction.document_id)


class IngestionHarness:
    def __init__(self) -> None:
        self.connection = connect_database(":memory:")
        self.extractor = FakeStructuredExtractor()
        self.ingestor = DocumentIngestor(
            DocumentStore(self.connection),
            ExtractionStore(self.connection),
            self.extractor,  # type: ignore[arg-type]
            ChunkStore(self.connection),
            EmbeddingStore(self.connection),
            chunk_size=200,
            chunk_overlap=20,
        )


def embedding_row_count(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0]


class TestShardParsing:
    def test_valid_shard_decodes(self) -> None:
        data = parse_shard(shard_bytes([conversation()]))
        assert isinstance(data, list)
        assert data[0]["title"] == "Sample Chat"

    def test_malformed_json_is_rejected(self) -> None:
        with pytest.raises(ChatGPTParseError):
            parse_shard(b"{definitely not json")

    def test_invalid_utf8_is_rejected(self) -> None:
        with pytest.raises(ChatGPTParseError):
            parse_shard(b"\xff\xfe\x00")

    def test_conversation_lists_are_recognized_structurally(self) -> None:
        assert is_conversation_list([conversation()])
        assert not is_conversation_list([])
        assert not is_conversation_list({"ads": []})
        assert not is_conversation_list([{"settings": True}])
        assert not is_conversation_list(["conversations"])
        assert not is_conversation_list([{"mapping": {"root": {}}}])

    def test_official_sidecar_shapes_are_never_conversations(self) -> None:
        sidecars = {
            "ads.json": {},
            "export_manifest.json": {"export_files": []},
            "user_settings.json": [{"announcements": {}}],
            "shared_conversations.json": [{"conversation_id": "x"}],
            "library_files.json": [{"app_id": "chatgpt-web"}],
            "conversation_asset_file_names.json": {"file-a.dat": "a.jpeg"},
        }
        for payload in sidecars.values():
            assert not is_conversation_list(payload)


class TestComposition:
    def test_title_and_speaker_labels_are_composed(self) -> None:
        assert text_of(conversation()) == (
            "Sample Chat\n\nUser:\nhello there\n\nChatGPT:\ngreetings"
        )

    def test_active_branch_order_is_preserved_root_to_leaf(self) -> None:
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", text_message("user", "first")),
            "b": node("b", "a", text_message("assistant", "second")),
            "c": node("c", "b", text_message("user", "third")),
        }
        text = text_of(conversation(mapping=mapping, current_node="c"))
        assert text.index("first") < text.index("second") < text.index("third")

    def test_role_boundaries_are_kept_per_message(self) -> None:
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", text_message("assistant", "one")),
            "b": node("b", "a", text_message("assistant", "two")),
            "c": node("c", "b", text_message("user", "three")),
        }
        text = text_of(conversation(mapping=mapping, current_node="c"))
        assert text.startswith("Sample Chat\n\nChatGPT:\none")
        assert "ChatGPT:\ntwo\n\nUser:\nthree" in text

    def test_unknown_roles_pass_through_verbatim(self) -> None:
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", text_message("tool", "42")),
        }
        assert "tool:\n42" in text_of(conversation(mapping=mapping, current_node="a"))

    def test_whitespace_only_messages_are_dropped(self) -> None:
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", text_message("user", "  \n\t ")),
            "b": node("b", "a", text_message("assistant", "real answer")),
        }
        text = text_of(conversation(mapping=mapping, current_node="b"))
        assert text == "Sample Chat\n\nChatGPT:\nreal answer"

    def test_surrounding_whitespace_is_normalized(self) -> None:
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", text_message("user", "\n  padded  \n")),
        }
        assert "User:\npadded" in text_of(
            conversation(mapping=mapping, current_node="a")
        )

    def test_voice_transcriptions_render_as_text(self) -> None:
        user_audio = {
            "author": {"role": "user"},
            "content": {
                "content_type": "multimodal_text",
                "parts": [
                    {
                        "content_type": "audio_transcription",
                        "direction": "in",
                        "text": "voice question about batteries",
                    },
                    {
                        "audio_asset_pointer": "file://x",
                        "content_type": "audio_asset_pointer",
                    },
                ],
            },
        }
        assistant_audio = {
            "author": {"role": "assistant"},
            "content": {
                "content_type": "multimodal_text",
                "parts": [
                    {
                        "content_type": "audio_transcription",
                        "direction": "out",
                        "text": "spoken answer about batteries",
                    }
                ],
            },
        }
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", user_audio),
            "b": node("b", "a", assistant_audio),
        }
        composed = compose_conversation_text(
            conversation(mapping=mapping, current_node="b")
        )
        assert (
            "User:\nvoice question about batteries\n\n"
            "ChatGPT:\nspoken answer about batteries" in composed.text
        )
        assert composed.message_count == 2
        assert composed.media_part_count == 1

    def test_media_pointer_parts_stay_out_of_the_text(self) -> None:
        image_message = {
            "author": {"role": "user"},
            "content": {
                "content_type": "multimodal_text",
                "parts": [
                    "what is in this picture?",
                    {
                        "asset_pointer": "file-service://file-abc",
                        "content_type": "image_asset_pointer",
                        "width": 1024,
                        "height": 768,
                    },
                ],
            },
        }
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", image_message),
        }
        composed = compose_conversation_text(
            conversation(mapping=mapping, current_node="a")
        )
        assert composed.text.endswith("User:\nwhat is in this picture?")
        assert composed.media_part_count == 1

    def test_reasoning_content_is_excluded_but_counted(self) -> None:
        thoughts = {
            "author": {"role": "assistant"},
            "content": {
                "content_type": "thoughts",
                "thoughts": [
                    {
                        "summary": "Evaluating costs",
                        "content": "internal reasoning draft",
                        "chunks": [],
                        "finished": True,
                    }
                ],
            },
        }
        recap = {
            "author": {"role": "assistant"},
            "content": {
                "content_type": "reasoning_recap",
                "content": "Recapped reasoning",
            },
        }
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", thoughts),
            "b": node("b", "a", recap),
            "c": node("c", "b", text_message("assistant", "the visible answer")),
        }
        composed = compose_conversation_text(
            conversation(mapping=mapping, current_node="c")
        )
        assert "internal reasoning" not in composed.text
        assert "Recapped reasoning" not in composed.text
        assert "ChatGPT:\nthe visible answer" in composed.text
        assert composed.reasoning_message_count == 2

    def test_blank_title_omits_the_section(self) -> None:
        text = text_of(conversation(title="   "))
        assert text.startswith("User:\nhello there")

    def test_ids_timestamps_and_models_stay_out_of_text(self) -> None:
        text = text_of(conversation())
        assert CONVERSATION_ID not in text
        assert "1753300000" not in text
        assert "gpt-4o" not in text


class TestBranchingPolicy:
    def two_branch_mapping(self) -> dict[str, dict]:
        """Edited prompt variant (detached) plus the active branch."""
        return {
            "root": node("root", None),
            "orig_user": node(
                "orig_user", "root", text_message("user", "original prompt", 1.0)
            ),
            "orig_reply": node(
                "orig_reply",
                "orig_user",
                text_message("assistant", "old answer", 2.0),
            ),
            "edited_user": node(
                "edited_user", "root", text_message("user", "edited prompt", 3.0)
            ),
            "leaf": node(
                "leaf",
                "edited_user",
                text_message("assistant", "new answer", 4.0),
            ),
        }

    def test_only_the_active_branch_is_rendered(self) -> None:
        text = text_of(
            conversation(mapping=self.two_branch_mapping(), current_node="leaf")
        )
        assert "edited prompt" in text
        assert "new answer" in text
        assert "original prompt" not in text
        assert "old answer" not in text

    def test_alternatives_are_counted_not_discarded_silently(self) -> None:
        metadata = record_for(
            conversation(mapping=self.two_branch_mapping(), current_node="leaf")
        ).metadata
        assert metadata["alternative_node_count"] == 2
        assert metadata["message_count"] == 2
        assert metadata["node_count"] == 5

    def test_tree_order_wins_over_later_timestamps_off_branch(self) -> None:
        # The detached regeneration carries a LATER create_time than the
        # whole active branch; it still never leaks into the text.
        mapping = {
            "root": node("root", None),
            "kept_user": node(
                "kept_user", "root", text_message("user", "kept question", 1.0)
            ),
            "kept_reply": node(
                "kept_reply",
                "kept_user",
                text_message("assistant", "kept reply", 2.0),
            ),
            "regen_reply": node(
                "regen_reply",
                "kept_user",
                text_message("assistant", "regenerated reply", 99.0),
            ),
        }
        text = text_of(conversation(mapping=mapping, current_node="kept_reply"))
        assert "kept reply" in text
        assert "regenerated reply" not in text

    def test_identity_ignores_pruned_alternative_branches(self) -> None:
        active_only = {
            "root": node("root", None),
            "kept_user": node(
                "kept_user", "root", text_message("user", "question", 1.0)
            ),
            "leaf": node("leaf", "kept_user", text_message("assistant", "answer", 2.0)),
        }
        with_variant = {
            **active_only,
            "variant": node(
                "variant", "root", text_message("assistant", "other answer", 9.0)
            ),
        }
        first = record_for(conversation(mapping=active_only))
        second = record_for(conversation(mapping=with_variant))
        assert first.content_hash == second.content_hash
        assert first.payload == second.payload


class TestRecordIdentity:
    def test_content_hash_derives_from_composed_text(self) -> None:
        record = record_for(conversation())
        assert record.source_type == "chatgpt"
        assert record.source_key == f"{CONVERSATION_ID}.json"
        assert record.payload is not None
        assert record.content_hash == compute_content_hash(record.payload)

    def test_json_key_ordering_does_not_change_identity(self) -> None:
        reordered = json.loads(json.dumps(conversation(), sort_keys=True))
        first = record_for(conversation())
        second = record_for(reordered)
        assert first.content_hash == second.content_hash
        assert first.payload == second.payload

    def test_shard_placement_does_not_change_identity(self) -> None:
        first = record_for(conversation(), shard="conversations-000.json")
        second = record_for(conversation(), shard="conversations-004.json")
        assert first.content_hash == second.content_hash
        assert first.source_key == second.source_key

    def test_changed_content_changes_identity(self) -> None:
        first = record_for(conversation())
        edited = conversation(
            mapping={
                "root": node("root", None),
                "a": node("a", "root", text_message("user", "changed")),
            },
            current_node="a",
        )
        assert first.content_hash != record_for(edited).content_hash

    def test_payload_equals_hashed_composed_bytes(self) -> None:
        record = record_for(conversation())
        assert record.payload is not None
        assert record.payload.decode("utf-8").startswith("Sample Chat")


class TestProvenanceAndTimestamps:
    def test_metadata_carries_provenance(self) -> None:
        metadata = record_for(conversation(), shard="conversations-001.json").metadata
        assert metadata["filename"] == f"{CONVERSATION_ID}.json"
        assert metadata["mime_type"] == "application/json"
        assert metadata["conversation_id"] == CONVERSATION_ID
        assert metadata["title"] == "Sample Chat"
        assert metadata["shard"] == "conversations-001.json"
        assert metadata["default_model_slug"] == "gpt-4o"
        assert metadata["is_archived"] is False

    def test_epoch_timestamps_are_normalized_to_utc_iso(self) -> None:
        record = record_for(conversation())
        assert record.created_at == "2025-07-23T19:46:40.500000+00:00"
        assert record.modified_at == "2025-07-23T19:56:40.750000+00:00"

    def test_missing_update_time_falls_back_to_create_time(self) -> None:
        record = record_for(conversation(update_time=None))
        assert record.modified_at == record.created_at

    def test_invalid_timestamp_type_is_rejected(self) -> None:
        with pytest.raises(ChatGPTParseError):
            record_for(conversation(create_time="2025-07-23"))

    def test_model_slug_omitted_when_absent(self) -> None:
        metadata = record_for(conversation(default_model_slug=None)).metadata
        assert "default_model_slug" not in metadata

    def test_attachment_names_travel_in_metadata_not_text(self) -> None:
        upload = {
            "author": {"role": "user"},
            "metadata": {
                "attachments": [{"id": "file-abc", "name": "photo.jpeg", "size": 123}]
            },
            "content": {"content_type": "text", "parts": ["check this"]},
        }
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", upload),
        }
        record = record_for(conversation(mapping=mapping, current_node="a"))
        assert record.metadata["attachment_names"] == ["photo.jpeg"]
        assert record.payload is not None
        assert "photo.jpeg" not in record.payload.decode("utf-8")

    def test_no_attachment_names_key_without_attachments(self) -> None:
        assert "attachment_names" not in record_for(conversation()).metadata


class TestValidation:
    def test_non_object_conversation_is_rejected(self) -> None:
        with pytest.raises(ChatGPTParseError):
            build_conversation_record([conversation()], "x.json")  # type: ignore[arg-type]

    def test_missing_mapping_is_rejected(self) -> None:
        broken = conversation()
        broken["mapping"] = None
        with pytest.raises(ChatGPTParseError):
            record_for(broken)

    def test_current_node_outside_mapping_is_rejected(self) -> None:
        with pytest.raises(ChatGPTParseError):
            record_for(conversation(current_node="ghost"))

    def test_dangling_parent_link_is_rejected(self) -> None:
        mapping = {
            "root": node("root", "missing"),
            "leaf": node("leaf", "root", text_message("user", "hi")),
        }
        with pytest.raises(ChatGPTParseError):
            record_for(conversation(mapping=mapping))

    def test_parent_cycle_is_rejected(self) -> None:
        mapping = {
            "root": node("root", "leaf"),
            "mid": node("mid", "root", text_message("user", "hi")),
            "leaf": node("leaf", "mid", text_message("assistant", "yo")),
        }
        with pytest.raises(ChatGPTParseError):
            record_for(conversation(mapping=mapping))

    def test_non_dict_mapping_node_is_rejected(self) -> None:
        mapping = {"root": "not a node"}
        with pytest.raises(ChatGPTParseError):
            record_for(conversation(mapping=mapping))  # type: ignore[arg-type]

    def test_non_dict_message_is_rejected(self) -> None:
        mapping = {
            "root": node("root", None),
            "leaf": node("leaf", "root", "not a message"),
        }
        with pytest.raises(ChatGPTParseError):
            record_for(conversation(mapping=mapping))  # type: ignore[arg-type]

    def test_message_without_role_is_rejected(self) -> None:
        orphan = {"content": {"content_type": "text", "parts": ["x"]}}
        mapping = {
            "root": node("root", None),
            "leaf": node("leaf", "root", orphan),
        }
        with pytest.raises(ChatGPTParseError):
            record_for(conversation(mapping=mapping))

    def test_parts_of_wrong_type_are_rejected(self) -> None:
        broken = {
            "author": {"role": "user"},
            "content": {"content_type": "text", "parts": "oops"},
        }
        mapping = {
            "root": node("root", None),
            "leaf": node("leaf", "root", broken),
        }
        with pytest.raises(ChatGPTParseError):
            record_for(conversation(mapping=mapping))

    def test_conversation_without_usable_content_is_empty(self) -> None:
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", text_message("user", "   ")),
            "b": node(
                "b",
                "a",
                {
                    "author": {"role": "assistant"},
                    "content": {
                        "content_type": "reasoning_recap",
                        "content": "only reasoning here",
                    },
                },
            ),
        }
        with pytest.raises(EmptyConversationError):
            record_for(conversation(mapping=mapping, current_node="b"))

    def test_conversation_without_id_is_rejected(self) -> None:
        broken = conversation()
        del broken["conversation_id"]
        del broken["id"]
        with pytest.raises(ChatGPTParseError):
            build_conversation_record(broken, "broken.json")

    def test_error_messages_never_echo_personal_content(self) -> None:
        secret = "SECRET-PERSONAL-CONTENT"
        orphan = {"content": {"content_type": "text", "parts": [secret]}}
        mapping = {
            "root": node("root", None),
            "leaf": node("leaf", "root", orphan),
        }
        with pytest.raises(ChatGPTParseError) as excinfo:
            record_for(conversation(mapping=mapping))
        assert secret not in str(excinfo.value)


class TestAdapterDiscovery:
    @pytest.fixture()
    def export_dir(self, tmp_path: Path) -> Path:
        root = tmp_path / "ChatGPT_export"
        root.mkdir()

        def write(name: str, payload: bytes) -> None:
            (root / name).write_bytes(payload)

        write(
            "conversations-000.json",
            shard_bytes(
                [
                    conversation(),
                    conversation(
                        conversation_id="bbb-222",
                        title="Second",
                    ),
                ]
            ),
        )
        write(
            "conversations-001.json",
            shard_bytes([conversation(conversation_id="ccc-333")]),
        )
        write("chat.html", b"<html><body>derived render duplicate</body></html>")
        write("file-photo.dat", b"\xff\xd8\xff\xe0 jpeg blob")
        write("ads.json", b"{}")
        write(
            "user_settings.json",
            b'[{"announcements": {"a": "2026-01-01T00:00:00"}}]',
        )
        write(
            "shared_conversations.json",
            b'[{"conversation_id": "shared-1", "title": "Shared"}]',
        )
        write("library_files.json", b'[{"app_id": "chatgpt-web"}]')
        write("conversation_asset_file_names.json", b'{"f.dat": "f.jpeg"}')
        write("export_manifest.json", b'{"export_files": []}')
        return root

    def test_protocol_conformance(self, export_dir: Path) -> None:
        assert isinstance(ChatGPTSourceAdapter(export_dir), SourceAdapter)

    def test_source_type_is_stable(self, export_dir: Path) -> None:
        assert ChatGPTSourceAdapter(export_dir).source_type == "chatgpt"

    def test_discovery_yields_all_conversations_sorted(self, export_dir: Path) -> None:
        records = ChatGPTSourceAdapter(export_dir).discover()
        assert [record.source_key for record in records] == [
            f"{CONVERSATION_ID}.json",
            "bbb-222.json",
            "ccc-333.json",
        ]

    def test_html_render_duplicate_is_excluded(self, export_dir: Path) -> None:
        keys = [r.source_key for r in ChatGPTSourceAdapter(export_dir).discover()]
        assert "chat.html" not in keys
        assert all(key.endswith(".json") for key in keys)

    def test_sidecars_and_attachments_yield_no_records(self, export_dir: Path) -> None:
        records = ChatGPTSourceAdapter(export_dir).discover()
        titles = {r.metadata["title"] for r in records}
        assert titles == {"Sample Chat", "Second"}
        assert len(records) == 3

    def test_shard_provenance_is_recorded(self, export_dir: Path) -> None:
        records = {r.source_key: r for r in ChatGPTSourceAdapter(export_dir).discover()}
        assert (
            records[f"{CONVERSATION_ID}.json"].metadata["shard"]
            == "conversations-000.json"
        )
        assert records["ccc-333.json"].metadata["shard"] == "conversations-001.json"

    def test_corrupt_shard_propagates_from_discovery(self, tmp_path: Path) -> None:
        root = tmp_path / "ChatGPT_export"
        root.mkdir()
        (root / "conversations-000.json").write_bytes(b"{broken json")
        with pytest.raises(ChatGPTParseError):
            ChatGPTSourceAdapter(root).discover()

    def test_duplicate_conversation_across_shards_is_rejected(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "ChatGPT_export"
        root.mkdir()
        (root / "conversations-000.json").write_bytes(shard_bytes([conversation()]))
        (root / "conversations-001.json").write_bytes(
            shard_bytes([conversation(title="dupe")])
        )
        with pytest.raises(ChatGPTParseError):
            ChatGPTSourceAdapter(root).discover()


class TestLoadRecord:
    @pytest.fixture()
    def adapter(self, tmp_path: Path) -> ChatGPTSourceAdapter:
        root = tmp_path / "ChatGPT_export"
        root.mkdir()
        (root / "conversations-000.json").write_bytes(
            shard_bytes([conversation(conversation_id="aaa-111")])
        )
        (root / "conversations-001.json").write_bytes(
            shard_bytes([conversation(conversation_id="bbb-222")])
        )
        (root / "chat.html").write_bytes(b"<html>render</html>")
        return ChatGPTSourceAdapter(root)

    def test_round_trip_by_synthetic_key(self, adapter: ChatGPTSourceAdapter) -> None:
        loaded = adapter.load_record("bbb-222.json")
        assert loaded.metadata["conversation_id"] == "bbb-222"
        assert loaded.metadata["shard"] == "conversations-001.json"

    def test_key_resolution_survives_resharding(
        self, adapter: ChatGPTSourceAdapter, tmp_path: Path
    ) -> None:
        resharded = tmp_path / "Reexported"
        resharded.mkdir()
        (resharded / "conversations-000.json").write_bytes(
            shard_bytes(
                [
                    conversation(conversation_id="bbb-222"),
                    conversation(conversation_id="aaa-111"),
                ]
            )
        )
        original = adapter.load_record("aaa-111.json")
        moved = ChatGPTSourceAdapter(resharded).load_record("aaa-111.json")
        assert original.content_hash == moved.content_hash
        assert original.source_key == moved.source_key

    def test_missing_conversation_raises(self, adapter: ChatGPTSourceAdapter) -> None:
        with pytest.raises(SourceNotFoundError):
            adapter.load_record("zzz-999.json")

    def test_non_json_suffix_is_unsupported(
        self, adapter: ChatGPTSourceAdapter
    ) -> None:
        with pytest.raises(UnsupportedConversationError):
            adapter.load_record("chat.html")

    def test_path_escape_is_rejected(self, adapter: ChatGPTSourceAdapter) -> None:
        with pytest.raises(PathOutsideExportError):
            adapter.load_record("../outside.json")

    def test_load_record_matches_discovery_output(
        self, adapter: ChatGPTSourceAdapter
    ) -> None:
        discovered = {r.source_key: r for r in adapter.discover()}
        loaded = adapter.load_record("aaa-111.json")
        assert loaded.content_hash == discovered["aaa-111.json"].content_hash
        assert loaded.metadata == discovered["aaa-111.json"].metadata


class TestDownstreamClassification:
    def test_substantive_conversation_is_text_heavy(self) -> None:
        long_answer = (
            "Deterministic chunking derives chunk identifiers from the "
            "content itself, so re-ingesting an unchanged document never "
            "produces new rows. The chunker splits on paragraph boundaries "
            "first and only falls back to sentence boundaries when a single "
            "paragraph is still too large for the configured window."
        )
        mapping = {
            "root": node("root", None),
            "a": node(
                "a", "root", text_message("user", "Explain deterministic chunking.")
            ),
            "b": node("b", "a", text_message("assistant", long_answer)),
        }
        classification = classify_document(
            extract_text(record_for(conversation(mapping=mapping, current_node="b")))
        )
        assert classification.kind is DocumentKind.TEXT_HEAVY

    def test_tiny_conversation_is_mixed(self) -> None:
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", text_message("user", "ok")),
        }
        classification = classify_document(
            extract_text(record_for(conversation(mapping=mapping, current_node="a")))
        )
        assert classification.kind is DocumentKind.MIXED


class TestOrchestrationIntegration:
    def make_adapter(self, directory: Path) -> ChatGPTSourceAdapter:
        return ChatGPTSourceAdapter(directory)

    def test_ingestion_counts_and_rerun_idempotency(self, tmp_path: Path) -> None:
        root = tmp_path / "ChatGPT_export"
        root.mkdir()
        long_answer = "well reasoned paragraph about determinism. " * 10
        mapping = {
            "root": node("root", None),
            "a": node("a", "root", text_message("user", "teach me chunking")),
            "b": node("b", "a", text_message("assistant", long_answer)),
        }
        tiny = {
            "root": node("root", None),
            "a": node("a", "root", text_message("user", "hi")),
        }
        (root / "conversations-000.json").write_bytes(
            shard_bytes(
                [
                    conversation(mapping=mapping, current_node="b"),
                    conversation(
                        mapping=tiny,
                        current_node="a",
                        conversation_id="tiny-1",
                    ),
                ]
            )
        )

        harness = IngestionHarness()
        adapter = self.make_adapter(root)
        summary = ingest_source(adapter, harness.ingestor)

        assert summary.source_type == "chatgpt"
        assert summary.documents == 2
        assert summary.kind_counts == {
            DocumentKind.TEXT_HEAVY.value: 1,
            DocumentKind.MIXED.value: 1,
        }
        assert summary.chunk_count > 0
        assert (
            summary.chunk_count
            == harness.connection.execute(
                "SELECT COUNT(*) FROM document_chunks"
            ).fetchone()[0]
        )
        assert len(harness.extractor.calls) == 1
        assert embedding_row_count(harness.connection) == 0

        rerun = ingest_source(adapter, harness.ingestor)
        assert rerun == summary
        assert len(harness.extractor.calls) == 1
        assert embedding_row_count(harness.connection) == 0
        assert (
            harness.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            == 2
        )


def test_chatgpt_module_has_no_infrastructure_dependencies() -> None:
    import personal_ai.sources.chatgpt as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in ("ollama", "httpx", "embedding", "sqlite"):
        assert forbidden not in lowered, forbidden
