"""Tests for SQLite-backed memory persistence.

Offline and local: real SQLite (``:memory:`` for unit behavior, ``tmp_path``
for durable-file persistence across a reconnect). No networks, no Ollama.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from personal_ai.memory import (
    Memory,
    MemoryDraft,
    MemoryEventType,
    MemoryNotFoundError,
    MemoryStatus,
    MemoryStore,
    MemoryValidationError,
    now_iso,
    open_memory_store,
)


def _memory(**overrides) -> Memory:
    base = {
        "kind": "preference",
        "content": "Prefers concise output.",
        "summary": "output style",
        "source_type": "user",
        "source_id": "test",
        "scope": "global",
        "scope_id": None,
        "confidence": 0.5,
        "importance": 0.5,
        "expires_at": None,
    }
    base.update(overrides)
    return MemoryDraft(**base).to_memory(now_iso())


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore(sqlite3.connect(":memory:"))


def test_schema_created_and_save_get_roundtrip(store: MemoryStore) -> None:
    memory = _memory()
    store.save(memory)
    assert store.get(memory.memory_id) == memory


def test_duplicate_import_is_idempotent(store: MemoryStore) -> None:
    memory = _memory()
    store.save(memory)
    store.save(memory)
    assert store.list(MemoryStatus.ACTIVE) == (memory,)


def test_save_validates_before_writing(store: MemoryStore) -> None:
    with pytest.raises(MemoryValidationError, match="content"):
        store.save(_memory(content="  "))


def test_get_unknown_returns_none(store: MemoryStore) -> None:
    assert store.get("mem-nope") is None


def test_list_filters_by_status(store: MemoryStore) -> None:
    active = _memory(content="stays active")
    store.save(active)
    store.save(_memory(kind="fact", content="going away", summary=""))
    second = next(
        m for m in store.list(MemoryStatus.ACTIVE) if m.content == "going away"
    )
    store.save(
        Memory(
            memory_id=second.memory_id,
            kind=second.kind,
            content=second.content,
            summary=second.summary,
            source_type=second.source_type,
            source_id=second.source_id,
            scope=second.scope,
            scope_id=second.scope_id,
            confidence=second.confidence,
            importance=second.importance,
            status=MemoryStatus.ARCHIVED,
            created_at=second.created_at,
            updated_at=second.updated_at,
            last_accessed_at=second.last_accessed_at,
            expires_at=second.expires_at,
        )
    )
    assert {m.memory_id for m in store.list(MemoryStatus.ACTIVE)} == {active.memory_id}
    assert len(store.list(MemoryStatus.ARCHIVED)) == 1


def test_counts_match_rows(store: MemoryStore) -> None:
    assert store.counts() == {
        "memories": 0,
        "active": 0,
        "archived": 0,
        "deleted": 0,
        "purged": 0,
    }
    store.save(_memory())
    assert store.counts()["active"] == 1
    assert store.counts()["memories"] == 1


def test_record_access_stamps_last_accessed(store: MemoryStore) -> None:
    memory = _memory()
    store.save(memory)
    assert store.get(memory.memory_id).last_accessed_at is None
    store.record_access(memory.memory_id)
    assert store.get(memory.memory_id).last_accessed_at is not None


def test_purge_removes_record_and_updates_counts(store: MemoryStore) -> None:
    memory = _memory()
    store.save(memory)
    store.purge(memory.memory_id)
    assert store.get(memory.memory_id) is None
    assert store.counts()["active"] == 0


def test_events_append_in_order_with_safe_payload(store: MemoryStore) -> None:
    memory = _memory()
    store.save(memory)
    store.append_event(
        MemoryEventType.CREATED, memory.memory_id, {"kind": "preference"}
    )
    store.append_event(
        MemoryEventType.ACCESSED, memory.memory_id, {"kind": "preference"}
    )
    events = store.memory_events(memory.memory_id)
    assert [e["event_type"] for e in events] == [
        "memory.created",
        "memory.accessed",
    ]
    assert all(e["payload"].get("kind") == "preference" for e in events)
    for event in events:
        assert event["memory_id"] == memory.memory_id
        assert event["seq"] >= 1


def test_open_memory_store_persists_across_reconnect(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    memory = _memory()
    connection, store = open_memory_store(path)
    store.save(memory)
    store.append_event(
        MemoryEventType.CREATED, memory.memory_id, {"kind": "preference"}
    )
    connection.close()

    reopened, reopened_store = open_memory_store(path)
    try:
        assert reopened_store.get(memory.memory_id) == memory
        assert reopened_store.counts()["active"] == 1
        assert len(reopened_store.memory_events(memory.memory_id)) == 1
    finally:
        reopened.close()


def test_store_rejects_bogus_status_on_list(store: MemoryStore) -> None:
    with pytest.raises(ValueError):
        store.list(MemoryStatus("bogus"))


def test_memory_not_found_error_is_an_exception_subclass() -> None:
    assert issubclass(MemoryNotFoundError, Exception)
