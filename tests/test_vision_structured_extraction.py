"""Vision-augmented structured extraction for image-heavy documents.

An ``IMAGE_HEAVY`` document whose vision-augmented text reaches the chunking
threshold is now routed through the shared structured extractor over that
augmented text, mirroring the text-heavy path: the extraction is normalized
deterministically, stamped with vision provenance (vision model + prompt
version), and persisted idempotently under the document id. All tests are
hermetic: vision and structured extractors are fakes, no model or network is
involved, and no production database is touched.
"""

import pathlib

import pytest

from personal_ai.documents.canonical import document_from_source_record
from personal_ai.documents.classifier import (
    TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS,
    DocumentKind,
    measure_text,
)
from personal_ai.documents.extractor import TextExtractionResult
from personal_ai.documents.structured import (
    MalformedStructuredOutputError,
    StructuredExtraction,
    normalize_structured_extraction,
)
from personal_ai.ingestion import (
    VISION_EXTRACTION_SCHEMA_VERSION,
    VISION_EXTRACTION_SOURCE,
    DocumentIngestor,
)
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
from tests.test_vision_ingestion import FakeVisionExtractor
from tests.test_vision_source_pipeline import make_image_record

_SUBSTANTIVE_TEXT = (
    "The vision board shows a beach house, a family portrait, "
    "and the word independent. "
) * 20

_SHORT_TEXT = "A meeting. " * 5


def _substantial(text: str) -> bool:
    return (
        measure_text(text).non_whitespace_character_count
        >= TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS
    )


def _image_heavy_pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page(width=200, height=200)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), 0)
        page.insert_image(pymupdf.Rect(2, 2, 8, 8), stream=pixmap.tobytes("png"))
    payload = doc.tobytes()
    doc.close()
    return payload


class ScriptedStructuredExtractor:
    """Scriptable structured extractor recording every extraction call.

    ``factory`` maps each extraction to the result to return; ``error``
    makes every call raise a controlled exception (propagation tests).
    """

    def __init__(
        self,
        *,
        factory=None,
        error: type[Exception] | None = None,
    ) -> None:
        self.factory = factory
        self.error = error
        self.calls: list[TextExtractionResult] = []

    def extract(self, extraction: TextExtractionResult) -> StructuredExtraction:
        self.calls.append(extraction)
        if self.error is not None:
            raise self.error("simulated structured extraction failure")
        if self.factory is not None:
            return self.factory(extraction)
        return StructuredExtraction(document_id=extraction.document_id)


class VisionStructuredHarness:
    """In-memory ingestion harness with a scripted structured extractor."""

    def __init__(
        self,
        structured: ScriptedStructuredExtractor,
        *,
        vision: FakeVisionExtractor | None = None,
    ) -> None:
        self.connection = connect_database(":memory:")
        self.document_store = DocumentStore(self.connection)
        self.extraction_store = ExtractionStore(self.connection)
        self.chunk_store = ChunkStore(self.connection)
        self.embedding_store = EmbeddingStore(self.connection)
        self.vision_store = VisionStore(self.connection)
        self.structured = structured
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


def _vision_harness(
    outputs: list[str] | None = None, **vision_kwargs
) -> tuple[VisionStructuredHarness, ScriptedStructuredExtractor]:
    vision = FakeVisionExtractor(
        outputs=outputs or [_SUBSTANTIVE_TEXT], **vision_kwargs
    )
    structured = ScriptedStructuredExtractor()
    return VisionStructuredHarness(structured, vision=vision), structured


def _provenance(extraction: StructuredExtraction) -> dict[str, object]:
    assert isinstance(extraction.metadata, dict)
    return extraction.metadata


