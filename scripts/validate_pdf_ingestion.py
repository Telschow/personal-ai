"""Validate corpus ingestion into a temporary database.

Usage:
    uv run scripts/validate_pdf_ingestion.py
"""

import pathlib
import sys
import tempfile

from personal_ai.documents.structured import StructuredExtraction
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

MODEL = "qwen3.5:9b"
PDF_DIR = pathlib.Path("raw_data_extracted/raw_data/pdfs")


class DryRunStructuredExtractor:
    """Records calls; returns fake extractions. Never touches Ollama."""

    def __init__(self) -> None:
        self.calls: list = []

    def extract(self, extraction):
        self.calls.append(extraction)
        return StructuredExtraction(
            document_id=extraction.document_id,
            summary=f"fake summary for {extraction.source_key}",
        )


def run() -> None:
    if not PDF_DIR.is_dir():
        print(f"PDF directory not found: {PDF_DIR}", file=sys.stderr)
        sys.exit(1)

    adapter = FilesystemSourceAdapter(PDF_DIR)

    with tempfile.TemporaryDirectory(prefix="personal_ai_validate_") as tmp:
        db_path = pathlib.Path(tmp) / "validate.db"
        connection = connect_database(db_path)
        try:
            document_store = DocumentStore(connection)
            extraction_store = ExtractionStore(connection)
            chunk_store = ChunkStore(connection)
            embedding_store = EmbeddingStore(connection)
            extractor = DryRunStructuredExtractor()

            ingestor = DocumentIngestor(
                document_store,
                extraction_store,
                extractor,
                chunk_store,
                embedding_store,
            )

            print(f"Discovering PDFs in {PDF_DIR} ...")
            summary = discover_source(adapter)
            print(f"Found {len(summary.source_keys)} source records.\n")

            print("Ingesting into temporary DB ...")
            result = ingest_source(adapter, ingestor)

            print("\nIngestion summary:")
            print(f"  source_type: {result.source_type}")
            print(f"  documents:   {result.documents}")
            print(f"  chunks:      {result.chunk_count}")
            print(f"  kind_counts: {result.kind_counts}")
            print(f"  extraction calls: {len(extractor.calls)}")

            # Query DB directly for richer stats
            total_chunks = connection.execute(
                "SELECT COUNT(*) FROM document_chunks"
            ).fetchone()[0]
            pages_with_chunks = connection.execute(
                "SELECT COUNT(DISTINCT page_number) FROM document_chunks WHERE page_number IS NOT NULL"
            ).fetchone()[0]
            docs_with_chunks = connection.execute(
                "SELECT COUNT(DISTINCT document_id) FROM document_chunks"
            ).fetchone()[0]
            page_distribution = connection.execute(
                "SELECT page_number, COUNT(*) FROM document_chunks "
                "WHERE page_number IS NOT NULL GROUP BY page_number ORDER BY page_number"
            ).fetchall()

            print("\nDatabase stats:")
            print(
                f"  total documents:     {connection.execute('SELECT COUNT(*) FROM documents').fetchone()[0]}"
            )
            print(f"  total chunks:        {total_chunks}")
            print(f"  docs with chunks:    {docs_with_chunks}")
            print(f"  unique pages with chunks: {pages_with_chunks}")
            print(f"  page distribution:   {dict(page_distribution)}")

            # Idempotency check
            print("\nIdempotency check: re-ingesting all records ...")
            ingest_source(adapter, ingestor)
            total_chunks2 = connection.execute(
                "SELECT COUNT(*) FROM document_chunks"
            ).fetchone()[0]
            docs2 = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            print(f"  documents after 2nd run: {docs2}")
            print(f"  chunks after 2nd run:    {total_chunks2}")
            if total_chunks == total_chunks2 and result.documents == docs2:
                print("  PASS: idempotent (no duplicates)")
            else:
                print(f"  FAIL: changed (chunks {total_chunks} -> {total_chunks2})")

        finally:
            connection.close()


if __name__ == "__main__":
    run()
