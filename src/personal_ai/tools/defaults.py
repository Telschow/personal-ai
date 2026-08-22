"""Construction of the default tool registry."""

from pathlib import Path

from personal_ai.tools.filesystem import FilesystemTool
from personal_ai.tools.registry import ToolDefinition, ToolRegistry


def create_default_registry(workspace: Path) -> ToolRegistry:
    """Create a registry containing the standard personal-AI tools."""
    filesystem = FilesystemTool(workspace)
    registry = ToolRegistry()

    registry.register(
        ToolDefinition(
            name="list_directory",
            description="List files and directories in the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Relative directory path. Defaults to the workspace root."
                        ),
                    }
                },
                "required": [],
            },
            handler=filesystem.list_directory,
        )
    )

    return registry
