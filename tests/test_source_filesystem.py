"""Integration tests for the filesystem source adapter."""

import datetime as dt
import os
from pathlib import Path

import pytest

from personal_ai.documents import document_from_source_record
from personal_ai.sources import FilesystemSourceAdapter, SourceRecord
from personal_ai.sources.filesystem import (
    PathOutsideWorkspaceError,
    SourceNotFoundError,
    UnsupportedFileError,
)
from personal_ai.storage import DocumentStore, connect_database

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def make_adapter(
    tmp_path: Path, name: str = "workspace"
) -> tuple[Path, FilesystemSourceAdapter]:
    workspace = tmp_path / name
    workspace.mkdir()
    return workspace, FilesystemSourceAdapter(workspace)


def write(workspace: Path, relative: str, data: bytes) -> Path:
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def discovered_keys(adapter: FilesystemSourceAdapter) -> list[str]:
    return [record.source_key for record in adapter.discover()]


def test_supported_extensions_are_discovered(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    for name in ("a.txt", "b.md", "c.pdf", "d.png", "e.jpg", "f.jpeg"):
        write(workspace, name, b"x")

    assert sorted(discovered_keys(adapter)) == [
        "a.txt",
        "b.md",
        "c.pdf",
        "d.png",
        "e.jpg",
        "f.jpeg",
    ]


def test_unsupported_files_are_ignored(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "data.bin", b"binary")
    write(workspace, "table.csv", b"a,b\n1,2")
    write(workspace, "noext", b"?")

    assert discovered_keys(adapter) == []


def test_extensions_match_case_insensitively(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "NOTE.MD", b"# hi")

    assert discovered_keys(adapter) == ["NOTE.MD"]


def test_nested_directories_are_discovered_with_relative_keys(
    tmp_path: Path,
) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "docs/deep/note.md", b"note")

    assert discovered_keys(adapter) == ["docs/deep/note.md"]
    assert adapter.discover()[0].source_type == "file"


