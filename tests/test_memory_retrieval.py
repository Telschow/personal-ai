"""Tests for deterministic lexical memory retrieval.

The core invariants: (1) identical store + query => identical results,
and search never mutates the store (``last_accessed_at`` stays untouched);
(2) scope is enforced — global memories are always visible, non-global
memories only within an explicit matching scope; (3) expired, archived, and
deleted memories never surface; (4) the documented ranking weights dominate
(0.5 relevance / 0.2 importance / 0.2 confidence / 0.1 recency).
"""

from __future__ import annotations

import sqlite3

import pytest

from personal_ai.memory.models import Memory, MemoryDraft, parse_iso
from personal_ai.memory.retriever import (
    MemoryRetriever,
    MemorySearchError,
    ScopeFilter,
    tokenize,
)
from personal_ai.memory.store import MemoryStore

BASE_NOW = "2026-08-30T00:00:00+00:00"


def _make_memory(**overrides) -> Memory:
    status = overrides.pop("status", "active")
    created_at = overrides.pop("created_at", BASE_NOW)
    base = {
        "kind": "preference",
        "content": "",
        "summary": "",
        "source_type": "user",
        "source_id": "t",
        "scope": "global",
        "scope_id": None,
        "confidence": 0.5,
        "importance": 0.5,
        "expires_at": None,
    }
    base.update(overrides)
    memory = MemoryDraft(**base).to_memory(created_at)
    if status != "active":
        memory = Memory(**dict(memory.to_dict(), status=status, updated_at=created_at))
    return memory


def _store_with(
    *memories: Memory, now: str = BASE_NOW
) -> tuple[MemoryStore, MemoryRetriever]:
    store = MemoryStore(sqlite3.connect(":memory:"))
    for memory in memories:
        store.save(memory)
    return store, MemoryRetriever(store, now=lambda: now)


def _prefer(content: str, **overrides) -> Memory:
    overrides.setdefault("kind", "preference")
    overrides.setdefault("confidence", 0.5)
    overrides.setdefault("importance", 0.5)
    return _make_memory(content=content, summary="", **overrides)


def test_tokenize_normalizes_and_splits() -> None:
    assert tokenize("Prefers Concise, local-first tools!") == (
        "prefers",
        "concise",
        "local",
        "first",
        "tools",
    )


def test_search_does_not_mutate_store() -> None:
    memory = _prefer("Prefers concise explanations.")
    store, retriever = _store_with(memory)
    before = store.get(memory.memory_id)
    retriever.search("concise")
    after = store.get(memory.memory_id)
    assert before == after
    assert after.last_accessed_at is None


def test_search_is_deterministic_across_calls() -> None:
    memories = [
        _prefer("Career growth at the leadership level.", importance=0.9),
        _prefer("Weekly fitness routine including running.", importance=0.4),
        _prefer("Leadership books on my reading list.", importance=0.6),
    ]
    _, retriever = _store_with(*memories)
    first = retriever.search("leadership")
    # swap store state via record_access must not be triggered by search
    for _ in range(5):
        assert retriever.search("leadership") == first


def test_relevance_dominates_ranking() -> None:
    on_topic = _prefer("leadership career goal planning", importance=0.1)
    off_topic = _prefer("cooking pasta", importance=0.9)
    _, retriever = _store_with(on_topic, off_topic)
    hits = retriever.search("leadership")
    # The off-topic memory shares no tokens with the query and is excluded,
    # regardless of its high importance.
    assert len(hits) == 1
    assert hits[0].memory.memory_id == on_topic.memory_id
    assert hits[0].relevance > 0.0


def test_moderate_relevance_beats_pure_importance() -> None:
    high_but_irrelevant = _prefer(
        "pasta importance but unrelated content", importance=0.9
    )
    partial_match = _prefer("leadership reading goals", importance=0.4)
    _, retriever = _store_with(high_but_irrelevant, partial_match)
    hits = retriever.search("leadership goals planning")
    assert hits[0].memory.memory_id == partial_match.memory_id


def test_empty_query_returns_all_active_in_scope() -> None:
    a = _prefer("alpha topic", importance=0.2)
    b = _prefer("beta topic", importance=0.9)
    _, retriever = _store_with(a, b)
    hits = retriever.search()
    assert len(hits) == 2
    assert hits[0].memory.memory_id == b.memory_id  # importance ordering
    assert all(h.relevance == 0.0 for h in hits)


def test_non_empty_query_with_zero_overlap_excludes_memory() -> None:
    _, retriever = _store_with(_prefer("only about pineapples"))
    assert retriever.search("unrelated words here") == ()


def test_global_visible_without_scopes() -> None:
    memory = _make_memory(content="global fact", scope="global")
    _, retriever = _store_with(memory)
    assert retriever.search("global")[0].memory.memory_id == memory.memory_id


