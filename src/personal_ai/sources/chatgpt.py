"""ChatGPT export adapter: official conversation exports become records.

The official ChatGPT export stores all conversations as JSON objects inside
shard files named ``conversations-*.json``. Each conversation carries a
``mapping`` tree (node id -> ``{id, message, parent}``) plus a
``current_node`` pointer to the leaf of the branch the user actually sees.
The export contains no per-conversation files, so the stable source key is
synthetic: ``<conversation_id>.json``. It survives re-exports that re-shard
conversations differently, which keeps document identity stable across
export runs.

Composition renders the active branch only: walking parents from
``current_node`` yields the canonical thread; sibling nodes left over from
edited prompts or regenerated responses are alternative variants that the
ChatGPT UI does not display. They are excluded from the text but counted in
metadata (``alternative_node_count``), so nothing is silently discarded.
Tree order is authoritative — message timestamps are metadata only, because
real exports contain create-time inversions along valid chains.

Within a message, string parts and ``audio_transcription`` parts render as
text (voice conversations carry real knowledge in transcriptions); media
pointer parts (images, audio blobs) are excluded from the pipeline and
counted, matching the project's media boundary. Model reasoning content
(``thoughts``, ``reasoning_recap``) is internal scratch work rather than
conversational knowledge: excluded from text, counted in metadata.
"""

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from personal_ai.documents.models import compute_content_hash
from personal_ai.sources.base import SourceError
from personal_ai.sources.models import SourceRecord

SOURCE_TYPE = "chatgpt"

_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".ruff_cache"}
)

_SPEAKER_LABELS = {"user": "User", "assistant": "ChatGPT"}

_REASONING_CONTENT_TYPES = frozenset({"thoughts", "reasoning_recap"})


class ChatGPTParseError(SourceError):
    """Raised when an export shard or conversation payload is malformed."""


class EmptyConversationError(SourceError):
    """Raised when a conversation's active branch has no usable content."""


class PathOutsideExportError(SourceError):
    """Raised when a requested source key escapes the export directory."""


class SourceNotFoundError(SourceError):
    """Raised when a requested conversation does not exist in the export."""


class UnsupportedConversationError(SourceError):
    """Raised when a requested file cannot be a ChatGPT conversation key."""


@dataclass(frozen=True, slots=True)
class ComposedConversation:
    """Composed plain text plus counters describing what was rendered."""

    text: str
    message_count: int
    media_part_count: int
    reasoning_message_count: int


def parse_shard(payload: bytes) -> object:
    """Decode one export JSON file into raw Python data."""
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "ChatGPT export payload is not valid UTF-8 JSON"
        raise ChatGPTParseError(msg) from exc


def is_conversation_list(data: object) -> bool:
    """Report whether decoded JSON is a non-empty list of conversations.

    Export sidecars are dicts, plain lists of unrelated settings objects,
    or empty lists; none of them is a conversation shard. The structural
    marker is every element being an object carrying both a ``mapping``
    tree and a ``current_node`` pointer.
    """
    return (
        isinstance(data, list)
        and bool(data)
        and all(
            isinstance(item, dict) and "mapping" in item and "current_node" in item
            for item in data
        )
    )


def _cleaned(value: object) -> str:
    """Strip string fields; any other JSON value contributes nothing."""
    if isinstance(value, str):
        return value.strip()
    return ""


def _conversation_id(conversation: dict[str, object]) -> str:
    """Return the stable identity of one conversation object."""
    conversation_id = _cleaned(conversation.get("conversation_id")) or _cleaned(
        conversation.get("id")
    )
    if not conversation_id:
        msg = "ChatGPT conversation has no usable conversation id"
        raise ChatGPTParseError(msg)
    return conversation_id


def _active_chain(mapping: dict[str, object], current_node: object) -> list[dict]:
    """Return nodes from the root to ``current_node`` in conversation order.

    The export has no ``children`` arrays; ancestry is reconstructed from
    parent pointers only. Dangling parents and cycles are structural damage
    and fail loudly instead of silently truncating the thread.
    """
    if not isinstance(mapping, dict):
        msg = "ChatGPT conversation mapping is not an object"
        raise ChatGPTParseError(msg)
    if not isinstance(current_node, str) or current_node not in mapping:
        msg = "ChatGPT conversation has an invalid current_node pointer"
        raise ChatGPTParseError(msg)

    chain: list[dict] = []
    visited: set[str] = set()
    node_id: str | None = current_node
    while node_id is not None:
        if node_id in visited:
            msg = "ChatGPT conversation mapping contains a parent cycle"
            raise ChatGPTParseError(msg)
        visited.add(node_id)
        node = mapping[node_id]
        if not isinstance(node, dict):
            msg = "ChatGPT conversation mapping node is not an object"
            raise ChatGPTParseError(msg)
        chain.append(node)
        parent = node.get("parent")
        if parent is not None and (
            not isinstance(parent, str) or parent not in mapping
        ):
            msg = "ChatGPT conversation mapping has a dangling parent link"
            raise ChatGPTParseError(msg)
        node_id = parent
    chain.reverse()
    return chain


