"""Hermetic tests for the v2 vision transcription contract.

Phase 53B: the v1 vision prompt under-described dense visual sources
(vision boards), leaving them below the substantive-text gate with nothing
searchable or extractable. v2 adds an exhaustive, fidelity-focused contract.

These tests assert meaningful contractual properties — never the exact
prompt byte-for-byte — plus cache-identity, provenance, and image-isolation
behavior across the v1/v2 boundary.
"""

import pathlib

from personal_ai.documents.classifier import (
    TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS,
    DocumentKind,
    measure_text,
)
from personal_ai.documents.vision import (
    VISION_PROMPT,
    VISION_PROMPT_VERSION,
    VISION_SYSTEM_PROMPT,
)
from personal_ai.ingestion import VISION_EXTRACTION_SCHEMA_VERSION, DocumentIngestor
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
from tests.test_vision_structured_extraction import (
    _SUBSTANTIVE_TEXT,
    ScriptedStructuredExtractor,
)

# Contract vocabulary groups. Each group maps a required behavior to a few
# representative tokens; a test passes if ANY of the tokens appears, so the
# tests stay robust to wording changes while still pinning the contract.
_EXHAUSTIVE_TEXT = (
    "heading",
    "label",
    "number",
    "date",
    "name",
    "goal",
    "category",
    "currency",
    "percentage",
    "unit",
)
_PRESERVE_EXACT = (
    "exactly as written",
    "exactly",
    "verbatim",
    "do not summariz",
    "do not shorten",
)
_PRESERVE_STRUCTURE = (
    "structure",
    "table",
    "list item",
    "rows",
    "grouping",
    "relationship",
    "grouped",
    "caption",
    "annotation",
)
_FIDELITY = (
    "illegible",
    "unclear",
    "unreadable",
    "do not guess",
    "do not invent",
    "instead of guessing",
    "instead of inventing",
)
_NO_VISUAL_PROSE = (
    "aesthetics",
    "beauty",
    "beautiful",
    "warm",
    "style",
)


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


class TestV2PromptIdentity:
    def test_prompt_version_is_v2(self) -> None:
        assert VISION_PROMPT_VERSION == "v2"

    def test_config_default_matches_bump(self) -> None:
        from personal_ai.config import DEFAULT_VISION_PROMPT_VERSION

        assert DEFAULT_VISION_PROMPT_VERSION == VISION_PROMPT_VERSION == "v2"


class TestV2PromptContract:
    def test_user_prompt_is_exhaustive(self) -> None:
        assert _has_any(VISION_PROMPT, ("exhaustive", "exhaustively", "as much"))
        assert _has_any(VISION_PROMPT, _EXHAUSTIVE_TEXT)
        assert "complete over concise" in VISION_PROMPT.casefold() or (
            "be complete" in VISION_PROMPT.casefold()
        )

    def test_user_prompt_preserves_exact_text(self) -> None:
        assert _has_any(VISION_PROMPT, _PRESERVE_EXACT)

    def test_user_prompt_preserves_structure(self) -> None:
        assert _has_any(VISION_PROMPT, _PRESERVE_STRUCTURE)

    def test_user_prompt_handles_uncertainty_honestly(self) -> None:
        assert _has_any(VISION_PROMPT, _FIDELITY)

    def test_user_prompt_discourages_visual_prose(self) -> None:
        assert _has_any(VISION_PROMPT, _NO_VISUAL_PROSE)

    def test_system_prompt_is_exhaustive_transcriber(self) -> None:
        assert _has_any(VISION_SYSTEM_PROMPT, ("exhaustive", "faithful"))
        assert "exactly as written" in VISION_SYSTEM_PROMPT.casefold()

    def test_system_prompt_distinguishes_observed_from_inferred(self) -> None:
        lowered = VISION_SYSTEM_PROMPT.casefold()
        assert "observe" in lowered and "infer" in lowered

    def test_system_prompt_forbids_invention(self) -> None:
        assert _has_any(VISION_SYSTEM_PROMPT, ("never invent", "do not invent"))

    def test_system_prompt_discourages_visual_prose(self) -> None:
        assert _has_any(VISION_SYSTEM_PROMPT, _NO_VISUAL_PROSE)

    def test_contract_v2_differs_from_legacy_v1(self) -> None:
        # v1 was the terse prompt; v2 must not be that prompt.
        assert (
            VISION_PROMPT.casefold()
            != "extract the visible text and content of this image."
        )


