"""Unit tests for the agent loop, using scripted client doubles."""

import json
from collections.abc import Callable, Sequence
from typing import Any

import httpx
import pytest

from personal_ai.agent import (
    MAX_TOOL_RESULT_CHARS,
    Agent,
    AgentError,
    MaxToolRoundsError,
)
from personal_ai.ollama_client import ChatMessage, ChatResponse, OllamaClient, ToolCall
from personal_ai.tools.registry import ToolDefinition, ToolRegistry

USER_TURN = [ChatMessage(role="user", content="What is the weather in Tokyo?")]

TOOL_CALL = ToolCall(id="call_1", name="get_weather", arguments={"city": "Tokyo"})


def text_response(content: str) -> ChatResponse:
    return ChatResponse(content=content, model="test-model", done=True)


def tool_response(*calls: ToolCall) -> ChatResponse:
    return ChatResponse(content="", model="test-model", done=True, tool_calls=calls)


class ScriptedClient:
    """Stands in for OllamaClient, replaying scripted responses."""

    def __init__(self, *responses: ChatResponse) -> None:
        self._responses = iter(responses)
        self.calls: list[tuple[list[ChatMessage], list[dict[str, object]] | None]] = []

    def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, object]] | None = None,
    ) -> ChatResponse:
        recorded_tools = list(tools) if tools is not None else None
        self.calls.append((list(messages), recorded_tools))
        try:
            return next(self._responses)
        except StopIteration:
            pytest.fail("ScriptedClient ran out of scripted responses")


def echo_handler(arguments: dict[str, object]) -> object:
    return arguments


def make_agent(
    client: ScriptedClient,
    handler: Callable[[dict[str, object]], object] = echo_handler,
    max_tool_rounds: int = 8,
    observer=None,
) -> tuple[Agent, ToolRegistry]:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="get_weather",
            description="Get the weather for a city",
            parameters={"type": "object"},
            handler=handler,
        )
    )
    return Agent(
        client, registry, max_tool_rounds=max_tool_rounds, observer=observer
    ), registry


def test_plain_text_response_is_returned_directly() -> None:
    client = ScriptedClient(text_response("Sunny, 22C"))
    agent, _ = make_agent(client)

    result = agent.run(USER_TURN)

    assert result == "Sunny, 22C"
    assert len(client.calls) == 1


def test_registry_schemas_are_sent_to_ollama() -> None:
    client = ScriptedClient(text_response("done"))
    agent, registry = make_agent(client)

    agent.run(USER_TURN)

    _, tools = client.calls[0]
    assert tools == registry.schemas()


def test_tool_call_round_executes_and_returns_final_answer() -> None:
    seen_arguments: list[dict[str, object]] = []

    def handler(arguments: dict[str, object]) -> object:
        seen_arguments.append(arguments)
        return "22C"

    client = ScriptedClient(tool_response(TOOL_CALL), text_response("Sunny, 22C"))
    agent, _ = make_agent(client, handler=handler)

    result = agent.run(USER_TURN)

    assert result == "Sunny, 22C"
    assert seen_arguments == [{"city": "Tokyo"}]
    assert len(client.calls) == 2


def test_conversation_carries_assistant_and_tool_messages() -> None:
    client = ScriptedClient(tool_response(TOOL_CALL), text_response("ok"))

    def handler(arguments: dict[str, object]) -> object:
        return "22C"

    agent, _ = make_agent(client, handler=handler)
    agent.run(USER_TURN)

    first_messages, _ = client.calls[0]
    second_messages, _ = client.calls[1]
    assert first_messages == USER_TURN
    assert len(second_messages) == 3
    assistant, tool_message = second_messages[1], second_messages[2]
    assert assistant.role == "assistant"
    assert assistant.content == ""
    assert assistant.tool_calls == (TOOL_CALL,)
    assert tool_message.role == "tool"
    assert tool_message.content == "22C"


