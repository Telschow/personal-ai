"""Validate Gemini conversation ingestion into the personal AI system."""

import argparse
import sys
import time
from pathlib import Path

from personal_ai.conversation_ingestion import ingest_gemini_conversations
from personal_ai.retrieval import RetrievalService
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentStore,
    ExtractionStore,
    connect_database,
)

GEMINI_DIR = Path("raw_data_extracted/raw_data/Gemini")


def smoke_test() -> None:
    """Quick structural validation without your own corpus."""
    connection = connect_database(":memory:")
    store = ConversationStore(connection)

    # Verify empty state
    assert store.count_conversations() == 0
    assert store.count_messages() == 0
    results = store.search("anything")
    assert results == ()

    # Verify RetrievalService works with empty conversation store
    chunk_store = ChunkStore(connection)
    extraction_store = ExtractionStore(connection)
    document_store = DocumentStore(connection)
    service = RetrievalService(chunk_store, extraction_store, document_store, store)
    results = service.search("anything")
    assert results == ()

    connection.close()
    print("Smoke test passed.")


def run_validation() -> None:
    """Full validation: ingest real Gemini corpus, verify, search."""
    if not GEMINI_DIR.is_dir():
        print(f"Gemini directory not found: {GEMINI_DIR}")
        sys.exit(1)

    connection = connect_database(":memory:")
    store = ConversationStore(connection)

    print("Ingesting Gemini conversations...")
    t_start = time.monotonic()
    summary = ingest_gemini_conversations(GEMINI_DIR, store)
    elapsed = time.monotonic() - t_start

    print(f"\nIngestion results ({elapsed:.1f}s):")
    print(f"  conversations discovered: {summary.conversations_discovered}")
    print(f"  conversations stored:     {summary.conversations_stored}")
    print(f"  messages stored:          {summary.messages_stored}")
    print(f"  .md files skipped:        {summary.md_files_skipped}")
    print(f"  aggregate files skipped:  {summary.aggregate_files_skipped}")

    # Verify integrity
    print("\nVerifying integrity...")
    conv_count = store.count_conversations(source_type="gemini")
    msg_count = store.count_messages()
    assert conv_count == summary.conversations_discovered
    assert msg_count == summary.messages_stored

    # Check no duplicate conversation IDs
    convs = store.list_conversations(source_type="gemini")
    conv_ids = [c.id for c in convs]
    assert len(conv_ids) == len(set(conv_ids)), "Duplicate conversation IDs found!"

    # Check no duplicate message IDs
    for conv in convs:
        msgs = store.list_messages(conv.id)
        msg_ids = [m.id for m in msgs]
        assert len(msg_ids) == len(set(msg_ids)), f"Duplicate message IDs in {conv.id}!"

    # Check message indexes are sequential per conversation
    for conv in convs:
        msgs = store.list_messages(conv.id)
        for i, msg in enumerate(msgs):
            assert msg.message_index == i, (
                f"Message index {msg.message_index} != expected {i} "
                f"in conversation {conv.id}"
            )

    # Check every message links to an existing conversation
    all_msgs = []
    for conv in convs:
        msgs = store.list_messages(conv.id)
        all_msgs.extend(msgs)
        for msg in msgs:
            assert msg.conversation_id == conv.id

    print(f"  {conv_count} conversations verified")
    print(f"  {msg_count} messages verified")
    print("  No duplicate IDs")
    print("  Sequential message indexes")
    print("  All message links valid")

    # Search validation
    print("\nSearch validation...")
    chunk_store = ChunkStore(connection)
    extraction_store = ExtractionStore(connection)
    document_store = DocumentStore(connection)
    service = RetrievalService(chunk_store, extraction_store, document_store, store)

    # Synthetic probes: these scripts only measure ingestion/retrieval mechanics.
    test_queries = ["Example Corp", "Project Atlas", "planning", "interview", "AI"]
    for query in test_queries:
        results = service.search(query, limit=3)
        conv_results = [r for r in results if r.result_type == "conversation"]
        print(f"  '{query}': {len(conv_results)} conversation hits")
        for r in conv_results[:2]:
            print(f"    [{r.speaker}] {r.title} (msg {r.message_index})")
            text_preview = r.text[:80] + "..." if len(r.text) > 80 else r.text
            print(f"      {text_preview}")

    # Idempotency check
    print("\nIdempotency check...")
    t_reingest = time.monotonic()
    summary2 = ingest_gemini_conversations(GEMINI_DIR, store)
    reingest_elapsed = time.monotonic() - t_reingest

    assert summary2.conversations_stored == 0
    assert summary2.messages_stored == 0
    print(
        f"  Re-ingestion: {reingest_elapsed:.2f}s, 0 new conversations, 0 new messages"
    )
    print("  Idempotency verified!")

    connection.close()
    print("\nValidation passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Run smoke test without your own corpus",
    )
    args = parser.parse_args()
    if args.smoke_only:
        smoke_test()
    else:
        run_validation()


if __name__ == "__main__":
    main()
