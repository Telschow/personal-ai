"""End-to-end integration tests for PDF ingestion through the full pipeline."""

import pymupdf

from personal_ai.documents.classifier import DocumentKind
from personal_ai.ingestion import DocumentIngestor
from personal_ai.orchestration import discover_source, ingest_source
from personal_ai.sources.filesystem import FilesystemSourceAdapter
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)

PDF_HEAVY_TEXT = "Meeting notes about the project. " * 30

# Threshold: classifier needs ≥200 non-whitespace characters for TEXT_HEAVY
_MIN_TEXT = "Dedicated meeting notes about the quarterly project review. " * 20


def _make_pdf_bytes(pages: list[str]) -> bytes:
    """Create a minimal in-memory PDF with the given page texts."""
    doc = pymupdf.open()
    for page_text in pages:
        page = doc.new_page(width=595, height=max(842, 40 + len(page_text) // 2))
        lines = [page_text[i : i + 80] for i in range(0, len(page_text), 80)]
        page.insert_text((36, 36), "\n".join(lines))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


class FakeStructuredExtractor:
    """Records calls; returns minimal structured extractions."""

    def __init__(self) -> None:
        self.calls: list = []

    def extract(self, extraction):
        from personal_ai.documents.structured import StructuredExtraction

        self.calls.append(extraction)
        return StructuredExtraction(
            document_id=extraction.document_id,
            summary=f"fake summary for {extraction.source_key}",
        )


class IngestionHarness:
    """Shared test harness for ingestion tests."""

    def __init__(self) -> None:
        self.connection = connect_database(":memory:")
        self.document_store = DocumentStore(self.connection)
        self.extraction_store = ExtractionStore(self.connection)
        self.chunk_store = ChunkStore(self.connection)
        self.embedding_store = EmbeddingStore(self.connection)
        self.extractor = FakeStructuredExtractor()
        self.ingestor = DocumentIngestor(
            self.document_store,
            self.extraction_store,
            self.extractor,
            self.chunk_store,
            self.embedding_store,
        )

    def close(self) -> None:
        self.connection.close()


# ---------------------------------------------------------------------------
# A. Two-page text PDF through full pipeline
# ---------------------------------------------------------------------------


class TestTwoPagePDFEndToEnd:
    def test_document_persisted_with_correct_metadata(self, tmp_path: object) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf = _make_pdf_bytes([_MIN_TEXT, _MIN_TEXT])
        (workspace / "two_page.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            records = adapter.discover()
            assert len(records) == 1

            result = harness.ingestor.ingest(records[0])

            assert result.kind is DocumentKind.TEXT_HEAVY
            doc = harness.document_store.get(result.document_id)
            assert doc is not None
            assert doc.source_type == "file"
            assert doc.source == "two_page.pdf"
            assert doc.mime_type is None  # top-level field not populated
            assert doc.metadata.get("mime_type") == "application/pdf"
        finally:
            harness.close()

    def test_chunks_persisted_with_page_numbers(self, tmp_path: object) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf = _make_pdf_bytes([_MIN_TEXT, _MIN_TEXT])
        (workspace / "two_page.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            records = adapter.discover()
            result = harness.ingestor.ingest(records[0])

            stored_chunks = harness.chunk_store.list_for_document(result.document_id)
            assert len(stored_chunks) > 0

            page_numbers = {c.page_number for c in stored_chunks}
            assert 1 in page_numbers
            assert 2 in page_numbers

            for chunk in stored_chunks:
                assert chunk.page_number is not None
                assert chunk.page_number in (1, 2)
        finally:
            harness.close()

    def test_page_one_chunks_contain_page_one_text(self, tmp_path: object) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        # Unique text per page (unique words repeated enough to pass 200-char threshold)
        page1_text = "Unique text alpha on page one. " * 15
        page2_text = "Unique text beta on page two. " * 15
        pdf = _make_pdf_bytes([page1_text, page2_text])
        (workspace / "test.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            records = adapter.discover()
            result = harness.ingestor.ingest(records[0])

            chunks = harness.chunk_store.list_for_document(result.document_id)
            page1_chunks = [c for c in chunks if c.page_number == 1]
            page2_chunks = [c for c in chunks if c.page_number == 2]

            assert len(page1_chunks) >= 1
            assert len(page2_chunks) >= 1

            page1_text = " ".join(c.text for c in page1_chunks)
            page2_text = " ".join(c.text for c in page2_chunks)

            assert "alpha" in page1_text
            assert "beta" in page2_text
            assert "alpha" not in page2_text
            assert "beta" not in page1_text
        finally:
            harness.close()

    def test_page_number_in_chunk_metadata(self, tmp_path: object) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf = _make_pdf_bytes(["First page", "Second page"])
        (workspace / "test.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            records = adapter.discover()
            result = harness.ingestor.ingest(records[0])

            chunks = harness.chunk_store.list_for_document(result.document_id)
            for chunk in chunks:
                assert "page_number" in chunk.metadata
                assert chunk.metadata["page_number"] == chunk.page_number
        finally:
            harness.close()


# ---------------------------------------------------------------------------
# B. Multi-page PDF with page-aware chunking
# ---------------------------------------------------------------------------


class TestPageAwareChunkingEndToEnd:
    def test_large_page_produces_multiple_chunks_all_same_page(
        self, tmp_path: object
    ) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        long_text = _MIN_TEXT * 3
        pdf = _make_pdf_bytes([long_text])
        (workspace / "long.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            records = adapter.discover()
            result = harness.ingestor.ingest(records[0])

            chunks = harness.chunk_store.list_for_document(result.document_id)
            assert len(chunks) > 1
            assert all(c.page_number == 1 for c in chunks)
        finally:
            harness.close()

    def test_three_page_document_preserves_all_page_numbers(
        self, tmp_path: object
    ) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pages = [
            "First page unique content alpha " * 15,
            "Second page unique content beta " * 15,
            "Third page unique content gamma " * 15,
        ]
        pdf = _make_pdf_bytes(pages)
        (workspace / "three.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            records = adapter.discover()
            result = harness.ingestor.ingest(records[0])

            chunks = harness.chunk_store.list_for_document(result.document_id)
            page_numbers = sorted({c.page_number for c in chunks})
            assert page_numbers == [1, 2, 3]

            for page_num in [1, 2, 3]:
                page_chunks = [c for c in chunks if c.page_number == page_num]
                assert len(page_chunks) > 0
        finally:
            harness.close()


# ---------------------------------------------------------------------------
# C. Non-PDF regression
# ---------------------------------------------------------------------------


class TestNonPDFRegression:
    def test_text_file_ingestion_still_works(self, tmp_path: object) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        text = "Meeting notes about the project with substantial content. " * 10
        (workspace / "notes.txt").write_bytes(text.encode())

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            records = adapter.discover()
            result = harness.ingestor.ingest(records[0])

            assert result.kind is DocumentKind.TEXT_HEAVY
            doc = harness.document_store.get(result.document_id)
            assert doc is not None
            assert doc.metadata.get("mime_type") == "text/plain"

            chunks = harness.chunk_store.list_for_document(result.document_id)
            assert len(chunks) > 0
            assert all(c.page_number is None for c in chunks)
        finally:
            harness.close()

    def test_mixed_pdf_skips_chunking(self, tmp_path: object) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        # Image-only PDF: should be IMAGE_HEAVY, no chunks
        doc = pymupdf.open()
        page = doc.new_page(width=200, height=200)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), 0)
        pixmap.clear_with(255)
        img_bytes = pixmap.tobytes("png")
        page.insert_image(pymupdf.Rect(10, 10, 190, 190), stream=img_bytes)
        pdf_bytes = doc.tobytes()
        doc.close()

        (workspace / "image_only.pdf").write_bytes(pdf_bytes)

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            records = adapter.discover()
            result = harness.ingestor.ingest(records[0])

            assert result.kind is DocumentKind.IMAGE_HEAVY
            assert result.chunks == ()
            assert result.structured_extraction is None
        finally:
            harness.close()


# ---------------------------------------------------------------------------
# D. Full pipeline through orchestration layer
# ---------------------------------------------------------------------------


class TestOrchestrationEndToEnd:
    def test_ingest_source_with_pdfs(self, tmp_path: object) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf1 = _make_pdf_bytes([_MIN_TEXT])
        pdf2 = _make_pdf_bytes([_MIN_TEXT, _MIN_TEXT])
        (workspace / "doc_alpha.pdf").write_bytes(pdf1)
        (workspace / "doc_beta.pdf").write_bytes(pdf2)

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            summary = discover_source(adapter)
            assert len(summary.source_keys) == 2

            result = ingest_source(adapter, harness.ingestor)
            assert result.documents == 2
            assert result.kind_counts.get("text_heavy", 0) == 2
            assert result.chunk_count > 0
        finally:
            harness.close()

    def test_idempotency_of_ingestion(self, tmp_path: object) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf = _make_pdf_bytes([_MIN_TEXT])
        (workspace / "test.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            records = adapter.discover()
            first = harness.ingestor.ingest(records[0])
            second = harness.ingestor.ingest(records[0])

            assert first.document_id == second.document_id
            assert first.kind == second.kind
            assert first.chunks == second.chunks
            assert len(harness.document_store.list_documents()) == 1
        finally:
            harness.close()


# ---------------------------------------------------------------------------
# E. Document metadata persistence
# ---------------------------------------------------------------------------


class TestDocumentMetadataPersistence:
    def test_pdf_metadata_survives_in_document(self, tmp_path: object) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf = _make_pdf_bytes([_MIN_TEXT, _MIN_TEXT])
        (workspace / "test.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            records = adapter.discover()
            result = harness.ingestor.ingest(records[0])

            doc = harness.document_store.get(result.document_id)
            assert doc is not None
            assert doc.metadata.get("mime_type") == "application/pdf"
            assert doc.metadata.get("extension") == ".pdf"
            assert "size_bytes" in doc.metadata
        finally:
            harness.close()

    def test_chunk_provenance_survives_in_storage(self, tmp_path: object) -> None:
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf = _make_pdf_bytes([_MIN_TEXT])
        (workspace / "test.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)
        harness = IngestionHarness()
        try:
            records = adapter.discover()
            result = harness.ingestor.ingest(records[0])

            chunks = harness.chunk_store.list_for_document(result.document_id)
            for chunk in chunks:
                assert "source_type" in chunk.metadata
                assert chunk.metadata["source_type"] == "file"
                assert "source_key" in chunk.metadata
                assert "content_hash" in chunk.metadata
                assert "chunk_index" in chunk.metadata
        finally:
            harness.close()
