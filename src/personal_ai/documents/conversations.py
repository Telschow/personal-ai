"""Domain models for conversations and their messages.

Conversations are first-class entities distinct from documents.  Each
conversation owns an ordered sequence of messages, each with a role,
optional speaker label, and text content.

The schema intentionally supports ChatGPT tree structures via
``parent_message_id`` and ``is_active_branch``.  Gemini conversations
are strictly linear (parent_message_id=None, is_active_branch=1).
ChatGPT conversations use these fields to preserve branching history.
"""

import hashlib
from dataclasses import dataclass, field


def compute_conversation_id(
    source_type: str, source_key: str, conversation_id: str
) -> str:
    """Derive a stable conversation identity from origin and export ID.

    Components are joined with NUL so field boundaries cannot merge.
    Re-ingesting unchanged content from the same source always yields
    the same identity.
    """
    material = f"{source_type}\x00{source_key}\x00{conversation_id}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def compute_message_id(conversation_id: str, message_index: int) -> str:
    """Derive a stable message identity from conversation and index.

    Gemini does not provide per-message IDs, so we synthesize them
    deterministically from the conversation ID and message position.
    This is idempotent: the same conversation always produces the
    same message IDs in the same order.
    """
    material = f"{conversation_id}\x00{message_index}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def compute_attachment_id(message_id: str, attachment_index: int) -> str:
    """Derive a stable attachment identity from message and position.

    ChatGPT provides attachment IDs in the export, but they may not be
    globally unique across messages.  We synthesize from message ID +
    position for guaranteed uniqueness and determinism.
    """
    material = f"{message_id}\x00{attachment_index}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Conversation:
    """A single ingested conversation."""

    id: str
    title: str
    source_type: str
    created_at: str
    modified_at: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """A single message within a conversation."""

    id: str
    conversation_id: str
    message_index: int
    role: str
    content_text: str
    speaker: str = ""
    content_type: str = "text"
    timestamp: str | None = None
    parent_message_id: str | None = None
    is_active_branch: bool = True
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConversationAttachment:
    """A file attachment linked to a conversation message."""

    id: str
    message_id: str
    filename: str
    mime_type: str
    size_bytes: int
    blob_path: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
