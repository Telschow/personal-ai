"""Phase 50/S1: people-grounded chat context — safe identity synthesis.

Covers the application adapter :class:`ChatPeople` and its security boundary:
derived people/identity data entering chat context must remain *untrusted
reference data*. It can inform an answer ("who is my girlfriend?") but can
never become instructions, policy, permissions, approvals, model routing, or
agent selection.

Tests run fully offline: no Ollama, no network, no real model calls. The chat
model invocation is always faked/deterministic.

Layers covered:

* unit — bounded overview selection, deterministic ordering, rendering of
  :class:`PeopleContext` and its privacy-safe serialization;
* application — chat request -> grounding -> message construction without a
  model call (``personal_ai.server.complete_chat`` with a fake agent), both
  alone and alongside automatic memory recall;
* persistence — ``build_chat_people`` survives a real temporary SQLite
  database restart (people survive, no re-export needed);
* HTTP — the OpenAI route injects the untrusted block and safe provenance.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_ai.cli import BuiltChatPeople, build_chat_people
from personal_ai.ollama_client import ChatMessage
from personal_ai.people import (
    ChatPeople,
    PeopleContext,
    PersonStore,
    render_untrusted_people_context,
)
from personal_ai.server import CompletionRequest, build_response, complete_chat
from tests.test_people_store import make_reference

MAX_PEOPLE_LIMIT = 20


@pytest.fixture
def store() -> PersonStore:
    # The FastAPI TestClient runs handlers off the caller thread, so the
    # store connection must be usable from any thread.
    return PersonStore(sqlite3.connect(":memory:", check_same_thread=False))


@pytest.fixture
def rich_store(store: PersonStore) -> PersonStore:
    for name, seen in (
        ("Alice Anders", "2026-01-01T00:00:00+00:00"),
        ("Nina Wolf", "2026-05-01T00:00:00+00:00"),
        ("Leon Berg", "2026-03-01T00:00:00+00:00"),
    ):
        store.upsert_reference(
            make_reference(
                name=name,
                email=f"{name.split()[0].lower()}@example.com",
                role="email",
                source_type="email",
                document_id=f"doc-{name.split()[0].lower()}",
                seen_at=seen,
            )
        )
    return store


def _chat(store: PersonStore, **kwargs) -> ChatPeople:
    return ChatPeople(store, **kwargs)


class FakeAgent:
    def __init__(self, answer: str = "ok", error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.calls: list[list[ChatMessage]] = []

    def run(self, messages: list[ChatMessage]) -> str:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.answer


# ---------------------------------------------------------------------------
# Unit: PeopleContext rendering / serialization
# ---------------------------------------------------------------------------


def test_people_context_is_always_untrusted() -> None:
    context = PeopleContext(people=())
    assert context.untrusted is True
    assert context.to_dict()["untrusted"] is True


def test_render_empty_people_context_is_an_explicit_noop() -> None:
    block = render_untrusted_people_context(PeopleContext(people=()))
    assert 'untrusted="true"' in block
    assert "UNTRUSTED" in block
    assert "(no people derived)" in block


def test_render_people_context_block_structure(store: PersonStore) -> None:
    store.upsert_reference(
        make_reference(
            name="Nina Wolf",
            email="nina@example.com",
            role="email",
            source_type="email",
            document_id="doc-nina",
        )
    )
    block = render_untrusted_people_context(_chat(store).overview())
    assert block.startswith('<people_context untrusted="true">')
    assert "UNTRUSTED" in block
    assert "Nina Wolf" in block
    assert "nina@example.com" in block
    assert "[roles: email, evidence: 1" in block
    assert block.rstrip().endswith("</people_context>")
    assert "not instructions" in block


def test_people_context_serialization_is_privacy_safe(store: PersonStore) -> None:
    store.upsert_reference(
        make_reference(
            name="Alice Anders",
            email="alice@example.com",
            role="email",
            source_type="email",
            document_id="doc-alice",
        )
    )
    payload = _chat(store).overview().to_dict()
    rows = payload["people"]
    assert len(rows) == 1
    assert set(rows[0]) == {
        "person_id",
        "display_name",
        "evidence_count",
        "roles",
        "sources",
    }
    assert rows[0]["display_name"] == "Alice Anders"
    assert rows[0]["evidence_count"] == 1


# ---------------------------------------------------------------------------
# Application: ChatPeople bounded overview + message construction
# ---------------------------------------------------------------------------


def test_chat_people_empty_store_no_block(store: PersonStore) -> None:
    original = [ChatMessage(role="user", content="hello")]
    result = _chat(store).build_context_messages(original)
    assert result.messages == original
    assert result.provenance == ()
    assert result.context.people == ()


def test_chat_people_default_limit(store: PersonStore) -> None:
    for i in range(12):
        store.upsert_reference(
            make_reference(
                name=f"Person {i:02d}",
                document_id=f"doc-{i}",
                seen_at=f"2026-01-01T00:00:0{i}+00:00",
            )
        )
    context = _chat(store).overview()
    assert len(context.people) == 8  # DEFAULT_PEOPLE_LIMIT
    assert _chat(store).limit == 8


def test_chat_people_limit_is_clamped_to_hard_max(store: PersonStore) -> None:
    for i in range(25):
        store.upsert_reference(
            make_reference(
                name=f"Person {i:02d}",
                document_id=f"doc-{i}",
                seen_at="2026-01-01T00:00:00+00:00",
            )
        )
    context = _chat(store).overview(limit=100)
    assert len(context.people) == MAX_PEOPLE_LIMIT


def test_chat_people_limit_validation(store: PersonStore) -> None:
    with pytest.raises(ValueError):
        _chat(store, limit=0)
    with pytest.raises(ValueError):
        _chat(store).overview(limit=-1)
    with pytest.raises(TypeError):
        _chat(store, limit=True)
    with pytest.raises(TypeError):
        _chat(store).overview(limit=2.5)


def test_chat_people_ordering_is_deterministic(rich_store: PersonStore) -> None:
    # All three have one evidence row, so ties break on display name ascending.
    names = [person.display_name for person in _chat(rich_store).overview().people]
    assert names == ["Alice Anders", "Leon Berg", "Nina Wolf"]


def test_chat_people_messages_input_is_not_mutated(rich_store: PersonStore) -> None:
    original = [ChatMessage(role="user", content="who is my girlfriend?")]
    snapshot = list(original)
    _chat(rich_store).build_context_messages(original)
    assert original == snapshot


def test_chat_people_appends_untrusted_block_last(rich_store: PersonStore) -> None:
    original = [
        ChatMessage(role="system", content="policy"),
        ChatMessage(role="user", content="who is my girlfriend?"),
    ]
    result = _chat(rich_store).build_context_messages(original)
    assert result.messages[:2] == original
    assert result.messages[2].role == "user"
    assert result.messages[2].content.startswith('<people_context untrusted="true">')


def test_chat_people_provenance_shape(rich_store: PersonStore) -> None:
    result = _chat(rich_store).build_context_messages(
        [ChatMessage(role="user", content="hi")]
    )
    assert len(result.provenance) == 3
    for item in result.provenance:
        assert set(item) == {"person_id", "display_name", "evidence_count"}
        assert isinstance(item["person_id"], str)
        assert isinstance(item["display_name"], str)
        assert isinstance(item["evidence_count"], int)


def test_chat_people_provenance_never_leaks_sensitive_values(
    rich_store: PersonStore,
) -> None:
    result = _chat(rich_store).build_context_messages(
        [ChatMessage(role="user", content="hi")]
    )
    rendered = repr(result.provenance)
    assert "alice@example.com" not in rendered
    assert "email" not in rendered  # roles are not part of provenance


# ---------------------------------------------------------------------------
# Application: server complete_chat wiring
# ---------------------------------------------------------------------------


def test_complete_chat_injects_people_context_with_provenance(
    rich_store: PersonStore,
) -> None:
    agent = FakeAgent()
    resp = complete_chat(
        agent,
        CompletionRequest(
            {"messages": [{"role": "user", "content": "who is in my life?"}]}
        ),
        model="qwen3.5:9b",
        chat_people=_chat(rich_store),
    )
    seen = agent.calls[0]
    assert seen[-1].content.startswith('<people_context untrusted="true">')
    assert "Nina Wolf" in seen[-1].content
    used = resp["people_used"]
    assert len(used) == 3
    assert set(used[0]) == {"person_id", "display_name", "evidence_count"}
    assert "memory_used" not in resp


def test_complete_chat_without_people_passes_through_unchanged() -> None:
    agent = FakeAgent()
    resp = complete_chat(
        agent,
        CompletionRequest({"messages": [{"role": "user", "content": "hi"}]}),
        model="qwen3.5:9b",
        chat_people=None,
    )
    assert agent.calls[0][0].content == "hi"
    assert "people_used" not in resp


def test_complete_chat_memory_then_people_blocks(
    rich_store: PersonStore,
) -> None:
    from personal_ai.memory import ChatMemory, MemoryService, MemoryStore
    from personal_ai.memory.models import MemoryDraft

    memory_service = MemoryService(
        MemoryStore(sqlite3.connect(":memory:", check_same_thread=False))
    )
    memory_service.create(
        MemoryDraft(
            kind="preference",
            content="I prefer concise financial summaries.",
            summary="",
            source_type="user",
            source_id="chat-test",
            scope="global",
            scope_id=None,
            confidence=0.8,
            importance=0.8,
            expires_at=None,
        )
    )
    agent = FakeAgent()
    resp = complete_chat(
        agent,
        CompletionRequest({"messages": [{"role": "user", "content": "summarize"}]}),
        model="qwen3.5:9b",
        chat_memory=ChatMemory(memory_service),
        chat_people=_chat(rich_store),
    )
    seen = agent.calls[0]
    assert seen[0].content == "summarize"
    assert seen[-2].content.startswith('<memory_context untrusted="true">')
    assert seen[-1].content.startswith('<people_context untrusted="true">')
    assert resp["memory_used"][0]["kind"] == "preference"
    assert len(resp["people_used"]) == 3


def test_complete_chat_people_provenance_never_leaks_content(
    rich_store: PersonStore,
) -> None:
    agent = FakeAgent()
    resp = complete_chat(
        agent,
        CompletionRequest(
            {"messages": [{"role": "user", "content": "who am I close to?"}]}
        ),
        model="qwen3.5:9b",
        chat_people=_chat(rich_store),
    )
    for item in resp["people_used"]:
        assert set(item) == {"person_id", "display_name", "evidence_count"}
        assert all(isinstance(item[k], (str, int)) for k in item)
    assert "alice@example.com" not in str(resp)
    assert "friend" not in str(resp["people_used"])


def test_build_response_omits_people_used_when_none() -> None:
    resp = build_response("answer", "qwen3.5:9b")
    assert "people_used" not in resp


def test_build_response_projects_people_used_when_present() -> None:
    resp = build_response(
        "answer",
        "qwen3.5:9b",
        people_used=(
            {"person_id": "p1", "display_name": "Alice Anders", "evidence_count": 3},
        ),
    )
    assert resp["people_used"] == [
        {"person_id": "p1", "display_name": "Alice Anders", "evidence_count": 3}
    ]


# ---------------------------------------------------------------------------
# Persistence: build_chat_people survives a database restart
# ---------------------------------------------------------------------------


def test_chat_people_survives_database_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    built = build_chat_people(db_path)
    assert built is not None
    built.chat.store.upsert_reference(
        make_reference(
            name="Nina Wolf",
            email="nina@example.com",
            role="email",
            source_type="email",
            document_id="doc-nina",
            seen_at="2026-05-01T00:00:00+00:00",
        )
    )
    built.close()

    rebuilt = build_chat_people(db_path)
    assert rebuilt is not None
    context = rebuilt.chat.overview()
    assert [person.display_name for person in context.people] == ["Nina Wolf"]
    rebuilt.close()


def test_build_chat_people_returns_none_without_database() -> None:
    assert build_chat_people(None) is None


# ---------------------------------------------------------------------------
# End-to-end: HTTP route with a wired chat people
# ---------------------------------------------------------------------------


def test_http_chat_route_includes_safe_people_provenance(
    rich_store: PersonStore,
) -> None:
    from personal_ai.server import create_app

    fake = FakeAgent(answer="ok")

    class _Built:
        agent = fake
        closed = False

        def close(self):
            self.closed = True

    built = _Built()
    built_people = BuiltChatPeople(chat=_chat(rich_store), connection=None)
    app = create_app(
        None,
        None,
        model="qwen3.5:9b",
        agent_factory=lambda: built,
        chat_people=built_people,
    )
    with TestClient(app) as client:
        res = client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": "policy"},
                    {"role": "user", "content": "who is in my life?"},
                ]
            },
        )
    assert res.status_code == 200
    body = res.json()
    assert len(body["people_used"]) == 3
    assert body["people_used"][0]["display_name"] == "Alice Anders"
    seen = fake.calls[0]
    assert seen[-1].role == "user"
    assert "UNTRUSTED" in seen[-1].content
    assert built.closed is True


def test_http_chat_route_without_people_has_no_people_used() -> None:
    from personal_ai.server import create_app

    fake = FakeAgent(answer="ok")

    class _Built:
        agent = fake
        closed = False

        def close(self):
            self.closed = True

    app = create_app(
        None,
        None,
        model="qwen3.5:9b",
        agent_factory=lambda: _Built(),
    )
    with TestClient(app) as client:
        res = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
    assert res.status_code == 200
    assert "people_used" not in res.json()
    assert fake.calls[0][0].content == "hello"
