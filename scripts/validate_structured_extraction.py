"""Validate real structured extraction against local Ollama.

Processes text-heavy PDFs through the real OllamaStructuredExtractor.
Uses a temporary database.  No cloud APIs.  Local Ollama only.

Usage:
    uv run scripts/validate_structured_extraction.py [--smoke-only]
"""

import json
import pathlib
import sys
import tempfile
import time

from personal_ai.documents.classifier import classify_document
from personal_ai.documents.extractor import extract_text
from personal_ai.ingestion import DocumentIngestor
from personal_ai.ollama_client import ChatMessage, OllamaClient
from personal_ai.ollama_structured import OllamaStructuredExtractor
from personal_ai.sources.filesystem import FilesystemSourceAdapter
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)

MODEL = "qwen3.5:9b"
OLLAMA_BASE_URL = "http://localhost:11434"
PDF_DIR = pathlib.Path("raw_data_extracted/raw_data/pdfs")


def identify_text_heavy_pdfs(adapter: FilesystemSourceAdapter) -> list:
    """Return only records that classify as TEXT_HEAVY."""
    text_heavy = []
    for record in adapter.discover():
        extracted = extract_text(record)
        classification = classify_document(extracted)
        if classification.kind.value == "text_heavy":
            text_heavy.append((record, extracted, classification))
    return text_heavy


def run_smoke_test(client: OllamaClient) -> bool:
    """Run extraction on 1 representative PDF.  Return True on success."""
    adapter = FilesystemSourceAdapter(PDF_DIR)
    text_heavy = identify_text_heavy_pdfs(adapter)

    if not text_heavy:
        print("No text-heavy PDFs found.", file=sys.stderr)
        return False

    record, extracted, _classification = text_heavy[0]
    print(f"Smoke test: {record.source_key}")
    print(f"  Text length: {len(extracted.text)} chars")
    print(f"  Pages: {len(extracted.pages) if extracted.pages else 'N/A'}")

    extractor = OllamaStructuredExtractor(client)
    start = time.monotonic()
    try:
        result = extractor.extract(extracted)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        return False
    elapsed = time.monotonic() - start

    print(f"  Model response in {elapsed:.1f}s")
    print(f"  Summary: {result.summary[:120]}...")
    print(f"  People: {result.people}")
    print(f"  Organizations: {result.organizations}")
    print(f"  Projects: {result.projects}")
    print(f"  Goals: {result.goals}")
    print(f"  Topics: {result.topics}")

    valid = all(
        isinstance(f, str)
        for f in result.people
        + result.organizations
        + result.projects
        + result.goals
        + result.topics
    )
    print(f"  Schema valid: {valid}")
    return valid


