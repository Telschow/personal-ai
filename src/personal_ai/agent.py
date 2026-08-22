"""Agent loop connecting Ollama chat completion with registered tools."""

import json
from collections.abc import Sequence

from personal_ai.ollama_client import (
    ChatMessage,
    ChatResponse,
    OllamaClient,
    ToolCall,
)
from personal_ai.tools.registry import ToolError, ToolRegistry

MAX_TOOL_RESULT_CHARS = 12000


class AgentError(Exception):
    """Base class for all errors raised by :class:`Agent`."""


class MaxToolRoundsError(AgentError):
    """Raised when the model keeps requesting tools past ``max_tool_rounds``."""


def _serialize_result(result: object) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result)
    except TypeError, ValueError:
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


class Agent:
    """Runs a synchronous tool-calling loop against a local Ollama model."""

    def __init__(
        self,
        client: OllamaClient,
        registry: ToolRegistry,
        max_tool_rounds: int = 8,
    ) -> None:
        if max_tool_rounds < 1:
            msg = f"max_tool_rounds must be >= 1, got {max_tool_rounds}"
            raise ValueError(msg)
        self.client = client
        self.registry = registry
        self.max_tool_rounds = max_tool_rounds

    def run(self, messages: Sequence[ChatMessage]) -> str:
        conversation = list(messages)
        schemas = self.registry.schemas()

        for _ in range(self.max_tool_rounds):
            response = self.client.chat(conversation, tools=schemas)
            if not response.tool_calls:
                return response.content
            conversation.append(self._assistant_message(response))
            for call in response.tool_calls:
                conversation.append(
                    ChatMessage(role="tool", content=self._execute(call))
                )

        msg = (
            f"Model exceeded {self.max_tool_rounds} tool rounds "
            "without producing a final answer"
        )
        raise MaxToolRoundsError(msg)

    def _assistant_message(self, response: ChatResponse) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
        )

    def _execute(self, call: ToolCall) -> str:
        try:
            output = _serialize_result(self.registry.execute(call.name, call.arguments))
        except ToolError as exc:
            output = _format_error(exc)
        return _bound_result(output)
