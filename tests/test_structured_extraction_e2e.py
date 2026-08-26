"""Integration test for structured extraction through the real Ollama extractor.

Mocks ONLY the HTTP boundary (httpx.MockTransport).  Uses real SQLite stores,
real extraction protocol, real parsing.  No Ollama server required.
"""

import json

import httpx
import pymupdf
import pytest

from personal_ai.ingestion import DocumentIngestor
from personal_ai.ollama_client import OllamaClient
from personal_ai.ollama_structured import (
    EXTRACTION_SCHEMA,
    STRUCTURED_EXTRACTION_SYSTEM_PROMPT,
    OllamaStructuredExtractor,
)
from personal_ai.sources.filesystem import FilesystemSourceAdapter
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)

FAKE_RESPONSE = {
    "summary": "Daniel works at Example GmbH on Project Apollo.",
    "people": ["Daniel Telschow"],
    "organizations": ["Example GmbH"],
    "projects": ["Project Apollo"],
    "goals": ["Learn local AI deployment"],
    "topics": ["career", "AI", "personal development"],
}

FAKE_RESPONSE_SECOND = {
    "summary": "Revised summary for the same document.",
    "people": ["Daniel Telschow", "Alice"],
    "organizations": ["Example GmbH", "BCG"],
    "projects": ["Project Apollo"],
    "goals": ["Learn local AI deployment", "Financial independence"],
    "topics": ["career", "AI"],
}