def compose_conversation_text(conversation: dict[str, object]) -> ComposedConversation:
    """Compose retrieval-ready plain text from one conversation object.

    Returns a :class:`ComposedConversation`. The title forms the first
    section when present. Every message on the active branch then appears
    as one block of ``<speaker>:`` followed by its text, in tree order with
    role boundaries preserved via stable speaker labels (``User`` /
    ``ChatGPT``; unknown roles pass through verbatim so no information is
    invented or dropped). String parts and voice transcriptions render;
    media pointer parts and model reasoning content never enter the text
    and are reported through counters so their exclusion stays visible.
    Message ids, timestamps, model slugs, and attachment pointers stay out
    of the text: they carry no conversational semantics and would pollute
    retrieval. Serialization noise (surrounding whitespace, whitespace-only
    messages) is normalized away, so identity depends only on semantic
    content.
    """
    sections: list[str] = []
    message_count = 0
    media_part_count = 0
    reasoning_message_count = 0

    title = _cleaned(conversation.get("title"))
    if title:
        sections.append(title)

    mapping = conversation.get("mapping")
    chain = _active_chain(mapping, conversation.get("current_node"))
    for node in chain:
        message = node.get("message")
        if message is None:
            continue
        if not isinstance(message, dict):
            msg = "ChatGPT conversation message is not an object"
            raise ChatGPTParseError(msg)
        author = message.get("author")
        role = author.get("role") if isinstance(author, dict) else None
        if not isinstance(role, str) or not role.strip():
            msg = "ChatGPT conversation message has no usable role"
            raise ChatGPTParseError(msg)
        content = message.get("content")
        if not isinstance(content, dict):
            msg = "ChatGPT conversation message content is not an object"
            raise ChatGPTParseError(msg)
        content_type = content.get("content_type")
        if content_type in _REASONING_CONTENT_TYPES:
            reasoning_message_count += 1
            continue
        parts = content.get("parts")
        if parts is None:
            continue
        if not isinstance(parts, list):
            msg = "ChatGPT conversation message parts must be a list"
            raise ChatGPTParseError(msg)

        texts: list[str] = []
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
                else:
                    media_part_count += 1

        if not texts:
            continue
        speaker = _SPEAKER_LABELS.get(role.strip().lower(), role.strip())
        sections.append(f"{speaker}:\n" + "\n\n".join(texts))
        message_count += 1

    return ComposedConversation(
        text="\n\n".join(sections),
        message_count=message_count,
        media_part_count=media_part_count,
        reasoning_message_count=reasoning_message_count,
    )


