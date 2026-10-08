"""Phase 50 — agent-layer people tools: profiles, registration, validation.

The read-only ``search_people`` and ``get_person`` tools are granted under a
new ``people.read`` permission, registered only when a person store is wired,
and consumed by the researcher agent's least-privilege policy. Everything is
offline (fake store, `tmp_path` SQLite, no Ollama, no network).
"""

from __future__ import annotations

import pytest

from personal_ai.agents.defs import (
    CURATOR,
    ENGINEER,
    ORCHESTRATOR,
    RESEARCHER,
    REVIEWER,
    build_default_agent_registry,
)
from personal_ai.agents.models import Permission, PolicyDecision, RiskLevel
from personal_ai.agents.policy import PolicyDenialError, PolicyEngine
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import (
    GET_PERSON,
    SEARCH_PEOPLE,
    build_default_agent_tools,
)
from personal_ai.people.models import (
    Person,
    PersonEvidence,
    normalize_identity,
    person_id_for,
)


class _FakePeople:
    def __init__(self) -> None:
        self.search_calls: list[str] = []
        self.get_calls: list[str] = []
        self.evidence_calls: list[str] = []
        self._people = {
            person_id_for(normalize_identity("Alice Anders")): Person(
                person_id=person_id_for(normalize_identity("Alice Anders")),
                identity=normalize_identity("Alice Anders"),
                display_name="Alice Anders",
                emails=("alice@example.com",),
                roles=("email",),
                sources=("email",),
                first_seen_at="2026-01-01T00:00:00+00:00",
                last_seen_at="2026-06-01T00:00:00+00:00",
                evidence_count=2,
            )
        }

    def search(
        self, query: str, *, limit: int = 20, offset: int = 0, role: str | None = None
    ):
        self.search_calls.append(query)
        return [self._people[person_id_for(normalize_identity("Alice Anders"))]]

    def get(self, person_id: str):
        self.get_calls.append(person_id)
        return self._people.get(person_id)

    def evidence_for(self, person_id: str, *, limit: int = 50, offset: int = 0):
        self.evidence_calls.append(person_id)
        return [
            PersonEvidence(
                person_id=person_id,
                document_id="doc-1",
                name="Alice Anders",
                email="alice@example.com",
                role="email",
                source_type="email",
                seen_at="2026-01-01T00:00:00+00:00",
            )
        ]


def _policy(person_store: object) -> PolicyEngine:
    return PolicyEngine(
        build_default_agent_tools(person_store=person_store),
        build_default_agent_registry(),
        build_default_skill_registry(),
    )


# =====================================================================
# Tool profiles
# =====================================================================


def test_people_tool_profiles_are_read_only() -> None:
    for tool in (SEARCH_PEOPLE, GET_PERSON):
        assert tool.risk is RiskLevel.READ
        assert tool.mutates_state is False
        assert tool.accesses_network is False
        assert tool.reads_private_data is True
        assert tool.deterministic is True
        assert tool.permissions == (Permission.PEOPLE_READ,)


def test_people_permission_exists() -> None:
    assert Permission.PEOPLE_READ.value == "people.read"


def test_agent_tools_registered_only_with_person_store() -> None:
    assert build_default_agent_tools().names() == ()
    tools = build_default_agent_tools(person_store=_FakePeople())
    assert {"search_people", "get_person"} <= set(tools.names())


# =====================================================================
# Researcher authorization / denial
# =====================================================================


def test_researcher_authorized_for_both_people_tools() -> None:
    policy = _policy(_FakePeople())
    for tool in ("search_people", "get_person"):
        assert policy.check_tool(RESEARCHER, tool).decision is PolicyDecision.ALLOWED


def test_researcher_profile_includes_people_tools_and_permission() -> None:
    assert "search_people" in RESEARCHER.tools
    assert "get_person" in RESEARCHER.tools
    assert "people-research" in RESEARCHER.skills
    assert Permission.PEOPLE_READ in RESEARCHER.permissions
    assert Permission.PEOPLE_READ in RESEARCHER.policy.allowed


def test_denied_agents_never_touch_person_store() -> None:
    for agent in (CURATOR, ENGINEER, ORCHESTRATOR, REVIEWER):
        store = _FakePeople()
        policy = _policy(store)
        for tool in ("search_people", "get_person"):
            check = policy.check_tool(agent, tool)
            assert check.decision is PolicyDecision.DENIED, (agent.id, tool)
            assert check.permission is Permission.PEOPLE_READ
            with pytest.raises(PolicyDenialError, match="people.read"):
                policy.execute(agent, tool, {"query": "x", "person_id": "x"})
            assert store.search_calls == []
            assert store.get_calls == []
            assert store.evidence_calls == []


def test_skills_supply_people_permission() -> None:
    # With no people policy flag, the skill's required permission is merged into
    # the effective permission set by the policy engine for the researcher.
    policy = PolicyEngine(
        build_default_agent_tools(
            person_store=object(),
        ),
        build_default_agent_registry(),
        build_default_skill_registry(),
    )
    assert (
        policy.check_tool(RESEARCHER, "search_people").decision
        is PolicyDecision.ALLOWED
    )


