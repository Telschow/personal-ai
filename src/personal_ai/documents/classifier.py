"""Deterministic classification of documents from measured content."""

from dataclasses import dataclass
from enum import StrEnum, unique

from personal_ai.documents.extractor import TextExtractionResult

TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS = 200


@unique
class DocumentKind(StrEnum):
    """Outcome kinds supported by document classification."""

    EMPTY = "empty"
    TEXT_HEAVY = "text_heavy"
    IMAGE_HEAVY = "image_heavy"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class DocumentCharacteristics:
    """Measured content signals for a single document.

    Text counts describe the extracted text. ``page_count`` and
    ``image_count`` stay ``None`` until a future analyzer (PDF pages,
    image inspection) can actually supply them; absence means unknown,
    never zero. Future analyzers fill them in and reuse :func:`classify`.
    """

    text_character_count: int
    non_whitespace_character_count: int
    line_count: int
    word_count: int
    page_count: int | None = None
    image_count: int | None = None


@dataclass(frozen=True, slots=True)
class DocumentClassification:
    """Deterministic classification verdict, including its evidence."""

    document_id: str
    kind: DocumentKind
    characteristics: DocumentCharacteristics


def _has_image_evidence(characteristics: DocumentCharacteristics) -> bool:
    return characteristics.image_count is not None and characteristics.image_count > 0


def _is_text_dominant(characteristics: DocumentCharacteristics) -> bool:
    return (
        characteristics.non_whitespace_character_count
        >= TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS
    )


def measure_text(text: str) -> DocumentCharacteristics:
    """Measure decoded text; the only signals available today."""
    return DocumentCharacteristics(
        text_character_count=len(text),
        non_whitespace_character_count=sum(1 for ch in text if not ch.isspace()),
        line_count=len(text.splitlines()),
        word_count=len(text.split()),
    )


def classify(
    document_id: str, characteristics: DocumentCharacteristics
) -> DocumentClassification:
    """Decide a document kind from measurements alone.

    Image evidence outranks text when present. Documents with neither
    image evidence nor usable text are ``EMPTY``. Small amounts of text
    without image evidence are ``MIXED``: with no image/page analysis yet
    the system cannot tell whether extraction simply missed content.
    """
    if _has_image_evidence(characteristics):
        if _is_text_dominant(characteristics):
            kind = DocumentKind.MIXED
        else:
            kind = DocumentKind.IMAGE_HEAVY
    elif characteristics.non_whitespace_character_count == 0:
        kind = DocumentKind.EMPTY
    elif _is_text_dominant(characteristics):
        kind = DocumentKind.TEXT_HEAVY
    else:
        kind = DocumentKind.MIXED

    return DocumentClassification(
        document_id=document_id,
        kind=kind,
        characteristics=characteristics,
    )


def classify_document(extraction: TextExtractionResult) -> DocumentClassification:
    """Classify straight from a Phase 4 extraction result.

    For PDFs, uses the page and image counts stored in extraction metadata
    by the PDF extractor (including zero image counts). For non-PDF
    documents, falls back to text-only measurement. Standalone raster
    images carry ``image_count`` metadata (from the image text extraction),
    which is honored as image evidence so an empty-text image classifies as
    ``IMAGE_HEAVY`` rather than ``EMPTY``.
    """
    characteristics = measure_text(extraction.text)

    page_count = extraction.metadata.get("pdf_page_count")
    image_count = extraction.metadata.get("pdf_image_count")
    if image_count is None:
        image_count = extraction.metadata.get("image_count")
    if page_count is not None or image_count is not None:
        characteristics = DocumentCharacteristics(
            text_character_count=characteristics.text_character_count,
            non_whitespace_character_count=characteristics.non_whitespace_character_count,
            line_count=characteristics.line_count,
            word_count=characteristics.word_count,
            page_count=page_count,
            image_count=image_count if image_count is not None else 0,
        )

    return classify(extraction.document_id, characteristics)
