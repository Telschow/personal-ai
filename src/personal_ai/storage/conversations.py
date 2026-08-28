"""SQLite-backed storage for conversations, their messages, and attachments."""

import json
import sqlite3
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from personal_ai.documents.conversations import (
    Conversation,
    ConversationAttachment,
    ConversationMessage,
)
from personal_ai.storage.chunks import _validate_boundary, _validate_range

_CONVERSATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    modified_at TEXT NOT NULL,
    metadata TEXT NOT NULL
)
"""

_MESSAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    speaker TEXT NOT NULL,
    content_text TEXT NOT NULL,
    content_type TEXT NOT NULL,
    timestamp TEXT,
    parent_message_id TEXT,
    is_active_branch INTEGER NOT NULL DEFAULT 1,
    metadata TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
)
"""

_ATTACHMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    blob_path TEXT NOT NULL,
    metadata TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES conversation_messages(id)
)
"""

_MESSAGE_INDEXES = (
    (
        "CREATE INDEX IF NOT EXISTS idx_conv_msg_conv "
        "ON conversation_messages (conversation_id, message_index)"
    ),
    ("CREATE INDEX IF NOT EXISTS idx_conv_msg_role ON conversation_messages (role)"),
)

_ATTACHMENT_INDEXES = (
    (
        "CREATE INDEX IF NOT EXISTS idx_conv_att_msg "
        "ON conversation_attachments (message_id)"
    ),
)

_CONVERSATION_INDEXES = (
    ("CREATE INDEX IF NOT EXISTS idx_conv_source_type ON conversations (source_type)"),
    ("CREATE INDEX IF NOT EXISTS idx_conv_created ON conversations (created_at)"),
)

_CONVERSATION_COLUMNS = (
    "id",
    "title",
    "source_type",
    "created_at",
    "modified_at",
    "metadata",
)

_MESSAGE_COLUMNS = (
    "id",
    "conversation_id",
    "message_index",
    "role",
    "speaker",
    "content_text",
    "content_type",
    "timestamp",
    "parent_message_id",
    "is_active_branch",
    "metadata",
)

_ATTACHMENT_COLUMNS = (
    "id",
    "message_id",
    "filename",
    "mime_type",
    "size_bytes",
    "blob_path",
    "metadata",
)


def _conversation_to_row(conv: Conversation) -> tuple[object, ...]:
    return (
        conv.id,
        conv.title,
        conv.source_type,
        conv.created_at,
        conv.modified_at,
        json.dumps(conv.metadata),
    )


def _row_to_conversation(row: tuple[object, ...]) -> Conversation:
    values = iter(row)
    return Conversation(
        id=str(next(values)),
        title=str(next(values)),
        source_type=str(next(values)),
        created_at=str(next(values)),
        modified_at=str(next(values)),
        metadata=json.loads(str(next(values))),
    )


def _message_to_row(msg: ConversationMessage) -> tuple[object, ...]:
    return (
        msg.id,
        msg.conversation_id,
        msg.message_index,
        msg.role,
        msg.speaker,
        msg.content_text,
        msg.content_type,
        msg.timestamp,
        msg.parent_message_id,
        1 if msg.is_active_branch else 0,
        json.dumps(msg.metadata),
    )


def _optional_str(value: object) -> str | None:
    """Convert a nullable column value to str or None."""
    return str(value) if value is not None else None


def _row_to_message(row: tuple[object, ...]) -> ConversationMessage:
    values = iter(row)
    return ConversationMessage(
        id=str(next(values)),
        conversation_id=str(next(values)),
        message_index=int(next(values)),
        role=str(next(values)),
        speaker=str(next(values)),
        content_text=str(next(values)),
        content_type=str(next(values)),
        timestamp=_optional_str(next(values)),
        parent_message_id=_optional_str(next(values)),
        is_active_branch=bool(int(next(values))),
        metadata=json.loads(str(next(values))),
    )


def _attachment_to_row(att: ConversationAttachment) -> tuple[object, ...]:
    return (
        att.id,
        att.message_id,
        att.filename,
        att.mime_type,
        att.size_bytes,
        att.blob_path,
        json.dumps(att.metadata),
    )


def _row_to_attachment(row: tuple[object, ...]) -> ConversationAttachment:
    values = iter(row)
    return ConversationAttachment(
        id=str(next(values)),
        message_id=str(next(values)),
        filename=str(next(values)),
        mime_type=str(next(values)),
        size_bytes=int(next(values)),
        blob_path=str(next(values)),
        metadata=json.loads(str(next(values))),
    )


@dataclass(frozen=True)
class ConversationSearchResult:
    """A search hit from conversation message search."""

    message_id: str
    conversation_id: str
    conversation_title: str
    message_index: int
    role: str
    speaker: str
    content_text: str
    score: float
    timestamp: str | None = None
    is_active_branch: bool = True


class ConversationStore:
    """Manages conversations, messages, and attachments in SQLite.

    Uses the same idempotent upsert pattern as other stores in this
    project: INSERT OR IGNORE for new rows, conditional UPDATE for
    changed rows, always returning a boolean indicating whether the
    row was new.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.execute(_CONVERSATIONS_SCHEMA)
        self._connection.execute(_MESSAGES_SCHEMA)
        self._connection.execute(_ATTACHMENTS_SCHEMA)
        for index_sql in _CONVERSATION_INDEXES:
            self._connection.execute(index_sql)
        for index_sql in _MESSAGE_INDEXES:
            self._connection.execute(index_sql)
        for index_sql in _ATTACHMENT_INDEXES:
            self._connection.execute(index_sql)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def save_conversation(self, conv: Conversation) -> bool:
        """Persist a conversation, returning True if newly inserted.

        On conflict (same id), updates mutable fields while preserving
        the original id and created_at.
        """
        row = _conversation_to_row(conv)
        cursor = self._connection.execute(
            "INSERT OR IGNORE INTO conversations "
            f"({', '.join(_CONVERSATION_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _CONVERSATION_COLUMNS)})",
            row,
        )
        if cursor.rowcount == 1:
            self._connection.commit()
            return True

        self._connection.execute(
            "UPDATE conversations SET "
            "title = ?, source_type = ?, modified_at = ?, metadata = ? "
            "WHERE id = ?",
            (
                conv.title,
                conv.source_type,
                conv.modified_at,
                json.dumps(conv.metadata),
                conv.id,
            ),
        )
        self._connection.commit()
        return False

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        row = self._connection.execute(
            f"SELECT {', '.join(_CONVERSATION_COLUMNS)} FROM conversations "
            "WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        return _row_to_conversation(row) if row is not None else None

    def list_conversations(
        self, *, source_type: str | None = None
    ) -> tuple[Conversation, ...]:
        if source_type is not None:
            rows = self._connection.execute(
                f"SELECT {', '.join(_CONVERSATION_COLUMNS)} FROM conversations "
                "WHERE source_type = ? ORDER BY created_at, id",
                (source_type,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                f"SELECT {', '.join(_CONVERSATION_COLUMNS)} FROM conversations "
                "ORDER BY created_at, id"
            ).fetchall()
        return tuple(_row_to_conversation(r) for r in rows)

    def count_conversations(self, *, source_type: str | None = None) -> int:
        if source_type is not None:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM conversations WHERE source_type = ?",
                (source_type,),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()
        return int(row[0]) if row is not None else 0

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def save_message(self, msg: ConversationMessage) -> bool:
        """Persist a message, returning True if newly inserted.

        On conflict (same id), updates mutable fields while preserving
        the original id and conversation_id.
        """
        row = _message_to_row(msg)
        cursor = self._connection.execute(
            "INSERT OR IGNORE INTO conversation_messages "
            f"({', '.join(_MESSAGE_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _MESSAGE_COLUMNS)})",
            row,
        )
        if cursor.rowcount == 1:
            self._connection.commit()
            return True

        self._connection.execute(
            "UPDATE conversation_messages SET "
            "message_index = ?, role = ?, speaker = ?, content_text = ?, "
            "content_type = ?, timestamp = ?, parent_message_id = ?, "
            "is_active_branch = ?, metadata = ? "
            "WHERE id = ?",
            (
                msg.message_index,
                msg.role,
                msg.speaker,
                msg.content_text,
                msg.content_type,
                msg.timestamp,
                msg.parent_message_id,
                1 if msg.is_active_branch else 0,
                json.dumps(msg.metadata),
                msg.id,
            ),
        )
        self._connection.commit()
        return False

    def save_messages(self, messages: tuple[ConversationMessage, ...]) -> int:
        """Persist a batch of messages, returning the count newly inserted."""
        count = 0
        for msg in messages:
            if self.save_message(msg):
                count += 1
        return count

    def get_message(self, message_id: str) -> ConversationMessage | None:
        row = self._connection.execute(
            f"SELECT {', '.join(_MESSAGE_COLUMNS)} FROM conversation_messages "
            "WHERE id = ?",
            (message_id,),
        ).fetchone()
        return _row_to_message(row) if row is not None else None

    def list_messages(
        self, conversation_id: str, *, include_inactive: bool = False
    ) -> tuple[ConversationMessage, ...]:
        if include_inactive:
            rows = self._connection.execute(
                f"SELECT {', '.join(_MESSAGE_COLUMNS)} FROM conversation_messages "
                "WHERE conversation_id = ? "
                "ORDER BY message_index, id",
                (conversation_id,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                f"SELECT {', '.join(_MESSAGE_COLUMNS)} FROM conversation_messages "
                "WHERE conversation_id = ? AND is_active_branch = 1 "
                "ORDER BY message_index, id",
                (conversation_id,),
            ).fetchall()
        return tuple(_row_to_message(r) for r in rows)

    def count_messages(
        self,
        conversation_id: str | None = None,
        *,
        active_only: bool = False,
    ) -> int:
        if conversation_id is not None:
            if active_only:
                row = self._connection.execute(
                    "SELECT COUNT(*) FROM conversation_messages "
                    "WHERE conversation_id = ? AND is_active_branch = 1",
                    (conversation_id,),
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT COUNT(*) FROM conversation_messages "
                    "WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
        else:
            if active_only:
                row = self._connection.execute(
                    "SELECT COUNT(*) FROM conversation_messages "
                    "WHERE is_active_branch = 1"
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT COUNT(*) FROM conversation_messages"
                ).fetchone()
        return int(row[0]) if row is not None else 0

    def count_messages_by_content_type(
        self, content_type: str, *, source_type: str | None = None
    ) -> int:
        """Count messages with a specific content_type, optionally filtered by source."""
        if source_type is not None:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM conversation_messages cm "
                "JOIN conversations c ON cm.conversation_id = c.id "
                "WHERE cm.content_type = ? AND c.source_type = ?",
                (content_type, source_type),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM conversation_messages WHERE content_type = ?",
                (content_type,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    def save_attachment(self, att: ConversationAttachment) -> bool:
        """Persist an attachment, returning True if newly inserted."""
        row = _attachment_to_row(att)
        cursor = self._connection.execute(
            "INSERT OR IGNORE INTO conversation_attachments "
            f"({', '.join(_ATTACHMENT_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _ATTACHMENT_COLUMNS)})",
            row,
        )
        if cursor.rowcount == 1:
            self._connection.commit()
            return True

        self._connection.execute(
            "UPDATE conversation_attachments SET "
            "filename = ?, mime_type = ?, size_bytes = ?, "
            "blob_path = ?, metadata = ? "
            "WHERE id = ?",
            (
                att.filename,
                att.mime_type,
                att.size_bytes,
                att.blob_path,
                json.dumps(att.metadata),
                att.id,
            ),
        )
        self._connection.commit()
        return False

    def save_attachments(self, attachments: tuple[ConversationAttachment, ...]) -> int:
        """Persist a batch of attachments, returning the count newly inserted."""
        count = 0
        for att in attachments:
            if self.save_attachment(att):
                count += 1
        return count

    def list_attachments_for_conversation(
        self, conversation_id: str
    ) -> tuple[ConversationAttachment, ...]:
        rows = self._connection.execute(
            "SELECT ca.id, ca.message_id, ca.filename, ca.mime_type, "
            "ca.size_bytes, ca.blob_path, ca.metadata "
            "FROM conversation_attachments ca "
            "JOIN conversation_messages cm ON ca.message_id = cm.id "
            "WHERE cm.conversation_id = ? "
            "ORDER BY cm.message_index, ca.id",
            (conversation_id,),
        ).fetchall()
        return tuple(_row_to_attachment(r) for r in rows)

    def count_attachments(self, conversation_id: str | None = None) -> int:
        if conversation_id is not None:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM conversation_attachments ca "
                "JOIN conversation_messages cm ON ca.message_id = cm.id "
                "WHERE cm.conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM conversation_attachments"
            ).fetchone()
        return int(row[0]) if row is not None else 0

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> tuple[ConversationSearchResult, ...]:
        """Search conversation messages by case-insensitive substring match.

        Excludes thought/reasoning messages (content_type in
        ('thoughts', 'reasoning_recap')) from normal search, as these
        are internal model reasoning, not conversational content.

        ``created_after`` and ``created_before`` are optional inclusive
        ISO-8601 bounds on the message timestamp (``cm.timestamp``), sharing
        the same validation and lexical comparison semantics as document
        temporal filtering. Messages without a timestamp never match a
        bounded window.

        Uses LIKE for simplicity and correctness. Results are scored
        by match quality and returned in descending score order.
        """
        if not isinstance(query, str) or not query.strip():
            return ()

        _validate_boundary("created_after", created_after)
        _validate_boundary("created_before", created_before)
        _validate_range("created", created_after, created_before)

        query_lower = query.strip().lower()
        conditions = [
            "cm.is_active_branch = 1",
            "cm.content_type NOT IN ('thoughts', 'reasoning_recap')",
            "(cm.content_text LIKE ? OR c.title LIKE ? OR cm.speaker LIKE ?)",
        ]
        params: list[object] = [
            f"%{query_lower}%",
            f"%{query_lower}%",
            f"%{query_lower}%",
        ]
        if created_after is not None or created_before is not None:
            conditions.append("cm.timestamp <> ''")
        if created_after is not None:
            conditions.append("cm.timestamp >= ?")
            params.append(created_after)
        if created_before is not None:
            conditions.append("cm.timestamp <= ?")
            params.append(created_before)

        where = " AND ".join(conditions)
        rows = self._connection.execute(
            "SELECT cm.id, cm.conversation_id, c.title, "
            "cm.message_index, cm.role, cm.speaker, cm.content_text, "
            "cm.timestamp, cm.is_active_branch "
            "FROM conversation_messages cm "
            "JOIN conversations c ON cm.conversation_id = c.id "
            f"WHERE {where}",
            params,
        ).fetchall()

        results: list[ConversationSearchResult] = []
        for row in rows:
            msg_id = str(row[0])
            conv_id = str(row[1])
            title = str(row[2])
            msg_index = int(row[3])
            role = str(row[4])
            speaker = str(row[5])
            content = str(row[6])
            timestamp = row[7] if row[7] is not None else None
            is_active = bool(row[8])

            score = 0.0
            if query_lower in content.lower():
                score = 0.6
                if content.lower().strip() == query_lower:
                    score = 1.0
                elif content.lower().startswith(query_lower):
                    score = 0.8
            if query_lower in title.lower():
                score = max(score, 0.5)
            if query_lower in speaker.lower():
                score = max(score, 0.3)

            results.append(
                ConversationSearchResult(
                    message_id=msg_id,
                    conversation_id=conv_id,
                    conversation_title=title,
                    message_index=msg_index,
                    role=role,
                    speaker=speaker,
                    content_text=content,
                    score=score,
                    timestamp=timestamp,
                    is_active_branch=is_active,
                )
            )

        results.sort(key=lambda r: (-r.score, r.conversation_id, r.message_index))
        return tuple(results[:limit])
