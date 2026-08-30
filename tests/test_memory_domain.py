"""Tests for the memory domain model: validation, identity, and JSON contract.

Pure, offline, and deterministic — no SQLite, no network, no Ollama.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from personal_ai.memory import (
    Memory,
    MemoryDraft,
    MemoryEventType,
    MemoryKind,
    MemoryScope,
    MemorySourceType,
    MemoryStatus,
    MemoryValidationError,
    new_memory_id,
    now_iso,
    parse_iso,
    validate_memory,
)


def test_new_memory_id_is_unique_and_prefixed() -> None:
    ids = {new_memory_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(memory_id.startswith("mem-") for memory_id in ids)


def test_now_iso_is_utc_iso_and_parseable() -> None:
    value = now_iso()
    parsed = parse_iso(value)
    assert parsed.tzinfo is not None
    assert parsed.tzinfo.utcoffset(parsed).total_seconds() == 0


def test_enum_values_are_stable_json_contract() -> None:
    assert [k.value for k in MemoryKind] == [
        "fact",
        "preference",
        "decision",
        "project_context",
        "entity",
        "summary",
        "instruction",
    ]
    assert [s.value for s in MemoryScope] == ["global", "agent", "project", "execution"]
    assert [s.value for s in MemoryStatus] == ["active", "archived", "deleted"]
    assert [t.value for t in MemorySourceType] == [
        "user",
        "execution",
        "artifact",
        "evidence",
        "imported",
        "system",
    ]
    assert MemoryEventType.CREATED == "memory.created"


def _draft(**overrides):
    base = {
        "kind": "preference",
        "content": "Prefers local-first tools.",
        "summary": "tooling",
        "source_type": "user",
        "source_id": "cli-1",
        "scope": "global",
        "scope_id": None,
        "confidence": 0.5,
        "importance": 0.5,
        "expires_at": None,
    }
    base.update(overrides)
    return MemoryDraft(**base)


def test_valid_draft_to_memory_sets_identity_and_provenance() -> None:
    created = "2026-08-30T00:00:00+00:00"
    memory = _draft().to_memory(created)
    assert memory.memory_id.startswith("mem-")
    assert memory.created_at == created == memory.updated_at
    assert memory.last_accessed_at is None
    assert memory.status is MemoryStatus.ACTIVE
    assert memory.kind is MemoryKind.PREFERENCE
    assert memory.scope is MemoryScope.GLOBAL
    assert memory.source_type is MemorySourceType.USER
    assert memory.source_id == "cli-1"
    validate_memory(memory)


def test_to_memory_roundtrip_via_list_unpacking() -> None:
    memory = _draft().to_memory("2026-08-30T00:00:00+00:00")
    fields = list(memory.to_dict())
    assert "memory_id" in fields
    assert "kind" in fields
    assert "content" in fields
    assert "summary" in fields
    assert "source_type" in fields
    assert "source_id" in fields
    assert "created_at" in fields


def test_to_dict_contains_operational_metadata() -> None:
    memory = _draft().to_memory("2026-08-30T00:00:00+00:00")
    data = memory.to_dict()
    assert data["scope"] == "global"
    assert data["kind"] == "preference"
    assert data["status"] == "active"
    assert data["confidence"] == 0.5
    assert data["importance"] == 0.5


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"content": "  "}, "content"),
        ({"confidence": 1.5}, "confidence"),
        ({"confidence": -0.1}, "confidence"),
        ({"importance": 1.1}, "importance"),
        ({"importance": -0.01}, "importance"),
        ({"kind": "not_a_kind"}, "kind"),
        ({"scope": "bogus"}, "scope"),
        ({"source_type": "nope"}, "source_type"),
    ],
)
def test_validate_rejects_invalid_drafts(overrides, match) -> None:
    with pytest.raises(MemoryValidationError, match=match):
        validate_memory(_draft(**overrides).to_memory("2026-08-30T00:00:00+00:00"))


def test_non_global_scope_requires_scope_id() -> None:
    with pytest.raises(MemoryValidationError, match="scope_id"):
        _draft(scope="project").to_memory("2026-08-30T00:00:00+00:00")
    validate_memory(_draft(scope="project", scope_id="proj-1").to_memory(now_iso()))


def test_global_scope_with_scope_id_rejected() -> None:
    with pytest.raises(MemoryValidationError, match="global"):
        _draft(scope="global", scope_id="unexpected").to_memory(now_iso())


def test_default_draft_values() -> None:
    memory = _draft().to_memory("2026-08-30T00:00:00+00:00")
    assert memory.kind is MemoryKind.PREFERENCE
    assert memory.scope is MemoryScope.GLOBAL


def test_parse_iso_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_iso("not-a-date")


def test_explicit_enum_inputs_are_accepted() -> None:
    memory = _draft(
        kind=MemoryKind.FACT,
        scope=MemoryScope.PROJECT,
        scope_id="proj-9",
        source_type=MemorySourceType.IMPORTED,
    ).to_memory(now_iso())
    assert memory.kind is MemoryKind.FACT
    assert memory.scope is MemoryScope.PROJECT
    validate_memory(memory)


def test_memory_is_immutable() -> None:
    memory = _draft().to_memory("2026-08-30T00:00:00+00:00")
    with pytest.raises(FrozenInstanceError):
        memory.content = "mutated"


def test_validate_accepts_complete_roundtrip_memory() -> None:
    original = _draft(
        scope="project",
        scope_id="proj-7",
        expires_at="2030-01-01T00:00:00+00:00",
    ).to_memory("2026-08-30T00:00:00+00:00")
    rebuilt = Memory(**original.to_dict())
    assert rebuilt == original
    validate_memory(rebuilt)
