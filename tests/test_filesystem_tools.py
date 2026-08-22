"""Tests for sandboxed filesystem tools."""

from pathlib import Path

import pytest

from personal_ai.tools.filesystem import (
    FilesystemTool,
    PathOutsideWorkspaceError,
)


def test_list_directory_returns_files_and_directories(tmp_path: Path) -> None:
    (tmp_path / "zebra.txt").write_text("z")
    (tmp_path / "alpha.txt").write_text("a")
    (tmp_path / "documents").mkdir()

    tool = FilesystemTool(tmp_path)

    result = tool.list_directory({})

    assert result == [
        {"name": "alpha.txt", "type": "file"},
        {"name": "documents", "type": "directory"},
        {"name": "zebra.txt", "type": "file"},
    ]


def test_list_directory_accepts_relative_path(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "notes.txt").write_text("hello")

    tool = FilesystemTool(tmp_path)

    assert tool.list_directory({"path": "documents"}) == [
        {"name": "notes.txt", "type": "file"},
    ]


def test_parent_traversal_cannot_escape_workspace(tmp_path: Path) -> None:
    tool = FilesystemTool(tmp_path)

    with pytest.raises(PathOutsideWorkspaceError):
        tool.list_directory({"path": ".."})


def test_absolute_path_outside_workspace_is_rejected(tmp_path: Path) -> None:
    tool = FilesystemTool(tmp_path)

    with pytest.raises(PathOutsideWorkspaceError):
        tool.list_directory({"path": "/etc"})


def test_missing_directory_raises(tmp_path: Path) -> None:
    tool = FilesystemTool(tmp_path)

    with pytest.raises(FileNotFoundError):
        tool.list_directory({"path": "missing"})


def test_file_is_not_a_directory(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello")

    tool = FilesystemTool(tmp_path)

    with pytest.raises(NotADirectoryError):
        tool.list_directory({"path": "notes.txt"})


def test_non_string_path_is_rejected(tmp_path: Path) -> None:
    tool = FilesystemTool(tmp_path)

    with pytest.raises(TypeError, match="path must be a string"):
        tool.list_directory({"path": 123})
