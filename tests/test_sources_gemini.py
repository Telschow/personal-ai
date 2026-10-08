"""Tests for the Gemini conversation source adapter."""

import json
from pathlib import Path

import pytest

from personal_ai.documents.classifier import DocumentKind, classify_document
from personal_ai.documents.extractor import extract_text
from personal_ai.sources.base import SourceAdapter
from personal_ai.sources.gemini import (
    EmptyConversationError,
    GeminiParseError,
    GeminiSourceAdapter,
    PathOutsideExportError,
    SourceNotFoundError,
    UnsupportedConversationError,
    build_conversation_record,
    compose_conversation_text,
    is_conversation_shape,
    parse_gemini_conversation,
)

DEFAULT_KEY = "20260101_Sample Conversation_abc123.json"


def conversation_bytes(**overrides: object) -> bytes:
    base: dict[str, object] = {
        "id": "abc123",
        "title": "Sample Conversation",
        "messages": [
            {"role": "user", "content": "What is a chunker?"},
            {"role": "assistant", "content": "It splits text deterministically."},
        ],
        "url": "https://gemini.google.com/app/abc123",
        "createdAt": "2026-01-01T10:00:00.000Z",
        "lastMessageAt": "2026-01-01T10:05:00.000Z",
        "exportedAt": "2026-07-25T00:00:00.000Z",
    }
    base.update(overrides)
    base.setdefault("messageCount", len(base["messages"]))
    return json.dumps(base).encode("utf-8")


def record_for(payload: bytes, source_key: str = DEFAULT_KEY):
    return build_conversation_record(source_key, payload)


def text_of(payload: bytes, **overrides: object) -> str:
    record = record_for(payload, **overrides)
    assert record.payload is not None
    return record.payload.decode("utf-8")


class TestParsing:
    def test_valid_payload_decodes_to_object(self) -> None:
        parsed = parse_gemini_conversation(conversation_bytes())
        assert parsed["id"] == "abc123"

    def test_malformed_json_is_rejected(self) -> None:
        with pytest.raises(GeminiParseError):
            parse_gemini_conversation(b"{not json")

    def test_invalid_utf8_is_rejected(self) -> None:
        with pytest.raises(GeminiParseError):
            parse_gemini_conversation(b"\xff\xfe\x00")

    def test_non_object_top_level_is_rejected(self) -> None:
        with pytest.raises(GeminiParseError):
            parse_gemini_conversation(b"[1, 2, 3]")

    def test_aggregate_shape_is_recognized(self) -> None:
        assert is_conversation_shape(json.loads(conversation_bytes()))
        assert not is_conversation_shape({"conversations": []})
        assert not is_conversation_shape([1, 2])


class TestComposition:
    def test_title_and_roles_are_composed(self) -> None:
        assert text_of(conversation_bytes()) == (
            "Sample Conversation\n\n"
            "User:\nWhat is a chunker?\n\n"
            "Gemini:\nIt splits text deterministically."
        )

    def test_message_order_is_preserved(self) -> None:
        payload = conversation_bytes(
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
                {"role": "user", "content": "third"},
            ]
        )
        text = text_of(payload)
        assert text.index("first") < text.index("second") < text.index("third")

    def test_role_information_is_preserved_per_message(self) -> None:
        text = text_of(conversation_bytes())
        assert "User:\nWhat is a chunker?" in text
        assert "Gemini:\nIt splits text deterministically." in text

    def test_assistant_first_and_repeated_roles_pass_through(self) -> None:
        payload = conversation_bytes(
            messages=[
                {"role": "assistant", "content": "hello"},
                {"role": "assistant", "content": "still me"},
                {"role": "user", "content": "hi"},
            ]
        )
        text = text_of(payload)
        assert text.startswith("Sample Conversation\n\nGemini:\nhello")
        assert "Gemini:\nstill me\n\nUser:\nhi" in text

    def test_unknown_roles_pass_through_verbatim(self) -> None:
        payload = conversation_bytes(
            messages=[{"role": "tool_output", "content": "42"}]
        )
        assert "tool_output:\n42" in text_of(payload)

    def test_whitespace_only_messages_are_dropped(self) -> None:
        payload = conversation_bytes(
            messages=[
                {"role": "user", "content": "  \n\t "},
                {"role": "assistant", "content": "real answer"},
            ],
            messageCount=2,
        )
        text = text_of(payload)
        assert text == "Sample Conversation\n\nGemini:\nreal answer"

    def test_surrounding_whitespace_in_content_is_normalized(self) -> None:
        payload = conversation_bytes(
            messages=[{"role": "user", "content": "\n  padded answer  \n"}]
        )
        assert "User:\npadded answer" in text_of(payload)

    def test_ids_timestamps_and_urls_stay_out_of_text(self) -> None:
        text = text_of(conversation_bytes())
        assert "abc123" not in text
        assert "gemini.google.com" not in text
        assert "createdAt" not in text

    def test_missing_title_omits_the_section(self) -> None:
        payload = conversation_bytes(title="")
        assert text_of(payload).startswith("User:\nWhat is a chunker?")

    def test_compose_reports_rendered_message_count(self) -> None:
        parsed = parse_gemini_conversation(conversation_bytes())
        text, rendered = compose_conversation_text(parsed)
        assert rendered == 2
        assert text.startswith("Sample Conversation\n\n")


