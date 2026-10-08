"""Tests for the source-adapter registry."""

from pathlib import Path

import pytest

from personal_ai.sources.filesystem import SOURCE_TYPE, FilesystemSourceAdapter
from personal_ai.sources.registry import (
    known_source_types,
    resolve_source_adapter,
)


def test_file_is_a_known_source_type() -> None:
    assert "file" in known_source_types()


def test_known_source_types_are_registration_ordered() -> None:
    assert known_source_types().count("file") == 1


def test_resolve_file_adapter(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    adapter = resolve_source_adapter("file", workspace)

    assert isinstance(adapter, FilesystemSourceAdapter)
    assert adapter.source_type == SOURCE_TYPE == "file"


def test_resolve_unknown_source_type_raises(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="Unknown source type 'nope'"):
        resolve_source_adapter("nope", tmp_path)
