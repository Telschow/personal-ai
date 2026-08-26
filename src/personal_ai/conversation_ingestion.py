"""Orchestration for ingesting conversations into ConversationStore.

This module bridges conversation loaders (which read JSON files and produce
domain objects) with the ConversationStore (which persists them). It is
intentionally separate from the document-based DocumentIngestor pipeline
because conversations are first-class entities that do not get flattened
into documents.

Idempotency: running ingestion twice on the same unchanged directory
produces the same conversation count and message count. The store's
upsert semantics ensure no duplicates.
"""

from dataclasses import dataclass
from pathlib import Path

from personal_ai.sources.chatgpt_loader import ChatGPTConversationLoader
from personal_ai.sources.gemini_loader import GeminiConversationLoader
from personal_ai.storage.conversations import ConversationStore


@dataclass(frozen=True, slots=True)
class GeminiIngestionSummary:
    """Outcome of ingesting a Gemini conversation directory."""

    conversations_discovered: int
    conversations_stored: int
    messages_stored: int
    md_files_skipped: int = 0
    aggregate_files_skipped: int = 0
    source_type: str = "gemini"


@dataclass(frozen=True, slots=True)
class ChatGPTIngestionSummary:
    """Outcome of ingesting a ChatGPT conversation export directory."""

    shards_discovered: int
    conversations_discovered: int
    conversations_stored: int
    messages_stored: int
    attachments_stored: int
    source_type: str = "chatgpt"


def _count_md_files(directory: Path) -> int:
    """Count .md files that are twin renders of conversation JSON files."""
    return len(list(directory.rglob("*.md")))


def _count_aggregate_files(directory: Path) -> int:
    """Count files starting with _ that were skipped."""
    count = 0
    for candidate in sorted(directory.rglob("*.json")):
        if candidate.is_file() and candidate.name.startswith("_"):
            count += 1
    return count


def ingest_gemini_conversations(
    directory: Path, store: ConversationStore
) -> GeminiIngestionSummary:
    """Load and persist all Gemini conversations from a directory.

    This is the primary entry point for Gemini conversation ingestion.
    It reads JSON files, creates Conversation and ConversationMessage
    domain objects, and stores them via ConversationStore with idempotent
    upsert semantics.
    """
    loader = GeminiConversationLoader(directory)
    all_conversations = loader.load_all()

    md_count = _count_md_files(directory)
    aggregate_count = _count_aggregate_files(directory)

    conversations_stored = 0
    messages_stored = 0

    for conv, messages in all_conversations:
        is_new = store.save_conversation(conv)
        if is_new:
            conversations_stored += 1

        batch_count = store.save_messages(messages)
        messages_stored += batch_count

    return GeminiIngestionSummary(
        conversations_discovered=len(all_conversations),
        conversations_stored=conversations_stored,
        messages_stored=messages_stored,
        md_files_skipped=md_count,
        aggregate_files_skipped=aggregate_count,
    )


def ingest_chatgpt_conversations(
    directory: Path, store: ConversationStore
) -> ChatGPTIngestionSummary:
    """Load and persist all ChatGPT conversations from an export directory.

    Reads shard files, creates Conversation, ConversationMessage, and
    ConversationAttachment domain objects, and stores them via
    ConversationStore with idempotent upsert semantics.

    The directory should contain ``conversations-*.json`` shard files
    (the official ChatGPT export format).
    """
    loader = ChatGPTConversationLoader(directory)
    shards = loader.discover_shards()
    all_results = loader.load_all()

    conversations_stored = 0
    messages_stored = 0
    attachments_stored = 0

    for conv, messages, attachments in all_results:
        is_new = store.save_conversation(conv)
        if is_new:
            conversations_stored += 1

        batch_count = store.save_messages(messages)
        messages_stored += batch_count

        if attachments:
            att_count = store.save_attachments(attachments)
            attachments_stored += att_count

    return ChatGPTIngestionSummary(
        shards_discovered=len(shards),
        conversations_discovered=len(all_results),
        conversations_stored=conversations_stored,
        messages_stored=messages_stored,
        attachments_stored=attachments_stored,
    )