class TestRecordIdentity:
    def test_content_hash_derives_from_composed_text(self) -> None:
        first = record_for(conversation_bytes())
        second = record_for(conversation_bytes())
        assert first.content_hash == second.content_hash
        assert first.source_type == "gemini"
        assert first.source_key == DEFAULT_KEY

    def test_json_key_ordering_does_not_change_identity(self) -> None:
        reordered = json.dumps(json.loads(conversation_bytes()), sort_keys=True).encode(
            "utf-8"
        )
        plain = conversation_bytes()
        assert record_for(plain).content_hash == record_for(reordered).content_hash
        assert record_for(plain).payload == record_for(reordered).payload

    def test_source_key_is_preserved_verbatim(self) -> None:
        key = "nested/20260101_Other_conv_def456.json"
        assert record_for(conversation_bytes(), source_key=key).source_key == key

    def test_payload_equals_hashed_composed_bytes(self) -> None:
        from personal_ai.documents.models import compute_content_hash

        record = record_for(conversation_bytes())
        assert record.payload is not None
        assert record.content_hash == compute_content_hash(record.payload)


class TestValidation:
    def test_messages_not_a_list_is_rejected(self) -> None:
        with pytest.raises(GeminiParseError):
            record_for(conversation_bytes(messages="oops"))

    def test_manifest_without_messages_is_unsupported(self) -> None:
        manifest = json.dumps(
            {"exported_at": "2026-07-25T00:00:00Z", "conversations": []}
        ).encode("utf-8")
        with pytest.raises(UnsupportedConversationError):
            record_for(manifest)

    def test_non_dict_message_is_rejected(self) -> None:
        with pytest.raises(GeminiParseError):
            record_for(conversation_bytes(messages=["not a dict"]))

    def test_missing_role_is_rejected(self) -> None:
        with pytest.raises(GeminiParseError):
            record_for(conversation_bytes(messages=[{"content": "orphan"}]))

    def test_non_string_role_is_rejected(self) -> None:
        with pytest.raises(GeminiParseError):
            record_for(conversation_bytes(messages=[{"role": 7, "content": "x"}]))

    def test_non_string_content_is_rejected(self) -> None:
        with pytest.raises(GeminiParseError):
            record_for(
                conversation_bytes(
                    messages=[{"role": "user", "content": {"parts": [1]}}]
                )
            )

    def test_declared_count_mismatch_is_rejected(self) -> None:
        with pytest.raises(GeminiParseError):
            record_for(conversation_bytes(messageCount=99))

    def test_conversation_without_usable_messages_is_empty(self) -> None:
        payload = conversation_bytes(
            messages=[{"role": "user", "content": "   "}], messageCount=1
        )
        with pytest.raises(EmptyConversationError):
            record_for(payload)

    def test_error_messages_never_echo_message_content(self) -> None:
        secret = "SECRET-PERSONAL-CONTENT"
        cases = [
            conversation_bytes(messages=["leak"]),
            conversation_bytes(messages=[{"content": "leak"}]),
            conversation_bytes(messages=[{"role": "user"}]),
            conversation_bytes(messages=[{"role": "user", "content": {"a": secret}}]),
        ]
        for payload in cases:
            with pytest.raises(GeminiParseError) as excinfo:
                record_for(payload)
            assert secret not in str(excinfo.value)


class TestProvenance:
    def test_metadata_carries_provenance(self) -> None:
        metadata = record_for(conversation_bytes()).metadata
        assert metadata["filename"] == DEFAULT_KEY.rsplit("/", 1)[-1]
        assert metadata["mime_type"] == "application/json"
        assert metadata["conversation_id"] == "abc123"
        assert metadata["title"] == "Sample Conversation"
        assert metadata["url"] == "https://gemini.google.com/app/abc123"
        assert metadata["message_count"] == 2

    def test_timestamps_are_normalized_to_utc(self) -> None:
        record = record_for(conversation_bytes())
        assert record.created_at == "2026-01-01T10:00:00+00:00"
        assert record.modified_at == "2026-01-01T10:05:00+00:00"

    def test_last_message_falls_back_to_created(self) -> None:
        payload = conversation_bytes(lastMessageAt=None)
        record = record_for(payload)
        assert record.modified_at == record.created_at

    def test_unparseable_timestamps_degrade_to_empty(self) -> None:
        payload = conversation_bytes(createdAt="not-a-date")
        record = record_for(payload)
        assert record.created_at == ""

    def test_blank_title_and_id_fall_back_to_filename_stem(self) -> None:
        payload = conversation_bytes(id="", title="   ")
        metadata = record_for(payload).metadata
        assert metadata["title"] == "20260101_Sample Conversation_abc123"
        assert metadata["conversation_id"] == "20260101_Sample Conversation_abc123"

    def test_url_omitted_from_metadata_when_absent(self) -> None:
        payload = conversation_bytes(url="")
        assert "url" not in record_for(payload).metadata