class TestNormalizeStructuredExtraction:
    def test_summary_and_items_are_stripped(self) -> None:
        normalized = normalize_structured_extraction(
            StructuredExtraction(
                document_id="d1",
                summary="   board summary  ",
                goals=("estate planning ", " run "),
                topics=("  travel \n",),
            )
        )

        assert normalized.summary == "board summary"
        assert normalized.goals == ("estate planning", "run")
        assert normalized.topics == ("travel",)

    def test_empty_items_are_dropped(self) -> None:
        normalized = normalize_structured_extraction(
            StructuredExtraction(
                document_id="d1",
                people=("", "   ", "Alice", "Bob", "\t"),
            )
        )

        assert normalized.people == ("Alice", "Bob")

    def test_exact_duplicates_are_collapsed_keeping_first_occurrence(self) -> None:
        normalized = normalize_structured_extraction(
            StructuredExtraction(
                document_id="d1",
                topics=("BMW", "bmw", "BMW", "  BMW  ", "BMW"),
            )
        )

        assert normalized.topics == ("BMW", "bmw")

    def test_accents_and_case_are_preserved(self) -> None:
        normalized = normalize_structured_extraction(
            StructuredExtraction(
                document_id="d1",
                topics=("München", "MUNCHEN", "München "),
            )
        )

        assert normalized.topics == ("München", "MUNCHEN")

    def test_metadata_is_preserved_unchanged(self) -> None:
        original = StructuredExtraction(
            document_id="d1",
            summary=" a ",
            metadata={"provenance": "manual"},
        )

        normalized = normalize_structured_extraction(original)

        assert normalized.summary == "a"
        assert normalized.metadata == {"provenance": "manual"}

    def test_metadata_is_not_shared_or_mutated(self) -> None:
        original = StructuredExtraction(
            document_id="d1",
            metadata={"provenance": "manual"},
        )

        normalized = normalize_structured_extraction(original)

        assert normalized.metadata == original.metadata
        normalized.metadata["extra"] = True
        assert "extra" not in original.metadata

    def test_empty_extraction_stays_empty(self) -> None:
        normalized = normalize_structured_extraction(
            StructuredExtraction(document_id="d1")
        )

        assert normalized.summary == ""
        assert normalized.people == ()
        assert normalized.organizations == ()
        assert normalized.projects == ()
        assert normalized.goals == ()
        assert normalized.topics == ()
        assert normalized.metadata == {}


class TestVisionStructuredGate:
    def test_image_heavy_document_gets_vision_structured_extraction(self) -> None:
        harness, structured = _vision_harness()
        try:
            result = harness.ingestor.ingest(make_image_record())

            assert result.kind is DocumentKind.IMAGE_HEAVY
            assert result.structured_extraction is not None
            assert _provenance(result.structured_extraction)["extraction_source"] == (
                VISION_EXTRACTION_SOURCE
            )
            assert _provenance(result.structured_extraction)["vision_model"] == (
                "vision-model"
            )
            assert (
                _provenance(result.structured_extraction)["vision_prompt_version"]
                == "v2"
            )
            assert _provenance(result.structured_extraction)["schema_version"] == (
                VISION_EXTRACTION_SCHEMA_VERSION
            )
            assert len(structured.calls) == 1
            assert structured.calls[0].text == _SUBSTANTIVE_TEXT

            stored = harness.extraction_store.get(result.document_id)
            assert stored is not None
            assert stored is not result.structured_extraction
            assert stored.summary == result.structured_extraction.summary
            assert _provenance(stored) == _provenance(result.structured_extraction)
        finally:
            harness.close()

    def test_image_heavy_pdf_gets_vision_structured_extraction(
        self, tmp_path: pathlib.Path
    ) -> None:
        harness, structured = _vision_harness()
        try:
            (tmp_path / "scan.pdf").write_bytes(_image_heavy_pdf())
            record = FilesystemSourceAdapter(tmp_path).discover()[0]

            result = harness.ingestor.ingest(record)

            assert result.kind is DocumentKind.IMAGE_HEAVY
            assert result.structured_extraction is not None
            assert _provenance(result.structured_extraction)["extraction_source"] == (
                VISION_EXTRACTION_SOURCE
            )
            assert (
                structured.calls[0].text
                == _SUBSTANTIVE_TEXT + "\n\n" + _SUBSTANTIVE_TEXT
            )
            assert len(structured.calls) == 1
        finally:
            harness.close()

    def test_empty_vision_output_never_touches_structured_extractor(self) -> None:
        harness, structured = _vision_harness(outputs=["   \n \t "])
        try:
            result = harness.ingestor.ingest(make_image_record())

            assert result.kind is DocumentKind.IMAGE_HEAVY
            assert result.structured_extraction is None
            assert structured.calls == []
            assert harness.extraction_store.get(result.document_id) is None
        finally:
            harness.close()

    def test_below_threshold_vision_text_skips_structured_extraction(self) -> None:
        harness, structured = _vision_harness(outputs=[_SHORT_TEXT])
        try:
            result = harness.ingestor.ingest(make_image_record())

            assert not _substantial(_SHORT_TEXT)
            assert result.kind is DocumentKind.IMAGE_HEAVY
            assert result.structured_extraction is None
            assert structured.calls == []
            assert harness.extraction_store.get(result.document_id) is None
        finally:
            harness.close()

    def test_image_without_vision_extractor_skips_structured_extraction(self) -> None:
        structured = ScriptedStructuredExtractor()
        harness = VisionStructuredHarness(structured, vision=None)
        try:
            result = harness.ingestor.ingest(make_image_record())

            assert result.kind is DocumentKind.IMAGE_HEAVY
            assert result.structured_extraction is None
            assert structured.calls == []
            assert harness.extraction_store.get(result.document_id) is None
        finally:
            harness.close()

    def test_text_heavy_document_keeps_text_provenance(self) -> None:
        harness, structured = _vision_harness()
        try:
            record = make_image_record(
                source_key="notes/meeting.txt",
                content_hash="hash-meeting-1",
                metadata={},
                payload=b"Quarterly review meeting notes. " * 10,
            )

            result = harness.ingestor.ingest(record)

            assert result.kind is DocumentKind.TEXT_HEAVY
            assert harness.vision.calls == []
            assert result.structured_extraction is not None
            assert "extraction_source" not in _provenance(result.structured_extraction)
            assert len(structured.calls) == 1
        finally:
            harness.close()


