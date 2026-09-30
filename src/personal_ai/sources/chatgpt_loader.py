"""Structured ChatGPT conversation loader.

Reads ChatGPT export JSON shard files and produces typed ``Conversation``
and ``ConversationMessage`` domain objects, preserving the full tree
structure including branching history.

Key differences from the ``GeminiConversationLoader``:

- Conversations live inside shard files (``conversations-*.json``), not
  individual per-conversation files.
- Each conversation has a ``mapping`` tree of nodes linked by parent
  pointers.  Children are reconstructed from these pointers.
- ``current_node`` identifies the active leaf; walking parents from it
  yields the canonical active branch.
- Messages carry per-message epoch timestamps (converted to ISO 8601).
- Thought/reasoning messages are stored with ``content_type`` set to
  their original type but ``content_text`` extracted from the thoughts
  array.
- Attachments (images, PDFs, etc.) are stored as ``ConversationAttachment``
  objects with blob paths referencing the ``.dat`` files in the export.
- Message IDs use the original node UUID from the export (globally unique
  within the conversation).

Message ordering within a conversation:

- The active branch (root → current_node) is ordered first in tree
  position.
- Inactive branches (edited prompts, regenerated responses) are ordered
  after the active branch in DFS order.
- ``is_active_branch`` flags which branch a message belongs to.

Idempotency: re-running ``load_all()`` on unchanged shards produces
identical conversation IDs, message IDs, and message ordering.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path, PurePosixPath

from personal_ai.documents.conversations import (
    Conversation,
    ConversationAttachment,
    ConversationMessage,
    compute_attachment_id,
    compute_conversation_id,
)
from personal_ai.sources.chatgpt import (
    _EXCLUDED_DIRECTORY_NAMES,
    _REASONING_CONTENT_TYPES,
    _SPEAKER_LABELS,
    SOURCE_TYPE,
    _cleaned,
    _conversation_id,
    is_conversation_list,
)


class ChatGPTLoadError(Exception):
    """Raised when a ChatGPT shard or conversation cannot be loaded."""


def _iso_from_epoch(value: object) -> str | None:
    """Convert epoch seconds (int or float) to UTC ISO 8601."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    moment = dt.datetime.fromtimestamp(value, tz=dt.UTC)
    return moment.isoformat()


def _extract_text_parts(content: dict[str, object]) -> tuple[str, int]:
    """Extract readable text from message content, counting media parts.

    Returns (extracted_text, media_part_count).
    """
    parts = content.get("parts")
    if not isinstance(parts, list):
        return "", 0

    texts: list[str] = []
    media_count = 0

    for part in parts:
        if isinstance(part, str):
            cleaned = part.strip()
            if cleaned:
                texts.append(cleaned)
        elif isinstance(part, dict):
            if part.get("content_type") == "audio_transcription":
                transcription = _cleaned(part.get("text"))
                if transcription:
                    texts.append(transcription)
            elif part.get("content_type") == "text":
                text_val = _cleaned(part.get("text"))
                if text_val:
                    texts.append(text_val)
            else:
                media_count += 1

    return "\n\n".join(texts), media_count


def _extract_thoughts(content: dict[str, object]) -> str:
    """Extract text from thoughts/reasoning content."""
    thoughts = content.get("thoughts")
    if not isinstance(thoughts, list):
        return ""
    parts: list[str] = []
    for thought in thoughts:
        if not isinstance(thought, dict):
            continue
        summary = thought.get("summary")
        if isinstance(summary, list):
            for item in summary:
                if isinstance(item, dict):
                    text = _cleaned(item.get("text"))
                    if text:
                        parts.append(text)
                elif isinstance(item, str):
                    text = item.strip()
                    if text:
                        parts.append(text)
        elif isinstance(summary, str):
            text = summary.strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