class TestAdapterDiscovery:
    @pytest.fixture()
    def export_dir(self, tmp_path: Path) -> Path:
        root = tmp_path / "Gemini"
        root.mkdir()
        (root / "b_second.json").write_bytes(conversation_bytes(id="b"))
        (root / "a_first.json").write_bytes(conversation_bytes(id="a"))
        (root / "a_first.md").write_bytes(b"# derived twin, never discovered")
        (root / "_all_conversations.json").write_bytes(b'{"conversations": []}')
        (root / "_urls_index.json").write_bytes(b"[]")
        (root / "gemini-export-manifest.json").write_bytes(
            json.dumps({"conversations": []}).encode("utf-8")
        )
        nested = root / "Takeout 3" / "NotebookLM"
        nested.mkdir(parents=True)
        (nested / "Growth metadata.json").write_bytes(b'{"title": "Growth"}')
        return root

    def test_protocol_conformance(self, export_dir: Path) -> None:
        assert isinstance(GeminiSourceAdapter(export_dir), SourceAdapter)

    def test_source_type_is_stable(self, export_dir: Path) -> None:
        assert GeminiSourceAdapter(export_dir).source_type == "gemini"

    def test_discovery_yields_only_conversations_sorted(self, export_dir: Path) -> None:
        records = GeminiSourceAdapter(export_dir).discover()
        assert [record.source_key for record in records] == [
            "a_first.json",
            "b_second.json",
        ]

    def test_md_twin_never_produces_records(self, export_dir: Path) -> None:
        keys = [
            record.source_key for record in GeminiSourceAdapter(export_dir).discover()
        ]
        assert not any(key.endswith(".md") for key in keys)

    def test_discovery_skips_nested_foreign_sidecars(self, export_dir: Path) -> None:
        keys = [
            record.source_key for record in GeminiSourceAdapter(export_dir).discover()
        ]
        assert all(not key.startswith("Takeout 3") for key in keys)

    def test_load_record_round_trip(self, export_dir: Path) -> None:
        adapter = GeminiSourceAdapter(export_dir)
        loaded = adapter.load_record("a_first.json")
        assert loaded.metadata["conversation_id"] == "a"

    def test_load_record_rejects_md_twin(self, export_dir: Path) -> None:
        with pytest.raises(UnsupportedConversationError):
            GeminiSourceAdapter(export_dir).load_record("a_first.md")

    def test_load_record_rejects_aggregates(self, export_dir: Path) -> None:
        with pytest.raises(UnsupportedConversationError):
            GeminiSourceAdapter(export_dir).load_record("_all_conversations.json")

    def test_load_record_rejects_missing_files(self, export_dir: Path) -> None:
        with pytest.raises(SourceNotFoundError):
            GeminiSourceAdapter(export_dir).load_record("missing.json")

    def test_load_record_rejects_path_escape(self, export_dir: Path) -> None:
        with pytest.raises(PathOutsideExportError):
            GeminiSourceAdapter(export_dir).load_record("../outside.json")

    def test_corrupt_conversation_propagates_from_discovery(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "Gemini"
        root.mkdir()
        (root / "broken.json").write_bytes(b"{definitely not json")
        with pytest.raises(GeminiParseError):
            GeminiSourceAdapter(root).discover()


class TestClassification:
    def test_substantive_conversation_is_text_heavy(self) -> None:
        payload = conversation_bytes(
            messages=[
                {"role": "user", "content": "Explain deterministic chunking."},
                {
                    "role": "assistant",
                    "content": (
                        "Deterministic chunking derives chunk identifiers from "
                        "the content itself, so re-ingesting an unchanged "
                        "document never produces new rows. The chunker splits "
                        "on paragraph boundaries first and only falls back to "
                        "sentence boundaries when a single paragraph is still "
                        "too large for the configured window."
                    ),
                },
            ]
        )
        extraction = extract_text(record_for(payload))
        classification = classify_document(extraction)
        assert classification.kind is DocumentKind.TEXT_HEAVY

    def test_tiny_conversation_is_mixed(self) -> None:
        payload = conversation_bytes(messages=[{"role": "user", "content": "ok"}])
        classification = classify_document(extract_text(record_for(payload)))
        assert classification.kind is DocumentKind.MIXED
