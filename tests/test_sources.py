"""Unit tests for source records and canonical document building."""

from pathlib import Path

from personal_ai.documents import (
    Document,
    compute_content_hash,
    compute_document_id,
    document_from_source_record,
)
from personal_ai.sources import FilesystemSourceAdapter, SourceAdapter
from personal_ai.sources.models import SourceRecord


def make_record(**overrides: object) -> SourceRecord:
    values: dict[str, object] = {
        "source_type": "file",
        "source_key": "notes/ideas.txt",
        "content_hash": "hash-1",
        "created_at": "2026-08-22T10:00:00+00:00",
        "modified_at": "2026-08-22T10:00:00+00:00",
    }
    values.update(overrides)
    metadata = values.pop("metadata", {})
    assert isinstance(metadata, dict)
    payload = values.pop("payload", None)
    assert isinstance(payload, bytes) or payload is None
    return SourceRecord(
        metadata=metadata,
        payload=payload,
        **values,  # type: ignore[arg-type]
    )


def test_record_defaults() -> None:
    record = make_record()

    assert record.payload is None
    assert record.metadata == {}


def test_record_metadata_defaults_are_not_shared() -> None:
    first = make_record()
    second = make_record()

    first.metadata["origin"] = "takeout"

    assert second.metadata == {}


def test_filesystem_adapter_satisfies_source_adapter_protocol(
    tmp_path: Path,
) -> None:
    adapter = FilesystemSourceAdapter(tmp_path)

    assert isinstance(adapter, SourceAdapter)
    assert adapter.source_type == "file"


def test_canonical_document_uses_stable_identity_helpers() -> None:
    record = make_record(content_hash=compute_content_hash(b"hello"))

    document = document_from_source_record(record)

    assert document.id == compute_document_id(
        "file", "notes/ideas.txt", record.content_hash
    )
    assert document.source == "notes/ideas.txt"
    assert document.source_type == "file"
    assert document.content_hash == record.content_hash
    assert document.created_at == record.created_at
    assert document.modified_at == record.modified_at


def test_identical_records_canonicalize_to_identical_documents() -> None:
    first = document_from_source_record(make_record())
    second = document_from_source_record(make_record())

    assert first == second


def test_changed_source_key_or_hash_changes_document_identity() -> None:
    base = document_from_source_record(make_record())
    moved = document_from_source_record(make_record(source_key="other/path.txt"))
    changed = document_from_source_record(make_record(content_hash="hash-2"))

    assert moved.id != base.id
    assert changed.id != base.id


def test_canonical_metadata_is_a_defensive_copy() -> None:
    record = make_record(metadata={"origin": "takeout"})

    document = document_from_source_record(record)
    record.metadata["mutated"] = True

    assert isinstance(document, Document)
    assert document.metadata == {"origin": "takeout"}
