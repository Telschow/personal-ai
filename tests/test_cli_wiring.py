"""Integration tests for wiring knowledge and temporal-event stores into the
CLI agent mode.

Phase 15 wires the existing stores/services into ``create_default_registry(...)``
so that agent mode run *with* ``--database`` exposes the ``search_knowledge``
and ``query_events`` tools, while agent mode run *without* ``--database`` keeps
its original filesystem-only registry (one positional argument).

These tests exercise the real ``cli._connect_agent_registry`` construction path
(end-to-end through ``cli.main()`` where practical) against temporary SQLite
databases. They require no Ollama server, no network, and no real corpus.
"""

from pathlib import Path
from typing import Self

import pytest

from personal_ai import cli
from personal_ai.documents.conversations import Conversation, ConversationMessage
from personal_ai.documents.models import compute_content_hash
from personal_ai.events.models import EVENT_TYPE_VIDEO_WATCH, Event, compute_event_id
from personal_ai.ingestion import DocumentIngestor
from personal_ai.sources.models import SourceRecord
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentStore,
    EmbeddingStore,
    EventStore,
    ExtractionStore,
)

TEXT_HEAVY_TEXT = "Meeting note about BCG consulting project. " * 20

EVENT_TIME = "2026-01-10T09:00:00+00:00"


def _source_record(payload: bytes, source_key: str = "notes.txt") -> SourceRecord:
    return SourceRecord(
        source_type="file",
        source_key=source_key,
        content_hash=compute_content_hash(payload),
        created_at="2026-08-26T10:00:00+00:00",
        modified_at="2026-08-26T10:00:00+00:00",
        payload=payload,
        metadata={"mime_type": "text/plain"},
    )


class FakeStructuredExtractor:
    """Deterministic structured extractor used to build real knowledge rows."""

    def extract(self, extraction):
        from personal_ai.documents.structured import StructuredExtraction

        return StructuredExtraction(
            document_id=extraction.document_id,
            summary="Summary about BCG project",
            people=("Daniel Telschow",),
            organizations=("BCG",),
            projects=("Project Apollo",),
            goals=("Career advancement",),
            topics=("consulting", "strategy"),
        )


def _watch_event(event_time: str, video_id: str, channel: str, title: str) -> Event:
    url = f"https://www.youtube.com/watch?v={video_id}"
    return Event(
        id=compute_event_id("youtube", EVENT_TYPE_VIDEO_WATCH, event_time, url),
        event_type=EVENT_TYPE_VIDEO_WATCH,
        event_time=event_time,
        source="youtube",
        url=url,
        channel_name=channel,
        title=title,
        duration_seconds=300.0,
        metadata={"video_id": video_id},
    )