def _make_pdf_bytes(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    lines = [text[i : i + 80] for i in range(0, len(text), 80)]
    page.insert_text((36, 36), "\n".join(lines))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


SYNTHETIC_TEXT = (
    "Daniel Telschow works at Example GmbH. "
    "He is leading Project Apollo which started in January 2026. "
    "His goal is to learn local AI deployment and build a personal knowledge system. "
    "The project involves career development, AI research, and personal growth. "
    "Daniel previously worked at BCG as a consultant. "
    "He is based in Munich and focuses on technology and finance."
) * 10


def chat_payload(content: str) -> dict:
    return {
        "model": "qwen3.5:9b",
        "created_at": "2026-08-26T12:00:00Z",
        "message": {"role": "assistant", "content": content},
        "done": True,
    }


def make_extractor(
    handler,
) -> tuple[OllamaStructuredExtractor, list[httpx.Request], OllamaClient]:
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = OllamaClient(
        model="qwen3.5:9b",
        transport=httpx.MockTransport(recording_handler),
    )
    return OllamaStructuredExtractor(client), requests, client


def make_ingestor(
    extractor,
):
    connection = connect_database(":memory:")
    document_store = DocumentStore(connection)
    extraction_store = ExtractionStore(connection)
    chunk_store = ChunkStore(connection)
    embedding_store = EmbeddingStore(connection)
    ingestor = DocumentIngestor(
        document_store,
        extraction_store,
        extractor,
        chunk_store,
        embedding_store,
    )
    return ingestor, connection


class TestExtractionIntegration:
    """Full extraction pipeline with mocked HTTP boundary."""

    def test_extraction_requested_and_persisted(self, tmp_path) -> None:
        """Ingest a synthetic PDF and verify extraction flows through the real pipeline."""
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf = _make_pdf_bytes(SYNTHETIC_TEXT)
        (workspace / "test.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)

        def handler(request):
            body = json.loads(request.content)
            messages = body["messages"]
            assert messages[0]["role"] == "system"
            assert messages[0]["content"] == STRUCTURED_EXTRACTION_SYSTEM_PROMPT
            assert messages[1]["role"] == "user"
            return httpx.Response(200, json=chat_payload(json.dumps(FAKE_RESPONSE)))

        extractor, requests, client = make_extractor(handler)
        ingestor, connection = make_ingestor(extractor)

        try:
            records = adapter.discover()
            assert len(records) == 1

            with client:
                result = ingestor.ingest(records[0])

            assert result.structured_extraction is not None
            assert result.structured_extraction.summary == FAKE_RESPONSE["summary"]
            assert result.structured_extraction.people == ("Daniel Telschow",)
            assert result.structured_extraction.organizations == ("Example GmbH",)
            assert result.structured_extraction.projects == ("Project Apollo",)
            assert result.structured_extraction.goals == ("Learn local AI deployment",)

            stored = ExtractionStore(connection).get(result.document_id)
            assert stored is not None
            assert stored.summary == FAKE_RESPONSE["summary"]
            assert stored.document_id == result.document_id

            assert len(requests) == 1
            body = json.loads(requests[0].content)
            assert body["think"] is False
            assert body["format"] == EXTRACTION_SCHEMA

        finally:
            connection.close()

    def test_correct_document_context_sent_to_model(self, tmp_path) -> None:
        """Verify the model receives the full document text, not individual chunks."""
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf = _make_pdf_bytes(SYNTHETIC_TEXT)
        (workspace / "test.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)

        def handler(request):
            body = json.loads(request.content)
            user_message = body["messages"][1]["content"]
            assert "Daniel Telschow" in user_message
            assert "Example GmbH" in user_message
            assert "Project Apollo" in user_message
            assert len(user_message) > 200
            return httpx.Response(200, json=chat_payload(json.dumps(FAKE_RESPONSE)))

        extractor, _, client = make_extractor(handler)
        ingestor, connection = make_ingestor(extractor)

        try:
            records = adapter.discover()
            with client:
                ingestor.ingest(records[0])
        finally:
            connection.close()

    def test_extraction_persists_correct_document_id_linkage(self, tmp_path) -> None:
        """Extraction document_id matches the ingested document's id."""
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf = _make_pdf_bytes(SYNTHETIC_TEXT)
        (workspace / "test.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)

        def handler(request):
            return httpx.Response(200, json=chat_payload(json.dumps(FAKE_RESPONSE)))

        extractor, _, client = make_extractor(handler)
        ingestor, connection = make_ingestor(extractor)

        try:
            records = adapter.discover()
            with client:
                result = ingestor.ingest(records[0])

            doc = DocumentStore(connection).get(result.document_id)
            assert doc is not None
            assert doc.source == "test.pdf"

            extraction = ExtractionStore(connection).get(result.document_id)
            assert extraction is not None
            assert extraction.document_id == result.document_id
            assert extraction.document_id == doc.id
        finally:
            connection.close()

    def test_repeated_extraction_does_not_create_duplicates(self, tmp_path) -> None:
        """Re-ingesting the same PDF reuses the persisted extraction, no new Ollama call."""
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf = _make_pdf_bytes(SYNTHETIC_TEXT)
        (workspace / "test.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=chat_payload(json.dumps(FAKE_RESPONSE)))

        extractor, _, client = make_extractor(handler)
        ingestor, connection = make_ingestor(extractor)

        try:
            records = adapter.discover()
            with client:
                first = ingestor.ingest(records[0])
                second = ingestor.ingest(records[0])

            assert first.document_id == second.document_id
            assert first.structured_extraction == second.structured_extraction
            assert call_count == 1

            extractions = connection.execute(
                "SELECT COUNT(*) FROM structured_extractions"
            ).fetchone()[0]
            assert extractions == 1
        finally:
            connection.close()

    def test_malformed_llm_output_raises_and_leaves_no_extraction(
        self, tmp_path
    ) -> None:
        """Malformed LLM output propagates error and does not persist a partial extraction."""
        import pathlib

        workspace = pathlib.Path(tmp_path)
        pdf = _make_pdf_bytes(SYNTHETIC_TEXT)
        (workspace / "test.pdf").write_bytes(pdf)

        adapter = FilesystemSourceAdapter(workspace)

        def handler(request):
            return httpx.Response(200, json=chat_payload("I cannot extract that."))

        extractor, _, client = make_extractor(handler)
        ingestor, connection = make_ingestor(extractor)

        try:
            records = adapter.discover()
            from personal_ai.documents.structured import MalformedStructuredOutputError

            with client, pytest.raises(MalformedStructuredOutputError):
                ingestor.ingest(records[0])

            extractions = connection.execute(
                "SELECT COUNT(*) FROM structured_extractions"
            ).fetchone()[0]
            assert extractions == 0
        finally:
            connection.close()

    def test_pdf_pages_are_combined_for_extraction(self, tmp_path) -> None:
        """Multi-page PDF sends all pages combined to the extractor."""
        import pathlib

        workspace = pathlib.Path(tmp_path)
        page1_text = "Page one: Daniel works at Example GmbH. " * 15
        page2_text = "Page two: Project Apollo goals and timeline. " * 15
        doc = pymupdf.open()
        for text in [page1_text, page2_text]:
            page = doc.new_page(width=595, height=max(842, 40 + len(text) // 2))
            lines = [text[i : i + 80] for i in range(0, len(text), 80)]
            page.insert_text((36, 36), "\n".join(lines))
        pdf_bytes = doc.tobytes()
        doc.close()
        (workspace / "multipage.pdf").write_bytes(pdf_bytes)

        adapter = FilesystemSourceAdapter(workspace)
        received_text = []

        def handler(request):
            body = json.loads(request.content)
            received_text.append(body["messages"][1]["content"])
            return httpx.Response(200, json=chat_payload(json.dumps(FAKE_RESPONSE)))

        extractor, _, client = make_extractor(handler)
        ingestor, connection = make_ingestor(extractor)

        try:
            records = adapter.discover()
            with client:
                result = ingestor.ingest(records[0])

            assert len(received_text) == 1
            assert "Page one" in received_text[0]
            assert "Page two" in received_text[0]
            assert result.chunks  # chunks are produced
        finally:
            connection.close()
