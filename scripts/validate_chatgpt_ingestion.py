"""Validate ChatGPT conversation ingestion into the personal AI system."""

import argparse
import sys
import time
from pathlib import Path

from personal_ai.conversation_ingestion import ingest_chatgpt_conversations
from personal_ai.retrieval import RetrievalService
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentStore,
    ExtractionStore,
    connect_database,
)

CHATGPT_DIR = Path("raw_data_extracted/raw_data/ChatGPT_export")


def smoke_test() -> None:
    """Quick structural validation without the real corpus."""
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
    """Full validation: ingest real ChatGPT corpus, verify, search."""
    if not CHATGPT_DIR.is_dir():
        print(f"ChatGPT export directory not found: {CHATGPT_DIR}")
        sys.exit(1)

    connection = connect_database(":memory:")
    store = ConversationStore(connection)

    print("Ingesting ChatGPT conversations...")
    t_start = time.monotonic()
    summary = ingest_chatgpt_conversations(CHATGPT_DIR, store)
    elapsed = time.monotonic() - t_start

    print(f"\nIngestion results ({elapsed:.1f}s):")
    print(f"  shards discovered:     {summary.shards_discovered}")
    print(f"  conversations discovered: {summary.conversations_discovered}")
    print(f"  conversations stored:  {summary.conversations_stored}")
    print(f"  messages stored:       {summary.messages_stored}")
    print(f"  attachments stored:    {summary.attachments_stored}")

    # Verify integrity
    print("\nVerifying integrity...")
    conv_count = store.count_conversations(source_type="chatgpt")
    msg_count = store.count_messages()
    assert conv_count == summary.conversations_discovered
    assert msg_count == summary.messages_stored

    # Check no duplicate conversation IDs
    convs = store.list_conversations(source_type="chatgpt")
    conv_ids = [c.id for c in convs]
    assert len(conv_ids) == len(set(conv_ids)), "Duplicate conversation IDs found!"

    # Check no duplicate message IDs
    for conv in convs:
        msgs = store.list_messages(conv.id, include_inactive=True)
        msg_ids = [m.id for m in msgs]
        assert len(msg_ids) == len(set(msg_ids)), f"Duplicate message IDs in {conv.id}!"

    # Check message indexes are unique per conversation
    for conv in convs:
        msgs = store.list_messages(conv.id, include_inactive=True)
        indices = [m.message_index for m in msgs]
        assert len(indices) == len(set(indices)), (
            f"Non-unique message indices in {conv.id}"
        )

    # Check every message links to an existing conversation
    all_msgs = []
    for conv in convs:
        msgs = store.list_messages(conv.id, include_inactive=True)
        all_msgs.extend(msgs)
        for msg in msgs:
            assert msg.conversation_id == conv.id

    # Check active vs inactive branch counts
    active_count = 0
    inactive_count = 0
    for conv in convs:
        msgs = store.list_messages(conv.id, include_inactive=True)
        for msg in msgs:
            if msg.is_active_branch:
                active_count += 1
            else:
                inactive_count += 1

    print(f"  {conv_count} conversations verified")
    print(
        f"  {msg_count} messages verified ({active_count} active, {inactive_count} inactive)"
    )
    print(f"  {summary.attachments_stored} attachments stored")
    print("  No duplicate IDs")
    print("  Unique message indices per conversation")
    print("  All message links valid")

    # Content type distribution
    thoughts_count = store.count_messages_by_content_type(
        "thoughts", source_type="chatgpt"
    )
    reasoning_count = store.count_messages_by_content_type(
        "reasoning_recap", source_type="chatgpt"
    )
    text_count = msg_count - thoughts_count - reasoning_count
    print(
        f"  Content types: {text_count} text, {thoughts_count} thoughts, {reasoning_count} reasoning_recap"
    )

    # Search validation
    print("\nSearch validation...")
    chunk_store = ChunkStore(connection)
    extraction_store = ExtractionStore(connection)
    document_store = DocumentStore(connection)
    service = RetrievalService(chunk_store, extraction_store, document_store, store)

    test_queries = ["BCG", "career", "goals", "AI", "Python"]
    for query in test_queries:
        results = service.search(query, limit=3)
        conv_results = [r for r in results if r.result_type == "conversation"]
        print(f"  '{query}': {len(conv_results)} conversation hits")
        for r in conv_results[:2]:
            ts = f" [{r.timestamp}]" if r.timestamp else ""
            branch = "" if r.is_active_branch else " (inactive)"
            print(f"    [{r.speaker}] {r.title} (msg {r.message_index}){ts}{branch}")
            text_preview = r.text[:80] + "..." if len(r.text) > 80 else r.text
            print(f"      {text_preview}")

    # Idempotency check
    print("\nIdempotency check...")
    t_reingest = time.monotonic()
    summary2 = ingest_chatgpt_conversations(CHATGPT_DIR, store)
    reingest_elapsed = time.monotonic() - t_reingest

    assert summary2.conversations_stored == 0
    assert summary2.messages_stored == 0
    assert summary2.attachments_stored == 0
    print(
        f"  Re-ingestion: {reingest_elapsed:.2f}s, "
        f"0 new conversations, 0 new messages, 0 new attachments"
    )
    print("  Idempotency verified!")

    connection.close()
    print("\nValidation passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Run smoke test without real corpus",
    )
    args = parser.parse_args()
    if args.smoke_only:
        smoke_test()
    else:
        run_validation()


if __name__ == "__main__":
    main()
