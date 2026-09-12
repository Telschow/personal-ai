"""Phase 50 — interactive chat access to search_people/get_person.

The people/identity tools are exposed to the chat ToolRegistry behind the new
``people.read`` permission. Registration is gated on a non-empty person store
so an empty DB never surfaces people tools; handlers run entirely through the
policy engine impersonating the researcher, with the same denial-before-read
invariants as the agent layer.

Everything is offline: hermetic SQLite person store, no Ollama, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_ai.agents.defs import (
    CURATOR,
    ENGINEER,
    ORCHESTRATOR,
    RESEARCHER,
    REVIEWER,
    build_default_agent_registry,
)
from personal_ai.agents.models import Permission, PolicyDecision
from personal_ai.agents.policy import PolicyDenialError, PolicyEngine
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import build_default_agent_tools
from personal_ai.people.models import PersonReference, normalize_identity, person_id_for
from personal_ai.people.store import PersonStore
from personal_ai.storage.documents import connect_database
from personal_ai.tools import ToolRegistry, create_default_registry
from personal_ai.tools.people import build_policy_gated_people_handler


def _seed_people(person_store: PersonStore) -> None:
    person_store.upsert_reference(
        PersonReference(
            document_id="doc-email-1",
            name="Marta García",
            email="marta@example.com",
            role="email",
            source_type="email",
            seen_at="2026-01-05T10:00:00+00:00",
        )
    )
    person_store.upsert_reference(
        PersonReference(
            document_id="doc-fin-1",
            name="Max Mustermann",
            email="",
            role="financial",
            source_type="financial",
            seen_at="2026-03-01T00:00:00+00:00",
        )
    )


def _schema_names(registry: ToolRegistry) -> set[str]:
    return {schema["function"]["name"] for schema in registry.schemas()}


@pytest.fixture
def person_store(tmp_path: Path) -> PersonStore:
    store = PersonStore(connect_database(tmp_path / "people.sqlite"))
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def empty_person_store(tmp_path: Path) -> PersonStore:
    store = PersonStore(connect_database(tmp_path / "people-empty.sqlite"))
    try:
        yield store
    finally:
        store.close()


class _RecordingPeople:
    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def search(
        self, query: str, *, limit: int = 20, offset: int = 0, role: str | None = None
    ):
        self.calls.append("people.search")
        return self._inner.search(query, limit=limit, offset=offset, role=role)

    def get(self, person_id: str):
        self.calls.append("people.get")
        return self._inner.get(person_id)

    def evidence_for(self, person_id: str, *, limit: int = 50, offset: int = 0):
        self.calls.append("people.evidence_for")
        return self._inner.evidence_for(person_id, limit=limit, offset=offset)


# =====================================================================
# Chat-gated registration
# =====================================================================


def test_chat_registry_registers_people_tools_only_with_people(
    person_store: PersonStore,
    empty_person_store: PersonStore,
    tmp_path: Path,
) -> None:
    _seed_people(person_store)
    with_people = create_default_registry(tmp_path, person_store=person_store)
    names = _schema_names(with_people)
    assert "search_people" in names
    assert "get_person" in names

    empty = create_default_registry(tmp_path, person_store=empty_person_store)
    assert "search_people" not in _schema_names(empty)
    assert "get_person" not in _schema_names(empty)

    bare = create_default_registry(tmp_path)
    assert "search_people" not in _schema_names(bare)


def test_chat_registry_people_tool_schemas(
    person_store: PersonStore, tmp_path: Path
) -> None:
    _seed_people(person_store)
    registry = create_default_registry(tmp_path, person_store=person_store)
    schemas = {
        schema["function"]["name"]: schema["function"] for schema in registry.schemas()
    }
    search = schemas["search_people"]
    assert search["parameters"]["required"] == ["query"]
    assert search["parameters"]["properties"]["query"]["type"] == "string"
    assert search["parameters"]["properties"]["limit"]["type"] == "integer"
    person = schemas["get_person"]
    assert person["parameters"]["required"] == ["person_id"]
    assert person["parameters"]["properties"]["evidence_limit"]["type"] == "integer"


# =====================================================================
# End-to-end through the chat registry
# =====================================================================


def test_chat_registry_search_people_end_to_end(
    person_store: PersonStore, tmp_path: Path
) -> None:
    _seed_people(person_store)
    registry = create_default_registry(tmp_path, person_store=person_store)
    data = registry.execute("search_people", {"query": "marta"})
    assert isinstance(data, dict)
    assert data["count"] == 1
    assert data["people"][0]["display_name"] == "Marta García"
    assert data["people"][0]["emails"] == ["marta@example.com"]
    assert data["people"][0]["roles"] == ["email"]


def test_chat_registry_get_person_end_to_end(
    person_store: PersonStore, tmp_path: Path
) -> None:
    _seed_people(person_store)
    registry = create_default_registry(tmp_path, person_store=person_store)
    person_id = person_id_for(normalize_identity("Max Mustermann"))
    data = registry.execute("get_person", {"person_id": person_id})
    assert isinstance(data, dict)
    assert data["status"] == "ok"
    assert data["person"]["display_name"] == "Max Mustermann"
    assert data["person"]["sources"] == ["financial"]
    assert data["person"]["evidence_count"] == 1
    assert data["evidence"][0]["role"] == "financial"


def test_chat_registry_not_found(person_store: PersonStore, tmp_path: Path) -> None:
    _seed_people(person_store)
    registry = create_default_registry(tmp_path, person_store=person_store)
    assert registry.execute("get_person", {"person_id": "missing"}) == {
        "status": "not_found",
        "person_id": "missing",
    }


def test_chat_registry_people_deterministic(
    person_store: PersonStore, tmp_path: Path
) -> None:
    _seed_people(person_store)
    registry = create_default_registry(tmp_path, person_store=person_store)
    first = registry.execute("search_people", {"query": "marta"})
    second = registry.execute("search_people", {"query": "marta"})
    assert first == second
    assert first["count"] == 1


def test_chat_registry_people_unknown_parameters_ignored(
    person_store: PersonStore, tmp_path: Path
) -> None:
    _seed_people(person_store)
    registry = create_default_registry(tmp_path, person_store=person_store)
    data = registry.execute(
        "search_people",
        {"query": "marta", "approve": True, "sql": "DROP TABLE people", "write": True},
    )
    assert data["count"] == 1
    person_id = person_id_for(normalize_identity("Marta García"))
    data = registry.execute(
        "get_person",
        {
            "person_id": person_id,
            "approve": True,
            "apply_candidate": True,
            "curate": "run",
        },
    )
    assert data["status"] == "ok"


# =====================================================================
# Policy gating through the chat handler
# =====================================================================


def test_chat_handler_runs_search_under_researcher(person_store: PersonStore) -> None:
    _seed_people(person_store)
    handler = build_policy_gated_people_handler(person_store)
    data = handler.search_people({"query": "max"})
    assert isinstance(data, dict)
    assert data["count"] == 1
    assert data["people"][0]["display_name"] == "Max Mustermann"


def test_chat_handler_runs_get_person_under_researcher(
    person_store: PersonStore,
) -> None:
    _seed_people(person_store)
    handler = build_policy_gated_people_handler(person_store)
    person_id = person_id_for(normalize_identity("Marta García"))
    data = handler.get_person({"person_id": person_id})
    assert isinstance(data, dict)
    assert data["status"] == "ok"
    assert data["person"]["emails"] == ["marta@example.com"]


def test_chat_denied_search_never_touches_store(person_store: PersonStore) -> None:
    _seed_people(person_store)
    people = _RecordingPeople(person_store)
    policy = _boss_policy_with_people(people)
    check = policy.check_tool(CURATOR, "search_people")
    assert check.decision is PolicyDecision.DENIED
    assert check.permission is Permission.PEOPLE_READ
    with pytest.raises(PolicyDenialError, match="people.read"):
        policy.execute(CURATOR, "search_people", {"query": "marta"})
    assert people.calls == []


def test_chat_denied_get_person_never_touches_store(person_store: PersonStore) -> None:
    _seed_people(person_store)
    for agent in (ENGINEER, ORCHESTRATOR, REVIEWER):
        people = _RecordingPeople(person_store)
        policy = _boss_policy_with_people(people)
        check = policy.check_tool(agent, "get_person")
        assert check.decision is PolicyDecision.DENIED, agent.id
        assert check.permission is Permission.PEOPLE_READ
        with pytest.raises(PolicyDenialError, match="people.read"):
            policy.execute(agent, "get_person", {"person_id": "any"})
        assert people.calls == [], f"{agent.id} reached the people store"


def test_chat_researcher_authorized_for_people_tools() -> None:
    policy = _boss_policy_with_people(object())
    for tool in ("search_people", "get_person"):
        assert policy.check_tool(RESEARCHER, tool).decision is PolicyDecision.ALLOWED


def test_chat_people_permission_is_domain_scoped() -> None:
    existing = {permission.value for permission in Permission}
    assert "people.read" in existing
    assert Permission.PEOPLE_READ is not Permission.CORPUS_SEARCH
    assert Permission.PEOPLE_READ is not Permission.MEMORY_READ


# =====================================================================
# No-write regression + privacy
# =====================================================================


def test_chat_registry_people_writes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "people-no-write.sqlite"
    store = PersonStore(connect_database(db))
    try:
        _seed_people(store)
        before_bytes = db.read_bytes()
        before_count = store.count()
        registry = create_default_registry(tmp_path, person_store=store)
        for _ in range(2):
            assert registry.execute("search_people", {"query": "marta"})["count"] == 1
            assert (
                registry.execute("get_person", {"person_id": "missing"})["status"]
                == "not_found"
            )
        assert db.read_bytes() == before_bytes
        assert store.count() == before_count
    finally:
        store.close()


def test_chat_registry_people_privacy(
    person_store: PersonStore, tmp_path: Path
) -> None:
    _seed_people(person_store)
    registry = create_default_registry(tmp_path, person_store=person_store)
    data = registry.execute("search_people", {"query": "marta"})
    blob = json.dumps(data, default=str)
    for marker in (
        "candidate_json",
        "statement_hash",
        "prompt",
        "model_output",
        "review_note",
        "p455w0rd",
        "subject",
        "body",
    ):
        assert marker not in blob, f"privacy marker leaked: {marker}"


def _boss_policy_with_people(people: object) -> PolicyEngine:
    return PolicyEngine(
        build_default_agent_tools(person_store=people),
        build_default_agent_registry(),
        build_default_skill_registry(),
    )
