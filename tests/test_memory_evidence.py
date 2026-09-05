"""Tests for multi-evidence provenance persistence.

Idempotent evidence attachment is a core durability guarantee: re-running the
same fact with the same evidence must not duplicate rows, and evidence carries
identifiers and timestamps only — never content.
"""

from __future__ import annotations

import sqlite3

import pytest

from personal_ai.memory.models import (
    MemoryDraft,
    MemoryEvidenceRef,
    MemoryValidationError,
)
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryNotFoundError, MemoryStore


def _service() -> MemoryService:
    store = MemoryStore(sqlite3.connect(":memory:"))
    return MemoryService(store)


def _seed(service: MemoryService) -> object:
    return service.create(
        MemoryDraft(
            kind="preference",
            content="The user prefers local-first tools.",
            source_type="user",
            source_id="seed",
        )
    )


def _ref(
    source_type: str = "email", source_id: str = "doc-1", **overrides
) -> MemoryEvidenceRef:
    base = {
        "source_type": source_type,
        "source_id": source_id,
        "source_document_id": None,
        "source_timestamp": "2026-01-05T10:00:00+00:00",
    }
    base.update(overrides)
    return MemoryEvidenceRef(**base)


def test_add_evidence_is_idempotent(service: MemoryService) -> None:
    memory = _seed(service)
    ref = _ref()
    assert service.add_evidence(memory.memory_id, (ref,)) == 1  # type: ignore[union-attr]
    assert service.add_evidence(memory.memory_id, (ref,)) == 0  # type: ignore[union-attr]
    assert len(service.evidence_for(memory.memory_id)) == 1  # type: ignore[union-attr]


def test_null_document_id_still_deduplicates(service: MemoryService) -> None:
    memory = _seed(service)
    assert service.add_evidence(memory.memory_id, (_ref(),)) == 1  # type: ignore[union-attr]
    assert service.add_evidence(memory.memory_id, (_ref(),)) == 0  # type: ignore[union-attr]
    assert len(service.evidence_for(memory.memory_id)) == 1  # type: ignore[union-attr]


def test_different_document_ids_both_kept(service: MemoryService) -> None:
    memory = _seed(service)
    service.add_evidence(  # type: ignore[union-attr]
        memory.memory_id,
        (
            _ref(source_document_id="d1"),
            _ref(source_document_id="d2"),
        ),
    )
    rows = service.evidence_for(memory.memory_id)  # type: ignore[union-attr]
    assert len(rows) == 2
    assert {r["source_document_id"] for r in rows} == {"d1", "d2"}


def test_evidence_rows_carry_identifiers_and_timestamps_only(
    service: MemoryService,
) -> None:
    memory = _seed(service)
    service.add_evidence(  # type: ignore[union-attr]
        memory.memory_id,
        (_ref(source_type="email", source_id="doc-9"),),
    )
    rows = service.evidence_for(memory.memory_id)  # type: ignore[union-attr]
    assert rows == (
        {
            "source_type": "email",
            "source_id": "doc-9",
            "source_document_id": None,
            "source_timestamp": "2026-01-05T10:00:00+00:00",
            "created_at": rows[0]["created_at"],
        },
    )
    assert set(rows[0]) == {
        "source_type",
        "source_id",
        "source_document_id",
        "source_timestamp",
        "created_at",
    }


def test_add_evidence_requires_existing_memory(service: MemoryService) -> None:
    with pytest.raises(MemoryNotFoundError):
        service.add_evidence("mem-missing", (_ref(),))


def test_evidence_entries_must_be_typed(service: MemoryService) -> None:
    memory = _seed(service)
    with pytest.raises(MemoryValidationError, match="MemoryEvidenceRef"):
        service.add_evidence(memory.memory_id, ({"source_type": "email"},))  # type: ignore[union-attr]


def test_ref_requires_nonempty_source_type_and_id() -> None:
    service = _service()
    memory = _seed(service)
    with pytest.raises(MemoryValidationError, match="source_type"):
        service.add_evidence(memory.memory_id, (_ref(source_type=""),))  # type: ignore[union-attr]
    with pytest.raises(MemoryValidationError, match="source_id"):
        service.add_evidence(memory.memory_id, (_ref(source_id=" "),))  # type: ignore[union-attr]


def test_ref_validates_timestamp() -> None:
    service = _service()
    memory = _seed(service)
    with pytest.raises(MemoryValidationError, match="timestamp"):
        service.add_evidence(memory.memory_id, (_ref(source_timestamp="nope"),))  # type: ignore[union-attr]


def test_evidence_survives_record_update(service: MemoryService) -> None:
    memory = _seed(service)
    service.add_evidence(memory.memory_id, (_ref(),))  # type: ignore[union-attr]
    service.update(memory.memory_id, confidence=0.99)  # type: ignore[union-attr]
    assert len(service.evidence_for(memory.memory_id)) == 1  # type: ignore[union-attr]


def test_events_do_not_echo_evidence_content(service: MemoryService) -> None:
    memory = _seed(service)
    events = service.events(memory.memory_id)  # type: ignore[union-attr]
    payloads = [e["payload"] for e in events]
    blob = str(payloads)
    assert "local-first" not in blob
    assert "source_id" not in blob


@pytest.fixture
def service() -> MemoryService:
    return _service()
