"""Gemini export adapter: one conversation JSON becomes one normalized record.

A Gemini Takeout-style export stores each conversation twice under the same
stem: an authoritative structured ``<name>.json`` object and a human-readable
``<name>.md`` render. Only the JSON twin is discovered; the ``.md`` render is
a derived duplicate and never yields its own record. Aggregate bookkeeping
files (``_all_conversations.json``, ``_urls_index.json``) and unrelated JSON
sidecars are excluded from discovery by name or by structural shape.
"""

import datetime as dt
import json
from pathlib import Path, PurePosixPath

from personal_ai.documents.models import compute_content_hash
from personal_ai.sources.base import SourceError
from personal_ai.sources.models import SourceRecord

SOURCE_TYPE = "gemini"

_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".ruff_cache"}
)

_SPEAKER_LABELS = {"user": "User", "assistant": "Gemini"}


class GeminiParseError(SourceError):
    """Raised when a conversation payload is malformed or incomplete."""


class EmptyConversationError(SourceError):
    """Raised when a conversation carries no usable message content."""


class PathOutsideExportError(SourceError):
    """Raised when a requested source key escapes the export directory."""


class SourceNotFoundError(SourceError):
    """Raised when a requested conversation does not exist in the export."""


class UnsupportedConversationError(SourceError):
    """Raised when a requested file is not a per-conversation JSON file."""


def _cleaned(value: object) -> str:
    """Strip string fields; any other JSON value contributes nothing."""
    if isinstance(value, str):
        return value.strip()
    return ""


def _normalize_instant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.astimezone(dt.UTC).isoformat()


def is_conversation_shape(data: object) -> bool:
    """Report whether decoded JSON has the top-level conversation shape.

    Export aggregates, manifests, and unrelated sidecars lack a top-level
    ``messages`` list; they are different document kinds, not corrupt
    conversations.
    """
    return isinstance(data, dict) and "messages" in data


def parse_gemini_conversation(
    payload: bytes, *, source_key: str = "<conversation>"
) -> dict[str, object]:
    """Decode one conversation payload into its JSON object."""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "Gemini conversation payload is not valid UTF-8 JSON"
        raise GeminiParseError(msg) from exc
    if not isinstance(data, dict):
        msg = (
            f"Gemini conversation {source_key!r} payload is a "
            f"{type(data).__name__}, expected an object"
        )
        raise GeminiParseError(msg)
    return data


def compose_conversation_text(conversation: dict[str, object]) -> tuple[str, int]:
    """Compose retrieval-ready plain text from one conversation.

    Returns ``(plain text, rendered message count)``. The title forms the
    first section when present. Every message then appears as one block of
    ``<speaker>:`` followed by the message text, in original order with
    original role information preserved via stable speaker labels
    (``User`` / ``Gemini``; unknown roles pass through verbatim so no
    information is invented or dropped). Message ids, timestamps, and URLs
    stay out of the text: they carry no conversational semantics and would
    pollute retrieval. Serialization noise (leading and trailing whitespace,
    whitespace-only messages) is normalized away. Identity therefore depends
    only on semantic content.
    """
    sections: list[str] = []
    rendered = 0

    title = _cleaned(conversation.get("title"))
    if title:
        sections.append(title)

    messages = conversation.get("messages")
    for index, message in enumerate(messages or []):
        if not isinstance(message, dict):
            msg = f"Gemini message {index} is not an object"
            raise GeminiParseError(msg)
        raw_role = message.get("role")
        if not isinstance(raw_role, str) or not raw_role.strip():
            msg = f"Gemini message {index} has no usable role"
            raise GeminiParseError(msg)
        raw_content = message.get("content")
        if not isinstance(raw_content, str):
            msg = f"Gemini message {index} content must be text"
            raise GeminiParseError(msg)
        content = raw_content.strip()
        if not content:
            continue
        speaker = _SPEAKER_LABELS.get(raw_role.strip().lower(), raw_role.strip())
        sections.append(f"{speaker}:\n{content}")
        rendered += 1

    return "\n\n".join(sections), rendered