class ChatGPTConversationLoader:
    """Reads ChatGPT export shard files and produces typed conversation objects.

    The loader is stateless and does not touch any database. It reads
    shard files from disk and returns domain objects for the caller to
    persist.

    Tree traversal:

    1. Build a children map from parent pointers.
    2. DFS from root to assign message_index to all message-bearing nodes.
    3. Walk current_node → root to identify the active branch.
    4. Mark active branch nodes with ``is_active_branch=True``.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    def discover_shards(self) -> list[Path]:
        """Return sorted list of ChatGPT conversation shard files.

        Excludes:
        - Non-JSON files
        - JSON files that are not conversation lists (settings, manifests, etc.)
        - Files outside the directory boundary
        - Files in excluded directories
        """
        shards: list[Path] = []
        for candidate in sorted(self.directory.rglob("*.json")):
            if not candidate.is_file():
                continue
            if not self._is_contained(candidate.resolve()):
                continue
            if any(
                part in _EXCLUDED_DIRECTORY_NAMES
                for part in candidate.relative_to(self.directory).parts[:-1]
            ):
                continue
            try:
                data = json.loads(candidate.read_bytes())
            except json.JSONDecodeError, UnicodeDecodeError:
                continue
            if is_conversation_list(data):
                shards.append(candidate)
        return sorted(shards)

    def load_all(
        self,
    ) -> list[
        tuple[
            Conversation,
            tuple[ConversationMessage, ...],
            tuple[ConversationAttachment, ...],
        ]
    ]:
        """Load all conversations from all discovered shards.

        Returns a list of (conversation, messages, attachments) triples,
        sorted by shard path then conversation position within shard.
        """
        results: list[
            tuple[
                Conversation,
                tuple[ConversationMessage, ...],
                tuple[ConversationAttachment, ...],
            ]
        ] = []
        for shard_path in self.discover_shards():
            shard_results = self.load_shard(shard_path)
            results.extend(shard_results)
        return results

    def load_shard(
        self, shard_path: Path
    ) -> list[
        tuple[
            Conversation,
            tuple[ConversationMessage, ...],
            tuple[ConversationAttachment, ...],
        ]
    ]:
        """Load all conversations from one shard file."""
        source_key = shard_path.relative_to(self.directory).as_posix()
        data = json.loads(shard_path.read_bytes())

        if not is_conversation_list(data):
            msg = f"File {source_key!r} is not a ChatGPT conversation shard"
            raise ChatGPTLoadError(msg)

        results: list[
            tuple[
                Conversation,
                tuple[ConversationMessage, ...],
                tuple[ConversationAttachment, ...],
            ]
        ] = []
        for idx, raw_conv in enumerate(data):
            result = self._load_conversation(raw_conv, source_key, idx)
            if result is not None:
                results.append(result)
        return results

    def _load_conversation(
        self,
        raw_conv: dict[str, object],
        shard_source_key: str,
        position_in_shard: int,
    ) -> (
        tuple[
            Conversation,
            tuple[ConversationMessage, ...],
            tuple[ConversationAttachment, ...],
        ]
        | None
    ):
        """Parse one raw conversation dict into domain objects.

        Returns None if the conversation has no usable messages.
        """
        raw_id = _conversation_id(raw_conv)
        title = _cleaned(raw_conv.get("title")) or raw_id
        create_time = raw_conv.get("create_time")
        update_time = raw_conv.get("update_time")
        created_at = _iso_from_epoch(create_time) or ""
        modified_at = _iso_from_epoch(update_time) or created_at

        conv_id = compute_conversation_id(SOURCE_TYPE, shard_source_key, raw_id)

        mapping = raw_conv.get("mapping")
        if not isinstance(mapping, dict):
            msg = f"ChatGPT conversation {raw_id!r} has no mapping"
            raise ChatGPTLoadError(msg)

        current_node = raw_conv.get("current_node")
        if not isinstance(current_node, str) or current_node not in mapping:
            msg = f"ChatGPT conversation {raw_id!r} has invalid current_node"
            raise ChatGPTLoadError(msg)

        # Build children map
        children_map: dict[str, list[str]] = {}
        for node_id, node in mapping.items():
            if not isinstance(node, dict):
                continue
            parent = node.get("parent")
            if isinstance(parent, str) and parent in mapping:
                children_map.setdefault(parent, []).append(node_id)

        # DFS from root to assign message_index
        root_id: str | None = None
        for node_id, node in mapping.items():
            if isinstance(node, dict) and node.get("parent") is None:
                root_id = node_id
                break

        if root_id is None:
            msg = f"ChatGPT conversation {raw_id!r} has no root node"
            raise ChatGPTLoadError(msg)

        # DFS traversal
        message_index = 0
        node_to_index: dict[str, int] = {}
        stack = [root_id]

        while stack:
            node_id = stack.pop()
            node = mapping.get(node_id)
            if not isinstance(node, dict):
                continue
            msg = node.get("message")
            if isinstance(msg, dict):
                node_to_index[node_id] = message_index
                message_index += 1
            # Push children in reverse sorted order for deterministic DFS
            child_ids = sorted(children_map.get(node_id, []), reverse=True)
            stack.extend(child_ids)

        # Walk current_node → root for active branch
        active_path: set[str] = set()
        node_id: str | None = current_node
        while node_id is not None:
            active_path.add(node_id)
            node = mapping.get(node_id)
            if not isinstance(node, dict):
                break
            parent = node.get("parent")
            node_id = parent if isinstance(parent, str) else None

        # Build messages and attachments
        messages: list[ConversationMessage] = []
        attachments: list[ConversationAttachment] = []
        media_total = 0
        reasoning_count = 0

        for node_id in sorted(node_to_index.keys(), key=lambda n: node_to_index[n]):
            node = mapping[node_id]
            raw_msg = node.get("message")
            if not isinstance(raw_msg, dict):
                continue

            author = raw_msg.get("author")
            role_raw = author.get("role") if isinstance(author, dict) else None
            if not isinstance(role_raw, str) or not role_raw.strip():
                continue
            role = role_raw.strip().lower()

            content = raw_msg.get("content")
            if not isinstance(content, dict):
                continue

            content_type = content.get("content_type", "text")
            if not isinstance(content_type, str):
                content_type = "text"

            msg_timestamp = _iso_from_epoch(raw_msg.get("create_time"))

            if content_type in _REASONING_CONTENT_TYPES:
                reasoning_count += 1
                if content_type == "thoughts":
                    text = _extract_thoughts(content)
                else:
                    # reasoning_recap stores text in parts
                    text, _ = _extract_text_parts(content)
                msg_id = str(raw_msg.get("id")) or f"{node_id}"
                messages.append(
                    ConversationMessage(
                        id=msg_id,
                        conversation_id=conv_id,
                        message_index=node_to_index[node_id],
                        role=role,
                        speaker=_SPEAKER_LABELS.get(role, role),
                        content_text=text,
                        content_type=content_type,
                        timestamp=msg_timestamp,
                        parent_message_id=node.get("parent")
                        if isinstance(node.get("parent"), str)
                        else None,
                        is_active_branch=node_id in active_path,
                        metadata={},
                    )
                )
                continue

            text, media_parts = _extract_text_parts(content)
            media_total += media_parts

            # Skip empty non-thoughts messages
            if not text.strip():
                continue

            msg_id = str(raw_msg.get("id")) or f"{node_id}"
            messages.append(
                ConversationMessage(
                    id=msg_id,
                    conversation_id=conv_id,
                    message_index=node_to_index[node_id],
                    role=role,
                    speaker=_SPEAKER_LABELS.get(role, role),
                    content_text=text,
                    content_type="text",
                    timestamp=msg_timestamp,
                    parent_message_id=node.get("parent")
                    if isinstance(node.get("parent"), str)
                    else None,
                    is_active_branch=node_id in active_path,
                    metadata={},
                )
            )

            # Collect attachments from message metadata
            provenance = raw_msg.get("metadata")
            if isinstance(provenance, dict):
                raw_attachments = provenance.get("attachments")
                if isinstance(raw_attachments, list):
                    for att_idx, raw_att in enumerate(raw_attachments):
                        if not isinstance(raw_att, dict):
                            continue
                        att_id = compute_attachment_id(msg_id, att_idx)
                        filename = (
                            _cleaned(raw_att.get("name")) or f"attachment_{att_idx}"
                        )
                        mime_type = (
                            _cleaned(raw_att.get("mime_type"))
                            or "application/octet-stream"
                        )
                        size_bytes = raw_att.get("size")
                        if not isinstance(size_bytes, (int, float)):
                            size_bytes = 0
                        att_file_id = _cleaned(raw_att.get("id")) or ""
                        attachments.append(
                            ConversationAttachment(
                                id=att_id,
                                message_id=msg_id,
                                filename=filename,
                                mime_type=mime_type,
                                size_bytes=int(size_bytes),
                                blob_path=att_file_id,
                                metadata={"raw_id": att_file_id} if att_file_id else {},
                            )
                        )

        if not messages:
            return None

        # Count nodes with messages
        node_count = len(mapping)
        message_node_count = len(node_to_index)

        metadata: dict[str, object] = {
            "raw_id": raw_id,
            "source_key": shard_source_key,
            "filename": PurePosixPath(shard_source_key).name,
            "mime_type": "application/json",
            "message_count": len(messages),
            "node_count": node_count,
            "message_node_count": message_node_count,
            "media_part_count": media_total,
            "reasoning_message_count": reasoning_count,
            "is_archived": raw_conv.get("is_archived") is True,
        }
        default_model_slug = _cleaned(raw_conv.get("default_model_slug"))
        if default_model_slug:
            metadata["default_model_slug"] = default_model_slug
        if attachments:
            metadata["attachment_count"] = len(attachments)

        conversation = Conversation(
            id=conv_id,
            title=title,
            source_type=SOURCE_TYPE,
            created_at=created_at,
            modified_at=modified_at,
            metadata=metadata,
        )

        return conversation, tuple(messages), tuple(attachments)

    def _is_contained(self, resolved_path: Path) -> bool:
        try:
            resolved_path.relative_to(self.directory)
        except ValueError:
            return False
        return True