def test_multiple_tool_calls_each_get_a_tool_message() -> None:
    weather = {"Tokyo": "22C", "Oslo": "-3C"}

    def handler(arguments: dict[str, object]) -> object:
        return weather[str(arguments["city"])]

    calls = (
        ToolCall(id="call_1", name="get_weather", arguments={"city": "Tokyo"}),
        ToolCall(id="call_2", name="get_weather", arguments={"city": "Oslo"}),
    )
    client = ScriptedClient(tool_response(*calls), text_response("both fetched"))
    agent, _ = make_agent(client, handler=handler)

    agent.run(USER_TURN)

    tool_messages = client.calls[1][0][2:]
    assert [m.content for m in tool_messages] == ["22C", "-3C"]
    assert all(m.role == "tool" for m in tool_messages)


def test_tool_failure_is_reported_as_tool_result() -> None:
    def broken(arguments: dict[str, object]) -> object:
        msg = "disk on fire"
        raise RuntimeError(msg)

    client = ScriptedClient(tool_response(TOOL_CALL), text_response("understood"))
    agent, _ = make_agent(client, handler=broken)

    result = agent.run(USER_TURN)

    assert result == "understood"
    tool_message = client.calls[1][0][2]
    assert "disk on fire" in tool_message.content
    assert "Traceback" not in tool_message.content


def test_unknown_tool_request_is_reported_back_to_model() -> None:
    executed: list[str] = []

    def handler(arguments: dict[str, object]) -> object:
        executed.append("ran")
        return "22C"

    bogus = ToolCall(id="call_x", name="delete_everything", arguments={})
    client = ScriptedClient(tool_response(bogus), text_response("sorry"))
    agent, _ = make_agent(client, handler=handler)

    result = agent.run(USER_TURN)

    assert result == "sorry"
    assert executed == []
    tool_message = client.calls[1][0][2]
    assert "delete_everything" in tool_message.content


def test_run_raises_after_exceeding_max_tool_rounds() -> None:
    client = ScriptedClient(*(tool_response(TOOL_CALL) for _ in range(4)))
    agent, _ = make_agent(client, max_tool_rounds=3)

    with pytest.raises(MaxToolRoundsError):
        agent.run(USER_TURN)

    assert len(client.calls) == 3


def test_max_tool_rounds_error_is_typed_agent_error() -> None:
    assert issubclass(MaxToolRoundsError, AgentError)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        pytest.param({"temp": 22}, {"temp": 22}, id="dict"),
        pytest.param([1, 2], [1, 2], id="list"),
        pytest.param(42, 42, id="int"),
        pytest.param(True, True, id="bool"),
        pytest.param(None, None, id="none"),
    ],
)
def test_non_string_results_are_json_serialized(
    result: object, expected: object
) -> None:
    client = ScriptedClient(tool_response(TOOL_CALL), text_response("ok"))
    agent, _ = make_agent(client, handler=lambda arguments: result)

    agent.run(USER_TURN)

    content = client.calls[1][0][2].content
    assert json.loads(content) == expected


def test_string_results_pass_through_unmodified() -> None:
    raw = "line one\nline two   "

    client = ScriptedClient(tool_response(TOOL_CALL), text_response("ok"))
    agent, _ = make_agent(client, handler=lambda arguments: raw)

    agent.run(USER_TURN)

    assert client.calls[1][0][2].content == raw


def test_oversized_results_are_truncated_explicitly() -> None:
    big = "x" * (MAX_TOOL_RESULT_CHARS + 5000)
    client = ScriptedClient(tool_response(TOOL_CALL), text_response("ok"))
    agent, _ = make_agent(client, handler=lambda arguments: big)

    agent.run(USER_TURN)

    content = client.calls[1][0][2].content
    assert content.startswith(big[:MAX_TOOL_RESULT_CHARS])
    assert len(content) < len(big)
    assert "truncated" in content
    assert str(len(big)) in content


def test_input_messages_are_not_mutated() -> None:
    client = ScriptedClient(tool_response(TOOL_CALL), text_response("ok"))
    agent, _ = make_agent(client, handler=lambda arguments: "22C")

    agent.run(USER_TURN)

    assert USER_TURN == [
        ChatMessage(role="user", content="What is the weather in Tokyo?")
    ]


def test_zero_max_tool_rounds_is_rejected_at_construction() -> None:
    client = ScriptedClient(text_response("hi"))

    with pytest.raises(ValueError):
        make_agent(client, max_tool_rounds=0)


