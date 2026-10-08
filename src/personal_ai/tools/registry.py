"""Typed registry for exposing Python tools to Ollama and executing its calls."""

import re
from collections.abc import Callable
from dataclasses import dataclass

TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

ToolHandler = Callable[[dict[str, object]], object]


class ToolError(Exception):
    """Base class for all errors raised by :class:`ToolRegistry`."""


class DuplicateToolError(ToolError):
    """Raised when a tool is registered under an already used name."""


class InvalidToolNameError(ToolError):
    """Raised when a tool name does not match the allowed format."""


class UnknownToolError(ToolError):
    """Raised when execution is requested for an unregistered tool name."""


class ToolArgumentError(ToolError):
    """Raised when tool arguments are not a JSON-style mapping."""


class ToolExecutionError(ToolError):
    """Raised when a registered tool handler fails unexpectedly."""


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A single tool exposed to the model, together with its callable."""

    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler


class ToolRegistry:
    """Holds explicitly registered tools and executes model-requested calls."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if not isinstance(tool.name, str) or not TOOL_NAME_PATTERN.fullmatch(tool.name):
            msg = f"Invalid tool name: {tool.name!r}"
            raise InvalidToolNameError(msg)
        if tool.name in self._tools:
            msg = f"Tool already registered: {tool.name!r}"
            raise DuplicateToolError(msg)
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.parameters),
                },
            }
            for tool in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict[str, object]) -> object:
        tool = self._tools.get(name)
        if tool is None:
            msg = f"No tool registered under name: {name!r}"
            raise UnknownToolError(msg)
        if not isinstance(arguments, dict):
            msg = f"Arguments for tool {name!r} must be an object, got {type(arguments).__name__}"
            raise ToolArgumentError(msg)

        try:
            return tool.handler(arguments)
        except Exception as exc:
            msg = f"Tool {name!r} failed during execution"
            raise ToolExecutionError(msg) from exc
