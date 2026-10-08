"""Agent loop connecting Ollama chat completion with registered tools."""

import json
import time
from collections.abc import Callable, Sequence

from personal_ai.ollama_client import (
    ChatMessage,
    ChatResponse,
    OllamaClient,
    ToolCall,
)
from personal_ai.tools.registry import ToolError, ToolRegistry

MAX_TOOL_RESULT_CHARS = 12000

AgentObserver = Callable[[dict[str, object]], None]


class AgentError(Exception):
    """Base class for all errors raised by :class:`Agent`."""


class MaxToolRoundsError(AgentError):
    """Raised when the model keeps requesting tools past ``max_tool_rounds``."""


def _serialize_result(result: object) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result)
    except (TypeError, ValueError):
        return repr(result)


def _bound_result(text: str) -> str:
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    omitted = len(text) - MAX_TOOL_RESULT_CHARS
    kept = text[:MAX_TOOL_RESULT_CHARS]
    return (
        f"{kept}\n[truncated: showing {MAX_TOOL_RESULT_CHARS} "
        f"of {len(text)} characters, {omitted} omitted]"
    )


def _format_error(exc: ToolError) -> str:
    detail = str(exc)
    if exc.__cause__ is not None:
        cause = exc.__cause__
        detail = f"{detail} ({type(cause).__name__}: {cause})"
    return f"error: {detail}"


def _sanitize_arguments(arguments: dict[str, object]) -> dict[str, object]:
    """Return tool arguments safe for operational logging.

    Only a bounded-per-key representation is produced; long values (for
    example query strings) are truncated so verbose output never dumps large
    payloads. This is for observability only and does not alter execution.
    """
    sanitized: dict[str, object] = {}
    for key, value in arguments.items():
        text = repr(value)
        sanitized[key] = text if len(text) <= 200 else text[:200] + "..."
    return sanitized


class Agent:
    """Runs a synchronous tool-calling loop against a local Ollama model."""

    def __init__(
        self,
        client: OllamaClient,
        registry: ToolRegistry,
        max_tool_rounds: int = 8,
        observer: AgentObserver | None = None,
    ) -> None:
        if max_tool_rounds < 1:
            msg = f"max_tool_rounds must be >= 1, got {max_tool_rounds}"
            raise ValueError(msg)
        self.client = client
        self.registry = registry
        self.max_tool_rounds = max_tool_rounds
        self.observer = observer

    def run(self, messages: Sequence[ChatMessage]) -> str:
        conversation = list(messages)
        schemas = self.registry.schemas()
        started = time.monotonic()

        for round_index in range(1, self.max_tool_rounds + 1):
            response = self.client.chat(conversation, tools=schemas)
            if not response.tool_calls:
                if self.observer is not None:
                    self.observer(
                        {
                            "event": "completed",
                            "round": round_index,
                            "latency_sec": time.monotonic() - started,
                        }
                    )
                return response.content
            self._notify(
                "round",
                {"round": round_index, "tool_calls": response.tool_calls},
            )
            conversation.append(self._assistant_message(response))
            for call in response.tool_calls:
                conversation.append(
                    ChatMessage(role="tool", content=self._execute(call))
                )

        msg = (
            f"Model exceeded {self.max_tool_rounds} tool rounds "
            "without producing a final answer"
        )
        if self.observer is not None:
            self.observer(
                {
                    "event": "max_rounds",
                    "round": self.max_tool_rounds,
                    "latency_sec": time.monotonic() - started,
                }
            )
        raise MaxToolRoundsError(msg)

    def _assistant_message(self, response: ChatResponse) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
        )

    def _notify(self, event: str, payload: dict[str, object]) -> None:
        if self.observer is None:
            return
        self.observer({"event": event, **payload})

    def _execute(self, call: ToolCall) -> str:
        self._notify(
            "tool_start",
            {
                "name": call.name,
                "arguments": _sanitize_arguments(call.arguments),
            },
        )
        tool_started = time.monotonic()
        try:
            output = _serialize_result(self.registry.execute(call.name, call.arguments))
            status = "ok"
        except ToolError as exc:
            output = _format_error(exc)
            status = "error"
        self._notify(
            "tool_end",
            {
                "name": call.name,
                "status": status,
                "latency_sec": time.monotonic() - tool_started,
            },
        )
        return _bound_result(output)