def test_project_memory_hidden_without_scope_filter() -> None:
    memory = _make_memory(
        content="project plan goals", scope="project", scope_id="proj-1"
    )
    _, retriever = _store_with(memory)
    assert retriever.search("project") == ()
    assert retriever.search("", scopes=(ScopeFilter("project", "proj-1"),))


def test_project_memory_requires_matching_scope_id() -> None:
    memory = _make_memory(
        content="secret project info", scope="project", scope_id="proj-1"
    )
    _, retriever = _store_with(memory)
    empty = retriever.search("", scopes=(ScopeFilter("project", "proj-other"),))
    assert empty == ()
    (hit,) = retriever.search("", scopes=(ScopeFilter("project", "proj-1"),))
    assert hit.memory.memory_id == memory.memory_id


def test_multiple_scopes_unioned() -> None:
    proj = _make_memory(content="project data", scope="project", scope_id="p1")
    agent = _make_memory(content="agent note", scope="agent", scope_id="a1")
    _, retriever = _store_with(proj, agent)
    scopes = (ScopeFilter("project", "p1"), ScopeFilter("agent", "a1"))
    assert {h.memory.memory_id for h in retriever.search("", scopes=scopes)} == {
        proj.memory_id,
        agent.memory_id,
    }


def test_expired_memory_excluded_unless_requested() -> None:
    memory = _make_memory(
        content="transient fact",
        expires_at="2026-01-01T00:00:00+00:00",
    )
    _, retriever = _store_with(memory)
    assert retriever.search("transient") == ()
    (hit,) = retriever.search("transient", include_expired=True)
    assert hit.memory.memory_id == memory.memory_id


def test_non_expired_future_memory_visible() -> None:
    memory = _make_memory(
        content="still valid",
        expires_at="2026-12-31T00:00:00+00:00",
    )
    _, retriever = _store_with(memory)
    assert retriever.search("valid")[0].memory.memory_id == memory.memory_id


def test_archived_and_deleted_never_retrieved() -> None:
    archived = _make_memory(content="archived secret", status="archived")
    deleted = _make_memory(content="deleted secret", status="deleted")
    _, retriever = _store_with(archived, deleted)
    assert retriever.search() == ()
    assert retriever.search("", include_expired=True) == ()


def test_scoperfilter_rejects_non_global_without_scope_id() -> None:
    with pytest.raises(MemorySearchError, match="scope_id"):
        ScopeFilter("project")


def test_limit_validation() -> None:
    _, retriever = _store_with()
    with pytest.raises(MemorySearchError):
        retriever.search("x", limit=0)


def test_limit_bounds_results() -> None:
    _, retriever = _store_with(_prefer("a one"), _prefer("b two"))
    assert len(retriever.search("", limit=1)) == 1


def test_recency_low_but_correct_ordering_when_all_tied() -> None:
    old = _make_memory(content="plain content", created_at="2026-01-01T00:00:00+00:00")
    new = _make_memory(content="plain content", created_at="2026-08-01T00:00:00+00:00")
    _, retriever = _store_with(old, new)
    hits = retriever.search("content")
    # recency is inside the linear formula; identical relevance => newer first
    assert hits[0].memory.memory_id == new.memory_id


def test_tie_break_by_created_at_then_id() -> None:
    id_a = "mem-aaaaaaaa"
    id_b = "mem-bbbbbbbb"
    m_a = Memory(
        memory_id=id_a,
        kind="preference",
        content="tie content",
        summary="",
        source_type="user",
        source_id="",
        scope="global",
        scope_id=None,
        confidence=0.5,
        importance=0.5,
        status="active",
        created_at="2026-08-30T00:00:00+00:00",
        updated_at="2026-08-30T00:00:00+00:00",
        last_accessed_at=None,
        expires_at=None,
    )
    m_b = Memory(
        memory_id=id_b,
        kind="preference",
        content="tie content",
        summary="",
        source_type="user",
        source_id="",
        scope="global",
        scope_id=None,
        confidence=0.5,
        importance=0.5,
        status="active",
        created_at="2026-08-30T00:00:00+00:00",
        updated_at="2026-08-30T00:00:00+00:00",
        last_accessed_at=None,
        expires_at=None,
    )
    _, retriever = _store_with(m_a, m_b)
    # identical created_at: memory_id ascending wins
    hits = retriever.search("tie")
    assert hits[0].memory.memory_id == id_a
    assert hits[1].memory.memory_id == id_b


def test_timestamped_hit_exposes_scores() -> None:
    memory = _prefer("leadership career growth", importance=0.7, confidence=0.8)
    _, retriever = _store_with(memory)
    (hit,) = retriever.search("leadership")
    assert hit.rank == 1
    assert 0.0 <= hit.score <= 1.0
    assert hit.to_dict()["memory"]["memory_id"] == memory.memory_id
    assert "score" in hit.to_dict()
    assert "rank" in hit.to_dict()


def test_parse_iso_roundtrip_targets() -> None:
    assert parse_iso("2026-08-30T00:00:00+00:00") is not None
