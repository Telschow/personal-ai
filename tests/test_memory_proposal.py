"""Tests for the policy-gated ``propose_memory`` chat tool and identity recall.

This is the durable long-term-memory milestone slice: the user-controlled way
to write memory from chat (CLI ``memory add`` already exists in the execution
CLI; this covers the chat proposal flow). Everything here is offline — no
Ollama, no network — using a real temporary SQLite :class:`MemoryStore`, a
real :class:`PolicyEngine`, and a scripted chat client.

Invariants under test:

* memory writes are default-deny: without an approver no proposal ever runs;
* approval is the single gate: granted -> the write lands with user
  provenance and an audit event; declined -> nothing is written and the agent
  observes an approval error;
* the chat tool registry exposes ``propose_memory`` only when an approver is
  configured, so default chat builds stay write-free;
* identity recall: with a stored identity memory, "Who am I?" gets the
  memory as untrusted context; without one, nothing is injected and the model
  must not be handed an invented identity;
* the personal-context overview reflects memory counts/kind breakdown and
  never leaks memory content.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from personal_ai.agent import Agent
from personal_ai.agents.defs import (
    RESEARCHER,
    build_default_agent_registry,
)
from personal_ai.agents.models import Permission
from personal_ai.agents.policy import (
    ApprovalRequiredError,
    PolicyDenialError,
    PolicyEngine,
)
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import (
    PROPOSE_MEMORY,
    AgentToolRegistry,
    _memory_write_handler,
)
from personal_ai.memory import (
    ChatMemory,
    MemoryService,
    MemorySourceType,
    MemoryStore,
)
from personal_ai.ollama_client import ChatMessage, ChatResponse, ToolCall
from personal_ai.tools.defaults import create_default_registry
from personal_ai.tools.memory import build_policy_gated_memory_proposal_handler
from personal_ai.tools.personal_context import PersonalContextService


@pytest.fixture
def service() -> MemoryService:
    """A real in-memory memory store behind the real service boundary."""
    return MemoryService(
        MemoryStore(sqlite3.connect(":memory:", check_same_thread=False))
    )


def _handler(service: MemoryService, approver: object | None):
    return build_policy_gated_memory_proposal_handler(service, approver=approver)


def _always(*_args: object) -> bool:
    return True


def _never(*_args: object) -> bool:
    return False


class _RecordingApprover:
    """Records the gate tuples the policy engine consulted per request."""

    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, agent_id: str, tool_name: str, permission: str) -> bool:
        self.calls.append((agent_id, tool_name, permission))
        return self.result


# ---- permission model ----


def test_memory_write_permission_exists() -> None:
    assert Permission.MEMORY_WRITE.value == "memory.write"
    assert Permission.MEMORY_WRITE is not Permission.MEMORY_READ
    assert Permission.MEMORY_READ.value == "memory.read"


# ---- policy gating on the handler ----


def test_proposal_without_approver_is_never_written(service: MemoryService) -> None:
    handle = _handler(service, approver=None)
    with pytest.raises(ApprovalRequiredError):
        handle({"content": "Preferred name is Atlas."})
    assert service.counts()["active"] == 0


def test_proposal_denied_is_never_written(service: MemoryService) -> None:
    handle = _handler(service, approver=_never)
    with pytest.raises(ApprovalRequiredError):
        handle({"content": "Preferred name is Atlas."})
    assert service.counts()["active"] == 0


def test_proposal_approved_writes_with_user_provenance(service: MemoryService) -> None:
    approver = _RecordingApprover(result=True)
    handle = _handler(service, approver=approver)
    result = handle({"content": "Preferred name is Atlas.", "kind": "fact"})

    assert result["status"] == "created"
    assert result["kind"] == "fact"
    assert result["scope"] == "global"
    assert approver.calls == [("curator", "propose_memory", "memory.write")]

    memory = service.get(result["memory_id"])
    assert memory.content == "Preferred name is Atlas."
    assert memory.source_type is MemorySourceType.USER
    assert memory.scope.value == "global"
    assert memory.scope_id is None
    events = service.events(memory.memory_id)
    assert any(event["event_type"] == "memory.created" for event in events)


def test_proposal_read_only_researcher_cannot_write(service: MemoryService) -> None:
    tools = AgentToolRegistry()
    tools.register(PROPOSE_MEMORY, _memory_write_handler(service))
    policy = PolicyEngine(
        tools,
        build_default_agent_registry(),
        build_default_skill_registry(),
        approver=_always,
    )
    with pytest.raises(PolicyDenialError):
        policy.execute(RESEARCHER, "propose_memory", {"content": "x"})


def test_proposal_rejects_blank_content(service: MemoryService) -> None:
    handle = _handler(service, approver=_always)
    with pytest.raises(TypeError):
        handle({"content": "   "})


def test_proposal_rejects_out_of_range_metadata(service: MemoryService) -> None:
    handle = _handler(service, approver=_always)
    with pytest.raises(ValueError):
        handle({"content": "valid", "confidence": 1.5})


# ---- chat tool registry surface ----


def test_default_registry_stays_write_free_without_approver(
    service: MemoryService, tmp_path: Path
) -> None:
    registry = create_default_registry(
        tmp_path,
        memory_service=service,
        memory_proposal_approver=None,
        personal_context_service=PersonalContextService(memory=service),
    )
    names = [tool["function"]["name"] for tool in registry.schemas()]
    assert "propose_memory" not in names
    assert "personal_context" in names  # read-only surface still wired


def test_default_registry_registers_propose_only_with_approver(
    service: MemoryService, tmp_path: Path
) -> None:
    registry = create_default_registry(
        tmp_path,
        memory_service=service,
        memory_proposal_approver=_always,
    )
    names = [tool["function"]["name"] for tool in registry.schemas()]
    assert "propose_memory" in names
    propose = next(
        tool
        for tool in registry.schemas()
        if tool["function"]["name"] == "propose_memory"
    )
    assert "content" in propose["function"]["parameters"]["required"]


def test_default_registry_without_memory_service_has_no_propose_tool(
    tmp_path: Path,
) -> None:
    registry = create_default_registry(tmp_path, memory_proposal_approver=_always)
    names = [tool["function"]["name"] for tool in registry.schemas()]
    assert "propose_memory" not in names


def test_tool_registry_execution_writes_on_approved_call(
    service: MemoryService, tmp_path: Path
) -> None:
    registry = create_default_registry(
        tmp_path,
        memory_service=service,
        memory_proposal_approver=_always,
    )
    registry.execute(
        "propose_memory",
        {"content": "Prefers espresso over tea.", "kind": "preference"},
    )
    assert service.counts()["active"] == 1


# ---- agent loop end to end (offline, scripted model) ----


class _ScriptedClient:
    def __init__(self, *responses: ChatResponse) -> None:
        self._responses = iter(responses)
        self.calls: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, object]] | None = None,
    ) -> ChatResponse:
        del tools
        self.calls.append(list(messages))
        try:
            return next(self._responses)
        except StopIteration:
            raise AssertionError("scripted client exhausted") from None


def _propose_call(content: str = "Preferred name is Atlas.") -> ToolCall:
    return ToolCall(
        id="call_1",
        name="propose_memory",
        arguments={"content": content, "kind": "fact"},
    )


def test_agent_loop_approved_proposal_persists_memory(
    service: MemoryService, tmp_path: Path
) -> None:
    registry = create_default_registry(
        tmp_path,
        memory_service=service,
        memory_proposal_approver=_always,
    )
    client = _ScriptedClient(
        ChatResponse(
            content="", model="test", done=True, tool_calls=(_propose_call(),)
        ),
        ChatResponse(content="Done.", model="test", done=True),
    )
    agent = Agent(client, registry)
    answer = agent.run([ChatMessage(role="user", content="Remember my name.")])
    assert answer == "Done."
    assert service.counts()["active"] == 1


def test_agent_loop_declines_do_not_persist_and_surface_error(
    service: MemoryService, tmp_path: Path
) -> None:
    registry = create_default_registry(
        tmp_path,
        memory_service=service,
        memory_proposal_approver=_never,
    )
    client = _ScriptedClient(
        ChatResponse(
            content="", model="test", done=True, tool_calls=(_propose_call(),)
        ),
        ChatResponse(
            content="I will not save that — it needs your approval.",
            model="test",
            done=True,
        ),
    )
    agent = Agent(client, registry)
    answer = agent.run([ChatMessage(role="user", content="Remember my name.")])
    assert "approval" in answer.lower()
    assert service.counts()["active"] == 0
    tool_messages = [
        message.content for message in client.calls[-1] if message.role == "tool"
    ]
    assert any("requires approval" in text for text in tool_messages)


# ---- identity recall: explicit memory vs no-invention ----


def _recall_chat(service: MemoryService) -> ChatMemory:
    return ChatMemory(service)


def test_who_am_i_without_memory_injects_nothing(service: MemoryService) -> None:
    chat = _recall_chat(service)
    result = chat.build_context_messages(
        [ChatMessage(role="user", content="Who am I?")]
    )
    assert result.messages == [ChatMessage(role="user", content="Who am I?")]
    assert result.provenance == ()


def test_who_am_i_with_identity_memory_recalls_it(service: MemoryService) -> None:
    memory = service.create_user_memory(
        "Preferred name is Atlas.", kind="fact", confidence=0.9, importance=0.9
    )
    chat = _recall_chat(service)
    result = chat.build_context_messages(
        [ChatMessage(role="user", content="Who am I?")]
    )
    assert any(
        "Preferred name is Atlas." in message.content for message in result.messages
    )
    provenance = list(result.provenance)
    assert len(provenance) == 1
    assert provenance[0]["memory_id"] == memory.memory_id
    assert provenance[0]["kind"] == "fact"
    assert "content" not in provenance[0]


def test_scope_aware_recall_keeps_identity_global_only(service: MemoryService) -> None:
    service.create_user_memory("Preferred name is Atlas.", kind="fact")
    service.create_user_memory(
        "Project note for proj-1.",
        kind="project_context",
        scope="project",
        scope_id="proj-1",
    )
    chat = _recall_chat(service)
    context = chat.recall("Who am I?")
    # Automatic chat recall derives global-only scopes by default; scoped
    # memories never leak into a chat identity answer.
    assert context.memories
    assert all(hit.memory.scope.value == "global" for hit in context.memories)
    assert all(hit.memory.scope_id is None for hit in context.memories)


# ---- personal-context overview stays content-free ----


def test_personal_context_overview_reports_memory_without_content(
    service: MemoryService, tmp_path: Path
) -> None:
    service.create_user_memory("Preferred name is Atlas.", kind="fact", importance=0.9)
    context_overview = PersonalContextService(memory=service).overview("memory")
    assert context_overview["available"] is True
    assert context_overview["count"] == 1
    assert context_overview["kind_breakdown"] == {"fact": 1}
    rendered = str(context_overview)
    assert "Atlas" not in rendered  # overview never leaks memory content
