"""SQLite-backed durable storage for memories and safe memory events.

Follows the project's storage conventions: the store class creates its tables
via ``CREATE TABLE IF NOT EXISTS`` on the shared
:func:`personal_ai.storage.documents.connect_database` connection, exactly
like :class:`personal_ai.execution.storage.OrchestrationStore`. Memory tables
therefore live in the same SQLite database as orchestration state (no second
connection layer) and survive process restarts.

The ``memory_events`` table is a safe audit trail: records carry ids, kinds,
scopes, and statuses — never private memory text.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from personal_ai.memory.models import (
    Memory,
    MemoryEvidenceRef,
    MemoryKind,
    MemoryScope,
    MemorySourceType,
    MemoryStatus,
    TemporalScope,
    now_iso,
    validate_memory,
)
from personal_ai.storage.documents import connect_database

_MEMORIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_id TEXT,
    confidence REAL NOT NULL,
    importance REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_accessed_at TEXT,
    expires_at TEXT,
    temporal_scope TEXT NOT NULL DEFAULT 'unknown'
)
"""

_MEMORY_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL
)
"""

_MEMORY_EVIDENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_evidence (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_document_id TEXT,
    source_timestamp TEXT,
    created_at TEXT NOT NULL
)
"""

# Retrieval filters on (status, scope, scope_id); listing orders by updated_at.
_MEMORIES_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_memories_status_scope "
    "ON memories(status, scope, scope_id)"
)
_MEMORY_EVENTS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_memory_events_memory ON memory_events(memory_id)"
)
_MEMORY_EVIDENCE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_memory_evidence_memory "
    "ON memory_evidence(memory_id)"
)
# Idempotent evidence: NULL document ids are normalized to '' so re-adding the
# same evidence does not create duplicates.
_MEMORY_EVIDENCE_DEDUPE = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_evidence_dedupe "
    "ON memory_evidence(memory_id, source_type, source_id, "
    "COALESCE(source_document_id, ''))"
)

_MEMORY_COLUMNS = (
    "memory_id",
    "kind",
    "content",
    "summary",
    "source_type",
    "source_id",
    "scope",
    "scope_id",
    "confidence",
    "importance",
    "status",
    "created_at",
    "updated_at",
    "last_accessed_at",
    "expires_at",
)


class MemoryNotFoundError(Exception):
    """Raised when a memory id does not exist in the store."""


