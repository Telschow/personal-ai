"""Unified personal-corpus end-to-end test: PDFs, text files, and email in
one searchable corpus with source identity and MIME-based filtering.

Phase 33: provenance (source_type/source) flows through ``search_documents``
and ``search_knowledge``; email titles use the subject; PDF filtering is
expressed as ``mime_types=["application/pdf"]`` — never a ``pdf`` source type.
"""

import mailbox as mailbox_mod
from pathlib import Path

import pymupdf
import pytest

from personal_ai.ingestion import DocumentIngestor
from personal_ai.orchestration import ingest_source
from personal_ai.retrieval import RetrievalService
from personal_ai.sources.email import EmailSourceAdapter
from personal_ai.sources.filesystem import FilesystemSourceAdapter
from personal_ai.storage import (
    ChunkStore,
    DocumentFilter,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)
from personal_ai.tools import create_default_registry

PHASE33_PDF_MARKER = "phase33pdf"
PHASE33_TEXT_MARKER = "phase33text"
PHASE33_EMAIL_MARKER = "phase33email"
EMAIL_SUBJECT = "Phase 33 Email Subject"
EMAIL_MESSAGE_ID = "<synthetic-phase33@example.test>"

_PDF_TEXT = f"Phase 33 PDF marker {PHASE33_PDF_MARKER} quarterly report. " * 40
_TEXT_TEXT = f"Phase 33 text marker {PHASE33_TEXT_MARKER} planning log. " * 40
_EMAIL_TEXT = f"Phase 33 email marker {PHASE33_EMAIL_MARKER} itinerary. " * 40


def _make_pdf_bytes(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    lines = [text[i : i + 80] for i in range(0, len(text), 80)]
    page.insert_text((36, 36), "\n".join(lines))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_email(
    *,
    subject: str | None,
    body: str,
    message_id: str,
) -> object:
    from email.mime.text import MIMEText

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = "sender@example.test"
    msg["To"] = "me@example.test"
    msg["Date"] = "Mon, 01 Jan 2026 12:00:00 +0000"
    if subject is not None:
        msg["Subject"] = subject
    msg["Message-ID"] = message_id
    return msg


def _write_mbox(root: Path, name: str, messages: list[object]) -> None:
    mbox_dir = root / name
    mbox_dir.mkdir(parents=True, exist_ok=True)
    mbox = mailbox_mod.mbox(str(mbox_dir / "mbox"))
    for msg in messages:
        mbox.add(msg)
    mbox.close()


class UnifiedCorpusHarness:
    """Document, extraction, chunk, and embedding stores over one connection."""

    def __init__(self) -> None:
        self.connection = connect_database(":memory:")
        self.document_store = DocumentStore(self.connection)
        self.extraction_store = ExtractionStore(self.connection)
        self.chunk_store = ChunkStore(self.connection)
        self.embedding_store = EmbeddingStore(self.connection)
        self.ingestor = DocumentIngestor(
            self.document_store,
            self.extraction_store,
            None,
            self.chunk_store,
            self.embedding_store,
        )

    def close(self) -> None:
        self.connection.close()


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "unified_notes.txt").write_text(_TEXT_TEXT)
    (tmp_path / "unified_report.pdf").write_bytes(_make_pdf_bytes(_PDF_TEXT))
    _write_mbox(
        tmp_path / "Email",
        "INBOX.mbox",
        [
            _make_email(
                subject=EMAIL_SUBJECT,
                body=_EMAIL_TEXT,
                message_id=EMAIL_MESSAGE_ID,
            ),
            _make_email(
                subject=None,
                body="Phase 33 no-subject phase33nosubject agenda. " * 40,
                message_id="<nonsubject@example.test>",
            ),
        ],
    )
    return tmp_path


@pytest.fixture()
def harness() -> UnifiedCorpusHarness:
    fixture = UnifiedCorpusHarness()
    yield fixture
    fixture.close()


def _ingest(workspace: Path, harness: UnifiedCorpusHarness) -> tuple[int, int]:
    file_summary = ingest_source(FilesystemSourceAdapter(workspace), harness.ingestor)
    email_summary = ingest_source(
        EmailSourceAdapter(workspace / "Email"), harness.ingestor
    )
    return file_summary.documents, email_summary.documents


def _service(harness: UnifiedCorpusHarness) -> RetrievalService:
    return RetrievalService(
        harness.chunk_store, harness.extraction_store, harness.document_store
    )


