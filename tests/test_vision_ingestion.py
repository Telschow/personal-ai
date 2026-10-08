"""Tests for vision routing and page augmentation inside ingestion."""

import pymupdf
import pytest

from personal_ai.documents.classifier import DocumentKind
from personal_ai.documents.structured import StructuredExtraction
from personal_ai.documents.vision import VisionExtractionError
from personal_ai.ingestion import DocumentIngestor
from personal_ai.ollama_client import OllamaConnectionError
from personal_ai.sources.filesystem import FilesystemSourceAdapter
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    VisionStore,
    connect_database,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

VISION_MARKER_ALPHA = "Vision marker alpha for page one content. "
VISION_MARKER_BETA = "Vision marker beta for page two content. "

_MIN_TEXT = "Dedicated meeting notes about the quarterly project review. " * 20


def _make_text_pdf(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    lines = [text[i : i + 80] for i in range(0, len(text), 80)]
    page.insert_text((36, 36), "\n".join(lines))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_mixed_pdf() -> bytes:
    """A readable text page plus an image page: MIXED by the classifier."""
    doc = pymupdf.open()
    text_page = doc.new_page(width=595, height=842)
    lines = [_MIN_TEXT[i : i + 80] for i in range(0, len(_MIN_TEXT), 80)]
    text_page.insert_text((36, 36), "\n".join(lines))
    image_page = doc.new_page(width=200, height=200)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), 0)
    pixmap.clear_with(255)
    image_page.insert_image(
        pymupdf.Rect(10, 10, 190, 190), stream=pixmap.tobytes("png")
    )
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_image_heavy_pdf() -> bytes:
    """Two blank pages carrying only images: IMAGE_HEAVY."""
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page(width=200, height=200)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), 0)
        pixmap.clear_with(255)
        page.insert_image(pymupdf.Rect(10, 10, 190, 190), stream=pixmap.tobytes("png"))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _discover_record(filename: str, payload: bytes, tmp_path) -> object:
    (tmp_path / filename).write_bytes(payload)
    adapter = FilesystemSourceAdapter(tmp_path)
    return adapter.discover()[0]


class FakeStructuredExtractor:
    """Records calls; returns minimal structured extractions."""

    def __init__(self) -> None:
        self.calls: list = []

    def extract(self, extraction):
        self.calls.append(extraction)
        return StructuredExtraction(
            document_id=extraction.document_id,
            summary=f"fake summary {len(self.calls)}",
        )


class FakeVisionExtractor:
    """Records rendered images; returns scripted per-call outputs.

    ``raise_call_index`` is the 1-based call that raises a controlled
    :class:`VisionExtractionError` (simulating a page-level render
    failure); ``raise_error`` substitutes the error type when provider
    failure propagation is under test.
    """

    def __init__(
        self,
        *,
        model: str = "vision-model",
        prompt_version: str = "v2",
        outputs: list[str] | None = None,
        raise_call_index: int | None = None,
        raise_error: type[Exception] = VisionExtractionError,
    ) -> None:
        self.model = model
        self.prompt_version = prompt_version
        self.outputs = outputs or [VISION_MARKER_ALPHA * 30]
        self.raise_call_index = raise_call_index
        self.raise_error = raise_error
        self.calls: list[bytes] = []

    def extract(self, image: bytes) -> str:
        self.calls.append(image)
        if (
            self.raise_call_index is not None
            and len(self.calls) == self.raise_call_index
        ):
            raise self.raise_error("simulated page-level failure")
        index = min(len(self.calls) - 1, len(self.outputs) - 1)
        return self.outputs[index]