class MemoryStore:
    """SQLite-backed CRUD store for :class:`Memory` records."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        for schema in (
            _MEMORIES_SCHEMA,
            _MEMORY_EVENTS_SCHEMA,
            _MEMORY_EVIDENCE_SCHEMA,
            _MEMORIES_INDEX,
            _MEMORY_EVENTS_INDEX,
            _MEMORY_EVIDENCE_INDEX,
            _MEMORY_EVIDENCE_DEDUPE,
        ):
            self._connection.execute(schema)
        # Backwards-compatible migration: pre-existing ``memories`` tables (e.g.
        # the production database) were created before ``temporal_scope``
        # existed. Adding the column is idempotent and safe for existing rows.
        self._ensure_column(
            "memories", "temporal_scope", "TEXT NOT NULL DEFAULT 'unknown'"
        )
        self._connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row[1]
            for row in self._connection.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }
        if column not in columns:
            self._connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    # ---- records ----
    def save(self, memory: Memory) -> Memory:
        """Create or update a memory record (idempotent on same content)."""
        validate_memory(memory)
        self._connection.execute(
            """
            INSERT INTO memories (
                memory_id, kind, content, summary, source_type, source_id,
                scope, scope_id, confidence, importance, status, created_at,
                updated_at, last_accessed_at, expires_at, temporal_scope
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                kind=excluded.kind, content=excluded.content,
                summary=excluded.summary, source_type=excluded.source_type,
                source_id=excluded.source_id, scope=excluded.scope,
                scope_id=excluded.scope_id, confidence=excluded.confidence,
                importance=excluded.importance, status=excluded.status,
                updated_at=excluded.updated_at,
                last_accessed_at=excluded.last_accessed_at,
                expires_at=excluded.expires_at,
                temporal_scope=excluded.temporal_scope
            """,
            _memory_to_row(memory),
        )
        self._connection.commit()
        return memory

    def get(self, memory_id: str) -> Memory | None:
        row = self._connection.execute(
            "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        return _row_to_memory(row) if row is not None else None

    def list(self, status: MemoryStatus | None = None) -> tuple[Memory, ...]:
        """All memories, optionally filtered by status, newest first."""
        if status is None:
            rows = self._connection.execute(
                "SELECT * FROM memories ORDER BY updated_at DESC, memory_id ASC"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM memories WHERE status = ? "
                "ORDER BY updated_at DESC, memory_id ASC",
                (status.value,),
            ).fetchall()
        return tuple(_row_to_memory(row) for row in rows)

    def counts(self) -> dict[str, int]:
        """Aggregate memory counts by lifecycle status (observability).

        ``purged`` counts purge events in the safe audit trail — purged
        records themselves no longer exist.
        """
        status_counts = {
            "candidate": 0,
            "active": 0,
            "superseded": 0,
            "archived": 0,
            "deleted": 0,
        }
        for status, count in self._connection.execute(
            "SELECT status, COUNT(*) FROM memories GROUP BY status"
        ):
            status_counts[status] = int(count)
        purged = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM memory_events WHERE event_type = ?",
                ("memory.purged",),
            ).fetchone()[0]
        )
        return {
            **status_counts,
            "purged": purged,
            "memories": sum(status_counts.values()),
        }

    def statistics(self) -> dict[str, object]:
        """Aggregate, content-free distributions for observability.

        Counts by lifecycle status, memory kind, and temporal scope, plus the
        total evidence-reference count. No statement content is read.
        """
        by_kind: dict[str, int] = {}
        by_temporal: dict[str, int] = {}
        for kind, count in self._connection.execute(
            "SELECT kind, COUNT(*) FROM memories GROUP BY kind"
        ):
            by_kind[str(kind)] = int(count)
        for temporal, count in self._connection.execute(
            "SELECT temporal_scope, COUNT(*) FROM memories GROUP BY temporal_scope"
        ):
            by_temporal[str(temporal)] = int(count)
        evidence = int(
            self._connection.execute("SELECT COUNT(*) FROM memory_evidence").fetchone()[0]
        )
        return {
            "by_kind": by_kind,
            "by_status": self.counts(),
            "by_temporal": by_temporal,
            "evidence": evidence,
        }

    def record_access(self, memory_id: str) -> None:
        """Stamp ``last_accessed_at`` (explicit callers only; never in search)."""
        if self.get(memory_id) is None:
            raise MemoryNotFoundError(f"Unknown memory: {memory_id}")
        self._connection.execute(
            "UPDATE memories SET last_accessed_at = ? WHERE memory_id = ?",
            (now_iso(), memory_id),
        )
        self._connection.commit()

    def purge(self, memory_id: str) -> None:
        """Physically remove a memory record (privacy-sensitive deletion)."""
        if self.get(memory_id) is None:
            raise MemoryNotFoundError(f"Unknown memory: {memory_id}")
        self._connection.execute(
            "DELETE FROM memories WHERE memory_id = ?", (memory_id,)
        )
        self._connection.commit()

    # ---- events ----
    def append_event(
        self,
        event_type: str,
        memory_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Record a safe memory lifecycle event (ids/kinds/scopes only)."""
        self._connection.execute(
            "INSERT INTO memory_events (memory_id, event_type, timestamp, payload) "
            "VALUES (?, ?, ?, ?)",
            (memory_id, event_type, now_iso(), json.dumps(payload or {})),
        )
        self._connection.commit()

    def memory_events(self, memory_id: str) -> tuple[dict[str, Any], ...]:
        rows = self._connection.execute(
            "SELECT seq, event_type, timestamp, payload FROM memory_events "
            "WHERE memory_id = ? ORDER BY seq ASC",
            (memory_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for seq, event_type, timestamp, payload in rows:
            out.append(
                {
                    "seq": seq,
                    "memory_id": memory_id,
                    "event_type": event_type,
                    "timestamp": timestamp,
                    "payload": json.loads(payload),
                }
            )
        return tuple(out)

    # ---- evidence ----
    def add_evidence(self, memory_id: str, refs: Sequence[MemoryEvidenceRef]) -> int:
        """Attach provenance references idempotently; returns rows inserted."""
        if self.get(memory_id) is None:
            raise MemoryNotFoundError(f"Unknown memory: {memory_id}")
        inserted = 0
        stamp = now_iso()
        for ref in refs:
            ref.validate()
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO memory_evidence ("
                "memory_id, source_type, source_id, source_document_id, "
                "source_timestamp, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    ref.source_type,
                    ref.source_id,
                    ref.source_document_id,
                    ref.source_timestamp,
                    stamp,
                ),
            )
            inserted += cursor.rowcount
        self._connection.commit()
        return inserted

    def evidence_for(self, memory_id: str) -> tuple[dict[str, object], ...]:
        """Provenance references for one memory, oldest first."""
        rows = self._connection.execute(
            "SELECT source_type, source_id, source_document_id, "
            "source_timestamp, created_at FROM memory_evidence "
            "WHERE memory_id = ? ORDER BY seq ASC",
            (memory_id,),
        ).fetchall()
        return tuple(
            {
                "source_type": row[0],
                "source_id": row[1],
                "source_document_id": row[2],
                "source_timestamp": row[3],
                "created_at": row[4],
            }
            for row in rows
        )


def _memory_to_row(memory: Memory) -> tuple[object, ...]:
    return (
        memory.memory_id,
        memory.kind.value,
        memory.content,
        memory.summary,
        memory.source_type.value,
        memory.source_id,
        memory.scope.value,
        memory.scope_id,
        memory.confidence,
        memory.importance,
        memory.status.value,
        memory.created_at,
        memory.updated_at,
        memory.last_accessed_at,
        memory.expires_at,
        memory.temporal_scope.value,
    )


def _row_to_memory(row: sqlite3.Row | tuple[object, ...]) -> Memory:
    values = tuple(row)
    return Memory(
        memory_id=values[0],
        kind=MemoryKind(values[1]),
        content=values[2],
        summary=values[3],
        source_type=MemorySourceType(values[4]),
        source_id=values[5],
        scope=MemoryScope(values[6]),
        scope_id=values[7],
        confidence=values[8],
        importance=values[9],
        status=MemoryStatus(values[10]),
        created_at=values[11],
        updated_at=values[12],
        last_accessed_at=values[13],
        expires_at=values[14],
        temporal_scope=_temporal_scope(values[15])
        if len(values) > 15 and values[15]
        else TemporalScope.UNKNOWN,
    )


def _temporal_scope(raw: object) -> TemporalScope:
    try:
        return TemporalScope(raw)  # type: ignore[arg-type]
    except ValueError:
        return TemporalScope.UNKNOWN


def open_memory_store(
    path: str | Path,
) -> tuple[sqlite3.Connection, MemoryStore]:
    """Open a SQLite database configured for memory storage.

    Reuses the shared personal-AI connection (identical to
    :func:`personal_ai.execution.storage.open_orchestration_store`), so memory
    and orchestration tables co-locate in one file by default.
    """
    connection = connect_database(path)
    return connection, MemoryStore(connection)