class TestVisionStructuredPersistence:
    def test_rerun_reuses_and_never_re_calls(self) -> None:
        harness, structured = _vision_harness()
        try:
            first = harness.ingestor.ingest(make_image_record())
            assert len(harness.vision.calls) == 1
            assert len(structured.calls) == 1

            second = harness.ingestor.ingest(make_image_record())

            assert len(harness.vision.calls) == 1
            assert len(structured.calls) == 1
            assert second.document_id == first.document_id
            count = harness.connection.execute(
                "SELECT COUNT(*) FROM structured_extractions"
            ).fetchone()[0]
            assert int(count) == 1
        finally:
            harness.close()

    def test_vision_model_change_re_extracts_and_overwrites(self) -> None:
        harness, structured = _vision_harness()
        try:
            harness.ingestor.ingest(make_image_record())

            new_vision = FakeVisionExtractor(
                model="new-vision-model", outputs=[_SUBSTANTIVE_TEXT]
            )
            harness.build(new_vision)
            second = harness.ingestor.ingest(make_image_record())

            assert len(structured.calls) == 2
            stored = harness.extraction_store.get(second.document_id)
            assert stored is not None
            assert _provenance(stored)["vision_model"] == "new-vision-model"

            harness.ingestor.ingest(make_image_record())
            assert len(structured.calls) == 2
            count = harness.connection.execute(
                "SELECT COUNT(*) FROM structured_extractions"
            ).fetchone()[0]
            assert int(count) == 1
        finally:
            harness.close()

    def test_vision_prompt_version_change_re_extracts(self) -> None:
        harness, structured = _vision_harness(prompt_version="v1")
        try:
            harness.ingestor.ingest(make_image_record())

            new_prompt = FakeVisionExtractor(
                prompt_version="v2", outputs=[_SUBSTANTIVE_TEXT]
            )
            harness.build(new_prompt)
            result = harness.ingestor.ingest(make_image_record())

            assert len(structured.calls) == 2
            stored = harness.extraction_store.get(result.document_id)
            assert stored is not None
            assert _provenance(stored)["vision_prompt_version"] == "v2"
        finally:
            harness.close()

    def test_text_origin_extraction_is_replaced_not_reused(self) -> None:
        harness, structured = _vision_harness()
        try:
            record = make_image_record()
            document = document_from_source_record(record)
            harness.document_store.add(document)
            harness.extraction_store.save(
                StructuredExtraction(
                    document_id=document.id,
                    summary="legacy text-path extraction",
                )
            )

            result = harness.ingestor.ingest(record)

            assert len(structured.calls) == 1
            stored = harness.extraction_store.get(result.document_id)
            assert stored is not None
            assert stored.summary != "legacy text-path extraction"
            assert _provenance(stored)["extraction_source"] == VISION_EXTRACTION_SOURCE
        finally:
            harness.close()


