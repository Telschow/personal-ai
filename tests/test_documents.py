"""Unit tests for document models and stable identity."""

from personal_ai.documents import (
    Document,
    DocumentChunk,
    compute_content_hash,
    compute_document_id,
)


def test_content_hash_is_deterministic_sha256() -> None:
    assert compute_content_hash(b"hello") == compute_content_hash(b"hello")
    assert (
        compute_content_hash(b"hello")
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_content_hash_differs_for_different_content() -> None:
    assert compute_content_hash(b"a") != compute_content_hash(b"b")


def test_document_id_is_stable_for_identical_inputs() -> None:
    first = compute_document_id("file", "notes/todo.txt", "hash-1")
    second = compute_document_id("file", "notes/todo.txt", "hash-1")

    assert first == second


def test_document_id_differs_when_any_component_differs() -> None:
    base = compute_document_id("file", "notes.txt", "hash-1")

    assert base != compute_document_id("pdf", "notes.txt", "hash-1")
    assert base != compute_document_id("file", "other/notes.txt", "hash-1")
    assert base != compute_document_id("file", "notes.txt", "hash-2")


def test_document_id_components_cannot_merge_ambiguously() -> None:
    shifted = ("ab", "c")
    other = ("a", "bc")

    assert compute_document_id("file", *shifted) != compute_document_id("file", *other)


def make_document(**overrides: object) -> Document:
    values: dict[str, object] = {
        "id": "doc-1",
        "source": "notes/ideas.txt",
        "source_type": "file",
        "content_hash": "hash-1",
        "created_at": "2026-08-22T10:00:00+00:00",
        "modified_at": "2026-08-22T10:00:00+00:00",
    }
    values.update(overrides)
    metadata = values.pop("metadata", {})
    assert isinstance(metadata, dict)
    return Document(metadata=metadata, **values)  # type: ignore[arg-type]


def test_document_optional_fields_default_to_none() -> None:
    document = make_document()

    assert document.path is None
    assert document.filename is None
    assert document.mime_type is None
    assert document.metadata == {}


def test_document_metadata_defaults_are_not_shared() -> None:
    first = make_document()
    second = make_document()

    first.metadata["topic"] = "goals"

    assert second.metadata == {}


def test_chunk_defaults() -> None:
    chunk = DocumentChunk(id="chunk-1", document_id="doc-1", text="body text")

    assert chunk.page_number is None
    assert chunk.metadata == {}


def test_chunk_metadata_defaults_are_not_shared() -> None:
    first = DocumentChunk(id="chunk-1", document_id="doc-1", text="one")
    second = DocumentChunk(id="chunk-2", document_id="doc-1", text="two")

    first.metadata["page"] = 3

    assert second.metadata == {}
