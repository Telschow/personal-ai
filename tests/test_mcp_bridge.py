"""Hermetic tests for the read-only MCP knowledge bridge."""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent

from personal_ai.documents.models import Document, DocumentChunk, compute_document_id
from personal_ai.mcp_server import READ_TOOL_NAMES, build_mcp_server
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryStore
from personal_ai.people.models import PersonReference
from personal_ai.people.store import PersonStore
from personal_ai.storage.chunks import ChunkStore
from personal_ai.storage.documents import DocumentStore, connect_database


def _call(server, name: str, arguments: dict[str, object]) -> object:
    async def _run() -> object:
        result = await server.call_tool(name, arguments)
        if result.structured_content is not None:
            return result.structured_content
        for block in result.content:
            if isinstance(block, TextContent):
                return json.loads(block.text)
        raise AssertionError(f"no content for {name}")

    return asyncio.run(_run())


def _tools(server) -> dict[str, dict[str, object]]:
    async def _list() -> dict[str, dict[str, object]]:
        tools = await server.list_tools()
        return {
            t.name: {"description": t.description, "schema": t.input_schema}
            for t in tools
        }

    return asyncio.run(_list())


@pytest.fixture
def database(tmp_path):
    db = tmp_path / "knowledge.db"
    connection = connect_database(db)
    connection.close()
    return db


@pytest.fixture
def seeded_database(tmp_path):
    db = tmp_path / "seeded.db"
    connection = connect_database(db)
    docs = DocumentStore(connection)
    chunks = ChunkStore(connection)
    memory = MemoryService(MemoryStore(connection))
    people = PersonStore(connection)

    digest = compute_document_id("file", "notes/goals.md", "abc123")
    docs.add(
        Document(
            id=digest,
            source="notes/goals.md",
            source_type="file",
            content_hash="abc123",
            created_at="2030-01-01T00:00:00Z",
            modified_at="2030-01-01T00:00:00Z",
            path="notes/goals.md",
            filename="goals.md",
            mime_type="text/markdown",
            metadata={"kind": ["text_heavy"]},
        )
    )
    chunks.add_many(
        [
            DocumentChunk(id=f"{digest}-{i}", document_id=digest, text=f"chunk {i}")
            for i in range(2)
        ]
    )
    mem = memory.create_user_memory(
        "The user wants to lead at BMW.", kind="goal", summary="career"
    )
    people.upsert_reference(
        PersonReference(
            document_id=digest,
            name="Alice Example",
            email="alice@example.invalid",
            role="to",
            source_type="email",
            seen_at="2030-01-02T00:00:00Z",
        )
    )
    connection.close()
    return {"database": db, "document_id": digest, "memory_id": mem.memory_id}


class TestBridgeRegistration:
    def test_only_read_tools_are_registered(self, database):
        server = build_mcp_server(database)
        assert set(_tools(server)) == set(READ_TOOL_NAMES)

    def test_no_write_tool_is_registered(self, database):
        assert "propose_memory" not in _tools(build_mcp_server(database))

    def test_tool_metadata_is_privacy_safe(self, database):
        tools = _tools(build_mcp_server(database))
        for name in ("search_documents", "get_document", "search_memory", "get_memory"):
            assert "untrusted" in tools[name]["description"]
        assert tools["search_memory"]["schema"]["required"] == ["query"]
        assert tools["get_document"]["schema"]["required"] == ["document_id"]
        assert tools["search_people"]["schema"]["required"] == ["query"]
        assert tools["get_person"]["schema"]["required"] == ["person_id"]


class TestBridgeReads:
    def test_get_document_not_found(self, database):
        result = _call(
            build_mcp_server(database), "get_document", {"document_id": "nope"}
        )
        assert result["status"] == "not_found"

    def test_empty_corpus_searches(self, database):
        server = build_mcp_server(database)
        assert _call(server, "search_documents", {"query": "goals"})["results"] == []
        assert _call(server, "search_memory", {"query": "goals"})["memories"] == []
        assert _call(server, "search_people", {"query": "nobody"})["count"] == 0

    def test_document_fetch_returns_seeded_chunks(self, seeded_database):
        server = build_mcp_server(seeded_database["database"])
        result = _call(
            server, "get_document", {"document_id": seeded_database["document_id"]}
        )
        assert result["status"] == "ok"
        assert result["chunk_count"] == 2
        assert result["document"]["filename"] == "goals.md"

    def test_memory_search_finds_seeded_memory(self, seeded_database):
        server = build_mcp_server(seeded_database["database"])
        result = _call(server, "search_memory", {"query": "BMW"})
        assert result["memories"]
        assert result["memories"][0]["memory_id"] == seeded_database["memory_id"]

    def test_people_search_finds_seeded_person(self, seeded_database):
        server = build_mcp_server(seeded_database["database"])
        result = _call(server, "search_people", {"query": "alice"})
        assert result["count"] == 1
        assert result["people"][0]["display_name"] == "Alice Example"

    def test_seeded_limits_and_caps(self, seeded_database):
        server = build_mcp_server(seeded_database["database"])
        doc = _call(
            server,
            "get_document",
            {"document_id": seeded_database["document_id"], "chunk_limit": 1},
        )
        assert doc["chunk_count"] == 1


class TestBridgeValidation:
    def test_invalid_argument_types_raise(self, database):
        server = build_mcp_server(database)
        with pytest.raises(ToolError):
            asyncio.run(server.call_tool("search_memory", {"query": 123}))

    def test_empty_required_arguments_raise(self, database):
        server = build_mcp_server(database)
        with pytest.raises(ToolError):
            asyncio.run(server.call_tool("get_document", {"document_id": ""}))