class VisionHarness:
    """Ingestion harness with a scriptable vision extractor."""

    def __init__(self, vision: FakeVisionExtractor | None = None) -> None:
        self.connection = connect_database(":memory:")
        self.document_store = DocumentStore(self.connection)
        self.extraction_store = ExtractionStore(self.connection)
        self.chunk_store = ChunkStore(self.connection)
        self.embedding_store = EmbeddingStore(self.connection)
        self.vision_store = VisionStore(self.connection)
        self.structured = FakeStructuredExtractor()
        self.vision = vision
        self.ingestor = self.build(vision)

    def build(self, vision: FakeVisionExtractor | None) -> DocumentIngestor:
        self.vision = vision
        self.ingestor = DocumentIngestor(
            self.document_store,
            self.extraction_store,
            self.structured,
            self.chunk_store,
            self.embedding_store,
            vision_extractor=vision,
            vision_store=self.vision_store,
        )
        return self.ingestor

    def close(self) -> None:
        self.connection.close()


def collect_pages(chunks) -> dict[int, str]:
    grouped: dict[int, str] = {}
    for chunk in chunks:
        parts = [grouped.get(chunk.page_number, ""), chunk.text]
        grouped[chunk.page_number] = " ".join(p for p in parts if p)
    return grouped


class TestImageHeavyVisionRouting:
    def test_image_heavy_document_is_vision_processed_and_searchable(
        self, tmp_path: object
    ) -> None:
        import pathlib

        record = _discover_record(
            "scan.pdf", _make_image_heavy_pdf(), pathlib.Path(tmp_path)
        )
        vision = FakeVisionExtractor(
            outputs=[VISION_MARKER_ALPHA * 30, VISION_MARKER_BETA * 30]
        )
        harness = VisionHarness(vision)
        try:
            result = harness.ingestor.ingest(record)

            assert result.kind is DocumentKind.IMAGE_HEAVY
            assert result.structured_extraction is not None
            assert (
                result.structured_extraction.metadata["extraction_source"] == "vision"
            )
            assert (
                result.structured_extraction.metadata["vision_model"] == "vision-model"
            )
            assert result.chunks
            assert len(vision.calls) == 2
            assert vision.calls[0][:8] == PNG_MAGIC

            assert len(harness.structured.calls) == 1
            assert "alpha" in harness.structured.calls[0].text
            assert "beta" in harness.structured.calls[0].text

            by_page = collect_pages(result.chunks)
            assert {1, 2} == set(by_page)
            assert "alpha" in by_page[1]
            assert "beta" in by_page[2]

            for page_number in (1, 2):
                stored = harness.vision_store.get(result.document_id, page_number)
                assert stored is not None
                assert stored.vision_model == "vision-model"
                assert stored.prompt_version == "v2"
        finally:
            harness.close()

    def test_rerun_uses_cache_without_any_vision_calls(self, tmp_path: object) -> None:
        import pathlib

        record = _discover_record(
            "scan.pdf", _make_image_heavy_pdf(), pathlib.Path(tmp_path)
        )
        harness = VisionHarness(FakeVisionExtractor())
        try:
            first = harness.ingestor.ingest(record)
            calls_after_first = len(harness.vision.calls)

            second = harness.ingestor.ingest(record)

            assert len(harness.vision.calls) == calls_after_first
            assert second.chunks == first.chunks
            assert second.document_id == first.document_id
            count = harness.connection.execute(
                "SELECT COUNT(*) FROM vision_pages WHERE document_id = ?",
                (first.document_id,),
            ).fetchone()[0]
            assert int(count) == 2
        finally:
            harness.close()

    def test_model_change_invalidates_cache_and_overwrites(
        self, tmp_path: object
    ) -> None:
        import pathlib

        record = _discover_record(
            "scan.pdf", _make_image_heavy_pdf(), pathlib.Path(tmp_path)
        )
        harness = VisionHarness(FakeVisionExtractor())
        try:
            harness.ingestor.ingest(record)

            new_vision = FakeVisionExtractor(model="new-vision-model")
            harness.build(new_vision)
            result = harness.ingestor.ingest(record)

            assert len(new_vision.calls) == 2
            stored = harness.vision_store.get(result.document_id, 1)
            assert stored is not None
            assert stored.vision_model == "new-vision-model"

            harness.ingestor.ingest(record)
            assert len(new_vision.calls) == 2
        finally:
            harness.close()

    def test_empty_model_output_is_not_cached_and_retried(
        self, tmp_path: object
    ) -> None:
        import pathlib

        record = _discover_record(
            "scan.pdf", _make_image_heavy_pdf(), pathlib.Path(tmp_path)
        )
        harness = VisionHarness(FakeVisionExtractor(outputs=["   \n  \t "]))
        try:
            first = harness.ingestor.ingest(record)

            assert first.chunks == ()
            assert len(harness.vision.calls) == 2
            assert harness.vision_store.get(first.document_id, 1) is None

            harness.ingestor.ingest(record)
            assert len(harness.vision.calls) == 4
        finally:
            harness.close()

    def test_page_level_failure_skips_only_that_page(self, tmp_path: object) -> None:
        import pathlib

        record = _discover_record(
            "scan.pdf", _make_image_heavy_pdf(), pathlib.Path(tmp_path)
        )
        vision = FakeVisionExtractor(raise_call_index=2)
        harness = VisionHarness(vision)
        try:
            result = harness.ingestor.ingest(record)

            assert result.document_id
            assert len(vision.calls) == 2
            assert harness.vision_store.get(result.document_id, 1) is not None
            assert harness.vision_store.get(result.document_id, 2) is None
            by_page = collect_pages(result.chunks)
            assert "alpha" in by_page.get(1, "")
            assert 2 not in by_page
        finally:
            harness.close()

    def test_provider_failure_propagates_with_persisted_state_intact(
        self, tmp_path: object
    ) -> None:
        import pathlib

        record = _discover_record(
            "scan.pdf", _make_image_heavy_pdf(), pathlib.Path(tmp_path)
        )
        harness = VisionHarness(
            FakeVisionExtractor(raise_call_index=2, raise_error=OllamaConnectionError)
        )
        try:
            with pytest.raises(OllamaConnectionError):
                harness.ingestor.ingest(record)

            assert (
                harness.document_store.get(
                    harness.document_store.list_documents()[0].id
                )
                is not None
            )
            cached = {
                row[1]
                for row in harness.connection.execute(
                    "SELECT document_id, page_number FROM vision_pages"
                ).fetchall()
            }
            assert 1 in cached
            assert 2 not in cached
        finally:
            harness.close()

    def test_image_heavy_without_vision_keeps_existing_behavior(
        self, tmp_path: object
    ) -> None:
        import pathlib

        record = _discover_record(
            "scan.pdf", _make_image_heavy_pdf(), pathlib.Path(tmp_path)
        )
        harness = VisionHarness()
        try:
            result = harness.ingestor.ingest(record)

            assert result.kind is DocumentKind.IMAGE_HEAVY
            assert result.chunks == ()
            assert result.structured_extraction is None
            assert harness.document_store.get(result.document_id) is not None
        finally:
            harness.close()


class TestOtherKindsNeverSeeVision:
    def test_text_heavy_never_reaches_vision(self, tmp_path: object) -> None:
        import pathlib

        record = _discover_record(
            "notes.pdf", _make_text_pdf(_MIN_TEXT), pathlib.Path(tmp_path)
        )
        harness = VisionHarness(FakeVisionExtractor())
        try:
            result = harness.ingestor.ingest(record)

            assert result.kind is DocumentKind.TEXT_HEAVY
            assert harness.vision.calls == []
            assert len(harness.structured.calls) == 1
            assert result.chunks
        finally:
            harness.close()

    def test_mixed_never_reaches_vision(self, tmp_path: object) -> None:
        import pathlib

        record = _discover_record(
            "mixed.pdf", _make_mixed_pdf(), pathlib.Path(tmp_path)
        )
        harness = VisionHarness(FakeVisionExtractor())
        try:
            result = harness.ingestor.ingest(record)

            assert result.kind is DocumentKind.MIXED
            assert harness.vision.calls == []
            assert harness.structured.calls == []
            assert result.chunks
        finally:
            harness.close()