def test_wire_format_matches_ollama_chat_api() -> None:
    payloads: list[dict[str, Any]] = [
        {
            "model": "test-model",
            "created_at": "2026-08-22T12:00:00Z",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_gvumtiaj",
                        "function": {
                            "index": 0,
                            "name": "get_weather",
                            "arguments": {"city": "Tokyo"},
                        },
                    }
                ],
            },
            "done": True,
        },
        {
            "model": "test-model",
            "created_at": "2026-08-22T12:00:01Z",
            "message": {"role": "assistant", "content": "22C"},
            "done": True,
        },
    ]
    requests: list[httpx.Request] = []
    responses = iter(payloads)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=next(responses))

    ollama = OllamaClient(model="test-model", transport=httpx.MockTransport(handler))
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="get_weather",
            description="Get the weather for a city",
            parameters={"type": "object"},
            handler=lambda arguments: "22C",
        )
    )

    agent = Agent(ollama, registry)
    result = agent.run([ChatMessage(role="user", content="weather?")])
    ollama.close()

    assert result == "22C"
    first = json.loads(requests[0].content)
    assert first["stream"] is False
    assert first["tools"] == registry.schemas()
    assert first["messages"] == [{"role": "user", "content": "weather?"}]

    second = json.loads(requests[1].content)
    assert second["messages"] == [
        {"role": "user", "content": "weather?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "get_weather", "arguments": {"city": "Tokyo"}}}
            ],
        },
        {"role": "tool", "content": "22C"},
    ]
    assert second["tools"] == registry.schemas()


class TestAgentObserver:
    """The optional observer reports operational progress without changing
    the default (no-observer) behavior or exposing hidden reasoning."""

    def test_observer_receives_tool_and_completion_events(self) -> None:
        events: list[dict[str, object]] = []
        client = ScriptedClient(
            tool_response(TOOL_CALL),
            text_response("final answer"),
        )
        agent, _ = make_agent(client, observer=events.append)

        assert agent.run(USER_TURN) == "final answer"

        kinds = [e["event"] for e in events]
        assert kinds == [
            "round",
            "tool_start",
            "tool_end",
            "completed",
        ]
        round_event = events[0]
        assert round_event["round"] == 1
        assert round_event["tool_calls"][0].name == "get_weather"
        start = events[1]
        assert start["name"] == "get_weather"
        assert "Tokyo" in str(start["arguments"])
        end = events[2]
        assert end["status"] == "ok"
        assert isinstance(end["latency_sec"], float)
        assert events[3]["latency_sec"] >= 0.0

    def test_observer_reports_tool_error_status(self) -> None:
        events: list[dict[str, object]] = []

        def failing(arguments: dict[str, object]) -> object:
            raise ValueError("boom")

        client = ScriptedClient(
            tool_response(TOOL_CALL),
            text_response("handled"),
        )
        agent, _ = make_agent(client, handler=failing, observer=events.append)
        agent.run(USER_TURN)

        end = next(e for e in events if e["event"] == "tool_end")
        assert end["status"] == "error"

    def test_observer_max_rounds_event(self) -> None:
        events: list[dict[str, object]] = []
        client = ScriptedClient(tool_response(TOOL_CALL), tool_response(TOOL_CALL))
        agent, _ = make_agent(client, max_tool_rounds=2, observer=events.append)

        with pytest.raises(MaxToolRoundsError):
            agent.run(USER_TURN)

        assert events[-1]["event"] == "max_rounds"
        assert events[-1]["round"] == 2

    def test_no_observer_behavior_unchanged(self) -> None:
        client = ScriptedClient(
            tool_response(TOOL_CALL),
            text_response("final answer"),
        )
        agent, _ = make_agent(client)
        assert agent.observer is None
        assert agent.run(USER_TURN) == "final answer"

    def test_arguments_sanitized_to_bounded_length(self) -> None:
        from personal_ai.agent import _sanitize_arguments

        big = "x" * 1000
        sanitized = _sanitize_arguments({"query": big})
        assert len(sanitized["query"]) <= 205
        assert sanitized["query"].endswith("...")
