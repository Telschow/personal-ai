"""Vision-first source pipeline: standalone raster images become documents.

A standalone PNG/JPG/JPEG has no extractable text of its own, so text
extraction yields an empty-text result carrying image evidence (the
classifier then labels it ``IMAGE_HEAVY``), and the ingestion vision stage
runs the vision extractor over the raw image bytes as a single derived
page so the image can become a chunked, searchable document. All tests are
hermetic: vision and structured extractors are fakes, no model or network
is involved, and no production database is touched.
"""

import pymupdf
import pytest

from personal_ai.documents import (
    TextExtractionError,
    compute_document_id,
    extract_text,
)
from personal_ai.documents.classifier import DocumentKind, classify_document
from personal_ai.documents.extractor import is_image_record
from personal_ai.ollama_client import OllamaConnectionError
from personal_ai.sources.models import SourceRecord
from tests.test_vision_ingestion import FakeVisionExtractor, VisionHarness

_VISION_BOARD_TEXT = (
    "The vision board shows a beach house, a family portrait, "
    "and the word independent. "
)


def make_image_record(**overrides: object) -> SourceRecord:
    values: dict[str, object] = {
        "source_type": "file",
        "source_key": "photos/vision-board.png",
        "content_hash": "hash-img-1",
        "created_at": "2026-08-22T10:00:00+00:00",
        "modified_at": "2026-08-22T10:00:00+00:00",
        "metadata": {"mime_type": "image/png"},
        "payload": make_image_bytes(),
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


def make_image_bytes() -> bytes:
    """A real (tiny, blank) PNG raster image."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), 0)
    pixmap.clear_with(255)
    image = pixmap.tobytes("png")
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    return image


def make_harness() -> VisionHarness:
    return VisionHarness(FakeVisionExtractor(outputs=[_VISION_BOARD_TEXT * 30]))


class TestRasterImageDetection:
    def test_png_is_detected(self) -> None:
        assert is_image_record(make_image_record(metadata={"mime_type": "image/png"}))

    def test_jpeg_and_jpg_mime_are_detected(self) -> None:
        assert is_image_record(
            make_image_record(source_key="a.jpeg", metadata={"mime_type": "image/jpeg"})
        )
        assert is_image_record(
            make_image_record(source_key="a.jpg", metadata={"mime_type": "image/jpeg"})
        )

    def test_extension_fallback_without_mime_type(self) -> None:
        for filename in ("pic.png", "pic.jpg", "pic.jpeg"):
            assert is_image_record(make_image_record(source_key=filename, metadata={}))

    def test_text_pdf_and_unknown_files_are_not_images(self) -> None:
        assert not is_image_record(make_image_record(source_key="a.txt", metadata={}))
        assert not is_image_record(make_image_record(source_key="a.md", metadata={}))
        assert not is_image_record(make_image_record(source_key="a.pdf", metadata={}))
        assert not is_image_record(make_image_record(source_key="a.bin", metadata={}))


class TestRasterImageTextExtraction:
    def test_extraction_returns_empty_text_with_image_evidence(self) -> None:
        record = make_image_record()

        result = extract_text(record)

        assert result.text == ""
        assert result.metadata["image_count"] == 1
        assert result.pages is None
        assert result.document_id == compute_document_id(
            record.source_type, record.source_key, record.content_hash
        )

    def test_extraction_preserves_original_metadata(self) -> None:
        result = extract_text(
            make_image_record(metadata={"mime_type": "image/png", "size_bytes": 42})
        )

        assert result.metadata["mime_type"] == "image/png"
        assert result.metadata["size_bytes"] == 42
        assert result.metadata["image_count"] == 1

    def test_raster_image_payload_never_raises_utf8_decode_error(self) -> None:
        result = extract_text(make_image_record())

        assert result.text == ""
        assert result.metadata["image_count"] == 1


class TestRasterImageClassification:
    def test_empty_text_image_classifies_image_heavy(self) -> None:
        result = extract_text(make_image_record())
        classification = classify_document(result)

        assert classification.kind is DocumentKind.IMAGE_HEAVY

    def test_jpg_record_classifies_image_heavy(self) -> None:
        result = extract_text(
            make_image_record(
                source_key="pics/scan.jpg",
                metadata={"mime_type": "image/jpeg"},
            )
        )
        classification = classify_document(result)

        assert classification.kind is DocumentKind.IMAGE_HEAVY


class TestRasterImageIngestion:
    def test_image_is_vision_processed_and_searchable(self) -> None:
        record = make_image_record()
        harness = make_harness()
        try:
            result = harness.ingestor.ingest(record)

            assert result.kind is DocumentKind.IMAGE_HEAVY
            assert result.structured_extraction is None
            assert harness.structured.calls == []
            assert harness.document_store.get(result.document_id) is not None

            assert len(harness.vision.calls) == 1
            assert harness.vision.calls[0][:8] == b"\x89PNG\r\n\x1a\n"

            assert result.chunks
            assert all(c.page_number == 1 for c in result.chunks)
            assert "beach house" in " ".join(c.text for c in result.chunks)

            stored = harness.vision_store.get(result.document_id, 1)
            assert stored is not None
            assert stored.vision_model == harness.vision.model
            assert stored.prompt_version == harness.vision.prompt_version
        finally:
            harness.close()

    def test_rerun_reuses_cache_without_any_vision_calls(self) -> None:
        record = make_image_record()
        harness = make_harness()
        try:
            first = harness.ingestor.ingest(record)
            calls_after_first = len(harness.vision.calls)

            second = harness.ingestor.ingest(record)

            assert len(harness.vision.calls) == calls_after_first
            assert second.document_id == first.document_id
            assert second.chunks == first.chunks
        finally:
            harness.close()

    def test_model_change_invalidates_cache_and_overwrites(self) -> None:
        record = make_image_record()
        harness = make_harness()
        try:
            harness.ingestor.ingest(record)

            new_vision = FakeVisionExtractor(
                model="new-vision-model", outputs=[_VISION_BOARD_TEXT * 30]
            )
            harness.build(new_vision)
            result = harness.ingestor.ingest(record)

            assert len(new_vision.calls) == 1
            stored = harness.vision_store.get(result.document_id, 1)
            assert stored is not None
            assert stored.vision_model == "new-vision-model"

            harness.ingestor.ingest(record)
            assert len(new_vision.calls) == 1
        finally:
            harness.close()

    def test_empty_vision_output_is_not_cached_and_unchunked(self) -> None:
        record = make_image_record()
        harness = VisionHarness(FakeVisionExtractor(outputs=["   \n  \t "]))
        try:
            first = harness.ingestor.ingest(record)

            assert first.kind is DocumentKind.IMAGE_HEAVY
            assert first.chunks == ()
            assert len(harness.vision.calls) == 1
            assert harness.vision_store.get(first.document_id, 1) is None

            harness.ingestor.ingest(record)
            assert len(harness.vision.calls) == 2
        finally:
            harness.close()

    def test_image_without_vision_configured_stored_unchunked(self) -> None:
        record = make_image_record()
        harness = VisionHarness()
        try:
            result = harness.ingestor.ingest(record)

            assert result.kind is DocumentKind.IMAGE_HEAVY
            assert result.chunks == ()
            assert result.structured_extraction is None
            assert harness.document_store.get(result.document_id) is not None
        finally:
            harness.close()

    def test_vision_provider_error_propagates_with_document_persisted(self) -> None:
        record = make_image_record()
        harness = VisionHarness(
            FakeVisionExtractor(raise_call_index=1, raise_error=OllamaConnectionError)
        )
        try:
            with pytest.raises(OllamaConnectionError):
                harness.ingestor.ingest(record)

            stored_documents = harness.document_store.list_documents()
            assert len(stored_documents) == 1
            assert harness.vision_store.get(stored_documents[0].id, 1) is None
        finally:
            harness.close()


class TestTextFilesUnchanged:
    def test_text_files_still_extract_via_utf8(self) -> None:
        record = make_image_record(
            source_key="notes/todo.txt",
            content_hash="hash-txt-1",
            metadata={},
            payload=b"Buy groceries\nWalk the dog\n",
        )

        assert not is_image_record(record)
        assert extract_text(record).text == "Buy groceries\nWalk the dog\n"

    def test_binary_text_file_still_raises_not_image(self) -> None:
        record = make_image_record(
            source_key="notes/corrupt.txt",
            content_hash="hash-txt-2",
            metadata={},
            payload=b"\xff\xfe invalid",
        )

        with pytest.raises(TextExtractionError):
            extract_text(record)