# =====================================================================
# Handler behavior
# =====================================================================


def test_search_handler_returns_stable_projection() -> None:
    store = _FakePeople()
    registry = build_default_agent_tools(person_store=store)
    policy = PolicyEngine(
        registry, build_default_agent_registry(), build_default_skill_registry()
    )
    data = policy.execute(RESEARCHER, "search_people", {"query": "alice"})
    assert isinstance(data, dict)
    assert set(data) == {"count", "people"}
    assert data["count"] == 1
    person = data["people"][0]
    assert set(person) == {
        "person_id",
        "display_name",
        "emails",
        "roles",
        "sources",
        "evidence_count",
        "first_seen_at",
        "last_seen_at",
    }
    assert data["people"][0]["display_name"] == "Alice Anders"
    assert store.search_calls == ["alice"]


def test_search_handler_requires_non_empty_query() -> None:
    policy = _policy(_FakePeople())
    with pytest.raises(TypeError):
        policy.execute(RESEARCHER, "search_people", {"query": 5})
    with pytest.raises(ValueError, match="must not be empty"):
        policy.execute(RESEARCHER, "search_people", {"query": "   "})


def test_search_handler_bounds_limit_and_rejects_bad_limit() -> None:
    store = _FakePeople()
    policy = PolicyEngine(
        build_default_agent_tools(person_store=store),
        build_default_agent_registry(),
        build_default_skill_registry(),
    )
    data = policy.execute(
        RESEARCHER, "search_people", {"query": "alice", "limit": 999999}
    )
    assert data["count"] == 1
    with pytest.raises(ValueError, match="limit must be >= 1"):
        policy.execute(RESEARCHER, "search_people", {"query": "alice", "limit": 0})
    with pytest.raises(TypeError, match="limit must be an integer"):
        policy.execute(RESEARCHER, "search_people", {"query": "alice", "limit": True})


def test_search_handler_rejects_overlong_query() -> None:
    policy = _policy(_FakePeople())
    with pytest.raises(ValueError, match="must not exceed"):
        policy.execute(RESEARCHER, "search_people", {"query": "x" * 500})


def test_get_handler_not_found_for_unknown_id() -> None:
    policy = _policy(_FakePeople())
    data = policy.execute(RESEARCHER, "get_person", {"person_id": "missing"})
    assert data == {"status": "not_found", "person_id": "missing"}


def test_get_handler_returns_person_and_bounded_evidence() -> None:
    store = _FakePeople()
    policy = PolicyEngine(
        build_default_agent_tools(person_store=store),
        build_default_agent_registry(),
        build_default_skill_registry(),
    )
    person_id = person_id_for(normalize_identity("Alice Anders"))
    data = policy.execute(RESEARCHER, "get_person", {"person_id": person_id})
    assert isinstance(data, dict)
    assert set(data) == {"status", "person", "evidence"}
    assert data["status"] == "ok"
    row = data["evidence"][0]
    assert set(row) == {
        "document_id",
        "name",
        "email",
        "role",
        "source_type",
        "seen_at",
    }
    assert store.get_calls == [person_id]
    assert store.evidence_calls == [person_id]


def test_get_handler_requires_person_id_and_bounds_evidence() -> None:
    store = _FakePeople()
    policy = PolicyEngine(
        build_default_agent_tools(person_store=store),
        build_default_agent_registry(),
        build_default_skill_registry(),
    )
    person_id = person_id_for(normalize_identity("Alice Anders"))
    with pytest.raises(TypeError):
        policy.execute(RESEARCHER, "get_person", {})
    with pytest.raises(ValueError, match="evidence_limit must be >= 1"):
        policy.execute(
            RESEARCHER, "get_person", {"person_id": person_id, "evidence_limit": 0}
        )
    with pytest.raises(TypeError, match="evidence_limit must be an integer"):
        policy.execute(
            RESEARCHER, "get_person", {"person_id": person_id, "evidence_limit": True}
        )


def test_unknown_parameters_ignored_not_honored() -> None:
    store = _FakePeople()
    policy = _policy(store)
    person_id = person_id_for(normalize_identity("Alice Anders"))
    data = policy.execute(
        RESEARCHER,
        "search_people",
        {"query": "alice", "approve": True, "sql": "DROP TABLE people", "write": True},
    )
    assert data["count"] == 1
    data = policy.execute(
        RESEARCHER,
        "get_person",
        {
            "person_id": person_id,
            "approve": True,
            "apply_candidate": True,
            "review_id": "1",
            "curate": "run",
        },
    )
    assert data["status"] == "ok"


def test_results_carry_no_message_content() -> None:
    store = _FakePeople()
    policy = _policy(store)
    data = policy.execute(RESEARCHER, "search_people", {"query": "alice"})
    blob = str(data)
    for marker in (
        "subject",
        "body",
        "candidate_json",
        "statement_hash",
        "prompt",
        "secret",
    ):
        assert marker not in blob, f"privacy marker leaked: {marker}"
