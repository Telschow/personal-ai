"""Hermetic wiring tests for retrieval-backend selection (Phase 51).

These exercise the real construction paths (``cli._connect_agent_registry``,
``cli.run_search`` via ``cli.main()``, and ``mcp_server.build_mcp_server``)
with the retrieval mode driven by monkeypatched environment, so no Ollama
server, network, or real corpus is required. The embedding provider is
replaced with a deterministic fake only where a non-keyword backend is
selected.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personal_ai import cli
from personal_ai.documents import Document, DocumentChunk, Embedding
from personal_ai.mcp_server import build_mcp_server
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    connect_database,
)

RETRIEVAL_MODE = "PERSONAL_AI_RETRIEVAL_MODE"
EMBEDDING_MODEL = "PERSONAL_AI_EMBEDDING_MODEL"
PROVIDER_MODEL = "semantic-test-model"


class MappingProvider:
    """Deterministic fake provider shared by the wiring tests."""

    def __init__(self) -> None:
        self.model = PROVIDER_MODEL

    def embed(self, text: str) -> Embedding:
        # "quarterly" is the query used in these tests; nearest vector is
        # (1.0, 0.0) which matches the semantic-only chunk below.
        return Embedding(model=self.model, vector=(1.0, 0.0))


def _seed_knowledge(
    connection,
    *,
    keyword_chunk_id: str = "kw",
    semantic_chunk_id: str = "sem",
) -> None:
    """Seed two documents/chunks: one keyword-only, one semantic-only.

    ``keyword_chunk_id`` text matches the query "quarterly" but its vector is
    orthogonal (semantic-near score 0); ``semantic_chunk_id`` text does NOT
    match "quarterly" but its vector is the closest. Keyword mode therefore
    returns only the former; hybrid mode returns both.
    """
    documents = DocumentStore(connection)
    chunks = ChunkStore(connection)
    embeddings = EmbeddingStore(connection)
    for document_id in ("d-kw", "d-sem"):
        documents.add(
            Document(
                id=document_id,
                source="notes.txt",
                source_type="file",
                content_hash="hash",
                created_at="2026-08-26T10:00:00+00:00",
                modified_at="2026-08-26T10:00:00+00:00",
                metadata={},
            )
        )
    chunks.add(
        DocumentChunk(id=keyword_chunk_id, document_id="d-kw", text="quarterly budget")
    )
    chunks.add(
        DocumentChunk(
            id=semantic_chunk_id, document_id="d-sem", text="guitar music theory"
        )
    )
    embeddings.add(Embedding(model=PROVIDER_MODEL, vector=(0.0, 1.0)), keyword_chunk_id)
    embeddings.add(
        Embedding(model=PROVIDER_MODEL, vector=(1.0, 0.0)), semantic_chunk_id
    )
    connection.commit()


def _seed_database(database: Path) -> Path:
    """Seed a knowledge database file on a disposable connection."""
    connection = connect_database(database)
    try:
        _seed_knowledge(connection)
    finally:
        connection.close()
    return database


def _hit_chunk_ids(results: object) -> set[str]:
    assert isinstance(results, dict)
    assert results["status"] == "results"
    return {hit["chunk_id"] for hit in results["results"]}


class TestConnectAgentRegistryMode:
    def test_keyword_default_searches_the_fts5_backend(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        if os.environ.get(RETRIEVAL_MODE) is not None:
            monkeypatch.delenv(RETRIEVAL_MODE, raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        database = _seed_database(tmp_path / "agent.db")

        registry, connection = cli._connect_agent_registry(workspace, database)
        try:
            hits = _hit_chunk_ids(
                registry.execute("search_documents", {"query": "quarterly"})
            )
            assert hits == {"kw"}
        finally:
            connection.close()

    def test_hybrid_mode_fuses_keyword_and_semantic_backends(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(RETRIEVAL_MODE, "hybrid")
        monkeypatch.setenv(EMBEDDING_MODEL, "nomic-embed-text")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        database = _seed_database(tmp_path / "agent.db")

        def fake_embedder(settings, *, base_url=None) -> MappingProvider:
            return MappingProvider()

        monkeypatch.setattr(cli, "create_embedder", fake_embedder)

        registry, connection = cli._connect_agent_registry(workspace, database)
        try:
            hits = _hit_chunk_ids(
                registry.execute("search_documents", {"query": "quarterly"})
            )
            assert hits == {"kw", "sem"}
        finally:
            connection.close()

    def test_semantic_mode_without_embedding_model_fails_loudly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(RETRIEVAL_MODE, "semantic")
        monkeypatch.delenv(EMBEDDING_MODEL, raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        database = tmp_path / "agent.db"

        with pytest.raises(ValueError) as exc_info:
            cli._connect_agent_registry(workspace, database)
        assert EMBEDDING_MODEL in str(exc_info.value)

    def test_invalid_mode_fails_loudly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(RETRIEVAL_MODE, "vector")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        database = tmp_path / "agent.db"

        with pytest.raises(ValueError):
            cli._connect_agent_registry(workspace, database)


class TestMainSearchMode:
    def test_keyword_default_search_preserves_existing_behavior(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        if os.environ.get(RETRIEVAL_MODE) is not None:
            monkeypatch.delenv(RETRIEVAL_MODE, raising=False)
        database = _seed_database(tmp_path / "knowledge.db")

        monkeypatch.setattr(
            "sys.argv",
            [
                "personal-ai",
                "--database",
                str(database),
                "--search",
                "quarterly",
            ],
        )
        cli.main()
        output = capsys.readouterr().out
        assert output.startswith("1 hits\n")
        assert "chunk=kw" in output


class TestMCPBridgeMode:
    def test_keyword_default_builds_read_only_bridge(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        if os.environ.get(RETRIEVAL_MODE) is not None:
            monkeypatch.delenv(RETRIEVAL_MODE, raising=False)
        database = _seed_database(tmp_path / "knowledge.db")

        server = build_mcp_server(database)

        async def _list() -> set[str]:
            tools = await server.list_tools()
            return {tool.name for tool in tools}

        import asyncio

        assert {"search_documents", "get_document", "search_memory"} <= asyncio.run(
            _list()
        )

    def test_hybrid_mode_builds_bridge_with_both_backends(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(RETRIEVAL_MODE, "hybrid")
        monkeypatch.setenv(EMBEDDING_MODEL, "nomic-embed-text")
        database = _seed_database(tmp_path / "knowledge.db")

        import asyncio
        import json

        from mcp.types import TextContent

        def fake_embedder(settings, *, base_url=None) -> MappingProvider:
            return MappingProvider()

        from personal_ai import mcp_server

        monkeypatch.setattr(mcp_server, "create_embedder", fake_embedder)

        server = build_mcp_server(database)

        async def _search() -> set[str]:
            result = await server.call_tool(
                "search_documents", {"query": "quarterly", "limit": 10}
            )
            if result.structured_content is not None:
                return {(hit.get("chunk_id", "")) for hit in result.structured_content}
            for block in result.content:
                if isinstance(block, TextContent):
                    payload = json.loads(block.text)
                    return {hit["chunk_id"] for hit in payload["results"]}
            raise AssertionError("no search result content")

        assert asyncio.run(_search()) == {"kw", "sem"}

    def test_semantic_mode_without_embedding_model_fails_loudly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(RETRIEVAL_MODE, "semantic")
        monkeypatch.delenv(EMBEDDING_MODEL, raising=False)
        database = _seed_database(tmp_path / "knowledge.db")

        with pytest.raises(ValueError) as exc_info:
            build_mcp_server(database)
        assert EMBEDDING_MODEL in str(exc_info.value)