def test_discovery_is_deterministically_ordered(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    for name in ("zeta.md", "alpha.md", "sub/middle.md", "Beta.md"):
        write(workspace, name, b"x")

    keys = discovered_keys(adapter)

    assert keys == sorted(keys)
    assert keys == discovered_keys(FilesystemSourceAdapter(workspace))


def test_excluded_directories_are_not_scanned(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "keep.md", b"x")
    for directory in (".git", ".venv", "__pycache__", "node_modules"):
        write(workspace, f"{directory}/leak.md", b"x")

    assert discovered_keys(adapter) == ["keep.md"]


def test_hidden_files_are_discovered(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, ".hidden-notes.txt", b"secret-ish")

    assert discovered_keys(adapter) == [".hidden-notes.txt"]


@pytest.mark.parametrize("key", ["..", "../outside.txt", "/etc/hostname"])
def test_escaping_source_keys_are_rejected(key: str, tmp_path: Path) -> None:
    _workspace, adapter = make_adapter(tmp_path)

    with pytest.raises(PathOutsideWorkspaceError):
        adapter.load_record(key)


def test_symlinked_file_outside_workspace_is_not_discovered(
    tmp_path: Path,
) -> None:
    workspace, adapter = make_adapter(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("sensitive")
    (workspace / "escape.txt").symlink_to(secret)

    assert discovered_keys(adapter) == []

    with pytest.raises(PathOutsideWorkspaceError):
        adapter.load_record("escape.txt")


def test_symlinked_file_inside_workspace_is_discovered_under_link_name(
    tmp_path: Path,
) -> None:
    workspace, adapter = make_adapter(tmp_path)
    real = write(workspace, "notes/real.md", b"# real")
    (workspace / "alias.md").symlink_to(real)

    keys = discovered_keys(adapter)

    assert keys == ["alias.md", "notes/real.md"]
    alias_record = adapter.load_record("alias.md")
    assert (
        alias_record.content_hash == adapter.load_record("notes/real.md").content_hash
    )


def test_symlinked_directories_are_not_traversed(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "realdir/notes.md", b"x")
    (workspace / "linkdir").symlink_to(workspace / "realdir", target_is_directory=True)

    assert discovered_keys(adapter) == ["realdir/notes.md"]


def test_text_record_preserves_decodable_payload(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    content = "héllo wörld".encode()
    write(workspace, "note.md", content)

    record = adapter.load_record("note.md")

    assert record.payload == content
    assert record.payload.decode("utf-8") == "héllo wörld"
    assert record.metadata["mime_type"] == "text/markdown"
    assert record.metadata["extension"] == ".md"
    assert record.metadata["size_bytes"] == len(content)


def test_binary_record_payload_is_preserved_exactly(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    payload = b"\x89PNG\r\n\x1a\n\xff\xfe\x00binary"
    write(workspace, "image.png", payload)

    record = adapter.load_record("image.png")

    assert record.payload == payload
    assert record.metadata["mime_type"] == "image/png"


def test_identical_content_at_different_paths_has_different_identity(
    tmp_path: Path,
) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "copy-a.txt", b"same bytes")
    write(workspace, "copy-b.txt", b"same bytes")

    records = {record.source_key: record for record in adapter.discover()}
    doc_a = document_from_source_record(records["copy-a.txt"])
    doc_b = document_from_source_record(records["copy-b.txt"])

    assert records["copy-a.txt"].content_hash == records["copy-b.txt"].content_hash
    assert doc_a.id != doc_b.id


def test_modifying_file_contents_changes_hash_and_id(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "diary.md", b"version one")
    before = document_from_source_record(adapter.load_record("diary.md"))

    write(workspace, "diary.md", b"version two")
    after = document_from_source_record(adapter.load_record("diary.md"))

    assert after.content_hash != before.content_hash
    assert after.id != before.id


def test_empty_file_yields_known_hash_and_zero_size(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "empty.txt", b"")

    record = adapter.load_record("empty.txt")

    assert record.content_hash == EMPTY_SHA256
    assert record.metadata["size_bytes"] == 0
    assert record.payload == b""


def test_file_mtime_is_reflected_as_utc_timestamp(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    path = write(workspace, "timestamped.txt", b"when?")
    epoch = 1750000000.0
    os.utime(path, (epoch, epoch))

    record = adapter.load_record("timestamped.txt")

    expected = dt.datetime.fromtimestamp(epoch, tz=dt.UTC).isoformat()
    assert record.modified_at == expected
    assert record.created_at == expected


def test_load_record_missing_file_raises(tmp_path: Path) -> None:
    _workspace, adapter = make_adapter(tmp_path)

    with pytest.raises(SourceNotFoundError):
        adapter.load_record("missing.txt")


def test_load_record_directory_raises_not_found(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "notes/inner.txt", b"x")

    with pytest.raises(SourceNotFoundError):
        adapter.load_record("notes")


def test_load_record_unsupported_extension_raises(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "data.bin", b"binary")

    with pytest.raises(UnsupportedFileError):
        adapter.load_record("data.bin")


def test_load_record_matches_discovered_record(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "notes/todo.md", b"- buy milk")

    loaded = adapter.load_record("notes/todo.md")

    assert loaded in adapter.discover()


def test_adapters_on_different_workspaces_are_independent(
    tmp_path: Path,
) -> None:
    first_workspace, first_adapter = make_adapter(tmp_path, "workspace-one")
    second_workspace, second_adapter = make_adapter(tmp_path, "workspace-two")
    write(first_workspace, "first-only.txt", b"one")
    write(second_workspace, "second-only.txt", b"two")

    first_keys = discovered_keys(first_adapter)
    second_keys = discovered_keys(second_adapter)

    assert first_keys == ["first-only.txt"]
    assert second_keys == ["second-only.txt"]


def test_records_flow_into_document_store_idempotently(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "notes/goals.md", b"# goals")

    documents = [document_from_source_record(record) for record in adapter.discover()]

    with DocumentStore(connect_database(":memory:")) as store:
        assert all(store.add(document) for document in documents)
        assert not any(store.add(document) for document in documents)

        stored = store.list_documents()
        assert len(stored) == 1
        assert stored[0] == documents[0]
        assert stored[0].metadata["extension"] == ".md"


def test_discover_returns_typed_source_records(tmp_path: Path) -> None:
    workspace, adapter = make_adapter(tmp_path)
    write(workspace, "typed.md", b"x")

    records = adapter.discover()

    assert len(records) == 1
    record = records[0]
    assert isinstance(record, SourceRecord)
    assert record.source_type == "file"
    assert record.source_key == "typed.md"
    assert record.payload == b"x"
