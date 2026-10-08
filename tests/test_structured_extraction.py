"""Unit tests for structured extraction models, parsing, and contract."""

import json
from dataclasses import asdict

import pytest

from personal_ai.documents import (
    MalformedStructuredOutputError,
    StructuredExtraction,
    StructuredExtractor,
    TextExtractionResult,
    parse_structured_extraction,
)

DOCUMENT_ID = "doc-1"
SECRET_MARKER = "s3cret-personal-content"


def make_extraction(text: str = "body text") -> TextExtractionResult:
    return TextExtractionResult(
        document_id=DOCUMENT_ID,
        source_type="file",
        source_key="notes/ideas.txt",
        content_hash="hash-1",
        text=text,
    )


class FakeStructuredExtractor:
    """Deterministic in-memory extractor; proves injection needs no Ollama."""

    def __init__(self, result: StructuredExtraction | None = None) -> None:
        self.calls: list[TextExtractionResult] = []
        self._result = result

    def extract(self, extraction: TextExtractionResult) -> StructuredExtraction:
        self.calls.append(extraction)
        if self._result is not None:
            return self._result
        return StructuredExtraction(document_id=extraction.document_id)


def test_full_payload_parses_into_typed_result() -> None:
    payload = {
        "summary": "Career planning notes",
        "people": ["Alice", "Bob"],
        "organizations": ["ACME"],
        "projects": ["kitchen renovation"],
        "goals": ["financial independence"],
        "topics": ["career", "finance"],
    }

    result = parse_structured_extraction(DOCUMENT_ID, payload)

    assert result == StructuredExtraction(
        document_id=DOCUMENT_ID,
        summary="Career planning notes",
        people=("Alice", "Bob"),
        organizations=("ACME",),
        projects=("kitchen renovation",),
        goals=("financial independence",),
        topics=("career", "finance"),
    )


def test_missing_fields_default_to_empty_values() -> None:
    result = parse_structured_extraction(DOCUMENT_ID, {})

    assert result == StructuredExtraction(document_id=DOCUMENT_ID)


def test_result_preserves_document_identity() -> None:
    result = parse_structured_extraction("other-doc", {"summary": "x"})

    assert result.document_id == "other-doc"


def test_metadata_defaults_are_not_shared() -> None:
    first = StructuredExtraction(document_id="doc-1")
    second = StructuredExtraction(document_id="doc-2")

    first.metadata["model"] = "test"

    assert second.metadata == {}


def test_unknown_keys_are_ignored() -> None:
    payload = {"summary": "ok", "hallucinated_field": ["ignored"], "confidence": 0.9}

    result = parse_structured_extraction(DOCUMENT_ID, payload)

    assert result.summary == "ok"


@pytest.mark.parametrize("payload", [["list"], "string", 42, None])
def test_non_object_payload_raises_malformed_output_error(
    payload: object,
) -> None:
    with pytest.raises(MalformedStructuredOutputError):
        parse_structured_extraction(DOCUMENT_ID, payload)


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("summary", {"secret": SECRET_MARKER}),
        ("people", {"secret": SECRET_MARKER}),
        ("people", ["alice", 3]),
        ("topics", {"not": "a list"}),
    ],
)
def test_wrongly_typed_fields_raise_without_leaking_values(
    field_name: str, bad_value: object
) -> None:
    payload: dict[str, object] = {field_name: bad_value}

    with pytest.raises(MalformedStructuredOutputError) as exc_info:
        parse_structured_extraction(DOCUMENT_ID, payload)

    assert field_name in str(exc_info.value)
    assert SECRET_MARKER not in str(exc_info.value)
    assert "alice" not in str(exc_info.value)


def test_structured_extraction_is_json_serializable() -> None:
    result = StructuredExtraction(
        document_id=DOCUMENT_ID, people=["Alice"], topics=["career"]
    )

    encoded = json.dumps(asdict(result))

    assert json.loads(encoded)["people"] == ["Alice"]


def test_fake_extractor_satisfies_protocol_without_ollama() -> None:
    fake = FakeStructuredExtractor()

    assert isinstance(fake, StructuredExtractor)

    result = fake.extract(make_extraction())

    assert isinstance(result, StructuredExtraction)
    assert result.document_id == DOCUMENT_ID
    assert len(fake.calls) == 1
