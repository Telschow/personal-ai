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
        message_index=int(str(next(values))),
        role=str(next(values)),
        speaker=str(next(values)),
        content_text=str(next(values)),
        content_type=str(next(values)),
        timestamp=_optional_str(next(values)),
        parent_message_id=_optional_str(next(values)),
        is_active_branch=bool(int(str(next(values)))),
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
        size_bytes=int(str(next(values))),
        blob_path=str(next(values)),
        metadata=json.loads(str(next(values))),
    )


_LIKE_ESCAPE = "\\"

_TERM_MATCH_FRAGMENT = (
    "(cm.content_text LIKE ? ESCAPE '\\' "
    "OR c.title LIKE ? ESCAPE '\\' "
    "OR cm.speaker LIKE ? ESCAPE '\\')"
)


def _escape_like(text: str) -> str:
    """Escape SQLite LIKE wildcards so a term matches literally.

    Backslash, ``%`` and ``_`` are escaped with the ``ESCAPE '\\'`` clause so
    user/model input is never interpreted as a wildcard.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _terms_from_query(query_lower: str) -> tuple[str, ...]:
    """Split a lowercased query into de-duplicated meaningful terms.

    Whitespace-separated tokens are kept only when they carry at least one
    alphanumeric character; punctuation-only fragments (``-``, ``.``, ``%``)
    are dropped. Duplicate terms collapse to one occurrence, preserving the
    first-seen order.
    """
    seen: dict[str, None] = {}
    for raw in query_lower.split():
        if any(ch.isalnum() for ch in raw):
            seen.setdefault(raw, None)
    return tuple(seen)


def _longest_content_run(terms: tuple[str, ...], content_l: str) -> int:
    """Length of the longest contiguous query phrase present in content.

    Counts the largest number of consecutive query terms (in query order)
    whose space-joined phrase appears contiguously in the content text. A
    phrase of length 2 or more is a specific multi-word concept that should
    be weighted above isolated generic matches; returns 0 when no term is
    present at all, 1 when only isolated terms match.
    """
    n = len(terms)
    if not any(term in content_l for term in terms):
        return 0
    best = 1
    for length in range(2, n + 1):
        found = False
        for i in range(n - length + 1):
            if " ".join(terms[i : i + length]) in content_l:
                found = True
                best = length
                break
        if not found:
            break
    return best


def _match_score(
    terms: tuple[str, ...],
    query_lower: str,
    content_l: str,
    title_l: str,
    speaker_l: str,
) -> float | None:
    """Score one message against the query terms, or None when it matches none.

    A message matches any term found in its content, conversation title, or
    speaker. Content matches are the primary relevance signal: the score
    rewards term coverage proportionally and adds a deterministic bonus for a
    contiguous multi-word phrase of the query found in the content, so a
    specific concept (e.g. "career transition") outranks a scattered set of
    generic term matches. Exact and leading-content matches are boosted.
    Title/speaker matches act as low-priority fallbacks that never outrank a
    genuine content match. The result is deterministic and, for a single-term
    query, identical to the historical whole-phrase scoring.
    """
    content_count = sum(1 for term in terms if term in content_l)
    content_run = _longest_content_run(terms, content_l)
    title_matched = any(term in title_l for term in terms)
    speaker_matched = any(term in speaker_l for term in terms)

    if content_count == 0 and not title_matched and not speaker_matched:
        return None

    total = len(terms)
    score = 0.0
    if content_count:
        score = 0.6 * (content_count / total)
        if content_run >= 2:
            score += 0.4 * (content_run / total)
        if content_l.strip() == query_lower:
            score = 1.0
        elif content_l.startswith(query_lower):
            score = max(score, 0.8)
        score = min(score, 1.0)
    if title_matched:
        score = max(score, 0.25)
    if speaker_matched:
        score = max(score, 0.15)
    return score


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
        self,
        *,
        source_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Conversation, ...]:
        """Return conversations in deterministic (created-at, id) order.

        Optionally filtered by an exact ``source_type``. ``limit`` bounds the
        number of rows returned and ``offset`` skips rows, letting callers
        page through a conversation corpus deterministically without loading
        it all into memory at once.
        """
        where = " WHERE source_type = ?" if source_type is not None else ""
        params: list[object] = []
        if source_type is not None:
            params.append(source_type)
        sql = (
            f"SELECT {', '.join(_CONVERSATION_COLUMNS)} FROM conversations"
            f"{where} ORDER BY created_at, id"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
            if offset:
                sql += " OFFSET ?"
                params.append(offset)
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)
        rows = self._connection.execute(sql, params).fetchall()
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
        self,
        conversation_id: str,
        *,
        include_inactive: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[ConversationMessage, ...]:
        """Return messages in deterministic (message_index, id) order.

        ``include_inactive`` controls whether non-active-branch rows are
        included. ``limit`` bounds the number of rows returned and ``offset``
        skips rows, letting callers page through a conversation without
        loading the whole message list into memory at once.
        """
        where = " WHERE conversation_id = ?"
        params: list[object] = [conversation_id]
        if not include_inactive:
            where += " AND is_active_branch = 1"
        sql = (
            f"SELECT {', '.join(_MESSAGE_COLUMNS)} FROM conversation_messages"
            f"{where} ORDER BY message_index, id"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
            if offset:
                sql += " OFFSET ?"
                params.append(offset)
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)
        rows = self._connection.execute(sql, params).fetchall()
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
        """Search conversation messages by case-insensitive multi-term match.

        The query is tokenized into lowercase terms on whitespace. A message
        matches when *any* meaningful term appears (case-insensitively) in its
        content, its conversation title, or its speaker. Matching each term as
        a literal SQLite ``LIKE`` substring (with ``%``/``_``/``\\`` escaped)
        keeps model-generated multi-word queries useful: ``"BCG career
        interview"`` finds messages mentioning any of those words rather than
        requiring the whole phrase contiguously.

        Thoughts/reasoning messages (content_type in ('thoughts',
        'reasoning_recap')) are excluded from normal search.

        ``created_after`` and ``created_before`` are optional inclusive
        ISO-8601 bounds on the message timestamp (``cm.timestamp``), sharing
        the same validation and lexical comparison semantics as document
        temporal filtering. Messages without a timestamp never match a
        bounded window.

        Results are scored by match quality (messages matching more terms
        rank higher) and returned in descending score order, with
        ``(conversation_id, message_index)`` as the deterministic tie-break.
        """
        if not isinstance(query, str) or not query.strip():
            return ()

        _validate_boundary("created_after", created_after)
        _validate_boundary("created_before", created_before)
        _validate_range("created", created_after, created_before)

        query_lower = query.strip().lower()
        terms = _terms_from_query(query_lower)
        if not terms:
            return ()

        conditions = [
            "cm.is_active_branch = 1",
            "cm.content_type NOT IN ('thoughts', 'reasoning_recap')",
            "(" + " OR ".join(_TERM_MATCH_FRAGMENT for _ in terms) + ")",
        ]
        params: list[object] = []
        for term in terms:
            pattern = f"%{_escape_like(term)}%"
            params.extend((pattern, pattern, pattern))
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

            score = _match_score(
                terms,
                query_lower,
                content.lower(),
                title.lower(),
                speaker.lower(),
            )
            if score is None:
                continue

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
