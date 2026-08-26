"""Provider-independent extraction of text from source records."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from personal_ai.documents.models import compute_document_id
from personal_ai.sources.models import SourceRecord


class TextExtractionError(Exception):
    """Raised when a record's payload cannot be decoded as UTF-8."""


class PDFExtractionError(Exception):
    """Raised when a PDF payload cannot be parsed by PyMuPDF."""


@dataclass(frozen=True, slots=True)
class PageExtraction:
    """Text extracted from a single page of a multi-page document.

    ``page_number`` is 1-based, matching human page numbering.
    """

    page_number: int
    text: str


@dataclass(frozen=True, slots=True)
class TextExtractionResult:
    """Text extracted from a single source record.

    ``document_id`` is derived with the same identity helpers used by
    canonicalization, so an extracted result can always be associated with
    its canonical document without recomputing identity at call sites.

    ``pages`` is ``None`` for non-paginated documents (plain text, markdown).
    For PDFs it contains one :class:`PageExtraction` per page that had
    extractable text.  The combined ``text`` field always contains the full
    verbatim text (all pages joined), so callers that do not need page
    boundaries can ignore ``pages`` unchanged.
    """

    document_id: str
    source_type: str
    source_key: str
    content_hash: str
    text: str
    metadata: dict[str, object] = field(default_factory=dict)
    pages: tuple[PageExtraction, ...] | None = None


def _is_pdf_record(record: SourceRecord) -> bool:
    """Detect whether a source record represents a PDF file.

    Checks the mime_type metadata first (set by FilesystemSourceAdapter),
    then falls back to the file extension for adapters that do not set
    mime_type.
    """
    mime = record.metadata.get("mime_type")
    if mime == "application/pdf":
        return True
    ext = record.metadata.get("extension")
    if ext == ".pdf":
        return True
    source_path = PurePosixPath(record.source_key)
    return source_path.suffix.lower() == ".pdf"


def _extract_pdf(record: SourceRecord, document_id: str) -> TextExtractionResult:
    """Extract text from a PDF payload using PyMuPDF.

    Returns a :class:`TextExtractionResult` with per-page ``pages`` and
    combined ``text``.  PDF-specific diagnostics are stored in ``metadata``
    so the classifier can use them for image-heavy / mixed detection.
    """
    import pymupdf

    if record.payload is None:
        msg = f"Source record {record.source_key!r} has no materialized payload"
        raise ValueError(msg)

    try:
        doc = pymupdf.open(stream=record.payload, filetype="pdf")
    except Exception as exc:
        msg = f"Source record {record.source_key!r} is not a valid PDF"
        raise PDFExtractionError(msg) from exc

    try:
        page_count = doc.page_count
        pages: list[PageExtraction] = []
        total_image_count = 0
        pages_with_text = 0

        for page_index in range(page_count):
            page = doc.load_page(page_index)
            page_text = page.get_text("text")
            images = page.get_images(full=True)
            image_count = len(images)
            total_image_count += image_count
            if page_text.strip():
                pages_with_text += 1
            pages.append(PageExtraction(page_number=page_index + 1, text=page_text))

        combined_text = "\n\n".join(p.text for p in pages)

        metadata = dict(record.metadata)
        metadata["pdf_page_count"] = page_count
        metadata["pdf_pages_with_text"] = pages_with_text
        metadata["pdf_image_count"] = total_image_count
        metadata["pdf_text_characters"] = len(combined_text)

        return TextExtractionResult(
            document_id=document_id,
            source_type=record.source_type,
            source_key=record.source_key,
            content_hash=record.content_hash,
            text=combined_text,
            metadata=metadata,
            pages=tuple(pages),
        )
    finally:
        doc.close()


def extract_text(record: SourceRecord) -> TextExtractionResult:
    """Extract text from a source record.

    For PDF records (detected by mime type or extension), uses PyMuPDF to
    extract per-page text.  For all other records, decodes the payload as
    strict UTF-8.

    Empty payloads yield empty text.  Invalid UTF-8 for non-PDF records
    raises :class:`TextExtractionError` instead of being silently replaced.
    Invalid PDF payloads raise :class:`PDFExtractionError`.
    """
    if record.payload is None:
        msg = f"Source record {record.source_key!r} has no materialized payload"
        raise ValueError(msg)

    document_id = compute_document_id(
        record.source_type, record.source_key, record.content_hash
    )

    if _is_pdf_record(record):
        return _extract_pdf(record, document_id)

    try:
        text = record.payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"Source record {record.source_key!r} payload is not valid UTF-8"
        raise TextExtractionError(msg) from exc

    return TextExtractionResult(
        document_id=document_id,
        source_type=record.source_type,
        source_key=record.source_key,
        content_hash=record.content_hash,
        text=text,
        metadata=dict(record.metadata),
    )
