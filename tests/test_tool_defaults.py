"""Tests for the default tool registry."""

from pathlib import Path

from personal_ai.storage import EventStore, connect_database
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


def test_registry_no_event_store_without_event_tool(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)
    names = {schema["function"]["name"] for schema in registry.schemas()}
    assert "query_events" not in names


def test_registry_registers_query_events_with_event_store(tmp_path: Path) -> None:
    connection = connect_database(":memory:")
    try:
        event_store = EventStore(connection)
        registry = create_default_registry(tmp_path, event_store=event_store)
        names = {schema["function"]["name"] for schema in registry.schemas()}
        assert "query_events" in names
        results = registry.execute("query_events", {"event_type": "search_query"})
        assert results == []
    finally:
        connection.close()


def test_registry_query_events_accepts_youtube_event_types(tmp_path: Path) -> None:
    connection = connect_database(":memory:")
    try:
        event_store = EventStore(connection)
        registry = create_default_registry(tmp_path, event_store=event_store)
        for event_type in ("video_watch", "youtube_search"):
            results = registry.execute("query_events", {"event_type": event_type})
            assert results == []
    finally:
        connection.close()
