"""Unit tests for PDF text extraction and page-aware chunking."""

import pymupdf
import pytest

from personal_ai.documents import (
    DocumentCharacteristics,
    DocumentKind,
    PDFExtractionError,
    TextExtractionError,
    chunk_document,
    classify,
    classify_document,
    extract_text,
)
from personal_ai.sources.models import SourceRecord


def _make_pdf_bytes(pages: list[str]) -> bytes:
    """Create a minimal in-memory PDF with the given page texts.

    Long texts are split into lines before insertion to ensure PyMuPDF
    can extract all content. A tall page is used to avoid truncation.
    """
    doc = pymupdf.open()
    for page_text in pages:
        # Use a tall page so all text fits without truncation
        page = doc.new_page(width=595, height=max(842, 40 + len(page_text) // 2))
        if len(page_text) <= 200:
            page.insert_text((36, 36), page_text)
        else:
            # Split into lines to avoid PyMuPDF rendering limits
            lines = [page_text[i : i + 80] for i in range(0, len(page_text), 80)]
            page.insert_text((36, 36), "\n".join(lines))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_image_only_pdf(page_count: int = 2) -> bytes:
    """Create a PDF with pages that contain only embedded images, no text."""
    doc = pymupdf.open()
    for _ in range(page_count):
        page = doc.new_page(width=200, height=200)
        # Create a small pixmap and embed it as an image
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), 0)
        pixmap.clear_with(255)
        img_bytes = pixmap.tobytes("png")
        img_rect = pymupdf.Rect(10, 10, 190, 190)
        page.insert_image(img_rect, stream=img_bytes)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_mixed_pdf() -> bytes:
    """Create a PDF with one text page and one image-only page."""
    doc = pymupdf.open()
    # Page 1: text — needs >200 non-whitespace chars for MIXED classification
    text = (
        "This is a substantial text page with enough content to be classified as text dominant. "
        * 5
    )
    page1 = doc.new_page(width=595, height=max(842, 40 + len(text) // 2))
    lines = [text[i : i + 80] for i in range(0, len(text), 80)]
    page1.insert_text((36, 36), "\n".join(lines))
    # Page 2: embedded image only
    page2 = doc.new_page(width=200, height=200)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), 0)
    pixmap.clear_with(128)
    img_bytes = pixmap.tobytes("png")
    page2.insert_image(pymupdf.Rect(10, 10, 190, 190), stream=img_bytes)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def make_record(
    payload: bytes,
    source_key: str = "docs/test.pdf",
    mime_type: str = "application/pdf",
    **overrides: object,
) -> SourceRecord:
    values: dict[str, object] = {
        "source_type": "file",
        "source_key": source_key,
        "content_hash": "hash-pdf",
        "created_at": "2026-08-22T10:00:00+00:00",
        "modified_at": "2026-08-22T10:00:00+00:00",
    }
    values.update(overrides)
    metadata = values.pop("metadata", {})
    if isinstance(metadata, dict):
        metadata.setdefault("mime_type", mime_type)
        metadata.setdefault("extension", ".pdf")
    return SourceRecord(
        metadata=metadata,
        payload=payload,
        **values,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# A. Two-page text PDF
# ---------------------------------------------------------------------------


class TestTwoPageTextPDF:
    def test_correct_page_count(self) -> None:
        pdf = _make_pdf_bytes(["Page one content here", "Page two content here"])
        record = make_record(pdf)

        result = extract_text(record)

        assert result.pages is not None
        assert len(result.pages) == 2

    def test_correct_text_per_page(self) -> None:
        pdf = _make_pdf_bytes(["First page text", "Second page text"])
        record = make_record(pdf)

        result = extract_text(record)

        assert result.pages is not None
        assert "First page text" in result.pages[0].text
        assert "Second page text" in result.pages[1].text

    def test_page_numbers_are_one_based(self) -> None:
        pdf = _make_pdf_bytes(["Alpha", "Beta", "Gamma"])
        record = make_record(pdf)

        result = extract_text(record)

        assert result.pages is not None
        assert [p.page_number for p in result.pages] == [1, 2, 3]

    def test_combined_text_contains_all_pages(self) -> None:
        pdf = _make_pdf_bytes(["Hello from page one", "Hello from page two"])
        record = make_record(pdf)

        result = extract_text(record)

        assert "Hello from page one" in result.text
        assert "Hello from page two" in result.text

    def test_pdf_metadata_in_result(self) -> None:
        pdf = _make_pdf_bytes(["Page A", "Page B"])
        record = make_record(pdf)

        result = extract_text(record)

        assert result.metadata["pdf_page_count"] == 2
        assert result.metadata["pdf_pages_with_text"] == 2
        assert result.metadata["pdf_image_count"] == 0
        assert result.metadata["pdf_text_characters"] > 0

    def test_document_identity_is_preserved(self) -> None:
        from personal_ai.documents.canonical import document_from_source_record

        pdf = _make_pdf_bytes(["Test"])
        record = make_record(pdf)

        result = extract_text(record)

        assert result.document_id == document_from_source_record(record).id
        assert result.source_type == "file"
        assert result.source_key == "docs/test.pdf"


# ---------------------------------------------------------------------------
# B. Multi-page PDF requiring chunking
# ---------------------------------------------------------------------------


class TestPageAwareChunking:
    def test_large_page_produces_multiple_chunks(self) -> None:
        long_text = "The quick brown fox jumps over the lazy dog. " * 100  # ~4500 chars
        pdf = _make_pdf_bytes([long_text])
        record = make_record(pdf)

        result = extract_text(record)
        chunks = chunk_document(result, chunk_size=1000, overlap=100)

        assert len(chunks) > 1

    def test_all_chunks_from_one_page_retain_page_number(self) -> None:
        page_text = "Sentence number {} throughout the document. ".format
        long_page = "".join(page_text(i) for i in range(200))  # ~8800 chars
        pdf = _make_pdf_bytes([long_page])
        record = make_record(pdf)

        result = extract_text(record)
        chunks = chunk_document(result, chunk_size=500, overlap=50)

        assert len(chunks) > 1
        assert all(c.page_number == 1 for c in chunks)

    def test_different_pages_get_different_page_numbers(self) -> None:
        page1_text = "First page " * 100
        page2_text = "Second page " * 100
        pdf = _make_pdf_bytes([page1_text, page2_text])
        record = make_record(pdf)

        result = extract_text(record)
        chunks = chunk_document(result, chunk_size=500, overlap=50)

        page1_chunks = [c for c in chunks if c.page_number == 1]
        page2_chunks = [c for c in chunks if c.page_number == 2]
        assert len(page1_chunks) > 0
        assert len(page2_chunks) > 0

    def test_chunk_index_is_global_across_pages(self) -> None:
        page1_text = "First page content " * 50
        page2_text = "Second page content " * 50
        pdf = _make_pdf_bytes([page1_text, page2_text])
        record = make_record(pdf)

        result = extract_text(record)
        chunks = chunk_document(result, chunk_size=500, overlap=50)

        indices = [c.metadata["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_page_number_in_chunk_metadata(self) -> None:
        pdf = _make_pdf_bytes(["Page one text here", "Page two text here"])
        record = make_record(pdf)

        result = extract_text(record)
        chunks = chunk_document(result, chunk_size=100, overlap=10)

        for chunk in chunks:
            assert "page_number" in chunk.metadata
            assert chunk.metadata["page_number"] == chunk.page_number

    def test_empty_page_produces_no_chunks(self) -> None:
        pdf = _make_pdf_bytes(["Some text here for page one", ""])
        record = make_record(pdf)

        result = extract_text(record)
        chunks = chunk_document(result, chunk_size=1000, overlap=100)

        page1_chunks = [c for c in chunks if c.page_number == 1]
        page2_chunks = [c for c in chunks if c.page_number == 2]
        assert len(page1_chunks) > 0
        assert len(page2_chunks) == 0

    def test_three_page_document_chunking(self) -> None:
        pages = ["Page one " * 50, "Page two " * 50, "Page three " * 50]
        pdf = _make_pdf_bytes(pages)
        record = make_record(pdf)

        result = extract_text(record)
        chunks = chunk_document(result, chunk_size=300, overlap=30)

        for page_num in [1, 2, 3]:
            page_chunks = [c for c in chunks if c.page_number == page_num]
            assert len(page_chunks) > 0
            assert all(c.page_number == page_num for c in page_chunks)


# ---------------------------------------------------------------------------
# C. Image-only PDF
# ---------------------------------------------------------------------------


class TestImageOnlyPDF:
    def test_extraction_does_not_crash(self) -> None:
        pdf = _make_image_only_pdf(2)
        record = make_record(pdf)

        result = extract_text(record)

        assert result.pages is not None
        assert len(result.pages) == 2

    def test_text_can_be_empty(self) -> None:
        pdf = _make_image_only_pdf(1)
        record = make_record(pdf)

        result = extract_text(record)

        assert result.text.strip() == ""

    def test_classification_is_image_heavy(self) -> None:
        pdf = _make_image_only_pdf(2)
        record = make_record(pdf)

        result = extract_text(record)
        classification = classify_document(result)

        assert classification.kind is DocumentKind.IMAGE_HEAVY

    def test_metadata_reports_images(self) -> None:
        pdf = _make_image_only_pdf(3)
        record = make_record(pdf)

        result = extract_text(record)

        assert result.metadata["pdf_page_count"] == 3
        assert result.metadata["pdf_image_count"] > 0
        assert result.metadata["pdf_pages_with_text"] == 0

    def test_image_only_pdf_produces_no_chunks(self) -> None:
        pdf = _make_image_only_pdf(2)
        record = make_record(pdf)

        result = extract_text(record)
        chunks = chunk_document(result)

        assert chunks == ()


# ---------------------------------------------------------------------------
# D. Mixed PDF
# ---------------------------------------------------------------------------


class TestMixedPDF:
    def test_one_text_page_one_image_page(self) -> None:
        pdf = _make_mixed_pdf()
        record = make_record(pdf)

        result = extract_text(record)

        assert result.pages is not None
        assert len(result.pages) == 2
        assert result.pages[0].text.strip() != ""
        assert result.pages[1].text.strip() == ""

    def test_classification_is_mixed(self) -> None:
        pdf = _make_mixed_pdf()
        record = make_record(pdf)

        result = extract_text(record)
        classification = classify_document(result)

        assert classification.kind is DocumentKind.MIXED

    def test_metadata_reports_both_text_and_images(self) -> None:
        pdf = _make_mixed_pdf()
        record = make_record(pdf)

        result = extract_text(record)

        assert result.metadata["pdf_page_count"] == 2
        assert result.metadata["pdf_pages_with_text"] == 1
        assert result.metadata["pdf_image_count"] > 0

    def test_chunks_only_from_text_page(self) -> None:
        pdf = _make_mixed_pdf()
        record = make_record(pdf)

        result = extract_text(record)
        chunks = chunk_document(result, chunk_size=500, overlap=50)

        assert all(c.page_number == 1 for c in chunks)


# ---------------------------------------------------------------------------
# E. Malformed / non-PDF binary input
# ---------------------------------------------------------------------------


class TestMalformedInput:
    def test_random_binary_raises_pdf_extraction_error(self) -> None:
        record = make_record(b"\x00\x01\x02\x03\x04\x05 random garbage")

        with pytest.raises(PDFExtractionError):
            extract_text(record)

    def test_error_message_includes_source_key(self) -> None:
        record = make_record(
            b"\x00\x01\x02\x03",
            source_key="docs/broken.pdf",
        )

        with pytest.raises(PDFExtractionError, match="broken.pdf"):
            extract_text(record)

    def test_error_has_original_cause(self) -> None:
        record = make_record(b"\x00\x01\x02\x03")

        with pytest.raises(PDFExtractionError) as exc_info:
            extract_text(record)

        assert exc_info.value.__cause__ is not None

    def test_empty_pdf_payload_raises_error(self) -> None:
        record = make_record(b"")

        with pytest.raises(PDFExtractionError):
            extract_text(record)

    def test_non_pdf_binary_with_pdf_extension_raises_error(self) -> None:
        record = make_record(
            b"\xff\xfe\x00\x00not a pdf at all",
            source_key="docs/fake.pdf",
            mime_type="application/pdf",
        )

        with pytest.raises(PDFExtractionError):
            extract_text(record)


# ---------------------------------------------------------------------------
# F. Regression: non-PDF files still work unchanged
# ---------------------------------------------------------------------------


class TestNonPDFRegression:
    def test_text_file_still_works(self) -> None:
        record = SourceRecord(
            source_type="file",
            source_key="notes/ideas.txt",
            content_hash="hash-1",
            created_at="2026-08-22T10:00:00+00:00",
            modified_at="2026-08-22T10:00:00+00:00",
            payload=b"Hello, world!",
            metadata={"mime_type": "text/plain", "extension": ".txt"},
        )

        result = extract_text(record)

        assert result.text == "Hello, world!"
        assert result.pages is None

    def test_markdown_file_still_works(self) -> None:
        record = SourceRecord(
            source_type="file",
            source_key="docs/readme.md",
            content_hash="hash-md",
            created_at="2026-08-22T10:00:00+00:00",
            modified_at="2026-08-22T10:00:00+00:00",
            payload=b"# Title\n\nContent here",
            metadata={"mime_type": "text/markdown", "extension": ".md"},
        )

        result = extract_text(record)

        assert result.text == "# Title\n\nContent here"
        assert result.pages is None

    def test_non_pdf_utf8_still_raises_on_invalid_utf8(self) -> None:
        from personal_ai.documents import TextExtractionError

        record = SourceRecord(
            source_type="file",
            source_key="broken.txt",
            content_hash="hash-broken",
            created_at="2026-08-22T10:00:00+00:00",
            modified_at="2026-08-22T10:00:00+00:00",
            payload=b"\xff\xfe invalid",
            metadata={"mime_type": "text/plain", "extension": ".txt"},
        )

        with pytest.raises(TextExtractionError):
            extract_text(record)

    def test_non_pdf_chunking_has_no_page_number(self) -> None:
        record = SourceRecord(
            source_type="file",
            source_key="notes/test.txt",
            content_hash="hash-test",
            created_at="2026-08-22T10:00:00+00:00",
            modified_at="2026-08-22T10:00:00+00:00",
            payload=b"Some text content for chunking test",
            metadata={"mime_type": "text/plain", "extension": ".txt"},
        )

        result = extract_text(record)
        chunks = chunk_document(result, chunk_size=20, overlap=5)

        assert all(c.page_number is None for c in chunks)
        assert all("page_number" not in c.metadata for c in chunks)

    def test_image_extension_not_confused_with_pdf(self) -> None:
        record = SourceRecord(
            source_type="file",
            source_key="images/photo.png",
            content_hash="hash-img",
            created_at="2026-08-22T10:00:00+00:00",
            modified_at="2026-08-22T10:00:00+00:00",
            payload=b"\x89PNG\r\n\x1a\n\x00",
            metadata={"mime_type": "image/png", "extension": ".png"},
        )

        with pytest.raises(TextExtractionError):
            extract_text(record)


# ---------------------------------------------------------------------------
# PDF detection edge cases
# ---------------------------------------------------------------------------


class TestPDFDetection:
    def test_detected_by_mime_type(self) -> None:
        record = make_record(b"\x00\x01", mime_type="application/pdf")
        assert record.metadata["mime_type"] == "application/pdf"
        # Even though the payload is garbage, extract_text should route to PDF path
        with pytest.raises(PDFExtractionError):
            extract_text(record)

    def test_detected_by_extension_in_metadata(self) -> None:
        record = SourceRecord(
            source_type="file",
            source_key="docs/report.pdf",
            content_hash="h",
            created_at="2026-08-22T10:00:00+00:00",
            modified_at="2026-08-22T10:00:00+00:00",
            payload=b"\x00\x01",
            metadata={"extension": ".pdf"},
        )
        with pytest.raises(PDFExtractionError):
            extract_text(record)

    def test_detected_by_source_key_suffix(self) -> None:
        record = SourceRecord(
            source_type="file",
            source_key="docs/file.pdf",
            content_hash="h",
            created_at="2026-08-22T10:00:00+00:00",
            modified_at="2026-08-22T10:00:00+00:00",
            payload=b"\x00\x01",
            metadata={},
        )
        with pytest.raises(PDFExtractionError):
            extract_text(record)

    def test_uppercase_pdf_extension_detected(self) -> None:
        record = SourceRecord(
            source_type="file",
            source_key="docs/file.PDF",
            content_hash="h",
            created_at="2026-08-22T10:00:00+00:00",
            modified_at="2026-08-22T10:00:00+00:00",
            payload=b"\x00\x01",
            metadata={"extension": ".PDF"},
        )
        with pytest.raises(PDFExtractionError):
            extract_text(record)


# ---------------------------------------------------------------------------
# Classification with PDF characteristics
# ---------------------------------------------------------------------------


class TestPDFClassification:
    def test_text_heavy_pdf(self) -> None:
        pages = ["Page with substantial text content " * 20] * 3
        pdf = _make_pdf_bytes(pages)
        record = make_record(pdf)

        result = extract_text(record)
        classification = classify_document(result)

        assert classification.kind is DocumentKind.TEXT_HEAVY
        assert classification.characteristics.page_count == 3
        assert classification.characteristics.image_count == 0

    def test_empty_pdf_classifies_as_empty(self) -> None:
        pdf = _make_pdf_bytes(["", "", ""])
        record = make_record(pdf)

        result = extract_text(record)
        classification = classify_document(result)

        assert classification.kind is DocumentKind.EMPTY

    def test_characteristics_from_pdf_are_used(self) -> None:
        characteristics = DocumentCharacteristics(
            text_character_count=500,
            non_whitespace_character_count=400,
            line_count=10,
            word_count=50,
            page_count=5,
            image_count=0,
        )

        classification = classify("doc-1", characteristics)

        assert classification.characteristics.page_count == 5
        assert classification.characteristics.image_count == 0
