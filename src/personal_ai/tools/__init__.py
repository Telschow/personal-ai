"""Tool infrastructure for the personal-ai agent."""

from personal_ai.tools.defaults import create_default_registry
from personal_ai.tools.registry import (
    DuplicateToolError,
    InvalidToolNameError,
    ToolArgumentError,
    ToolDefinition,
    ToolError,
    ToolExecutionError,
    ToolHandler,
    ToolRegistry,
    UnknownToolError,
)

__all__ = [
    "DuplicateToolError",
    "InvalidToolNameError",
    "ToolArgumentError",
    "ToolDefinition",
    "ToolError",
    "ToolExecutionError",
    "ToolHandler",
    "ToolRegistry",
    "UnknownToolError",
    "create_default_registry",
]