class TestV2CacheIdentityAndProvenance:
    def make_harness(self, vision: FakeVisionExtractor):
        connection = connect_database(":memory:")
        ingestor = DocumentIngestor(
            DocumentStore(connection),
            ExtractionStore(connection),
            ScriptedStructuredExtractor(),
            ChunkStore(connection),
            EmbeddingStore(connection),
            vision_extractor=vision,
            vision_store=VisionStore(connection),
        )
        return connection, ingestor

    def test_prompt_version_change_invalidates_vision_cache(
        self, tmp_path: pathlib.Path
    ) -> None:
        record = make_image_record()
        connection, ingestor = self.make_harness(
            FakeVisionExtractor(prompt_version="v1", outputs=[_SUBSTANTIVE_TEXT])
        )
        try:
            first = ingestor.ingest(record)
            stored = connection.execute(
                "SELECT vision_model, prompt_version FROM vision_pages"
            ).fetchone()
            assert stored[1] == "v1"

            v2_vision = FakeVisionExtractor(
                prompt_version="v2", outputs=[_SUBSTANTIVE_TEXT]
            )
            v2_ingestor = DocumentIngestor(
                DocumentStore(connection),
                ExtractionStore(connection),
                ScriptedStructuredExtractor(),
                ChunkStore(connection),
                EmbeddingStore(connection),
                vision_extractor=v2_vision,
                vision_store=VisionStore(connection),
            )
            result = v2_ingestor.ingest(record)

            assert len(v2_vision.calls) == 1
            stored = connection.execute(
                "SELECT prompt_version FROM vision_pages"
            ).fetchone()
            assert stored[0] == "v2"
            rows = connection.execute(
                "SELECT COUNT(*) FROM vision_pages WHERE prompt_version='v1'"
            ).fetchone()[0]
            assert int(rows) == 0  # v1 row replaced in place, no duplicates
            assert result.document_id == first.document_id
        finally:
            connection.close()

    def test_v2_provenance_is_recorded_on_extraction(
        self, tmp_path: pathlib.Path
    ) -> None:
        connection = connect_database(":memory:")
        structured = ScriptedStructuredExtractor()
        ingestor = DocumentIngestor(
            DocumentStore(connection),
            ExtractionStore(connection),
            structured,
            ChunkStore(connection),
            EmbeddingStore(connection),
            vision_extractor=FakeVisionExtractor(
                prompt_version="v2", outputs=[_SUBSTANTIVE_TEXT]
            ),
            vision_store=VisionStore(connection),
        )
        try:
            result = ingestor.ingest(make_image_record())

            assert result.kind is DocumentKind.IMAGE_HEAVY
            assert structured.calls  # structured extractor invoked on vision text
            assert structured.calls[0].text == _SUBSTANTIVE_TEXT  # text only
            assert (
                result.structured_extraction.metadata["extraction_source"] == "vision"
            )
            assert (
                result.structured_extraction.metadata["vision_prompt_version"] == "v2"
            )
            assert (
                result.structured_extraction.metadata["schema_version"]
                == VISION_EXTRACTION_SCHEMA_VERSION
            )
        finally:
            connection.close()

    def test_structured_extractor_never_receives_an_image(self) -> None:
        connection = connect_database(":memory:")
        structured = ScriptedStructuredExtractor()
        vision = FakeVisionExtractor(outputs=[_SUBSTANTIVE_TEXT])
        ingestor = DocumentIngestor(
            DocumentStore(connection),
            ExtractionStore(connection),
            structured,
            ChunkStore(connection),
            EmbeddingStore(connection),
            vision_extractor=vision,
            vision_store=VisionStore(connection),
        )
        try:
            ingestor.ingest(make_image_record())

            # Exactly one vision call (the image), and the structured
            # extractor received a TextExtractionResult carrying text only.
            assert len(vision.calls) == 1
            assert len(structured.calls) == 1
            assert structured.calls[0].text == _SUBSTANTIVE_TEXT
            payload = structured.calls[0]
            assert not hasattr(payload, "images")
        finally:
            connection.close()

    def test_v2_text_passes_substantive_gate_via_measure(self) -> None:
        # The defect in Phase 53 was v1 text ~11-23 chars vs the 200-char
        # gate. The contract target is that v2 text is measured by the same
        # gate machinery that decides chunking.
        assert (
            measure_text(_SUBSTANTIVE_TEXT).non_whitespace_character_count
            >= TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS
        )
