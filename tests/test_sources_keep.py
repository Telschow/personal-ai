"""Tests for the Google Keep source adapter."""

import json
from pathlib import Path

import pytest

from personal_ai.documents.classifier import DocumentKind, classify_document
from personal_ai.documents.extractor import extract_text
from personal_ai.documents.models import compute_content_hash
from personal_ai.sources.base import SourceAdapter
from personal_ai.sources.keep import (
    EmptyNoteError,
    KeepParseError,
    KeepSourceAdapter,
    PathOutsideExportError,
    SourceNotFoundError,
    TrashedNoteError,
    UnsupportedNoteError,
    build_note_record,
    compose_note_text,
    parse_keep_note,
)

SOURCE_KEY = "notes/example.json"
CREATED_USEC = 1699999999_123456
EDITED_USEC = 1700000000_000000
EXPECTED_CREATED = "2023-11-14T22:13:19.123456+00:00"
EXPECTED_EDITED = "2023-11-14T22:13:20+00:00"

ANNOTATION_KEYS = {"description", "source", "title", "url"}
LIST_ITEM_KEYS = {"text", "textHtml", "isChecked"}


def make_note(**overrides: object) -> dict[str, object]:
    """A minimized note shaped like real Google Keep exports."""
    note: dict[str, object] = {
        "color": "DEFAULT",
        "isTrashed": False,
        "isPinned": False,
        "isArchived": False,
        "title": "Groceries",
        "textContent": "Milk and eggs",
        "createdTimestampUsec": CREATED_USEC,
        "userEditedTimestampUsec": EDITED_USEC,
    }
    note.update(overrides)
    return note


def make_payload(note: dict[str], **dumps_kwargs: object) -> bytes:
    return json.dumps(note, **dumps_kwargs).encode("utf-8")


def record_for(**overrides: object):
    return build_note_record(SOURCE_KEY, make_payload(make_note(**overrides)))


def write_note(root: Path, name: str, payload: bytes | None = None) -> Path:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload if payload is not None else make_payload(make_note()))
    return target


class TestParsing:
    def test_valid_object_parses(self) -> None:
        assert parse_keep_note(make_payload(make_note()))["title"] == "Groceries"

    @pytest.mark.parametrize(
        ("payload", "description"),
        [
            (b"not json at all", "invalid JSON"),
            (b"[1, 2]", "JSON array"),
            (b'"\\"just a string\\""', "JSON string"),
        ],
    )
    def test_malformed_payloads_raise(self, payload: bytes, description: str) -> None:
        with pytest.raises(KeepParseError):
            parse_keep_note(payload)


class TestComposition:
    def test_title_and_body_join_with_blank_line(self) -> None:
        record = record_for()
        assert record.payload is not None
        assert record.payload.decode("utf-8") == "Groceries\n\nMilk and eggs"

    def test_title_only_note_keeps_title_as_text(self) -> None:
        record = record_for(textContent="")
        assert record.payload is not None
        assert record.payload.decode("utf-8") == "Groceries"

    def test_list_items_become_plain_lines_in_order(self) -> None:
        record = record_for(
            title="Packlist",
            textContent="",
            listContent=[
                {"text": "Passport", "isChecked": True},
                {"text": "", "isChecked": False},
                {"text": "  Charger ", "isChecked": False},
            ],
        )
        assert record.payload is not None
        assert record.payload.decode("utf-8") == "Packlist\n\nPassport\nCharger"

    def test_checklist_toggle_never_changes_identity(self) -> None:
        def items(checked: bool) -> list[dict[str, object]]:
            return [
                {"text": "Lachs", "isChecked": checked},
                {"text": "3 Kichererbsen", "isChecked": not checked},
            ]

        unticked = record_for(listContent=items(False))
        ticked = record_for(listContent=items(True))
        assert unticked.content_hash != ""
        assert unticked.content_hash == ticked.content_hash
        assert unticked.payload == ticked.payload

    def test_annotation_block_appends_title_description_url(self) -> None:
        record = record_for(
            title="Flat hunt",
            textContent="Visited three flats",
            annotations=[
                {
                    "description": "2 rooms, 680 euros",
                    "source": "WEBLINK",
                    "title": "Expose 42",
                    "url": "https://example.de/expose/42",
                }
            ],
        )
        assert record.payload is not None
        assert record.payload.decode("utf-8") == (
            "Flat hunt\n\n"
            "Visited three flats\n\n"
            "Expose 42\n"
            "2 rooms, 680 euros\n"
            "https://example.de/expose/42"
        )

    def test_annotation_url_already_in_body_is_not_duplicated(self) -> None:
        url = "https://www.immobilienscout24.de/expose/136240924"
        record = record_for(
            title="ImmobilienScout24",
            textContent=url,
            annotations=[
                {
                    "description": "",
                    "source": "WEBLINK",
                    "title": "Ich bin kein Roboter - ImmobilienScout24",
                    "url": url,
                }
            ],
        )
        assert record.payload is not None
        text = record.payload.decode("utf-8")
        assert text.count(url) == 1
        assert "Ich bin kein Roboter" in text

    def test_non_string_bookkeeping_fields_contribute_nothing(self) -> None:
        noisy = make_note(
            title=None,
            textContent=None,
            listContent=[{"text": None}],
            annotations=[{"title": 42, "url": {"deep": True}}],
        )
        assert compose_note_text(noisy) == ""