class TestConnectAgentRegistry:
    """The real CLI construction path (used by ``main`` with ``--database``)."""

    def test_registry_exposes_and_executes_knowledge_and_event_tools(
        self, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        database = tmp_path / "agent.db"

        registry, connection = cli._connect_agent_registry(workspace, database)
        try:
            names = {tool["function"]["name"] for tool in registry.schemas()}
            assert "list_directory" in names
            assert "search_documents" in names
            assert "search_knowledge" in names
            assert "query_events" in names
            # Exactly the expected tools; no accidental duplication/re-design.
            assert names == {
                "list_directory",
                "search_documents",
                "search_knowledge",
                "query_events",
            }

            # Populate knowledge rows through the real ingest path on the same
            # connection that backs the registry's retrieval service.
            document_store = DocumentStore(connection)
            extraction_store = ExtractionStore(connection)
            chunk_store = ChunkStore(connection)
            embedding_store = EmbeddingStore(connection)
            DocumentIngestor(
                document_store,
                extraction_store,
                FakeStructuredExtractor(),
                chunk_store,
                embedding_store,
            ).ingest(_source_record(TEXT_HEAVY_TEXT.encode()))

            # Populate temporal events through the real event store.
            event_store = EventStore(connection)
            event_store.save_events(
                (
                    _watch_event(EVENT_TIME, "AAA", "FinanceTV", "Finance Deep Dive"),
                    _watch_event(
                        "2026-02-01T12:00:00+00:00",
                        "BBB",
                        "GardenTV",
                        "Gardening Basics",
                    ),
                )
            )

            # search_knowledge executes against the real retrieval service.
            knowledge_results = registry.execute("search_knowledge", {"query": "BCG"})
            assert isinstance(knowledge_results, list)
            assert len(knowledge_results) > 0
            texts = [
                r["text"]
                for r in knowledge_results
                if r["result_type"] == "structured_extraction"
            ]
            assert any("Summary about BCG project" in text for text in texts)

            # query_events executes against the real event store, including the
            # Phase 14 keyword filter.
            event_results = registry.execute(
                "query_events",
                {"operation": "events", "keyword": "finance"},
            )
            assert isinstance(event_results, list)
            assert len(event_results) == 1
            assert event_results[0]["title"] == "Finance Deep Dive"

            all_events = registry.execute("query_events", {})
            assert isinstance(all_events, list)
            assert len(all_events) == 2
        finally:
            connection.close()

    def test_search_knowledge_created_before_filters_conversations(
        self, tmp_path: Path
    ) -> None:
        """Phase 16: search_knowledge time bounds are honored for conversation
        results through the full registry path
        (search_knowledge -> RetrievalService -> ConversationStore)."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        database = tmp_path / "agent.db"

        registry, connection = cli._connect_agent_registry(workspace, database)
        try:
            conversation_store = ConversationStore(connection)
            conversation_store.save_conversation(
                Conversation(
                    id="conv-1",
                    title="BCG Career",
                    source_type="gemini",
                    created_at="2026-01-01T00:00:00+00:00",
                    modified_at="2026-02-16T00:00:00+00:00",
                    metadata={},
                )
            )
            conversation_store.save_message(
                ConversationMessage(
                    id="m-jan",
                    conversation_id="conv-1",
                    message_index=0,
                    role="user",
                    speaker="User",
                    content_text="BCG early January",
                    content_type="text",
                    timestamp="2026-01-15T00:00:00+00:00",
                )
            )
            conversation_store.save_message(
                ConversationMessage(
                    id="m-feb",
                    conversation_id="conv-1",
                    message_index=1,
                    role="user",
                    speaker="User",
                    content_text="BCG mid February",
                    content_type="text",
                    timestamp="2026-02-15T00:00:00+00:00",
                )
            )

            results = registry.execute(
                "search_knowledge",
                {
                    "query": "BCG",
                    "filter": {"created_before": "2026-02-01T00:00:00+00:00"},
                },
            )
            conv_texts = [
                r["text"] for r in results if r["result_type"] == "conversation"
            ]
            assert "BCG early January" in conv_texts
            assert "BCG mid February" not in conv_texts
        finally:
            connection.close()


class TestMainWithDatabase:
    """End-to-end wiring of ``cli.main()`` agent mode with ``--database``."""

    def test_agent_with_database_gets_store_backed_registry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        captured: dict[str, object] = {}
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        database = tmp_path / "agent.db"

        # Pre-populate the database file so the registry's views have data.
        from personal_ai.storage import EventStore, connect_database

        connection = connect_database(database)
        try:
            event_store = EventStore(connection)
            event_store.save_events(
                (_watch_event(EVENT_TIME, "AAA", "FinanceTV", "Finance Deep Dive"),)
            )
        finally:
            connection.close()

        class FakeClient:
            def __enter__(self) -> Self:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        class FakeAgent:
            def __init__(
                self, client: object, registry: object, observer: object = None
            ) -> None:
                captured["registry"] = registry

            def run(self, messages: object) -> str:
                return "Agent response."

        monkeypatch.setattr(cli, "OllamaClient", lambda model: FakeClient())
        monkeypatch.setattr(cli, "Agent", FakeAgent)
        monkeypatch.setattr(
            "sys.argv",
            [
                "personal-ai",
                "--workspace",
                str(workspace),
                "--database",
                str(database),
                "What did I watch?",
            ],
        )

        cli.main()

        registry = captured["registry"]
        names = {t["function"]["name"] for t in registry.schemas()}
        assert "search_knowledge" in names
        assert "query_events" in names
        assert "list_directory" in names
        assert capsys.readouterr().out == "Agent response.\n"

    def test_agent_without_database_calls_registry_single_positional(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        captured: dict[str, object] = {}

        class FakeRegistry:
            pass

        class FakeClient:
            def __enter__(self) -> Self:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        class FakeAgent:
            def __init__(
                self, client: object, registry: object, observer: object = None
            ) -> None:
                captured["registry"] = registry

            def run(self, messages: object) -> str:
                return "Agent response."

        registry = FakeRegistry()

        def fake_create_default_registry(path):
            # Prove backward compatibility: exactly one positional argument.
            captured["registry"] = registry
            return registry

        monkeypatch.setattr(
            cli, "create_default_registry", fake_create_default_registry
        )
        monkeypatch.setattr(cli, "OllamaClient", lambda model: FakeClient())
        monkeypatch.setattr(cli, "Agent", FakeAgent)
        monkeypatch.setattr(
            "sys.argv",
            [
                "personal-ai",
                "--workspace",
                str(tmp_path),
                "What files are here?",
            ],
        )

        cli.main()

        assert captured["registry"] is registry
        assert capsys.readouterr().out == "Agent response.\n"


class TestVerboseMode:
    """``--verbose`` exposes operational progress on stderr without altering
    the final answer on stdout or exposing hidden reasoning."""

    def _run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys,
        tmp_path: Path,
        verbose_args: list[str],
    ) -> dict[str, object]:
        captured: dict[str, object] = {}
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        class FakeClient:
            def __enter__(self) -> Self:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        class FakeAgent:
            def __init__(
                self, client: object, registry: object, observer: object = None
            ) -> None:
                captured["observer"] = observer
                if observer is not None:
                    observer(
                        {
                            "event": "tool_start",
                            "name": "search_knowledge",
                            "arguments": {"query": "'BCG'"},
                        }
                    )
                    observer({"event": "completed", "round": 1, "latency_sec": 1.5})

            def run(self, messages: object) -> str:
                return "Agent response."

        monkeypatch.setattr(cli, "OllamaClient", lambda model: FakeClient())
        monkeypatch.setattr(cli, "Agent", FakeAgent)
        monkeypatch.setattr(
            "sys.argv",
            ["personal-ai", "--workspace", str(workspace), *verbose_args, "Prompt?"],
        )

        cli.main()
        return captured

    def test_verbose_emits_operational_info_to_stderr(
        self, monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
    ) -> None:
        captured = self._run(monkeypatch, capsys, tmp_path, ["--verbose"])
        assert captured["observer"] is not None
        captured_out = capsys.readouterr()
        assert captured_out.out == "Agent response.\n"
        assert "[tool] search_knowledge" in captured_out.err
        assert "[agent] completed" in captured_out.err
        assert "round" in captured_out.err

    def test_default_mode_has_no_observer_and_no_stderr_output(
        self, monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
    ) -> None:
        captured = self._run(monkeypatch, capsys, tmp_path, [])
        assert captured["observer"] is None
        captured_out = capsys.readouterr()
        assert captured_out.out == "Agent response.\n"
        assert captured_out.err == ""
