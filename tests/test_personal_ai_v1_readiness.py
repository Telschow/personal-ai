"""Personal AI v1 readiness: end-to-end acceptance and determinism tests.

Phase 38. Empirical audit that the system is usable for daily personal AI
work with a realistic synthetic corpus, exercising the production consumer
path: ingestion -> durable storage -> chat tool registration -> retrieval
tool -> agent -> answer.

Hermetic by design: no Ollama server, no network, no real personal data, and
no randomness. The fixture corpus (7 documents, 13 chunks) uses stable
identifiers, aware ISO-8601 timestamps, and MIME metadata exactly like the
filesystem source adapter produces, so keyword searches, metadata filters,
restart recovery, and the agent path all behave as they would on real data.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from personal_ai.agent import Agent
from personal_ai.cli import run_search
from personal_ai.config import (
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    DEFAULT_CHAT_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    load_api_settings,
    load_embedding_settings,
    load_ollama_settings,
)
from personal_ai.documents import (
    Document,
    DocumentChunk,
    compute_content_hash,
    compute_document_id,
)
from personal_ai.ingestion import DocumentIngestor
from personal_ai.memory import MemoryService, MemoryStore
from personal_ai.ollama_client import ChatMessage, ChatResponse, ToolCall
from personal_ai.orchestration import ingest_source
from personal_ai.retrieval import (
    RETRIEVAL_ERROR_UNAVAILABLE,
    RETRIEVAL_STATUS_ERROR,
    RETRIEVAL_STATUS_NO_MATCHES,
    RETRIEVAL_STATUS_RESULTS,
    RetrievalService,
)
from personal_ai.sources.filesystem import FilesystemSourceAdapter
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)
from personal_ai.tools import create_default_registry
from personal_ai.tools.registry import ToolExecutionError
from personal_ai.tools.search import SearchTool

# ─── Realistic personal-AI fixture corpus ────────────────────────────────────
# Seven documents across projects, personal life, reading, health, and travel
# spanning January–February 2026. Content is synthetic but realistic enough
# that the acceptance queries read like real daily questions.

_DOCUMENTS = [
    # (id, source, source_type, created_at, mime_type, metadata)
    (
        "doc-alpha",
        "projects/alpha/notes.md",
        "file",
        "2026-01-05T09:00:00+00:00",
        "text/markdown",
        {"filename": "notes.md", "mime_type": "text/markdown", "project": "alpha"},
    ),
    (
        "doc-beta",
        "projects/beta/deployment.md",
        "file",
        "2026-02-10T09:00:00+00:00",
        "text/markdown",
        {"filename": "deployment.md", "mime_type": "text/markdown", "project": "beta"},
    ),
    (
        "doc-reading",
        "personal/reading.md",
        "file",
        "2026-02-02T09:00:00+00:00",
        "text/markdown",
        {"filename": "reading.md", "mime_type": "text/markdown"},
    ),
    (
        "doc-txt",
        "scratch/quick.txt",
        "file",
        "2026-02-15T09:00:00+00:00",
        "text/plain",
        {"filename": "quick.txt", "mime_type": "text/plain"},
    ),
    (
        "doc-journal",
        "journal/review.md",
        "file",
        "2026-02-28T09:00:00+00:00",
        "text/markdown",
        {"filename": "review.md", "mime_type": "text/markdown"},
    ),
    (
        "doc-health",
        "personal/health.md",
        "file",
        "2026-02-25T09:00:00+00:00",
        "text/markdown",
        {"filename": "health.md", "mime_type": "text/markdown"},
    ),
    (
        "doc-travel",
        "travel/ideas.md",
        "file",
        "2026-02-18T09:00:00+00:00",
        "text/markdown",
        {"filename": "ideas.md", "mime_type": "text/markdown"},
    ),
]

_CHUNKS = [
    # (chunk_id, document_id, chunk_index, text)
    (
        "alpha-architecture",
        "doc-alpha",
        0,
        (
            "Project Alpha architecture: the ingestion API retrieval worker and "
            "admin UI share one PostgreSQL database with hourly snapshots for "
            "disaster recovery."
        ),
    ),
    (
        "alpha-decision-backup",
        "doc-alpha",
        1,
        (
            "Project Alpha decision: keep nightly backup copies for twelve months "
            "then archive quarterly snapshots for three years. Retention is "
            "enforced by the scheduled job not by hand."
        ),
    ),
    (
        "alpha-todo-sync",
        "doc-alpha",
        2,
        (
            "Project Alpha todo: finish the sync worker refactor before the March "
            "release. No new features until the refactor lands."
        ),
    ),
    (
        "beta-deployment",
        "doc-beta",
        0,
        (
            "Project Beta deployments run on the staging cluster behind the "
            "gateway using blue-green releases with automated rollback."
        ),
    ),
    (
        "beta-timeline",
        "doc-beta",
        1,
        (
            "Project Beta timeline: staging review in March and production "
            "rollout after the April load test. The March release is the final "
            "go-no-go gate."
        ),
    ),
    (
        "reading-design",
        "doc-reading",
        0,
        (
            "Reading note on The Design of Everyday Things by Don Norman: "
            "affordances beat instructions for everyday objects. Good design is "
            "invisible."
        ),
    ),
    (
        "scratch-note",
        "doc-txt",
        0,
        (
            "Scratch note: remember to back up the honeymoon photos and buy "
            "travel insurance for the June trip before the end of February."
        ),
    ),
    (
        "journal-retro",
        "doc-journal",
        0,
        (
            "February journal: the sync worker refactor went smoothly and the "
            "new backup schedule freed up Sunday evenings for reading."
        ),
    ),
    (
        "health-sleep",
        "doc-health",
        0,
        (
            "Sleep routine: target seven and a half hours each night. Wind down "
            "starts at twenty-two thirty with no screens after twenty-three "
            "hundred."
        ),
    ),
    (
        "health-fitness",
        "doc-health",
        1,
        (
            "Fitness plan: three strength sessions and two easy cardio sessions "
            "each week. Focus on compound movements and consistent progression."
        ),
    ),
    (
        "travel-ideas",
        "doc-travel",
        0,
        (
            "Travel ideas: Japan self-drive over two weeks in October or the "
            "Dolomites hut trek in September with low gear requirements."
        ),
    ),
    (
        "travel-budget",
        "doc-travel",
        1,
        (
            "Travel budget: flights and hotels stay within the six thousand euro "
            "envelope for the year. Track monthly against this cap."
        ),
    ),
]

_TOTAL_CHUNKS = len(_CHUNKS)


def _seed_corpus(connection: sqlite3.Connection) -> None:
    """Seed the document and chunk stores with the realistic corpus."""
    documents = DocumentStore(connection)
    chunks = ChunkStore(connection)
    for doc_id, source, source_type, created_at, mime_type, metadata in _DOCUMENTS:
        documents.add(
            Document(
                id=doc_id,
                source=source,
                source_type=source_type,
                content_hash=compute_content_hash(f"{doc_id}".encode()),
                created_at=created_at,
                modified_at=created_at,
                mime_type=mime_type,
                metadata=dict(metadata),
            )
        )
    chunks.add_many(
        DocumentChunk(
            id=chunk_id,
            document_id=document_id,
            text=text,
            metadata={"chunk_index": chunk_index},
        )
        for chunk_id, document_id, chunk_index, text in _CHUNKS
    )


def _db_checksum(connection: sqlite3.Connection) -> str:
    """Deterministic checksum over every user table, row-ordered."""
    tables = sorted(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )
    parts: list[tuple[str, list[tuple[object, ...]]]] = []
    for table in tables:
        rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
        parts.append((table, rows))
    return hashlib.sha256(repr(parts).encode()).hexdigest()


class WiredKnowledge:
    """Stores plus the default chat registry over one connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        workspace: Path,
        *,
        retrieval: bool = True,
        memory: MemoryService | None = None,
        seed: bool = True,
    ) -> None:
        self.connection = connection
        self.documents = DocumentStore(connection)
        self.chunks = ChunkStore(connection)
        self.extractions = ExtractionStore(connection)
        self.retrieval_service = (
            RetrievalService(self.chunks, self.extractions, self.documents)
            if retrieval
            else None
        )
        if seed:
            _seed_corpus(connection)
        self.registry = create_default_registry(
            workspace,
            chunk_store=self.chunks,
            retrieval_service=self.retrieval_service,
            document_store=self.documents,
            memory_service=memory,
        )


