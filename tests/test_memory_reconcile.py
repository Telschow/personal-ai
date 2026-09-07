"""Tests for deterministic candidate reconciliation and memory lifecycle.

Offline and local: in-memory SQLite, no network, no Ollama. These tests pin
the four reconciliation outcomes (create / add-evidence / supersede / conflict)
plus the lifecycle (candidate -> active -> superseded) and the guarantee that
superseded/archived/deleted/candidate memories never surface in retrieval.
"""

from __future__ import annotations

import sqlite3
import unicodedata

import pytest

from personal_ai.memory.models import (
    Memory,
    MemoryCandidate,
    MemoryDraft,
    MemoryEvidenceRef,
    MemoryStatus,
    MemoryValidationError,
)
from personal_ai.memory.retriever import MemoryRetriever
from personal_ai.memory.service import MemoryConflictError, MemoryService
from personal_ai.memory.store import MemoryStore


def _service() -> MemoryService:
    return MemoryService(MemoryStore(sqlite3.connect(":memory:")))


def _evidence(
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


def _candidate(**overrides) -> MemoryCandidate:
    base = {
        "statement": "The user prefers local-first tools.",
        "kind": "preference",
        "confidence": 0.9,
        "durability": 0.8,
        "relevance": 0.8,
        "specificity": 0.7,
        "recurrence": 1,
        "utility": 0.7,
        "temporal_scope": "current",
        "assertion_status": "asserted",
        "summary": "tooling",
        "evidence": (_evidence(),),
    }
    base.update(overrides)
    return MemoryCandidate(**base)


def _seed(service: MemoryService, content: str, **overrides) -> object:
    base = {
        "kind": "preference",
        "content": content,
        "summary": "",
        "source_type": "user",
        "source_id": "seed",
        "scope": "global",
        "scope_id": None,
        "confidence": 0.9,
        "importance": 0.5,
        "expires_at": None,
    }
    base.update(overrides)
    return service.create(MemoryDraft(**base))


def test_create_writes_active_memory_with_evidence(service: MemoryService) -> None:
    result = service.apply_candidate(_candidate())
    assert result["status"] == "created"
    memory_id = result["memory_id"]
    assert service.counts()["active"] == 1
    memory = service.get(memory_id)  # type: ignore[arg-type]
    assert memory.status is MemoryStatus.ACTIVE
    assert len(service.evidence_for(memory_id)) == 1  # type: ignore[arg-type]


def test_exact_duplicate_gains_evidence_not_second_record(
    service: MemoryService,
) -> None:
    created = service.apply_candidate(_candidate())
    assert created["status"] == "created"
    result = service.apply_candidate(
        _candidate(evidence=(_evidence(source_type="chatgpt", source_id="convo-1"),))
    )
    assert result["status"] == "updated"
    assert service.counts()["active"] == 1
    assert len(service.evidence_for(result["memory_id"])) == 2  # type: ignore[arg-type]


def test_duplicate_normalization_ignores_case_and_punctuation(
    service: MemoryService,
) -> None:
    _seed(service, "This is a fact.")
    result = service.apply_candidate(_candidate(statement="this is a fact!"))
    assert result["status"] == "updated"
    assert service.counts()["active"] == 1


def test_nfkc_composed_and_decomposed_german_merge_onto_one_memory(
    service: MemoryService,
) -> None:
    """München (composed ü) and Mu\\u0308nchen (decomposed u+diaeresis) are one fact."""
    _seed(service, "Der nutzer wohnt in München.", kind="location_context")
    result = service.apply_candidate(
        _candidate(
            statement="Der nutzer wohnt in Mu\u0308nchen",
            kind="location_context",
            evidence=(_evidence(source_type="chatgpt", source_id="convo-de"),),
        )
    )
    assert result["status"] == "updated"
    assert service.counts()["active"] == 1
    memory = service.list()[0]
    assert memory.content == "Der nutzer wohnt in München."
    assert memory.content == unicodedata.normalize("NFKC", memory.content)


def test_distinct_languages_create_separate_memories_without_false_merge(
    service: MemoryService,
) -> None:
    """German and English renditions of the same fact stay separate records.

    Reconciliation is lexical and conservative: below the overlap threshold the
    fact is created, never silently merged or flagged as a conflict.
    """
    first = service.apply_candidate(
        _candidate(
            statement="Der nutzer wohnt in Berlin",
            kind="location_context",
            evidence=(_evidence(source_type="chatgpt", source_id="convo-de"),),
        )
    )
    second = service.apply_candidate(
        _candidate(
            statement="The user lives in Berlin",
            kind="location_context",
            evidence=(_evidence(source_type="chatgpt", source_id="convo-en"),),
        )
    )
    assert first["status"] == "created"
    assert second["status"] == "created"
    assert service.counts()["active"] == 2
    contents = {m.content for m in service.list()}
    assert contents == {"Der nutzer wohnt in Berlin", "The user lives in Berlin"}


def test_nfkc_decomposed_spanish_merges_onto_existing_memory(
    service: MemoryService,
) -> None:
    """Á (composed) and A+combining acute (decomposed) are one fact."""
    _seed(service, "El usuario trabaja en Málaga.", kind="work")
    result = service.apply_candidate(
        _candidate(
            statement="El usuario trabaja en Ma\u0301laga",
            kind="work",
            evidence=(_evidence(source_type="gemini", source_id="convo-es"),),
        )
    )
    assert result["status"] == "updated"
    assert service.counts()["active"] == 1
    memory = service.list()[0]
    assert memory.content == "El usuario trabaja en Málaga."


def test_unicode_case_normalized_duplicate_merges_on_shared_normalization(
    service: MemoryService,
) -> None:
    """MÜNCHEN (case + accent normalized) reconciles onto München (Phase 23)."""
    _seed(service, "Der Nutzer wohnt in München.", kind="location_context")
    result = service.apply_candidate(
        _candidate(
            statement="Der Nutzer wohnt in MÜNCHEN",
            kind="location_context",
            evidence=(_evidence(source_type="chatgpt", source_id="convo-de"),),
        )
    )
    assert result["status"] == "updated"
    assert service.counts()["active"] == 1
    # The merge added the candidate as the first/only evidence row.
    assert len(service.evidence_for(result["memory_id"])) == 1  # type: ignore[arg-type]


def test_emoji_in_statement_does_not_break_lexical_merge(
    service: MemoryService,
) -> None:
    """Emoji tokenizes to nothing; the surrounding lexical words still merge."""
    _seed(service, "Der Nutzer liebt Berlin.", kind="preference")
    result = service.apply_candidate(
        _candidate(
            statement="Der Nutzer liebt Berlin ❤️",
            evidence=(_evidence(source_type="gemini", source_id="convo-e"),),
        )
    )
    assert result["status"] == "updated"
    assert service.counts()["active"] == 1


def test_stronger_newer_fact_supersedes_old(service: MemoryService) -> None:
    old = _seed(
        service,
        "The user's preferred name is Alex.",
        confidence=0.5,
    )
    result = service.apply_candidate(
        _candidate(
            statement="The user's preferred name is Atlas.",
            confidence=0.95,
            evidence=(
                _evidence(
                    source_type="chatgpt",
                    source_id="convo-9",
                    source_timestamp="2026-06-01T10:00:00+00:00",
                ),
            ),
        )
    )
    assert result["status"] == "created"
    assert result["superseded_id"] == old.memory_id
    assert service.counts()["active"] == 1
    assert service.counts()["superseded"] == 1
    superseded = service.get(old.memory_id)  # type: ignore[arg-type]
    assert superseded.status is MemoryStatus.SUPERSEDED

    hits = service.search("preferred name")
    assert len(hits) == 1
    assert hits[0].memory.content == "The user's preferred name is Atlas."


def test_supersede_records_audit_event_with_replaced_by(service: MemoryService) -> None:
    old = _seed(service, "The user's preferred name is Alex.", confidence=0.5)
    result = service.apply_candidate(
        _candidate(
            statement="The user's preferred name is Atlas.",
            confidence=0.95,
            evidence=(_evidence(source_type="chatgpt", source_id="convo-9"),),
        )
    )
    events = service.events(old.memory_id)  # type: ignore[arg-type]
    supersede_events = [e for e in events if e["event_type"] == "memory.superseded"]
    assert len(supersede_events) == 1
    payload = supersede_events[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["replaced_by"] == result["memory_id"]
    assert "content" not in payload


def test_ambiguous_related_fact_conflicts_without_write(service: MemoryService) -> None:
    _seed(service, "The user's preferred name is Alex.", confidence=0.9)
    with pytest.raises(MemoryConflictError, match="ambiguous"):
        service.apply_candidate(
            _candidate(
                statement="The user's preferred name is Atlas.",
                confidence=0.91,
            )
        )
    assert service.counts()["active"] == 1
    assert (
        service.get(service.list(MemoryStatus.ACTIVE)[0].memory_id).content
        == "The user's preferred name is Alex."
    )


def test_unrelated_fact_is_created(service: MemoryService) -> None:
    _seed(service, "Prefers concise output.")
    result = service.apply_candidate(
        _candidate(statement="The user runs three times a week.")
    )
    assert result["status"] == "created"
    assert service.counts()["active"] == 2


def test_archived_duplicate_does_not_block_new_record(service: MemoryService) -> None:
    old = _seed(service, "The user prefers local-first tools.")
    service.archive(old.memory_id)  # type: ignore[union-attr]
    result = service.apply_candidate(_candidate())
    assert result["status"] == "created"
    assert service.counts()["active"] == 1
    assert service.counts()["archived"] == 1


def test_supersede_requires_active_record(service: MemoryService) -> None:
    old = _seed(service, "A fact.")
    service.archive(old.memory_id)  # type: ignore[union-attr]
    other = service.create(
        MemoryDraft(
            kind="preference",
            content="A newer fact.",
            source_type="user",
            source_id="seed",
        )
    )
    with pytest.raises(MemoryValidationError, match="supersede"):
        service.supersede(old.memory_id, other.memory_id)  # type: ignore[union-attr]


def test_candidate_and_superseded_never_retrieved() -> None:
    """Only active memories surface in retrieval (store-level guarantee)."""
    store = MemoryStore(sqlite3.connect(":memory:"))
    draft = MemoryDraft(
        kind="preference",
        content="An inactive memory.",
        source_type="user",
        source_id="x",
    )
    active = draft.to_memory("2026-01-05T10:00:00+00:00")
    candidate = Memory(
        memory_id=active.memory_id + "-cand",
        kind=active.kind,
        content=active.content,
        source_type=active.source_type,
        source_id=active.source_id,
        status=MemoryStatus.CANDIDATE,
        created_at=active.created_at,
        updated_at=active.updated_at,
    )
    superseded = Memory(
        memory_id=active.memory_id + "-sup",
        kind=active.kind,
        content=active.content,
        source_type=active.source_type,
        source_id=active.source_id,
        status=MemoryStatus.SUPERSEDED,
        created_at=active.created_at,
        updated_at=active.updated_at,
    )
    store.save(candidate)
    store.save(superseded)
    store.save(active)
    assert store.counts()["candidate"] == 1
    assert store.counts()["superseded"] == 1
    hits = MemoryRetriever(store).search("inactive")
    assert [h.memory.memory_id for h in hits] == [active.memory_id]


@pytest.fixture
def service() -> MemoryService:
    return _service()