class TestUnifiedCorpusE2E:
    def test_all_sources_land_in_one_searchable_corpus(
        self,
        workspace: Path,
        harness: UnifiedCorpusHarness,
    ) -> None:
        files, emails = _ingest(workspace, harness)
        assert (files, emails) == (2, 2)
        assert harness.chunk_store.count() > 0

        service = _service(harness)

        pdf_hits = service.search(PHASE33_PDF_MARKER)
        text_hits = service.search(PHASE33_TEXT_MARKER)
        email_hits = service.search(PHASE33_EMAIL_MARKER)

        assert pdf_hits and all(r.source_type == "file" for r in pdf_hits)
        assert text_hits and all(r.source_type == "file" for r in text_hits)
        assert email_hits and all(r.source_type == "email" for r in email_hits)

    def test_result_provenance_carries_source_and_source_type(
        self,
        workspace: Path,
        harness: UnifiedCorpusHarness,
    ) -> None:
        _ingest(workspace, harness)
        service = _service(harness)

        pdf = next(
            r for r in service.search(PHASE33_PDF_MARKER) if r.source_type == "file"
        )
        email = next(
            r for r in service.search(PHASE33_EMAIL_MARKER) if r.source_type == "email"
        )

        assert pdf.title == "unified_report.pdf"
        assert email.title == EMAIL_SUBJECT
        assert email.source_type == "email"

    def test_email_without_subject_falls_back_to_source_identity(
        self,
        workspace: Path,
        harness: UnifiedCorpusHarness,
    ) -> None:
        _ingest(workspace, harness)
        service = _service(harness)

        hits = service.search("phase33nosubject")
        assert hits
        document = harness.document_store.get(hits[0].document_id)
        assert document is not None
        assert document.metadata.get("subject") == ""
        assert all(r.title == document.source for r in hits)
        assert hits[0].source_type == "email"

    def test_mime_filter_isolates_pdf_documents(
        self,
        workspace: Path,
        harness: UnifiedCorpusHarness,
    ) -> None:
        _ingest(workspace, harness)
        service = _service(harness)

        pdf_only = service.search(
            PHASE33_PDF_MARKER, filters=DocumentFilter(mime_types=("application/pdf",))
        )
        no_pdf = service.search(
            PHASE33_TEXT_MARKER,
            filters=DocumentFilter(mime_types=("application/pdf",)),
        )
        no_email = service.search(
            PHASE33_EMAIL_MARKER,
            filters=DocumentFilter(mime_types=("application/pdf",)),
        )

        assert pdf_only
        assert no_pdf == ()
        assert no_email == ()
        assert all(r.source_type == "file" for r in pdf_only)

    def test_source_and_mime_filters_combine(
        self,
        workspace: Path,
        harness: UnifiedCorpusHarness,
    ) -> None:
        _ingest(workspace, harness)
        service = _service(harness)

        hits = service.search(
            PHASE33_PDF_MARKER,
            filters=DocumentFilter(
                source_types=("email",), mime_types=("application/pdf",)
            ),
        )

        assert hits == ()

    def test_source_types_pdf_is_not_the_pdf_representation(
        self,
        workspace: Path,
        harness: UnifiedCorpusHarness,
    ) -> None:
        """PDFs are ``file`` sources; their format is expressed as a MIME type."""
        _ingest(workspace, harness)
        service = _service(harness)

        legacy = service.search(
            PHASE33_PDF_MARKER,
            filters=DocumentFilter(source_types=("pdf",)),
        )
        modern = service.search(
            PHASE33_PDF_MARKER,
            filters=DocumentFilter(mime_types=("application/pdf",)),
        )

        assert legacy == ()
        assert modern

    def test_search_documents_tool_output_includes_provenance(
        self,
        workspace: Path,
        harness: UnifiedCorpusHarness,
        tmp_path: Path,
    ) -> None:
        _ingest(workspace, harness)
        registry = create_default_registry(
            tmp_path,
            chunk_store=harness.chunk_store,
            retrieval_service=_service(harness),
        )

        results = registry.execute("search_documents", {"query": PHASE33_EMAIL_MARKER})

        assert results["status"] == "results"
        assert results["results"]
        hit = results["results"][0]
        assert {
            "document_id",
            "chunk_id",
            "chunk_index",
            "text",
            "rank",
            "source_type",
            "source",
        } <= set(hit)
        assert hit["source_type"] == "email"
        assert hit["source"] == "synthetic-phase33@example.test"

        pdf_only = registry.execute(
            "search_documents",
            {
                "query": PHASE33_EMAIL_MARKER,
                "filter": {"mime_types": ["application/pdf"]},
            },
        )
        assert pdf_only["status"] == "no_matches"
        assert pdf_only["results"] == []

    def test_search_knowledge_tool_output_includes_provenance(
        self,
        workspace: Path,
        harness: UnifiedCorpusHarness,
        tmp_path: Path,
    ) -> None:
        _ingest(workspace, harness)
        registry = create_default_registry(
            tmp_path,
            chunk_store=harness.chunk_store,
            retrieval_service=_service(harness),
        )

        results = registry.execute("search_knowledge", {"query": PHASE33_EMAIL_MARKER})

        assert results["status"] == "results"
        assert results["results"]
        hit = results["results"][0]
        assert hit["source_type"] == "email"
        assert hit["title"] == EMAIL_SUBJECT