def text_response(content: str) -> ChatResponse:
    return ChatResponse(content=content, model="test-model", done=True)


def tool_response(*calls: ToolCall) -> ChatResponse:
    return ChatResponse(content="", model="test-model", done=True, tool_calls=calls)


class ScriptedClient:
    """Stands in for OllamaClient, replaying scripted responses."""

    def __init__(self, *responses: ChatResponse) -> None:
        self._responses = iter(responses)
        self.calls: list[tuple[list[ChatMessage], list[dict[str, object]] | None]] = []

    def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, object]] | None = None,
    ) -> ChatResponse:
        self.calls.append((list(messages), list(tools) if tools is not None else None))
        try:
            return next(self._responses)
        except StopIteration:
            pytest.fail("ScriptedClient ran out of scripted responses")


# ─── Scenario A: exact decision retrieval ────────────────────────────────────


class TestScenarioAExactDecision:
    """The user asks about one specific decision; search retrieves exactly
    the right chunk as the top hit, and the agent grounds its answer on it."""

    def test_direct_tool_path_returns_decision_as_top_hit(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            envelope = wired.registry.execute(
                "search_documents",
                {"query": "backup retention decision", "limit": 5},
            )

            assert envelope["status"] == RETRIEVAL_STATUS_RESULTS
            results = envelope["results"]
            assert len(results) == 1
            assert results[0]["chunk_id"] == "alpha-decision-backup"
            assert results[0]["document_id"] == "doc-alpha"
            assert "Project Alpha decision" in results[0]["text"]
            assert results[0]["source_type"] == "file"
        finally:
            connection.close()

    def test_agent_grounds_answer_on_retrieved_decision(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            call = ToolCall(
                id="call_search_backup",
                name="search_documents",
                arguments={"query": "backup retention decision", "limit": 5},
            )
            client = ScriptedClient(
                tool_response(call),
                text_response(
                    "Keep nightly backup copies for twelve months, then archive "
                    "quarterly snapshots."
                ),
            )
            agent = Agent(client, wired.registry)
            result = agent.run(
                [ChatMessage(role="user", content="What did I decide about backup?")]
            )

            assert result == (
                "Keep nightly backup copies for twelve months, then archive "
                "quarterly snapshots."
            )
            tool_message = client.calls[1][0][2]
            assert tool_message.role == "tool"
            assert "alpha-decision-backup" in tool_message.content
            assert "Project Alpha decision" in tool_message.content
        finally:
            connection.close()

    def test_agent_observer_records_search_path(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            events: list[dict[str, object]] = []
            call = ToolCall(
                id="call_obs",
                name="search_documents",
                arguments={"query": "backup retention decision", "limit": 5},
            )
            client = ScriptedClient(tool_response(call), text_response("done"))
            agent = Agent(client, wired.registry, observer=events.append)
            agent.run([ChatMessage(role="user", content="backup?")])

            kinds = [event["event"] for event in events]
            assert kinds == ["round", "tool_start", "tool_end", "completed"]
            start = next(e for e in events if e["event"] == "tool_start")
            assert start["name"] == "search_documents"
            end = next(e for e in events if e["event"] == "tool_end")
            assert end["status"] == "ok"
        finally:
            connection.close()


# ─── Scenario B: paraphrase ──────────────────────────────────────────────────


class TestScenarioBParaphrase:
    """A paraphrase that keeps the core content terms still finds the right
    chunk. A genuine zero-overlap query honestly returns no_matches rather
    than fabricating results (the documented keyword-limitation behavior)."""

    def test_paraphrase_retaining_content_terms_finds_decision(
        self, tmp_path: Path
    ) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            envelope = wired.registry.execute(
                "search_documents", {"query": "keep the backup copies"}
            )

            assert envelope["status"] == RETRIEVAL_STATUS_RESULTS
            ids = [hit["chunk_id"] for hit in envelope["results"]]
            assert "alpha-decision-backup" in ids
        finally:
            connection.close()

    def test_zero_overlap_query_honestly_returns_no_matches(
        self, tmp_path: Path
    ) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            envelope = wired.registry.execute(
                "search_documents", {"query": "data storage horizon"}
            )

            assert envelope["status"] == RETRIEVAL_STATUS_NO_MATCHES
            assert envelope["results"] == []
            assert envelope["total_returned"] == 0
            assert envelope["truncated"] is False
            assert envelope["error"] is None
        finally:
            connection.close()

    def test_identical_queries_are_deterministic(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            first = wired.registry.execute(
                "search_documents", {"query": "backup retention decision", "limit": 10}
            )
            second = wired.registry.execute(
                "search_documents", {"query": "backup retention decision", "limit": 10}
            )

            assert first["status"] == second["status"]
            assert first["results"] == second["results"]
        finally:
            connection.close()


# ─── Scenario C: metadata filtering ──────────────────────────────────────────


class TestScenarioCFilterByProjectScope:
    """A date window narrows results to one project scope; MIME filters
    include and exclude deterministically."""

    def test_unfiltered_query_matches_multiple_documents(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            envelope = wired.registry.execute("search_documents", {"query": "worker"})

            assert envelope["status"] == RETRIEVAL_STATUS_RESULTS
            ids = [hit["chunk_id"] for hit in envelope["results"]]
            assert set(ids) == {
                "alpha-architecture",
                "alpha-todo-sync",
                "journal-retro",
            }
        finally:
            connection.close()

    def test_date_window_isolates_project_alpha(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            envelope = wired.registry.execute(
                "search_documents",
                {
                    "query": "worker",
                    "filter": {
                        "created_after": "2026-01-01T00:00:00+00:00",
                        "created_before": "2026-01-31T23:59:59+00:00",
                    },
                },
            )

            assert envelope["status"] == RETRIEVAL_STATUS_RESULTS
            ids = [hit["chunk_id"] for hit in envelope["results"]]
            assert set(ids) == {"alpha-architecture", "alpha-todo-sync"}
        finally:
            connection.close()

    def test_mime_filter_positive_and_negative(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            plain = wired.registry.execute(
                "search_documents",
                {"query": "back", "filter": {"mime_types": ["text/plain"]}},
            )
            markdown = wired.registry.execute(
                "search_documents",
                {"query": "back", "filter": {"mime_types": ["text/markdown"]}},
            )

            assert [hit["chunk_id"] for hit in plain["results"]] == ["scratch-note"]
            assert markdown["status"] == RETRIEVAL_STATUS_NO_MATCHES
            assert markdown["results"] == []
        finally:
            connection.close()

    def test_source_type_filter_includes_open(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            envelope = wired.registry.execute(
                "search_documents",
                {"query": "worker", "filter": {"source_types": ["file"]}},
            )

            assert envelope["status"] == RETRIEVAL_STATUS_RESULTS
            assert envelope["total_returned"] == 3
        finally:
            connection.close()


# ─── Scenario D: restart / recovery ──────────────────────────────────────────


class TestScenarioDRestart:
    """All knowledge is durable: closing and reopening the database loses
    nothing, and a registry built on the reopened store answers identically."""

    def test_data_persists_across_close_and_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "restart.db"

        first = connect_database(db_path)
        try:
            _seed_corpus(first)
            chunks = ChunkStore(first)
            initial_count = chunks.count()
            initial_hit = chunks.search("backup retention decision")
            assert initial_count == _TOTAL_CHUNKS
            assert initial_hit[0].chunk_id == "alpha-decision-backup"
        finally:
            first.close()

        second = connect_database(db_path)
        try:
            chunks = ChunkStore(second)
            assert chunks.count() == _TOTAL_CHUNKS
            reopened = chunks.search("backup retention decision")
            assert reopened[0].chunk_id == initial_hit[0].chunk_id
        finally:
            second.close()

    def test_registry_on_reopened_database_answers_identically(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "restart-registry.db"

        first = connect_database(db_path)
        _seed_corpus(first)
        first.close()

        second = connect_database(db_path)
        try:
            wired = WiredKnowledge(second, tmp_path)
            envelope = wired.registry.execute(
                "search_documents",
                {"query": "backup retention decision", "limit": 5},
            )

            assert envelope["status"] == RETRIEVAL_STATUS_RESULTS
            assert envelope["results"][0]["chunk_id"] == "alpha-decision-backup"
        finally:
            second.close()


# ─── Scenario E: retrieval failure ───────────────────────────────────────────


class TestScenarioERetrievalFailure:
    """An underlying store failure becomes the safe error envelope, never an
    empty successful result."""

    def test_failing_index_yields_error_envelope(self) -> None:
        class FailingIndex:
            def search(self, query: str, limit: int = 10, filters=None) -> tuple:
                raise RuntimeError("database unavailable")

        envelope = SearchTool(FailingIndex()).search_documents(
            {"query": "backup retention"}
        )

        assert envelope["status"] == RETRIEVAL_STATUS_ERROR
        assert envelope["error"] == RETRIEVAL_ERROR_UNAVAILABLE
        assert envelope["results"] == []
        assert envelope["total_returned"] == 0
        assert envelope["truncated"] is False
        assert envelope["query_length"] == len("backup retention")

    def test_empty_queries_return_no_matches(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            for query in ("", "   "):
                envelope = wired.registry.execute("search_documents", {"query": query})
                assert envelope["status"] == RETRIEVAL_STATUS_NO_MATCHES
                assert envelope["results"] == []
        finally:
            connection.close()


# ─── Read-only invariant ─────────────────────────────────────────────────────


class TestReadOnlyInvariant:
    """Search and fetch never mutate the database: a battery of read
    operations leaves every user table byte-identical."""

    def test_search_battery_leaves_database_unchanged(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path, retrieval=True)
            before = _db_checksum(connection)

            wired.registry.execute("search_documents", {"query": "worker", "limit": 3})
            wired.registry.execute("search_documents", {"query": "backup", "limit": 10})
            wired.registry.execute(
                "search_documents",
                {
                    "query": "release",
                    "filter": {"created_after": "2026-01-01T00:00:00+00:00"},
                },
            )
            wired.registry.execute("search_documents", {"query": "quantum nothing"})
            wired.registry.execute(
                "search_documents",
                {"query": "backup retention decision", "limit": 1},
            )
            wired.registry.execute("search_knowledge", {"query": "worker"})
            wired.registry.execute("get_document", {"document_id": "doc-alpha"})

            assert _db_checksum(connection) == before
        finally:
            connection.close()

    def test_get_document_does_not_mutate_database(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path, retrieval=True)
            before = _db_checksum(connection)

            wired.registry.execute(
                "get_document", {"document_id": "doc-alpha", "chunk_limit": 5}
            )
            wired.registry.execute("get_document", {"document_id": "doc-nonexistent"})

            assert _db_checksum(connection) == before
        finally:
            connection.close()


# ─── Memory isolation ────────────────────────────────────────────────────────


class TestMemoryIsolation:
    """The chat tool surface registers memory tools only when their
    dependencies are wired; search never exposes a write surface."""

    def test_registry_without_memory_has_no_memory_tools(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path, memory=None)
            names = [schema["function"]["name"] for schema in wired.registry.schemas()]

            assert "get_memory" not in names
            assert "propose_memory" not in names
            assert "search_memory" not in names
            assert "search_documents" in names
        finally:
            connection.close()

    def test_memory_service_enables_fetch_but_never_write(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            memory = MemoryService(MemoryStore(connection))
            wired = WiredKnowledge(connection, tmp_path, memory=memory)
            names = [schema["function"]["name"] for schema in wired.registry.schemas()]

            assert "get_memory" in names
            assert "propose_memory" not in names
        finally:
            connection.close()

    def test_search_never_writes_memory(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            memory = MemoryService(MemoryStore(connection))
            wired = WiredKnowledge(connection, tmp_path, memory=memory)
            before = memory.statistics()

            wired.registry.execute("search_documents", {"query": "worker"})
            wired.registry.execute("search_knowledge", {"query": "worker"})

            assert memory.statistics() == before
        finally:
            connection.close()


# ─── Error handling ──────────────────────────────────────────────────────────


class TestErrorHandling:
    """Invalid tool arguments raise before any query runs."""

    def test_invalid_filter_date_raises(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            with pytest.raises(ToolExecutionError):
                wired.registry.execute(
                    "search_documents",
                    {"query": "worker", "filter": {"created_after": "yesterday"}},
                )
        finally:
            connection.close()

    def test_non_string_query_raises(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            with pytest.raises(ToolExecutionError):
                wired.registry.execute("search_documents", {"query": 42})
        finally:
            connection.close()

    def test_boolean_and_negative_limits_raise(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            for bad_limit in (True, -1):
                with pytest.raises(ToolExecutionError):
                    wired.registry.execute(
                        "search_documents", {"query": "worker", "limit": bad_limit}
                    )
        finally:
            connection.close()

    def test_unknown_arguments_raise(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            with pytest.raises(ToolExecutionError):
                wired.registry.execute(
                    "search_documents", {"query": "worker", "sql": "DROP TABLE x"}
                )
        finally:
            connection.close()


# ─── Consumer tool surface ───────────────────────────────────────────────────


class TestConsumerToolSurface:
    """The default chat registry surface and envelope semantics."""

    def test_search_documents_registered_when_chunks_exist(
        self, tmp_path: Path
    ) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            names = [schema["function"]["name"] for schema in wired.registry.schemas()]

            assert "search_documents" in names
            search_schema = next(
                s
                for s in wired.registry.schemas()
                if s["function"]["name"] == "search_documents"
            )
            assert search_schema["function"]["parameters"]["required"] == ["query"]
        finally:
            connection.close()

    def test_limit_bounds_results_and_marks_truncated(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            envelope = wired.registry.execute(
                "search_documents", {"query": "backup", "limit": 1}
            )

            assert len(envelope["results"]) == 1
            assert envelope["truncated"] is True
            assert envelope["total_returned"] == 1
        finally:
            connection.close()

    def test_keyword_default_survives(self, tmp_path: Path) -> None:
        """The chat search tool remains the literal keyword FTS5 path: all
        query terms must appear in a match (no semantic/hybrid behavior)."""
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            in_one = wired.registry.execute(
                "search_documents", {"query": "backup copies"}
            )
            split_across = wired.registry.execute(
                "search_documents", {"query": "backup photos"}
            )
            # "backup" and "copies" co-occur only in the decision chunk;
            # "backup" and "photos" each appear in the corpus but never in a
            # single chunk, so the AND-match finds nothing.
            assert in_one["status"] == RETRIEVAL_STATUS_RESULTS
            assert [h["chunk_id"] for h in in_one["results"]] == [
                "alpha-decision-backup"
            ]
            assert split_across["status"] == RETRIEVAL_STATUS_NO_MATCHES
        finally:
            connection.close()

    def test_search_knowledge_unifies_documents(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path, retrieval=True)
            envelope = wired.registry.execute(
                "search_knowledge", {"query": "backup retention decision", "limit": 5}
            )

            assert envelope["status"] == RETRIEVAL_STATUS_RESULTS
            texts = [hit["text"] for hit in envelope["results"]]
            assert any("Project Alpha decision" in text for text in texts)
            assert all(hit["document_id"] is not None for hit in envelope["results"])
        finally:
            connection.close()

    def test_get_document_fetches_with_chunk_window(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path, retrieval=True)
            envelope = wired.registry.execute(
                "get_document", {"document_id": "doc-alpha", "chunk_limit": 5}
            )

            assert envelope["status"] == "ok"
            assert envelope["document"]["document_id"] == "doc-alpha"
            assert [c["chunk_id"] for c in envelope["chunks"]] == [
                "alpha-architecture",
                "alpha-decision-backup",
                "alpha-todo-sync",
            ]
        finally:
            connection.close()


# ─── Identity, provenance, determinism ───────────────────────────────────────


class TestIdentityAndDeterminism:
    """Stable document identity, content hashes, and deterministic chunk
    ordering that make ingestion idempotent."""

    def test_document_identity_is_stable(self) -> None:
        assert compute_document_id("file", "a.md", "hash-1") == compute_document_id(
            "file", "a.md", "hash-1"
        )
        assert compute_document_id("file", "a.md", "hash-1") != compute_document_id(
            "file", "a.md", "hash-2"
        )
        assert compute_content_hash(b"a") == compute_content_hash(b"a")
        assert compute_content_hash(b"a") != compute_content_hash(b"b")

    def test_chunk_listing_is_deterministic_and_ordered(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            _seed_corpus(connection)
            chunks = ChunkStore(connection)

            alpha = chunks.list_for_document("doc-alpha")
            assert [c.id for c in alpha] == [
                "alpha-architecture",
                "alpha-decision-backup",
                "alpha-todo-sync",
            ]
            assert alpha[0].metadata["chunk_index"] == 0
        finally:
            connection.close()

    def test_corpus_identity_and_provenance(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            wired = WiredKnowledge(connection, tmp_path)
            document = wired.documents.get("doc-alpha")
            assert document is not None
            assert document.source_type == "file"
            assert document.source == "projects/alpha/notes.md"

            envelope = wired.registry.execute("search_documents", {"query": "photos"})
            hit = envelope["results"][0]
            assert hit["chunk_id"] == "scratch-note"
            assert hit["document_id"] == "doc-txt"
            assert hit["source_type"] == "file"
            assert hit["source"] == "scratch/quick.txt"
        finally:
            connection.close()


# ─── Configuration defaults ──────────────────────────────────────────────────


class TestConfigurationDefaults:
    """Default configuration resolves to the production local-first values,
    and the embedding model may be absent without breaking ingestion."""

    def test_ollama_and_chat_defaults(self) -> None:
        assert load_ollama_settings({}).base_url == DEFAULT_OLLAMA_BASE_URL
        assert DEFAULT_CHAT_MODEL == "qwen3.5:9b"
        api = load_api_settings({})
        assert api.model == DEFAULT_CHAT_MODEL
        assert api.host == DEFAULT_API_HOST
        assert api.port == DEFAULT_API_PORT

    def test_embedding_model_defaults_to_none(self) -> None:
        assert load_embedding_settings({}).model is None


# ─── CLI user-facing search ──────────────────────────────────────────────────


class TestCLISearch:
    """The `--search` consumer path prints human-readable results."""

    def test_run_search_prints_decision_hit(self, tmp_path: Path, capsys) -> None:
        db_path = tmp_path / "cli.db"
        connection = connect_database(db_path)
        _seed_corpus(connection)
        connection.close()

        run_search("backup retention decision", db_path, limit=5)
        captured = capsys.readouterr()

        assert "1 hits" in captured.out
        assert "alpha-decision-backup" in captured.out
        assert "Project Alpha decision" in captured.out

    def test_run_search_no_matches(self, tmp_path: Path, capsys) -> None:
        db_path = tmp_path / "cli.db"
        connection = connect_database(db_path)
        _seed_corpus(connection)
        connection.close()

        run_search("data storage horizon", db_path, limit=5)
        captured = capsys.readouterr()

        assert "No matching documents." in captured.out


# ─── Realistic end-to-end ingestion workflow ─────────────────────────────────


class TestEndToEndIngestion:
    """Real files flow through the production pipeline (discovery ->
    classification -> chunking -> storage) and become searchable through the
    same consumer path the agent uses. Re-ingestion is idempotent."""

    def _workspace(self, tmp_path: Path) -> Path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "alpha-notes.md").write_text(
            "Project Alpha architecture and decision notes. We keep nightly "
            "backup copies for twelve months then archive quarterly snapshots "
            "for three years. Retention is enforced by the scheduled backup "
            "job, not by hand. The ingestion API, retrieval worker, and admin "
            "UI share one PostgreSQL database with hourly snapshots for "
            "disaster recovery and full business continuity planning across "
            "the whole engineering team this year.\n"
        )
        (workspace / "travel-plan.txt").write_text(
            "Travel planning for the year ahead. The two ideas are a Japan "
            "self-drive over two weeks in October or the Dolomites hut trek "
            "in September with low gear requirements. The budget caps flights "
            "and hotels at six thousand euro for the whole year and we track "
            "monthly spending against that envelope to stay on target.\n"
        )
        return workspace

    def _ingestor(
        self, connection: sqlite3.Connection
    ) -> tuple[DocumentIngestor, DocumentStore, ChunkStore, EmbeddingStore]:
        document_store = DocumentStore(connection)
        extraction_store = ExtractionStore(connection)
        chunk_store = ChunkStore(connection)
        embedding_store = EmbeddingStore(connection)
        ingestor = DocumentIngestor(
            document_store,
            extraction_store,
            None,
            chunk_store,
            embedding_store,
        )
        return ingestor, document_store, chunk_store, embedding_store

    def test_workspace_files_become_searchable(self, tmp_path: Path) -> None:
        workspace = self._workspace(tmp_path)
        db_path = tmp_path / "e2e.db"
        connection = connect_database(db_path)
        try:
            ingestor, _document_store, chunk_store, _ = self._ingestor(connection)
            summary = ingest_source(FilesystemSourceAdapter(workspace), ingestor)

            assert summary.documents == 2
            assert summary.chunk_count >= 2
            assert chunk_store.count() >= 2

            wired = WiredKnowledge(connection, tmp_path, seed=False)
            assert wired.chunks.count() == chunk_store.count()

            envelope = wired.registry.execute(
                "search_documents", {"query": "nightly backup copies"}
            )
            assert envelope["status"] == RETRIEVAL_STATUS_RESULTS
            assert any("Project Alpha" in hit["text"] for hit in envelope["results"])
        finally:
            connection.close()

    def test_reingestion_is_idempotent(self, tmp_path: Path) -> None:
        workspace = self._workspace(tmp_path)
        db_path = tmp_path / "idempotent.db"
        adapter = FilesystemSourceAdapter(workspace)

        first = connect_database(db_path)
        try:
            ingestor, _, chunk_store, _ = self._ingestor(first)
            ingest_source(adapter, ingestor)
            first_doc_count = _documents_count(first)
            first_chunk_count = chunk_store.count()
        finally:
            first.close()

        second = connect_database(db_path)
        try:
            ingestor, _, chunk_store, _ = self._ingestor(second)
            ingest_source(adapter, ingestor)
            second_doc_count = _documents_count(second)
            second_chunk_count = chunk_store.count()
        finally:
            second.close()

        assert second_doc_count == first_doc_count
        assert second_chunk_count == first_chunk_count
        assert first_doc_count >= 1
        assert first_chunk_count >= 2


def _documents_count(connection: sqlite3.Connection) -> int:
    return len(DocumentStore(connection).list_documents())


class TestFixtureCorpusScale:
    """The synthetic corpus is large enough to be a realistic acceptance
    fixture (5+ documents, 10+ chunks) and every seeded document is
    retrievable."""

    def test_corpus_meets_scale_target(self) -> None:
        assert len(_DOCUMENTS) >= 5
        assert _TOTAL_CHUNKS >= 10

    def test_every_seeded_document_is_chunked(self, tmp_path: Path) -> None:
        connection = connect_database(":memory:")
        try:
            _seed_corpus(connection)
            chunks = ChunkStore(connection)
            for doc_id, _, _, _, _, _ in _DOCUMENTS:
                assert chunks.list_for_document(doc_id)
        finally:
            connection.close()