class TestRecordPolicy:
    def test_full_metadata_and_timestamps(self) -> None:
        record = record_for(isPinned=True, isArchived=True)
        assert record.source_type == "google_keep"
        assert record.source_key == SOURCE_KEY
        assert record.created_at == EXPECTED_CREATED
        assert record.modified_at == EXPECTED_EDITED
        assert record.metadata == {
            "filename": "example.json",
            "mime_type": "application/json",
            "title": "Groceries",
            "is_pinned": True,
            "is_archived": True,
            "annotation_count": 0,
        }

    def test_content_hash_covers_materialized_payload(self) -> None:
        record = record_for()
        assert record.payload is not None
        assert record.content_hash == compute_content_hash(record.payload)

    @pytest.mark.parametrize("trashed", [True])
    def test_trashed_note_refuses_to_load_even_when_rich(self, trashed: bool) -> None:
        with pytest.raises(TrashedNoteError):
            record_for(textContent="Important archived knowledge", isTrashed=trashed)

    @pytest.mark.parametrize(
        ("overrides", "case"),
        [
            (
                {"title": "", "textContent": ""},
                "all fields blank",
            ),
            ({"title": "", "textContent": "   \n  "}, "whitespace only"),
            (
                {
                    "title": "",
                    "textContent": "",
                    "annotations": [{"description": "  ", "url": ""}],
                },
                "blank annotation fields only",
            ),
        ],
    )
    def test_contentless_notes_are_rejected(
        self, overrides: dict[str, object], case: str
    ) -> None:
        with pytest.raises(EmptyNoteError):
            record_for(**overrides)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("createdTimestampUsec", None),
            ("userEditedTimestampUsec", None),
            ("createdTimestampUsec", True),
            ("createdTimestampUsec", "1699999999"),
            ("createdTimestampUsec", 1699999999.5),
        ],
    )
    def test_invalid_timestamps_are_schema_violations(
        self, field: str, value: object
    ) -> None:
        with pytest.raises(KeepParseError):
            record_for(**{field: value})

    def test_missing_timestamp_field_is_a_violation(self) -> None:
        note = make_note()
        del note["userEditedTimestampUsec"]
        with pytest.raises(KeepParseError):
            build_note_record(SOURCE_KEY, make_payload(note))

    def test_json_key_order_does_not_change_the_record(self) -> None:
        note = make_note(
            annotations=[dict.fromkeys(ANNOTATION_KEYS, "")],
            listContent=[dict.fromkeys(LIST_ITEM_KEYS)],
        )
        ordered = build_note_record(SOURCE_KEY, make_payload(note, sort_keys=True))
        shuffled = build_note_record(SOURCE_KEY, make_payload(note))
        assert ordered == shuffled


class TestDownstreamShape:
    def test_short_bookmark_notes_intentionally_classify_as_mixed(self) -> None:
        record = record_for(title="Papers", textContent="https://arxiv.org/abs/123")
        classification = classify_document(extract_text(record))
        assert classification.kind is DocumentKind.MIXED


class TestKeepSourceAdapter:
    @pytest.fixture()
    def export_dir(self, tmp_path: Path) -> Path:
        root = tmp_path / "Takeout" / "Keep"
        root.mkdir(parents=True)
        write_note(root, "a_first.json")
        write_note(
            root,
            "b_bookmark.json",
            make_payload(make_note(title="", textContent="https://x.example/a")),
        )
        write_note(root, "nested/c_third.json")
        write_note(root, "d_trashed.json", make_payload(make_note(isTrashed=True)))
        write_note(
            root,
            "e_empty.json",
            make_payload(make_note(title="", textContent="")),
        )
        (root / "f_notes.txt").write_text("not a keep note")
        (root / "g_page.html").write_text("<html></html>")
        write_note(root, ".git/h_hidden.json")
        return root

    def test_discovers_only_usable_notes_deterministically(
        self, export_dir: Path
    ) -> None:
        adapter = KeepSourceAdapter(export_dir)
        discovered = adapter.discover()
        assert [record.source_key for record in discovered] == [
            "a_first.json",
            "b_bookmark.json",
            "nested/c_third.json",
        ]
        assert all(record.source_type == "google_keep" for record in discovered)

    def test_discover_propagates_corrupt_files(self, export_dir: Path) -> None:
        write_note(export_dir, "z_corrupt.json", b"{broken")
        adapter = KeepSourceAdapter(export_dir)
        with pytest.raises(KeepParseError):
            adapter.discover()

    def test_load_record_matches_direct_build(self, export_dir: Path) -> None:
        adapter = KeepSourceAdapter(export_dir)
        loaded = adapter.load_record("a_first.json")
        direct = build_note_record(
            "a_first.json", (export_dir / "a_first.json").read_bytes()
        )
        assert loaded == direct

    def test_load_record_rejects_escapes_and_bad_targets(
        self, export_dir: Path
    ) -> None:
        adapter = KeepSourceAdapter(export_dir)
        with pytest.raises(PathOutsideExportError):
            adapter.load_record("../outside.json")
        with pytest.raises(SourceNotFoundError):
            adapter.load_record("missing.json")
        with pytest.raises(UnsupportedNoteError):
            adapter.load_record("f_notes.txt")

    def test_adapter_satisfies_source_protocol(self, export_dir: Path) -> None:
        assert isinstance(KeepSourceAdapter(export_dir), SourceAdapter)