def run_full_extraction() -> None:
    """Process all text-heavy PDFs through real Ollama."""
    adapter = FilesystemSourceAdapter(PDF_DIR)
    text_heavy = identify_text_heavy_pdfs(adapter)
    print(f"\nText-heavy PDFs to process: {len(text_heavy)}")

    with tempfile.TemporaryDirectory(prefix="personal_ai_extract_") as tmp:
        db_path = pathlib.Path(tmp) / "extraction.db"
        connection = connect_database(db_path)
        try:
            document_store = DocumentStore(connection)
            extraction_store = ExtractionStore(connection)
            chunk_store = ChunkStore(connection)
            embedding_store = EmbeddingStore(connection)

            client = OllamaClient(model=MODEL, base_url=OLLAMA_BASE_URL)
            extractor = OllamaStructuredExtractor(client)
            ingestor = DocumentIngestor(
                document_store,
                extraction_store,
                extractor,
                chunk_store,
                embedding_store,
            )

            successes = 0
            failures = 0
            total_facts = 0
            start_all = time.monotonic()

            with client:
                for i, (record, extracted, classification) in enumerate(text_heavy, 1):
                    print(f"\n[{i}/{len(text_heavy)}] {record.source_key}")
                    print(
                        f"  Text: {len(extracted.text)} chars, "
                        f"Pages: {len(extracted.pages) if extracted.pages else 'N/A'}"
                    )

                    start = time.monotonic()
                    try:
                        result = ingestor.ingest(record)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  FAILED: {exc}")
                        failures += 1
                        continue
                    elapsed = time.monotonic() - start

                    ext = result.structured_extraction
                    if ext is None:
                        print(f"  No extraction (classified as {result.kind.value})")
                        failures += 1
                        continue

                    facts = (
                        len(ext.people)
                        + len(ext.organizations)
                        + len(ext.projects)
                        + len(ext.goals)
                        + len(ext.topics)
                    )
                    total_facts += facts
                    successes += 1

                    print(f"  Extracted in {elapsed:.1f}s")
                    print(f"  Summary: {ext.summary[:100]}...")
                    print(
                        f"  Entities: {facts} "
                        f"(people={len(ext.people)}, orgs={len(ext.organizations)}, "
                        f"projects={len(ext.projects)}, goals={len(ext.goals)}, "
                        f"topics={len(ext.topics)})"
                    )

            elapsed_all = time.monotonic() - start_all
            print(f"\n{'=' * 60}")
            print("Extraction complete")
            print(f"  Model: {MODEL}")
            print(f"  Documents processed: {len(text_heavy)}")
            print(f"  Successes: {successes}")
            print(f"  Failures: {failures}")
            print(f"  Total structured facts: {total_facts}")
            print(f"  Total duration: {elapsed_all:.1f}s")
            if successes:
                print(f"  Avg per document: {elapsed_all / successes:.1f}s")

            # Verify persistence
            stored_count = connection.execute(
                "SELECT COUNT(*) FROM structured_extractions"
            ).fetchone()[0]
            print(f"  Stored extractions: {stored_count}")

            # Verify no duplicates
            doc_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[
                0
            ]
            print(f"  Total documents: {doc_count}")

            # Show 2-3 representative results (structural summary only)
            print("\nSample extractions (structure only):")
            rows = connection.execute(
                "SELECT document_id, summary, people, organizations, projects, goals, topics "
                "FROM structured_extractions LIMIT 3"
            ).fetchall()
            for row in rows:
                doc_id = row[0][:16]
                summary_preview = row[1][:80] if row[1] else ""
                people = json.loads(row[2])
                orgs = json.loads(row[3])
                projects = json.loads(row[4])
                goals = json.loads(row[5])
                topics = json.loads(row[6])
                print(f'  [{doc_id}...] "{summary_preview}"')
                print(f"    people={people}, orgs={orgs}, projects={projects}")
                print(f"    goals={goals}, topics={topics}")

            # Idempotency check
            print("\nIdempotency check: re-ingesting all records ...")
            re_start = time.monotonic()
            with client:
                for record, _extracted, _cls in text_heavy:
                    ingestor.ingest(record)
            re_elapsed = time.monotonic() - re_start
            stored_after = connection.execute(
                "SELECT COUNT(*) FROM structured_extractions"
            ).fetchone()[0]
            docs_after = connection.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0]
            print(
                f"  Re-extraction took {re_elapsed:.1f}s (vs {elapsed_all:.1f}s first run)"
            )
            print(f"  Extractions after 2nd run: {stored_after}")
            print(f"  Documents after 2nd run: {docs_after}")
            if stored_after == stored_count and docs_after == doc_count:
                print("  PASS: idempotent (no duplicates)")
            else:
                print(f"  FAIL: changed (extractions {stored_count} -> {stored_after})")

        finally:
            connection.close()


def main() -> None:
    smoke_only = "--smoke-only" in sys.argv

    print(f"Verifying Ollama at {OLLAMA_BASE_URL} ...")
    try:
        client = OllamaClient(model=MODEL, base_url=OLLAMA_BASE_URL)
        with client:
            # Quick connectivity check
            response = client.chat(
                [ChatMessage(role="user", content="Say OK")],
                think=False,
            )
            print(f"Ollama available, model: {response.model}")
    except Exception as exc:  # noqa: BLE001
        print(f"Ollama not available: {exc}", file=sys.stderr)
        sys.exit(1)

    print("\nSmoke test ...")
    client = OllamaClient(model=MODEL, base_url=OLLAMA_BASE_URL)
    with client:
        ok = run_smoke_test(client)
    if not ok:
        print("Smoke test failed.", file=sys.stderr)
        sys.exit(1)

    if smoke_only:
        print("\nSmoke test passed.  Run without --smoke-only for full extraction.")
        return

    run_full_extraction()


if __name__ == "__main__":
    main()
