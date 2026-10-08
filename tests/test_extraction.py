"""Unit tests for provider-independent text extraction."""

import pytest

from personal_ai.documents import (
    TextExtractionError,
    compute_content_hash,
    compute_document_id,
    document_from_source_record,
    extract_text,
)
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


def test_extracts_utf8_text_including_non_ascii() -> None:
    record = make_record(payload="Hello, München 🌍".encode())

    result = extract_text(record)

    assert result.text == "Hello, München 🌍"


def test_empty_payload_yields_empty_text() -> None:
    result = extract_text(make_record(payload=b""))

    assert result.text == ""


def test_invalid_utf8_raises_text_extraction_error() -> None:
    record = make_record(source_key="broken.txt", payload=b"\xff\xfe s3cret")

    with pytest.raises(TextExtractionError) as exc_info:
        extract_text(record)

    assert "broken.txt" in str(exc_info.value)
    assert "s3cret" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, UnicodeDecodeError)


def test_result_preserves_canonical_document_identity() -> None:
    record = make_record(
        source_type="mail",
        source_key="msg-42",
        content_hash=compute_content_hash(b"body"),
        payload=b"body",
    )

    result = extract_text(record)

    assert result.document_id == compute_document_id(
        "mail", "msg-42", record.content_hash
    )
    assert result.document_id == document_from_source_record(record).id
    assert result.source_type == "mail"
    assert result.source_key == "msg-42"
    assert result.content_hash == record.content_hash


def test_result_metadata_is_preserved_defensively() -> None:
    record = make_record(
        metadata={"mime_type": "text/plain"},
        payload=b"text",
    )

    result = extract_text(record)
    record.metadata["mutated"] = True

    assert result.metadata == {"mime_type": "text/plain"}

    result.metadata["changed"] = 1

    assert record.metadata == {"mime_type": "text/plain", "mutated": True}


def test_missing_payload_raises_value_error() -> None:
    with pytest.raises(ValueError, match="no materialized payload"):
        extract_text(make_record(payload=None))
