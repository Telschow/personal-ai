"""Validate unified retrieval across chunks + structured extractions."""

import argparse
import time
from pathlib import Path

from personal_ai.storage.pdf import is_pdf_path

from personal_ai.ingestion import DocumentIngestor
from personal_ai.ollama_structured import OllamaStructuredExtractor
from personal_ai.retrieval import RetrievalService
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)

DATA_DIR = Path("raw_data_extracted")
PDF_DIR = DATA_DIR / "raw_data" / "pdfs"
DATABASE = Path("knowledge.db")


def smoke_test() -> None:
    """Quick structural validation without corpus or Ollama."""
    connection = connect_database(":memory:")
    chunk_store = ChunkStore(connection)
    extraction_store = ExtractionStore(connection)
    document_store = DocumentStore(connection)
    service = RetrievalService(chunk_store, extraction_store, document_store)

    results = service.search("anything")
    assert results == ()
    connection.close()
    print("Smoke test passed.")


def run_validation() -> None:
    """Full validation: ingest corpus, run searches, measure performance."""
    connection = connect_database(str(DATABASE))
    chunk_store = ChunkStore(connection)
    extraction_store = ExtractionStore(connection)
    document_store = DocumentStore(connection)
    embedding_store = EmbeddingStore(connection)
    extractor = OllamaStructuredExtractor()
    ingestor = DocumentIngestor(
        document_store, extraction_store, extractor, chunk_store, embedding_store
    )

    service = RetrievalService(chunk_store, extraction_store, document_store)

    total_documents = 0
    total_chunks = 0
    total_extractions = 0
    t_start = time.monotonic()

    for path in sorted(PDF_DIR.glob("*.pdf")):
        if not is_pdf_path(path):
            continue
        payload = path.read_bytes()
        # Manual ingestion to use SourceRecord
        from personal_ai.documents.models import compute_content_hash
        from personal_ai.sources.models import SourceRecord

        sr = SourceRecord(
            source_type="pdf",
            source_key=f"pdfs/{path.name}",
            content_hash=compute_content_hash(payload),
            created_at=None,
            modified_at=None,
            payload=payload,
            metadata={},
        )
        result = ingestor.ingest(sr)
        if result.status == "skipped":
            continue
        total_documents += 1

    # Count totals
    for row in connection.execute("SELECT COUNT(*) FROM document_chunks"):
        total_chunks = row[0]
    for row in connection.execute("SELECT COUNT(*) FROM structured_extractions"):
        total_extractions = row[0]

    elapsed = time.monotonic() - t_start

    print(
        f"\nCorpus: {total_documents} documents, {total_chunks} chunks, {total_extractions} extractions"
    )
    print(f"Ingestion time: {elapsed:.1f}s")

    # Test queries
    # Generic, clearly synthetic probes: this script only measures retrieval
    # mechanics (latency, hit counts), so the corpus content is irrelevant.
    # No real employer, person, or career topic may appear here.
    test_queries = [
        "Example Corp",
        "Alice Example",
        "Project Atlas",
        "quarterly planning",
        "product strategy",
        "systems engineering",
        "team leadership",
        "documentation",
    ]

    search_results = {}
    t_search = time.monotonic()
    for query in test_queries:
        results = service.search(query, limit=5)
        search_results[query] = results

    search_elapsed = time.monotonic() - t_search
    avg_ms = (search_elapsed / len(test_queries)) * 1000 if test_queries else 0

    print(f"\nSearch performance: {avg_ms:.1f}ms average per query")

    # Report results
    for query in test_queries:
        results = search_results[query]
        chunk_hits = [r for r in results if r.result_type == "chunk"]
        extraction_hits = [
            r for r in results if r.result_type == "structured_extraction"
        ]
        print(
            f"  '{query}': {len(chunk_hits)} chunks, {len(extraction_hits)} extractions"
        )
        for r in results[:2]:
            print(f"    [{r.result_type}] {r.title} (score={r.score:.3f})")

    connection.close()

    # Validate
    assert total_documents > 0
    assert total_chunks > 0
    assert total_extractions > 0
    assert len(test_queries) == 8
    print("\nValidation passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Run smoke test without Ollama or your own corpus",
    )
    args = parser.parse_args()
    if args.smoke_only:
        smoke_test()
    else:
        run_validation()


if __name__ == "__main__":
    main()
