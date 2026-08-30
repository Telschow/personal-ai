"""Tests for mounting the memory layer on the execution control plane.

Verifies both states: (a) ``ControlPlane`` without a ``memory`` service — the
execution runtime works normally and every ``memory_*`` call raises
:class:`MemoryNotConfiguredError`; (b) ``ControlPlane`` with a ``memory``
service — the full CRUD/search/lifecycle surface works over the plane.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personal_ai.agents.defs import build_default_agent_registry
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import build_default_agent_tools
from personal_ai.execution import ControlPlane, open_orchestration_store
from personal_ai.execution.models import PlanStatus
from personal_ai.memory import (
    MemoryDraft,
    MemoryNotConfiguredError,
    MemoryNotFoundError,
    MemoryStatus,
    ScopeFilter,
)
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryStore
from personal_ai.retrieval import RetrievalService
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentStore,
    ExtractionStore,
    connect_database,
)


def _memory_plane(
    tmp_path: Path, *, with_memory: bool
) -> tuple[ControlPlane, MemoryService | None]:
    conn, store = open_orchestration_store(tmp_path / "cp.db")
    memory = MemoryService(MemoryStore(conn)) if with_memory else None
    corpus = connect_database(tmp_path / "corpus.db")
    retrieval_service = RetrievalService(
        ChunkStore(corpus),
        ExtractionStore(corpus),
        DocumentStore(corpus),
        ConversationStore(corpus),
    )
    cp = ControlPlane(
        store,
        agents=build_default_agent_registry(),
        skills=build_default_skill_registry(),
        tools=build_default_agent_tools(
            retrieval_service=retrieval_service, workspace=tmp_path / "ws"
        ),
        memory=memory,
    )
    return cp, memory


def test_without_memory_execution_works_and_memory_raises(tmp_path: Path) -> None:
    cp, _ = _memory_plane(tmp_path, with_memory=False)

    plan = cp.create_execution("career goals")
    cp.run_execution(plan.plan_id)
    assert cp.get_execution(plan.plan_id).status is not PlanStatus.PLANNED
    assert cp.events(plan.plan_id)

    with pytest.raises(MemoryNotConfiguredError):
        cp.memory_create_user("I like coffee.")
    with pytest.raises(MemoryNotConfiguredError):
        cp.memory_list()
    with pytest.raises(MemoryNotConfiguredError):
        cp.memory_search("coffee")
    with pytest.raises(MemoryNotConfiguredError):
        cp.memory_archive("mem-x")
    with pytest.raises(MemoryNotConfiguredError):
        cp.memory_service  # noqa: B018


def test_with_memory_full_surface(tmp_path: Path) -> None:
    cp, memory = _memory_plane(tmp_path, with_memory=True)
    assert memory is not None

    created = cp.memory_create_user(
        "Prefers early-morning focus sessions.",
        summary="work habits",
        confidence=0.8,
        importance=0.6,
    )
    assert created.memory_id.startswith("mem-")
    assert cp.memory_get(created.memory_id) == created

    (hit,) = cp.memory_search("focus", limit=5)
    assert hit.memory.memory_id == created.memory_id

    updated = cp.memory_update(created.memory_id, importance=0.9)
    assert updated.importance == 0.9

    assert {m.memory_id for m in cp.memory_list()} == {created.memory_id}
    assert cp.memory_events(created.memory_id)

    archived = cp.memory_archive(created.memory_id)
    assert archived.status is MemoryStatus.ARCHIVED
    assert cp.memory_search("focus") == ()

    # undelete path is out of scope: archive keeps provenance, list shows it
    assert [m.status for m in cp.memory_list(MemoryStatus.ARCHIVED)] == [
        MemoryStatus.ARCHIVED
    ]

    recreated = cp.memory_create_user("New fact after archive.")
    cp.memory_delete(recreated.memory_id)
    assert cp.memory_get(recreated.memory_id).status is MemoryStatus.DELETED

    purged = cp.memory_create_user("Temporary secret.")
    cp.memory_purge(purged.memory_id)
    with pytest.raises(MemoryNotFoundError):
        cp.memory_get(purged.memory_id)


def test_memory_scoped_search_via_plane(tmp_path: Path) -> None:
    cp, _ = _memory_plane(tmp_path, with_memory=True)
    cp.memory_create_user(
        "Quarterly project roadmap.",
        scope="project",
        scope_id="proj-7",
    )
    assert cp.memory_search("roadmap") == ()
    (hit,) = cp.memory_search("roadmap", scopes=(ScopeFilter("project", "proj-7"),))
    assert hit.memory.scope.value == "project"


def test_memory_service_requires_explicit_provenance() -> None:
    service = MemoryService(MemoryStore(__import__("sqlite3").connect(":memory:")))
    with pytest.raises(TypeError):
        service.create(MemoryDraft(content="no kind or source"))  # type: ignore[call-arg]


def test_memory_option_is_backward_compatible_slot() -> None:
    import inspect

    signature = inspect.signature(ControlPlane.__init__)
    assert "memory" in signature.parameters
    assert signature.parameters["memory"].default is None