def build_conversation_record(source_key: str, payload: bytes) -> SourceRecord:
    """Normalize one conversation file into the shared source representation.

    The record's payload is the composed plain text (exactly the bytes that
    are hashed), so semantically identical conversations yield identical
    identity regardless of JSON key ordering. Provenance (conversation id,
    title, URL, timestamps, message count) travels in metadata only.
    """
    conversation = parse_gemini_conversation(payload, source_key=source_key)
    if not is_conversation_shape(conversation):
        msg = f"Gemini file {source_key!r} is not a conversation"
        raise UnsupportedConversationError(msg)

    stem = PurePosixPath(source_key).stem
    messages = conversation["messages"]
    if not isinstance(messages, list):
        msg = f"Gemini conversation {source_key!r} has invalid messages"
        raise GeminiParseError(msg)

    declared_count = conversation.get("messageCount")
    if isinstance(declared_count, int) and declared_count != len(messages):
        msg = (
            f"Gemini conversation {source_key!r} declares {declared_count} "
            f"messages but contains {len(messages)}"
        )
        raise GeminiParseError(msg)

    text, rendered_count = compose_conversation_text(conversation)
    if not rendered_count:
        msg = f"Gemini conversation {source_key!r} has no usable content"
        raise EmptyConversationError(msg)

    conversation_id = _cleaned(conversation.get("id")) or stem
    title = _cleaned(conversation.get("title")) or stem
    url = _cleaned(conversation.get("url"))

    metadata: dict[str, object] = {
        "filename": PurePosixPath(source_key).name,
        "mime_type": "application/json",
        "conversation_id": conversation_id,
        "title": title,
        "message_count": len(messages),
    }
    if url:
        metadata["url"] = url

    materialized = text.encode("utf-8")
    created_at = _normalize_instant(conversation.get("createdAt")) or ""
    modified_at = _normalize_instant(conversation.get("lastMessageAt")) or created_at
    return SourceRecord(
        source_type=SOURCE_TYPE,
        source_key=source_key,
        content_hash=compute_content_hash(materialized),
        created_at=created_at,
        modified_at=modified_at,
        payload=materialized,
        metadata=metadata,
    )


class GeminiSourceAdapter:
    """Yields Gemini conversations below an export directory as records.

    Discovery reads ``*.json`` files deterministically (sorted by relative
    POSIX path), skipping aggregate bookkeeping files whose names start
    with an underscore, JSON files that are structurally not conversations
    (export manifests, NotebookLM sidecars), and the derived ``.md`` twins
    which are never discovered at all. Corrupt conversation files propagate
    their parse error so broken exports are noticed instead of silently
    shrinking the corpus.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE

    def discover(self) -> list[SourceRecord]:
        records: list[SourceRecord] = []
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
            source_key = candidate.relative_to(self.directory).as_posix()
            data = parse_gemini_conversation(
                candidate.read_bytes(), source_key=source_key
            )
            if not is_conversation_shape(data):
                continue
            records.append(
                build_conversation_record(source_key, candidate.read_bytes())
            )
        records.sort(key=lambda record: record.source_key)
        return records

    def load_record(self, source_key: str) -> SourceRecord:
        """Load a single conversation by its export-relative POSIX path."""
        candidate = (self.directory / source_key).resolve()
        try:
            candidate.relative_to(self.directory)
        except ValueError as exc:
            msg = f"Path escapes Gemini export directory: {source_key!r}"
            raise PathOutsideExportError(msg) from exc

        if not candidate.is_file():
            msg = f"No such Gemini conversation: {source_key!r}"
            raise SourceNotFoundError(msg)
        if candidate.suffix.lower() != ".json":
            msg = f"Not a Gemini conversation file: {source_key!r}"
            raise UnsupportedConversationError(msg)
        if candidate.name.startswith("_"):
            msg = f"Gemini aggregate files are not conversations: {source_key!r}"
            raise UnsupportedConversationError(msg)

        return build_conversation_record(source_key, candidate.read_bytes())

    def _is_contained(self, resolved_path: Path) -> bool:
        try:
            resolved_path.relative_to(self.directory)
        except ValueError:
            return False
        return True