def _iso_from_epoch(value: object) -> str | None:
    """Normalize an epoch-seconds timestamp to UTC ISO format."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = "ChatGPT conversation timestamp is not an epoch number"
        raise ChatGPTParseError(msg)
    moment = dt.datetime.fromtimestamp(value, tz=dt.UTC)
    return moment.isoformat()


def build_conversation_record(
    conversation: dict[str, object],
    source_key: str,
    *,
    shard: str | None = None,
) -> SourceRecord:
    """Normalize one conversation object into the shared source representation.

    The record's payload is the composed active-branch plain text (exactly
    the bytes that are hashed), so semantically identical conversations
    yield identical identity regardless of JSON key ordering, shard
    placement, or pruned alternative branches. Provenance (conversation id,
    title, timestamps, counts, attachment names) travels in metadata only.
    """
    if not isinstance(conversation, dict):
        msg = f"ChatGPT conversation {source_key!r} payload is not an object"
        raise ChatGPTParseError(msg)

    composed = compose_conversation_text(conversation)
    if not composed.message_count:
        msg = f"ChatGPT conversation {source_key!r} has no usable content"
        raise EmptyConversationError(msg)

    conversation_id = _conversation_id(conversation)
    mapping = conversation["mapping"]
    node_count = len(mapping) if isinstance(mapping, dict) else 0
    # Alternative variants are message-bearing nodes off the active branch
    # (edited prompts, regenerated responses): counted, never rendered.
    chain_nodes = _active_chain(mapping, conversation.get("current_node"))
    chain_ids = {id(node) for node in chain_nodes}
    alternative_nodes = sum(
        1
        for node in mapping.values()
        if isinstance(node, dict)
        and node.get("message") is not None
        and id(node) not in chain_ids
    )

    attachments: list[str] = []
    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        provenance = message.get("metadata")
        if not isinstance(provenance, dict):
            continue
        for attachment in provenance.get("attachments") or []:
            if isinstance(attachment, dict):
                name = _cleaned(attachment.get("name"))
                if name:
                    attachments.append(name)

    metadata: dict[str, object] = {
        "filename": PurePosixPath(source_key).name,
        "mime_type": "application/json",
        "conversation_id": conversation_id,
        "title": _cleaned(conversation.get("title")) or conversation_id,
        "is_archived": conversation.get("is_archived") is True,
        "message_count": composed.message_count,
        "node_count": node_count,
        "alternative_node_count": max(0, alternative_nodes),
        "media_part_count": composed.media_part_count,
        "reasoning_message_count": composed.reasoning_message_count,
    }
    default_model_slug = _cleaned(conversation.get("default_model_slug"))
    if default_model_slug:
        metadata["default_model_slug"] = default_model_slug
    if attachments:
        metadata["attachment_names"] = sorted(attachments)
    if shard:
        metadata["shard"] = shard

    materialized = composed.text.encode("utf-8")
    created_at = _iso_from_epoch(conversation.get("create_time")) or ""
    modified_at = _iso_from_epoch(conversation.get("update_time")) or created_at
    return SourceRecord(
        source_type=SOURCE_TYPE,
        source_key=source_key,
        content_hash=compute_content_hash(materialized),
        created_at=created_at,
        modified_at=modified_at,
        payload=materialized,
        metadata=metadata,
    )


def source_key_for(conversation: dict[str, object]) -> str:
    """Return the stable synthetic source key of one conversation object."""
    return f"{_conversation_id(conversation)}.json"


class ChatGPTSourceAdapter:
    """Yields ChatGPT conversations below an export directory as records.

    Discovery reads ``*.json`` files deterministically (sorted by relative
    POSIX path). Files whose decoded shape is not a list of mapping-carrying
    conversation objects are different document kinds — the ``chat.html``
    render duplicate, ``ads.json``, settings, library, manifest, and asset
    sidecars — and are excluded structurally, never by name list. Corrupt
    JSON propagates its parse error so broken exports are noticed instead of
    silently shrinking the corpus. Media attachments (``*.dat`` blobs) are
    not discovered at all: they belong to the separate media workflow.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE

    def discover(self) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        seen_ids: dict[str, str] = {}
        for shard_path, data in self._iter_conversation_shards():
            records.extend(
                self._records_from_shard(data, shard=shard_path, seen_ids=seen_ids)
            )
        records.sort(key=lambda record: record.source_key)
        return records

    def load_record(self, source_key: str) -> SourceRecord:
        """Load a single conversation by its ``<conversation_id>.json`` key."""
        candidate = (self.directory / source_key).resolve()
        try:
            candidate.relative_to(self.directory)
        except ValueError as exc:
            msg = f"Path escapes ChatGPT export directory: {source_key!r}"
            raise PathOutsideExportError(msg) from exc

        if candidate.suffix.lower() != ".json":
            msg = f"Not a ChatGPT conversation key: {source_key!r}"
            raise UnsupportedConversationError(msg)

        wanted = candidate.stem
        matches: list[tuple[str, dict]] = []
        for shard_path, data in self._iter_conversation_shards():
            for conversation in data:
                if _conversation_id(conversation) == wanted:
                    matches.append((shard_path, conversation))

        if not matches:
            msg = f"No such ChatGPT conversation: {source_key!r}"
            raise SourceNotFoundError(msg)
        if len(matches) > 1:
            msg = f"ChatGPT export contains duplicate conversation id {wanted!r}"
            raise ChatGPTParseError(msg)

        shard_path, conversation = matches[0]
        return build_conversation_record(conversation, source_key, shard=shard_path)

    def _iter_conversation_shards(self) -> list[tuple[str, list[dict]]]:
        """Return ``(relative path, conversations)`` for each shard in order.

        Non-shard JSON files (settings, manifests, asset sidecars) are
        skipped structurally; corrupt JSON propagates so broken exports are
        noticed instead of silently shrinking the corpus.
        """
        shards: list[tuple[str, list[dict]]] = []
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
            source_path = candidate.relative_to(self.directory).as_posix()
            data = parse_shard(candidate.read_bytes())
            if is_conversation_list(data):
                shards.append((source_path, data))
        return shards

    def _records_from_shard(
        self,
        data: list[dict],
        *,
        shard: str,
        seen_ids: dict[str, str],
    ) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for conversation in data:
            conversation_id = _conversation_id(conversation)
            if conversation_id in seen_ids:
                msg = (
                    f"ChatGPT export contains duplicate conversation "
                    f"id {conversation_id!r}"
                )
                raise ChatGPTParseError(msg)
            seen_ids[conversation_id] = shard
            records.append(
                build_conversation_record(
                    conversation,
                    source_key_for(conversation),
                    shard=shard,
                )
            )
        return records

    def _is_contained(self, resolved_path: Path) -> bool:
        try:
            resolved_path.relative_to(self.directory)
        except ValueError:
            return False
        return True
