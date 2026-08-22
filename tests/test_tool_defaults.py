"""Tests for the default tool registry."""

from pathlib import Path

from personal_ai.tools import create_default_registry


def test_default_registry_contains_list_directory(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)

    assert registry.schemas() == [
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List files and directories in the workspace.",
                "parameters": {
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
            },
        }
    ]


def test_default_registry_executes_list_directory(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello")
    (tmp_path / "documents").mkdir()

    registry = create_default_registry(tmp_path)

    assert registry.execute("list_directory", {}) == [
        {"name": "documents", "type": "directory"},
        {"name": "hello.txt", "type": "file"},
    ]
