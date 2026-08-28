"""Command-line interface for the personal AI agent."""

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from personal_ai.agent import Agent, AgentObserver
from personal_ai.ingestion import DocumentIngestor
from personal_ai.ollama_client import ChatMessage, OllamaClient
from personal_ai.ollama_structured import OllamaStructuredExtractor
from personal_ai.orchestration import ingest_source
from personal_ai.retrieval import (
    RetrievalService,
    SearchDocumentsRequest,
    search_documents,
)
from personal_ai.sources.base import SourceError
from personal_ai.sources.registry import known_source_types, resolve_source_adapter
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentStore,
    EmbeddingStore,
    EventStore,
    ExtractionStore,
    connect_database,
)
from personal_ai.tools import ToolRegistry, create_default_registry

MODEL = "qwen3.5:9b"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the personal AI agent.")
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Directory the agent is allowed to inspect.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        help=(
            "SQLite database holding the ingested knowledge base. In agent "
            "mode this also enables the knowledge-search and temporal-event "
            "tools."
        ),
    )
    parser.add_argument(
        "--ingest",
        dest="ingest_source_args",
        nargs=2,
        metavar=("SOURCE", "PATH"),
        help=(
            "Ingest a source directory into the knowledge base instead of "
            "running the agent; requires --database. Known sources: "
            + ", ".join(known_source_types())
            + "."
        ),
    )
    parser.add_argument(
        "--search",
        dest="search_query",
        metavar="QUERY",
        help=(
            "Search the knowledge base instead of running the agent; "
            "requires --database."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of search results (default: 10).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Print operational agent progress (rounds, tool calls, timing, "
            "completion) to stderr. The final answer is unaffected."
        ),
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Question or instruction for the agent.",
    )

    args = parser.parse_args()
    modes = [
        flag
        for flag, value in (
            ("--search", args.search_query),
            ("--ingest", args.ingest_source_args),
        )
        if value is not None
    ]
    if len(modes) > 1:
        parser.error(f"{modes[0]} and {modes[1]} are mutually exclusive")
    if not modes:
        # Agent mode keeps the original required-argument behavior.
        if args.workspace is None:
            parser.error("the following arguments are required: --workspace")
        if args.prompt is None:
            parser.error("the following arguments are required: prompt")
    elif args.database is None:
        parser.error(f"{modes[0]} requires --database")
    return args


def run_search(query: str, database: Path, limit: int) -> None:
    """Print keyword-search results from the knowledge database."""
    connection = connect_database(database)
    try:
        chunk_store = ChunkStore(connection)
        results = search_documents(
            chunk_store, SearchDocumentsRequest(query=query, limit=limit)
        )
    finally:
        connection.close()

    if not results:
        print("No matching documents.")
        return

    print(f"{len(results)} hits")
    for position, hit in enumerate(results, start=1):
        print(
            f"{position}. rank={hit.rank:.4f} "
            f"chunk={hit.chunk_id} document={hit.document_id}"
        )
        print(f"   {hit.text}")


def run_ingest(source_type: str, source_path: Path, database: Path) -> None:
    """Ingest one source directory into the knowledge database."""
    if not source_path.is_dir():
        raise SystemExit(f"Source path is not a directory: {source_path}")

    try:
        adapter = resolve_source_adapter(source_type, source_path)
    except SourceError as exc:
        raise SystemExit(str(exc)) from exc

    connection = connect_database(database)
    try:
        document_store = DocumentStore(connection)
        extraction_store = ExtractionStore(connection)
        chunk_store = ChunkStore(connection)
        embedding_store = EmbeddingStore(connection)

        with OllamaClient(model=MODEL) as client:
            ingestor = DocumentIngestor(
                document_store,
                extraction_store,
                OllamaStructuredExtractor(client),
                chunk_store,
                embedding_store,
            )
            summary = ingest_source(adapter, ingestor)
    finally:
        connection.close()

    print(f"source_type: {summary.source_type}")
    print(f"documents: {summary.documents}")
    print("kind_counts:")
    for kind in sorted(summary.kind_counts):
        print(f"  {kind}: {summary.kind_counts[kind]}")
    print(f"chunks: {summary.chunk_count}")


def _connect_agent_registry(
    workspace: Path, database: Path
) -> tuple[ToolRegistry, sqlite3.Connection]:
    """Build the agent tool registry backed by the knowledge and event stores.

    Opens the workspace knowledge database and wires the existing stores into
    the default tool registry so the agent can use ``search_knowledge``
    (via :class:`~personal_ai.retrieval.RetrievalService`) and ``query_events``
    (via :class:`~personal_ai.storage.events.EventStore`).

    Returns the registry together with the underlying connection so the caller
    can keep the stores alive for the whole agent session and close it
    cleanly on exit.
    """
    connection = connect_database(database)
    try:
        document_store = DocumentStore(connection)
        chunk_store = ChunkStore(connection)
        extraction_store = ExtractionStore(connection)
        conversation_store = ConversationStore(connection)
        event_store = EventStore(connection)
        retrieval_service = RetrievalService(
            chunk_store,
            extraction_store,
            document_store,
            conversation_store,
        )
        registry = create_default_registry(
            workspace,
            chunk_store=chunk_store,
            retrieval_service=retrieval_service,
            event_store=event_store,
        )
        return registry, connection
    except BaseException:
        connection.close()
        raise


@dataclass
class BuiltAgent:
    """A fully constructed, ready-to-run Agent together with its resources.

    ``build_agent`` is the single canonical construction path for the
    production Agent. Both the CLI and the HTTP API use it so the two entry
    points share identical wiring (model, ToolRegistry, stores, Agent). The
    Ollama client is entered at construction and closed on :meth:`close`,
    preserving the historical ``with OllamaClient(...)`` lifecycle.
    """

    agent: Agent
    client: OllamaClient
    connection: sqlite3.Connection | None = None

    def __enter__(self) -> Agent:
        return self.agent

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the Ollama client and any opened database connection."""
        exit_client = getattr(self.client, "__exit__", None)
        if callable(exit_client):
            exit_client(None, None, None)
        if self.connection is not None:
            self.connection.close()


def build_agent(
    workspace: Path,
    database: Path | None = None,
    *,
    model: str = MODEL,
    observer: AgentObserver | None = None,
) -> BuiltAgent:
    """Construct the production Agent from workspace and database settings.

    This is the only place a production Agent is assembled. The ToolRegistry
    is wired from the workspace-database stores (knowledge retrieval + event
    queries) through the default registry, and the Agent is built around a
    caller-supplied ``OllamaClient``. The client is entered as a context
    manager (as the CLI has always done) and the returned
    :class:`BuiltAgent` holds the open resources, closed via ``close()`` or
    ``with``.
    """
    connection: sqlite3.Connection | None = None
    if database is not None:
        registry, connection = _connect_agent_registry(workspace, database)
    else:
        registry = create_default_registry(workspace)

    client = OllamaClient(model=model)
    enter_client = getattr(client, "__enter__", None)
    if callable(enter_client):
        enter_client()
    agent = Agent(client, registry, observer=observer)
    return BuiltAgent(agent=agent, client=client, connection=connection)


def _make_agent_observer() -> AgentObserver:
    """Return an observer that prints operational agent progress to stderr.

    Only round number, tool names, sanitized tool arguments, success/error
    status, timing, and completion state are printed. Hidden model reasoning,
    secrets, and raw tool payloads are never exposed.
    """

    def observe(event: dict[str, object]) -> None:
        kind = event["event"]
        if kind == "round":
            calls = event["tool_calls"]
            print(
                f"[agent] round {event['round']} ({len(calls)} tool call(s))",
                file=sys.stderr,
            )
        elif kind == "tool_start":
            print(
                f"[tool] {event['name']}({event['arguments']})",
                file=sys.stderr,
            )
        elif kind == "tool_end":
            print(
                f"[tool] {event['name']} "
                f"{event['status']} in {event['latency_sec']:.2f}s",
                file=sys.stderr,
            )
        elif kind == "completed":
            print(
                f"[agent] completed in {event['latency_sec']:.2f}s "
                f"after {event['round']} rounds",
                file=sys.stderr,
            )
        elif kind == "max_rounds":
            print(
                f"[agent] stopped: exceeded max rounds "
                f"after {event['latency_sec']:.2f}s",
                file=sys.stderr,
            )

    return observe


def main() -> None:
    args = parse_args()

    if args.search_query is not None:
        run_search(args.search_query, args.database, args.limit)
        return

    if args.ingest_source_args is not None:
        source_type, source_path = args.ingest_source_args
        run_ingest(source_type, Path(source_path), args.database)
        return

    workspace = args.workspace.resolve()

    if not workspace.is_dir():
        raise SystemExit(f"Workspace is not a directory: {workspace}")

    observer = _make_agent_observer() if args.verbose else None
    built = build_agent(
        workspace,
        args.database,
        model=MODEL,
        observer=observer,
    )
    try:
        response = built.agent.run(
            [
                ChatMessage(
                    role="user",
                    content=args.prompt,
                )
            ]
        )
    finally:
        built.close()

    print(response)


if __name__ == "__main__":
    main()