class TestVisionStructuredNormalizationThroughPipeline:
    def test_extractor_output_is_normalized_before_persistence(self) -> None:
        vision = FakeVisionExtractor(outputs=[_SUBSTANTIVE_TEXT])
        structured = ScriptedStructuredExtractor(
            factory=lambda extraction: StructuredExtraction(
                document_id=extraction.document_id,
                summary="  board  ",
                goals=("  run  ", "", "run"),
                topics=("  beach house  ", "beach house", "BMW", "bmw"),
            )
        )
        harness = VisionStructuredHarness(structured, vision=vision)
        try:
            result = harness.ingestor.ingest(make_image_record())

            assert result.structured_extraction is not None
            assert result.structured_extraction.summary == "board"
            assert result.structured_extraction.goals == ("run",)
            assert result.structured_extraction.topics == (
                "beach house",
                "BMW",
                "bmw",
            )

            stored = harness.extraction_store.get(result.document_id)
            assert stored is not None
            assert stored.summary == "board"
            assert stored.topics == ("beach house", "BMW", "bmw")
        finally:
            harness.close()

    def test_vision_structured_extraction_is_searchable(self) -> None:
        vision = FakeVisionExtractor(
            outputs=["The beach house and independent goals are visible. " * 20]
        )
        structured = ScriptedStructuredExtractor(
            factory=lambda extraction: StructuredExtraction(
                document_id=extraction.document_id,
                summary="A vision board for the beach house.",
                topics=("beach house", "independent"),
            )
        )
        harness = VisionStructuredHarness(structured, vision=vision)
        try:
            result = harness.ingestor.ingest(make_image_record())

            hits = harness.extraction_store.search("beach house")

            assert [h.document_id for h in hits] == [result.document_id]
            assert "topics" in hits[0].matched_fields
        finally:
            harness.close()


class TestVisionStructuredFailures:
    def test_validation_failure_propagates_and_persists_nothing(self) -> None:
        vision = FakeVisionExtractor(outputs=[_SUBSTANTIVE_TEXT])
        structured = ScriptedStructuredExtractor(error=MalformedStructuredOutputError)
        harness = VisionStructuredHarness(structured, vision=vision)
        try:
            with pytest.raises(MalformedStructuredOutputError):
                harness.ingestor.ingest(make_image_record())

            count = harness.connection.execute(
                "SELECT COUNT(*) FROM structured_extractions"
            ).fetchone()[0]
            assert int(count) == 0
            assert len(harness.document_store.list_documents()) == 1
        finally:
            harness.close()

    def test_provider_failure_propagates_and_plain_retry_recovers(self) -> None:
        vision = FakeVisionExtractor(outputs=[_SUBSTANTIVE_TEXT])
        structured = ScriptedStructuredExtractor(error=OllamaConnectionError)
        harness = VisionStructuredHarness(structured, vision=vision)
        try:
            with pytest.raises(OllamaConnectionError):
                harness.ingestor.ingest(make_image_record())
            assert (
                harness.extraction_store.get(
                    harness.document_store.list_documents()[0].id
                )
                is None
            )

            structured.error = None
            result = harness.ingestor.ingest(make_image_record())

            stored = harness.extraction_store.get(result.document_id)
            assert stored is not None
            assert _provenance(stored)["extraction_source"] == VISION_EXTRACTION_SOURCE
            assert len(structured.calls) == 2
        finally:
            harness.close()
