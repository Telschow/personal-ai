"""Unit tests for deterministic document classification."""

import pytest

from personal_ai.documents import (
    TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS,
    DocumentCharacteristics,
    DocumentKind,
    TextExtractionResult,
    classify,
    classify_document,
    measure_text,
)

DOCUMENT_ID = "doc-1"


def make_extraction(text: str) -> TextExtractionResult:
    return TextExtractionResult(
        document_id=DOCUMENT_ID,
        source_type="file",
        source_key="notes/ideas.txt",
        content_hash="hash-1",
        text=text,
    )


def make_characteristics(**overrides: object) -> DocumentCharacteristics:
    values: dict[str, object] = {
        "text_character_count": 0,
        "non_whitespace_character_count": 0,
        "line_count": 0,
        "word_count": 0,
    }
    values.update(overrides)
    return DocumentCharacteristics(**values)  # type: ignore[arg-type]


def test_measure_text_counts_are_exact() -> None:
    characteristics = measure_text("Hello, München 🌍\nsecond line")

    assert characteristics.text_character_count == 28
    assert characteristics.non_whitespace_character_count == 24
    assert characteristics.line_count == 2
    assert characteristics.word_count == 5


def test_characteristics_page_and_image_counts_default_to_unknown() -> None:
    characteristics = measure_text("some text")

    assert characteristics.page_count is None
    assert characteristics.image_count is None


def test_empty_text_classifies_as_empty() -> None:
    assert classify_document(make_extraction("")).kind is DocumentKind.EMPTY


def test_whitespace_only_text_classifies_as_empty() -> None:
    assert classify_document(make_extraction(" \n\t   ")).kind is DocumentKind.EMPTY


def test_clearly_text_heavy_content_classifies_as_text_heavy() -> None:
    text = "\n".join(
        f"Line {number} of the meeting notes with several meaningful words"
        for number in range(12)
    )

    assert classify_document(make_extraction(text)).kind is DocumentKind.TEXT_HEAVY


@pytest.mark.parametrize(
    ("non_whitespace_characters", "expected"),
    [
        (TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS - 1, DocumentKind.MIXED),
        (TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS, DocumentKind.TEXT_HEAVY),
    ],
)
def test_threshold_boundary_between_mixed_and_text_heavy(
    non_whitespace_characters: int, expected: DocumentKind
) -> None:
    text = "a" * non_whitespace_characters

    assert classify_document(make_extraction(text)).kind is expected


def test_small_low_content_text_is_not_confidently_text_heavy() -> None:
    assert classify_document(make_extraction("hi")).kind is DocumentKind.MIXED


def test_classification_is_deterministic() -> None:
    extraction = make_extraction("deterministic body of text")

    assert classify_document(extraction) == classify_document(extraction)


def test_classification_preserves_identity_and_measurements() -> None:
    classification = classify_document(make_extraction("body text"))

    assert classification.document_id == DOCUMENT_ID
    assert classification.characteristics == measure_text("body text")


def test_classification_result_never_contains_document_text() -> None:
    classification = classify_document(make_extraction("topsecret contents"))

    assert "topsecret" not in repr(classification)


def test_image_evidence_with_no_usable_text_is_image_heavy() -> None:
    characteristics = make_characteristics(image_count=4)

    assert classify(DOCUMENT_ID, characteristics).kind is DocumentKind.IMAGE_HEAVY


def test_image_evidence_with_substantial_text_is_mixed() -> None:
    characteristics = make_characteristics(
        text_character_count=300,
        non_whitespace_character_count=TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS,
        word_count=50,
        image_count=4,
    )

    assert classify(DOCUMENT_ID, characteristics).kind is DocumentKind.MIXED


def test_zero_image_count_is_not_image_evidence() -> None:
    characteristics = make_characteristics(image_count=0)

    assert classify(DOCUMENT_ID, characteristics).kind is DocumentKind.EMPTY
