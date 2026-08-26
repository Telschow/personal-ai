"""Structured Gemini conversation loader.

Reads Gemini Takeout JSON files and produces typed ``Conversation`` and
``ConversationMessage`` domain objects without flattening into text blobs.

Filtering rules mirror the existing ``GeminiSourceAdapter``:

- ``*.json`` files are discovered recursively, sorted by relative path.
- Files starting with ``_`` (aggregates, indexes) are excluded.
- Files whose decoded JSON does not have a top-level ``messages`` list
  are excluded.
- Derived ``.md`` twins are never discovered (only ``*.json``).
- Nested directories containing unrelated Takeout artifacts are traversed
  but their non-conversation files are filtered by structural shape.

Message IDs are deterministic: ``SHA-256(conversation_id + NUL + message_index)``.
Gemini does not provide per-message timestamps; ``message.timestamp`` is
always ``None``.
"""

from pathlib import Path, PurePosixPath

from personal_ai.documents.conversations import (
    Conversation,
    ConversationMessage,
    compute_conversation_id,
    compute_message_id,
)
from personal_ai.sources.gemini import (
    _EXCLUDED_DIRECTORY_NAMES,
    _SPEAKER_LABELS,
    SOURCE_TYPE,
    GeminiParseError,
    _cleaned,
    _normalize_instant,
    is_conversation_shape,
    parse_gemini_conversation,
)


class GeminiLoadError(Exception):
    """Raised when a conversation file cannot be loaded as structured data."""


class GeminiConversationLoader:
    """Reads Gemini export files and produces typed conversation objects.

    The loader is stateless and does not touch any database. It reads
    files from disk and returns domain objects for the caller to persist.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    def discover_files(self) -> list[Path]:
        """Return sorted list of canonical Gemini conversation JSON files.

        Excludes:
        - Files starting with ``_`` (aggregates, indexes)
        - Non-JSON files (never discovered)
        - Files outside the directory boundary
        - Files in excluded directories
        - JSON files that lack conversation shape
        """
        files: list[Path] = []
        for candidate in sorted(self.directory.rglob("*.json")):
            if not candidate.is_file():
                continue
            if candidate.name.startswith("_"):
                continue
            if not self._is_contained(candidate.resolve()):
                continue
            if any(
                part in _EXCLUDED_DIRECTORY_NAMES
                for part in candidate.relative_to(self.directory).parts[:-1]
            ):
                continue
            try:
                data = parse_gemini_conversation(
                    candidate.read_bytes(),
                    source_key=candidate.relative_to(self.directory).as_posix(),
                )
            except GeminiParseError:
                continue
            if not is_conversation_shape(data):
                continue
            files.append(candidate)
        return sorted(files)

    def load_file(
        self, path: Path
    ) -> tuple[Conversation, tuple[ConversationMessage, ...]]:
        """Load one Gemini JSON file into a Conversation and its messages.

        The conversation ID is taken from the ``id`` field in the JSON,
        falling back to the filename stem.  Message IDs are deterministic
        from (conversation_id, message_index).
        """
        source_key = path.relative_to(self.directory).as_posix()
        data = parse_gemini_conversation(path.read_bytes(), source_key=source_key)

        if not is_conversation_shape(data):
            msg = f"Gemini file {source_key!r} is not a conversation"
            raise GeminiLoadError(msg)

        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            msg = f"Gemini conversation {source_key!r} has no messages list"
            raise GeminiLoadError(msg)

        stem = PurePosixPath(source_key).stem
        raw_id = _cleaned(data.get("id")) or stem
        title = _cleaned(data.get("title")) or stem
        url = _cleaned(data.get("url"))
        created_at = _normalize_instant(data.get("createdAt")) or ""
        modified_at = _normalize_instant(data.get("lastMessageAt")) or created_at

        conv_id = compute_conversation_id(SOURCE_TYPE, source_key, raw_id)

        metadata: dict[str, object] = {
            "filename": PurePosixPath(source_key).name,
            "mime_type": "application/json",
            "raw_id": raw_id,
            "source_key": source_key,
            "message_count": len(raw_messages),
        }
        if url:
            metadata["url"] = url

        conversation = Conversation(
            id=conv_id,
            title=title,
            source_type=SOURCE_TYPE,
            created_at=created_at,
            modified_at=modified_at,
            metadata=metadata,
        )

        messages: list[ConversationMessage] = []
        for index, raw_msg in enumerate(raw_messages):
            if not isinstance(raw_msg, dict):
                continue
            raw_role = raw_msg.get("role")
            if not isinstance(raw_role, str):
                continue
            raw_content = raw_msg.get("content")
            if not isinstance(raw_content, str):
                continue

            role = raw_role.strip().lower()
            speaker = _SPEAKER_LABELS.get(role, role)
            content = raw_content.strip()

            msg_id = compute_message_id(conv_id, index)

            messages.append(
                ConversationMessage(
                    id=msg_id,
                    conversation_id=conv_id,
                    message_index=index,
                    role=role,
                    speaker=speaker,
                    content_text=content,
                    content_type="text",
                    timestamp=None,  # Gemini does not provide per-message timestamps
                    parent_message_id=None,  # Gemini is linear, no tree
                    is_active_branch=True,
                    metadata={},
                )
            )

        return conversation, tuple(messages)

    def load_all(self) -> list[tuple[Conversation, tuple[ConversationMessage, ...]]]:
        """Load all discovered conversations into domain objects.

        Returns a list of (conversation, messages) pairs, sorted by
        source file path for deterministic ordering.
        """
        results: list[tuple[Conversation, tuple[ConversationMessage, ...]]] = []
        for path in self.discover_files():
            conv, msgs = self.load_file(path)
            results.append((conv, msgs))
        return results

    def _is_contained(self, resolved_path: Path) -> bool:
        try:
            resolved_path.relative_to(self.directory)
        except ValueError:
            return False
        return True
