"""Command-line interface for the personal AI agent."""

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from personal_ai.agent import Agent, AgentObserver
from personal_ai.config import load_ollama_settings, load_vision_settings
from personal_ai.conversation_ingestion import (
    ingest_chatgpt_conversations,
    ingest_gemini_conversations,
)
from personal_ai.event_ingestion import (
    ingest_chrome_history,
    ingest_youtube_history,
)
from personal_ai.ingestion import DocumentIngestor
from personal_ai.memory import (
    ACTIVITY,
    CHROME_HISTORY_EVENT_SOURCE,
    CONVERSATION_SOURCE_TYPES,
    EMAIL,
    FINANCIAL,
    GENERIC_DOCUMENT_SOURCE,
    WORKOUTS,
    ChatMemory,
    ConversationCurationAdapter,
    ConversationMemoryIngestor,
    CurationConfig,
    CurationError,
    CurationExtractionMode,
    CurationReport,
    CurationStore,
    DocumentCurationAdapter,
    EventCurationAdapter,
    MemoryCurationRunner,
    MemoryService,
    WorkoutCurationAdapter,
    open_memory_store,
)
from personal_ai.memory.corpus_audit import (
    DEFAULT_AUDIT_LIMIT,
    DEFAULT_AUDIT_MAX_MESSAGES,
)
from personal_ai.memory.curation import (
    DEFAULT_MAX_MODEL_CALLS,
    DEFAULT_UNIT_TIMEOUT_SECONDS,
    CurationAdapterRegistry,
)
from personal_ai.memory.orchestration import (
    DEFAULT_ORCHESTRATION_LIMIT,
    DEFAULT_ORCHESTRATION_MAX_MODEL_CALLS,
    DEFAULT_ORCHESTRATION_SAMPLE,
)
from personal_ai.memory.proposals import DEFAULT_MAX_MESSAGES, DEFAULT_MAX_RETRIES
from personal_ai.memory.store import MemoryStore
from personal_ai.ollama_client import ChatMessage, OllamaClient
from personal_ai.ollama_structured import OllamaStructuredExtractor
from personal_ai.ollama_vision import OllamaVisionExtractor
from personal_ai.orchestration import ingest_source
from personal_ai.people import (
    ChatPeople,
    PersonIndexer,
    PersonStore,
)
from personal_ai.retrieval import (
    RetrievalService,
    SearchDocumentsRequest,
    search_documents,
)
from personal_ai.sources.base import SourceError
from personal_ai.sources.chrome_history import SOURCE_TYPE as CHROME_SOURCE_TYPE
from personal_ai.sources.email import SOURCE_TYPE as EMAIL_SOURCE_TYPE
from personal_ai.sources.financial import SOURCE_TYPE as FINANCIAL_SOURCE_TYPE
from personal_ai.sources.registry import known_source_types, resolve_source_adapter
from personal_ai.sources.youtube_history import SOURCE_TYPE as YOUTUBE_SOURCE_TYPE
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentStore,
    EmbeddingStore,
    EventStore,
    ExtractionStore,
    VisionStore,
    connect_database,
)
from personal_ai.tools import ToolRegistry, create_default_registry
from personal_ai.tools.personal_context import PersonalContextService
from personal_ai.workouts import (
    WorkoutQueryService,
    WorkoutStore,
    import_workout_directory,
    open_workout_store,
)

MODEL = "qwen3.5:9b"


EVENT_SOURCE_TYPES = (CHROME_SOURCE_TYPE, YOUTUBE_SOURCE_TYPE)


def is_conversation_source(source_type: str) -> bool:
    """True when ``--ingest`` routes to the conversation store."""
    return source_type in CONVERSATION_SOURCE_TYPES


def ingestable_source_types() -> tuple[str, ...]:
    """All source types accepted by ``--ingest`` (document + event sources).

    Document sources come from the source-adapter registry; event sources
    are the temporal/chrome-history and YouTube history exports, which are
    ingested through the event store rather than the document pipeline.
    Conversation sources (chatgpt/gemini) are kept in the list because they
    remain valid ``--ingest`` targets; they route to the conversation store.
    """
    return tuple(known_source_types()) + EVENT_SOURCE_TYPES


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "workouts":
        args = _build_workouts_parser().parse_args(argv[1:])
        args.command = "workouts"
        return args
    if argv and argv[0] == "memory":
        args = _build_memory_parser().parse_args(argv[1:])
        args.command = "memory"
        return args
    if argv and argv[0] == "people":
        args = _build_people_parser().parse_args(argv[1:])
        args.command = "people"
        return args
    return _parse_agent_args(argv)


def _parse_agent_args(argv: list[str]) -> argparse.Namespace:
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
            + ", ".join(ingestable_source_types())
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
        "--memory",
        action="store_true",
        help=(
            "After --ingest of a conversation source (chatgpt/gemini), run "
            "bounded, deterministic memory extraction over the stored "
            "conversations and print an aggregate-only report."
        ),
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

    if args.memory:
        if args.ingest_source_args is None:
            parser.error("--memory requires --ingest with a conversation source")
        source_type = args.ingest_source_args[0]
        if not is_conversation_source(source_type):
            parser.error(
                "--memory is only supported for conversation sources "
                f"({', '.join(sorted(CONVERSATION_SOURCE_TYPES))})"
            )
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
        vision_store = VisionStore(connection)
        vision_settings = load_vision_settings()

        def build_ingestor(
            client: OllamaClient | None,
            vision_extractor: OllamaVisionExtractor | None = None,
        ) -> DocumentIngestor:
            extractor = None if client is None else OllamaStructuredExtractor(client)
            return DocumentIngestor(
                document_store,
                extraction_store,
                extractor,
                chunk_store,
                embedding_store,
                vision_extractor=vision_extractor,
                vision_store=vision_store,
            )

        if source_type in (EMAIL_SOURCE_TYPE, FINANCIAL_SOURCE_TYPE):
            # Email and financial extraction are fully local and deterministic;
            # no model call is needed, so structured and vision extraction are
            # disabled entirely.
            summary = ingest_source(adapter, build_ingestor(None))
        else:
            with OllamaClient(model=MODEL) as client:
                if vision_settings.model:
                    with OllamaClient(model=vision_settings.model) as vision_client:
                        vision_extractor = OllamaVisionExtractor(
                            vision_client,
                            prompt_version=vision_settings.prompt_version,
                        )
                        summary = ingest_source(
                            adapter, build_ingestor(client, vision_extractor)
                        )
                else:
                    summary = ingest_source(adapter, build_ingestor(client))
    finally:
        connection.close()

    print(f"source_type: {summary.source_type}")
    print(f"documents: {summary.documents}")
    print("kind_counts:")
    for kind in sorted(summary.kind_counts):
        print(f"  {kind}: {summary.kind_counts[kind]}")
    print(f"chunks: {summary.chunk_count}")


def run_event_ingest(source_type: str, source_path: Path, database: Path) -> None:
    """Ingest one temporal/activity source (chrome_history, youtube) into the event store.

    Event sources are fully local and deterministic: they are parsed into
    typed events and persisted through :class:`EventStore` with no model call.
    Idempotency follows from deterministic event identity, so re-running the
    same unchanged source inserts no new rows.
    """
    if not source_path.is_dir():
        raise SystemExit(f"Source path is not a directory: {source_path}")

    connection = connect_database(database)
    try:
        store = EventStore(connection)
        if source_type == CHROME_SOURCE_TYPE:
            summary = ingest_chrome_history(source_path, store)
        elif source_type == YOUTUBE_SOURCE_TYPE:
            summary = ingest_youtube_history(source_path, store)
        else:
            raise SourceError(f"Unknown event source type {source_type!r}")
    finally:
        connection.close()

    print(f"source_type: {summary.source_type}")
    print(f"files: {summary.files_discovered}")
    print(f"records: {summary.records_discovered}")
    print(f"events_stored: {summary.events_stored}")
    if source_type == CHROME_SOURCE_TYPE:
        print(f"search_queries: {summary.search_queries}")
        print(f"url_visits: {summary.url_visits}")
    else:
        print(f"video_watches: {summary.video_watches}")
        print(f"youtube_searches: {summary.youtube_searches}")
    print(f"skipped: {summary.skipped}")


def run_conversation_ingest(
    source_type: str,
    source_path: Path,
    database: Path,
    *,
    memory: bool,
) -> None:
    """Ingest a conversation export (chatgpt/gemini) into the conversation store.

    Conversation sources are fully local and deterministic: exports are parsed
    into typed conversations and messages and persisted through
    :class:`ConversationStore` with no model call. Idempotency follows from
    deterministic (SHA-256) conversation/message identities, so re-running the
    same unchanged source inserts no new rows.

    When ``memory`` is true, bounded deterministic memory extraction runs over
    the stored conversations of this source type and its aggregate-only report
    is printed. No message content or identifiers are ever printed.
    """
    if not source_path.is_dir():
        raise SystemExit(f"Source path is not a directory: {source_path}")

    connection = connect_database(database)
    try:
        store = ConversationStore(connection)
        if source_type == "chatgpt":
            summary = ingest_chatgpt_conversations(source_path, store)
        elif source_type == "gemini":
            summary = ingest_gemini_conversations(source_path, store)
        else:
            raise SourceError(f"Unknown conversation source type {source_type!r}")
    finally:
        connection.close()

    print(f"source_type: {summary.source_type}")
    print(f"conversations: {summary.conversations_stored}")
    print(f"messages: {summary.messages_stored}")
    if source_type == "chatgpt":
        print(f"shards: {summary.shards_discovered}")
        print(f"attachments: {summary.attachments_stored}")
    else:
        print(f"md_files_skipped: {summary.md_files_skipped}")
        print(f"aggregate_files_skipped: {summary.aggregate_files_skipped}")

    if memory:
        _run_memory_extraction(source_type, database)


def _run_memory_extraction(source_type: str, database: Path) -> None:
    """Run bounded conversation memory extraction; print aggregate counts only."""
    connection = connect_database(database)
    memory_connection, memory_store = open_memory_store(database)
    try:
        conversation_store = ConversationStore(connection)
        service = MemoryService(memory_store)
        report = ConversationMemoryIngestor(conversation_store, service).ingest(
            source_type
        )
    finally:
        memory_connection.close()
        connection.close()

    counts = report.summary()
    print("memory:")
    for label in sorted(counts):
        print(f"  {label}: {counts[label]}")


def _build_workouts_parser() -> argparse.ArgumentParser:
    """Parser for ``personal-ai workouts ...`` (import/list/show/exercises).

    Kept separate from the agent CLI parser: the ``workouts`` verb is
    dispatched in :func:`parse_args` when it is the first positional token, so
    the existing agent flag surface (``--workspace``, ``--database``,
    positional ``prompt``) is untouched.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--database",
        type=Path,
        required=True,
        help="SQLite database holding the workout store (required).",
    )
    common.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-consumable JSON instead of human text.",
    )
    parser = argparse.ArgumentParser(
        prog="personal-ai workouts",
        description="Manage the personal workout dataset (local, offline).",
    )
    sub = parser.add_subparsers(
        dest="verb", required=True, metavar="{import,list,show,exercises}"
    )

    importer = sub.add_parser(
        "import",
        parents=[common],
        help="Import workout exports from a directory or file.",
    )
    importer.add_argument(
        "path",
        type=Path,
        help="Directory (or single export file) containing workout exports.",
    )

    lst = sub.add_parser("list", parents=[common], help="List workouts newest-first.")
    lst.add_argument(
        "--date-from", help="Inclusive start day (YYYY-MM-DD) on session date."
    )
    lst.add_argument(
        "--date-to", help="Inclusive end day (YYYY-MM-DD) on session date."
    )
    lst.add_argument("--program", dest="program_id", help="Restrict to one program id.")
    lst.add_argument("--limit", type=int, help="Maximum number of workouts to list.")

    show = sub.add_parser("show", parents=[common], help="Show one full workout.")
    show.add_argument("workout_id", help="Stable workout id (the wkt-... string).")

    exercises = sub.add_parser("exercises", parents=[common], help="List exercises.")
    exercises.add_argument(
        "--name", help="Filter by a movement name (case-insensitive)."
    )
    exercises.add_argument(
        "--workout", dest="workout_id", help="Restrict to one workout."
    )
    exercises.add_argument(
        "--limit", type=int, help="Maximum number of exercises to list."
    )
    return parser


def _build_memory_parser() -> argparse.ArgumentParser:
    """Parser for ``personal-ai memory ...`` (curate/curate-all/runs/review).

    Kept separate from the agent CLI parser: the ``memory`` verb is dispatched
    in :func:`parse_args` when it is the first positional token, so the
    existing agent flag surface (``--workspace``, ``--database``, positional
    ``prompt``) is untouched.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--database",
        type=Path,
        required=True,
        help="SQLite database holding the memory store (required).",
    )
    common.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-consumable JSON instead of human text.",
    )
    parser = argparse.ArgumentParser(
        prog="personal-ai memory",
        description="Durable, resumable memory curation (local, offline).",
    )
    sub = parser.add_subparsers(
        dest="verb",
        required=True,
        metavar="{curate,curate-all,runs,review,corpus-audit}",
    )

    curate = sub.add_parser(
        "curate", parents=[common], help="Run bounded memory curation for one source."
    )
    curate.add_argument(
        "--source",
        choices=tuple(CONVERSATION_SOURCE_TYPES)
        + (EMAIL, FINANCIAL, GENERIC_DOCUMENT_SOURCE, WORKOUTS, ACTIVITY),
        default="chatgpt",
        help=(
            "Corpus source to curate: a conversation source (chatgpt/gemini), "
            "email, financial (counts only), document (generic documents), "
            "workout, or activity (chrome history). Default: chatgpt."
        ),
    )
    curate.add_argument(
        "--mode",
        "--extraction",
        dest="extraction",
        choices=("deterministic", "llm"),
        default="deterministic",
        help="How candidates are proposed (default: deterministic).",
    )
    curate.add_argument(
        "--model",
        help="Ollama model for --extraction llm (default: the chat model).",
    )
    curate.add_argument(
        "--limit",
        "--max-units",
        dest="limit",
        type=int,
        default=100,
        help="Max units to curate (default: 100).",
    )
    curate.add_argument("--offset", type=int, default=0, help="Skip the first N units.")
    curate.add_argument(
        "--max-messages",
        type=int,
        default=DEFAULT_MAX_MESSAGES,
        help="Max bounded records (messages/windows/chunks) per unit.",
    )
    curate.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Model retries per unit before the unit is marked failed.",
    )
    curate.add_argument(
        "--max-model-calls",
        type=int,
        default=DEFAULT_MAX_MODEL_CALLS,
        help="Max LLM calls for the whole run (default 0 = unlimited).",
    )
    curate.add_argument(
        "--min-signal",
        type=int,
        default=0,
        help="In LLM mode, process units below this deterministic signal "
        "deterministically instead of calling the model.",
    )
    curate.add_argument(
        "--sample",
        type=int,
        default=0,
        help="In LLM mode, at most this many strongest-signal units call the "
        "model before falling back to deterministic extraction (0 = unlimited).",
    )
    curate.add_argument(
        "--unit-timeout",
        dest="unit_timeout_seconds",
        type=float,
        default=DEFAULT_UNIT_TIMEOUT_SECONDS,
        help="Per-unit time budget in seconds; timeouts are recorded as failed.",
    )
    curate.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze the window without writing anything.",
    )
    curate.add_argument(
        "--resume",
        action="store_true",
        help="Resume the newest incomplete (running/failed) run for the requested "
        "source/mode; completed runs are never resumed.",
    )

    # --- curate-all ----------------------------------------------------------
    curate_all = sub.add_parser(
        "curate-all",
        parents=[common],
        help="Run bounded curation over ALL corpus sources sequentially.",
    )
    curate_all.add_argument(
        "--sources",
        nargs="*",
        default=None,
        help="Subset of sources to curate (default: all 7 sources in priority order).",
    )
    curate_all.add_argument(
        "--mode",
        choices=("adaptive", "deterministic"),
        default="adaptive",
        help=(
            "adaptive = LLM for chat/email/document, deterministic for "
            "financial/workout/activity. deterministic = all deterministic. "
            "Default: adaptive."
        ),
    )
    curate_all.add_argument(
        "--model",
        help="Ollama model for LLM-capable sources (default: the chat model).",
    )
    curate_all.add_argument(
        "--limit",
        "--max-units",
        dest="limit",
        type=int,
        default=DEFAULT_ORCHESTRATION_LIMIT,
        help=f"Max units per source (default: {DEFAULT_ORCHESTRATION_LIMIT}).",
    )
    curate_all.add_argument(
        "--offset", type=int, default=0, help="Skip first N units per source."
    )
    curate_all.add_argument(
        "--max-messages",
        type=int,
        default=DEFAULT_MAX_MESSAGES,
        help="Max bounded records (messages/windows/chunks) per unit.",
    )
    curate_all.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Model retries per unit before the unit is marked failed.",
    )
    curate_all.add_argument(
        "--max-model-calls",
        type=int,
        default=DEFAULT_ORCHESTRATION_MAX_MODEL_CALLS,
        help=f"Max LLM calls for the WHOLE orchestration (default: {DEFAULT_ORCHESTRATION_MAX_MODEL_CALLS}).",
    )
    curate_all.add_argument(
        "--min-signal",
        type=int,
        default=0,
        help="In adaptive mode, process units below this deterministic signal deterministically.",
    )
    curate_all.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_ORCHESTRATION_SAMPLE,
        help=f"In adaptive mode, at most this many LLM units per source before fallback (default: {DEFAULT_ORCHESTRATION_SAMPLE}).",
    )
    curate_all.add_argument(
        "--unit-timeout",
        dest="unit_timeout_seconds",
        type=float,
        default=DEFAULT_UNIT_TIMEOUT_SECONDS,
        help="Per-unit time budget in seconds; timeouts are recorded as failed.",
    )
    curate_all.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze all sources without writing anything.",
    )
    curate_all.add_argument(
        "--resume",
        action="store_true",
        help="Resume the newest incomplete (running/failed) runs for ALL "
        "requested sources; completed runs are never resumed.",
    )

    runs = sub.add_parser(
        "runs", parents=[common], help="List curation runs newest-first."
    )
    runs.add_argument("--source", help="Restrict to one conversation source.")
    runs.add_argument(
        "--limit", type=int, default=20, help="Maximum number of runs to list."
    )

    review = sub.add_parser(
        "review",
        parents=[common],
        help="Exception-only review of escalated candidates.",
    )
    review.add_argument("--run", dest="run_id", help="Restrict to one run_id.")
    review.add_argument(
        "--category",
        choices=("require_approval", "conflict"),
        help="Filter pending reviews by category (default: all).",
    )
    review.add_argument(
        "--show",
        action="store_true",
        help="Show candidate statements (default is aggregate-only counts).",
    )
    review.add_argument(
        "--limit", type=int, default=200, help="Maximum number of rows to list."
    )
    review.add_argument(
        "--approve",
        type=int,
        metavar="REVIEW_ID",
        help="Approve one pending review item by ID.",
    )
    review.add_argument(
        "--reject",
        type=int,
        metavar="REVIEW_ID",
        help="Reject one pending review item by ID.",
    )
    review.add_argument(
        "--note",
        default="",
        help="Optional note to attach to the approve/reject decision.",
    )
    review.add_argument(
        "--audit",
        action="store_true",
        help=(
            "Show a privacy-safe recent view of the durable review audit trail "
            "(metadata only; no statements or evidence)."
        ),
    )
    review.add_argument(
        "--audit-counts",
        action="store_true",
        help="Show aggregate-only review audit counts (never content).",
    )
    review.add_argument(
        "--since",
        metavar="TIMESTAMP",
        help=(
            "Inclusive lower bound for audit time window (ISO-8601, UTC). "
            "Examples: 2026-09-01, 2026-09-01T00:00:00Z, 2026-09-01T00:00:00+00:00"
        ),
    )
    review.add_argument(
        "--until",
        metavar="TIMESTAMP",
        help=(
            "Inclusive upper bound for audit time window (ISO-8601, UTC). "
            "Examples: 2026-09-06, 2026-09-06T23:59:59Z, 2026-09-06T23:59:59+00:00"
        ),
    )

    # --- corpus-audit --------------------------------------------------------
    # Read-only, bounded, aggregate-only audit over RAW conversation exports.
    # Deliberately has NO --database flag: it never touches any SQLite database
    # other than an optional disposable scratch file for the idempotency check.
    corpus_audit = sub.add_parser(
        "corpus-audit",
        help="Bounded aggregate-only audit of raw conversation exports.",
    )
    corpus_audit.add_argument(
        "--source",
        choices=tuple(CONVERSATION_SOURCE_TYPES),
        default="chatgpt",
        help="Conversation source to audit (default: chatgpt).",
    )
    corpus_audit.add_argument(
        "--path",
        type=Path,
        required=True,
        metavar="EXPORT_DIR",
        help="Directory containing the raw conversation export files.",
    )
    corpus_audit.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_AUDIT_LIMIT,
        help=f"Max conversations to sample per source (default: {DEFAULT_AUDIT_LIMIT}).",
    )
    corpus_audit.add_argument(
        "--max-messages",
        type=int,
        default=DEFAULT_AUDIT_MAX_MESSAGES,
        help=f"Max messages per conversation to audit (default: {DEFAULT_AUDIT_MAX_MESSAGES}).",
    )
    corpus_audit.add_argument(
        "--scratch-db",
        type=Path,
        metavar="PATH",
        help=(
            "Optional disposable SQLite file for the two-pass idempotency "
            "check on a temporary copy of the sampled conversations. Never "
            "a production database."
        ),
    )
    corpus_audit.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-consumable JSON instead of human text.",
    )
    return parser


def _build_people_parser() -> argparse.ArgumentParser:
    """Parser for ``personal-ai people ...`` (index/list).

    Kept separate from the agent CLI parser: the ``people`` verb is
    dispatched in :func:`parse_args` when it is the first positional token,
    so the existing agent flag surface (``--workspace``, ``--database``,
    positional ``prompt``) is untouched.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--database",
        type=Path,
        required=True,
        help="SQLite database holding the document and people stores (required).",
    )
    common.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-consumable JSON instead of human text.",
    )
    parser = argparse.ArgumentParser(
        prog="personal-ai people",
        description="People / identity layer: derive people from your corpus (local, offline).",
    )
    sub = parser.add_subparsers(dest="verb", required=True, metavar="{index,list}")

    index = sub.add_parser(
        "index", parents=[common], help="Derive people identities from your corpus."
    )
    index.add_argument(
        "--source",
        choices=(EMAIL, FINANCIAL),
        default=EMAIL,
        help=(
            "Source to derive people from: 'email' (From/To/Cc headers) or "
            "'financial' (payer/payee/counterparty). Default: email."
        ),
    )
    index.add_argument(
        "--path",
        type=Path,
        help=(
            "Source directory for the adapter route. When omitted, already-"
            "ingested email documents are indexed from their stored metadata "
            "(required for --source financial)."
        ),
    )
    index.add_argument(
        "--limit",
        type=int,
        help="Maximum number of records to index (default: all).",
    )

    lister = sub.add_parser(
        "list", parents=[common], help="List the derived people identities."
    )
    lister.add_argument("--query", help="Filter identities by a name/email substring.")
    lister.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum identities to list (default: 50).",
    )
    return parser


def run_people(args: argparse.Namespace) -> int:
    """Dispatch a ``people`` subcommand and return a process exit code."""
    verb = args.verb
    database = args.database
    if verb == "index":
        return _run_people_index(database, args)
    if verb == "list":
        return _run_people_list(database, args)
    raise SystemExit(f"Unknown people verb: {verb}")


def _person_cli_dict(person) -> dict[str, object]:
    return {
        "person_id": person.person_id,
        "display_name": person.display_name,
        "emails": list(person.emails),
        "roles": list(person.roles),
        "sources": list(person.sources),
        "evidence_count": person.evidence_count,
        "first_seen_at": person.first_seen_at,
        "last_seen_at": person.last_seen_at,
    }


def _run_people_index(database: Path, args: argparse.Namespace) -> int:
    """Derive people identities from an adapter route or stored documents.

    With ``--path`` the selected source adapter discovers records directly
    (the financial route has no stored metadata). Without ``--path`` the
    already-ingested email documents are indexed from their stored headers.
    Re-indexing is idempotent: unchanged records add no evidence and no new
    people.
    """
    source_type = args.source
    connection = connect_database(database)
    try:
        store = PersonStore(connection)
        indexer = PersonIndexer(store)
        if args.path is not None:
            if not args.path.is_dir():
                raise SystemExit(f"Source path is not a directory: {args.path}")
            try:
                adapter = resolve_source_adapter(source_type, args.path)
            except SourceError as exc:
                raise SystemExit(str(exc)) from exc
            report = indexer.index_records(adapter.discover(), limit=args.limit)
        else:
            if source_type != EMAIL:
                raise SystemExit(f"--path is required to index source {source_type!r}")
            document_store = DocumentStore(connection)
            report = indexer.index_documents(
                document_store, source_type=EMAIL, limit=args.limit
            )
    finally:
        connection.close()

    if args.json:
        _print_json({"source": report.source_type, **report.summary()})
        return 0
    print(f"people index: {report.source_type or 'unknown'}")
    print(f"  records: {report.records}")
    print(f"  references: {report.references}")
    print(f"  people_before: {report.people_before}")
    print(f"  people_after: {report.people_after}")
    print(f"  new_people: {report.new_people}")
    return 0


def _run_people_list(database: Path, args: argparse.Namespace) -> int:
    """List the derived people identities (deterministic order)."""
    connection = connect_database(database)
    try:
        store = PersonStore(connection)
        if args.query:
            people = store.search(args.query, limit=args.limit)
        else:
            people = store.list(limit=args.limit)
    finally:
        connection.close()

    if args.json:
        _print_json({"people": [_person_cli_dict(person) for person in people]})
        return 0
    if not people:
        print("No people derived yet. Run 'personal-ai people index --database ...'")
        return 0
    for person in people:
        print(f"{person.person_id}")
        print(f"  {person.display_name}")
        print(f"  roles: {', '.join(person.roles) or '-'}")
        print(f"  emails: {', '.join(person.emails) or '-'}")
        print(
            f"  evidence: {person.evidence_count}  first: {person.first_seen_at}  "
            f"last: {person.last_seen_at}"
        )
    return 0


def run_memory(args: argparse.Namespace) -> int:
    """Dispatch a ``memory`` subcommand and return a process exit code."""
    verb = args.verb
    if verb == "corpus-audit":
        return _run_memory_corpus_audit(args)
    database = args.database
    if verb == "curate":
        return _run_memory_curate(database, args)
    if verb == "curate-all":
        return _run_memory_curate_all(database, args)
    if verb == "runs":
        return _run_memory_runs(database, args)
    if verb == "review":
        return _run_memory_review(database, args)
    raise SystemExit(f"Unknown memory verb: {verb}")


def _run_memory_corpus_audit(args: argparse.Namespace) -> int:
    """Run a bounded, aggregate-only audit over raw conversation exports.

    Reads only the raw export files. Nothing is written except, optionally, a
    disposable scratch SQLite file used to prove two-pass idempotency. The
    output is aggregate-only: counts and category tallies, never content.
    """
    import tempfile

    from personal_ai.memory.corpus_audit import run_corpus_audit

    scratch: Path | None = getattr(args, "scratch_db", None)
    cleanup: Path | None = None
    try:
        if scratch is None:
            fd, scratch_name = tempfile.mkstemp(prefix="corpus-audit-", suffix=".db")
            import os

            os.close(fd)
            scratch = Path(scratch_name)
            cleanup = scratch
        report = run_corpus_audit(
            args.source,
            args.path,
            limit=args.limit,
            max_messages=args.max_messages,
            scratch_database=scratch,
        )
    finally:
        if cleanup is not None:
            cleanup.unlink(missing_ok=True)

    summary = report.summary()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    print(f"source_type: {summary['source_type']}")
    print(f"export_path: {summary['export_path']}")
    print(f"conversations_available: {summary['conversations_available']}")
    print(f"conversations_sampled: {summary['conversations_sampled']}")
    print(f"messages_sampled: {summary['messages_sampled']}")
    print(f"user_messages: {summary['user_messages']}")
    print(f"languages: {summary['languages']}")
    print(f"mixed_language_messages: {summary['mixed_language_messages']}")
    print(f"unknown_messages: {summary['unknown_messages']}")
    print(f"candidates: {summary['candidates']}")
    print(f"candidates_by_kind: {summary['candidates_by_kind']}")
    print(f"candidates_by_language: {summary['candidates_by_language']}")
    print(f"recurring_candidates: {summary['recurring_candidates']}")
    print(f"temporal: {summary['temporal']}")
    print(f"skip_reasons: {summary['skip_reasons']}")
    print(f"policy_decisions: {summary['policy_decisions']}")
    print(f"sensitivity: {summary['sensitivity']}")
    print(f"secret_rejected: {summary['secret_rejected']}")
    print(f"require_approval: {summary['require_approval']}")
    print(f"evidence_valid: {summary['evidence_valid']}")
    print(f"evidence_invalid: {summary['evidence_invalid']}")
    print(f"non_ascii_messages: {summary['non_ascii_messages']}")
    print(f"umlaut_or_accent_messages: {summary['umlaut_or_accent_messages']}")
    print(f"tokens_total: {summary['tokens_total']}")
    print(f"unicode_tokens: {summary['unicode_tokens']}")
    print(f"zero_token_messages: {summary['zero_token_messages']}")
    print(f"zero_token_by_category: {summary['zero_token_by_category']}")
    print(f"ascii_folded_messages: {summary['ascii_folded_messages']}")
    print(f"phase21a_verb_sentences: {summary['phase21a_verb_sentences']}")
    print(f"phase21a_detected_es: {summary['phase21a_detected_es']}")
    print(f"phase21a_language_unknown: {summary['phase21a_language_unknown']}")
    print(f"unicode_errors: {summary['unicode_errors']}")
    print(f"model_calls: {summary['model_calls']}")
    print(f"llm_proposal_layer: {summary['llm_proposal_layer']}")
    idempotency = summary["idempotency"]
    if idempotency is None:
        print("idempotency: not_checked")
    else:
        print(
            "idempotency: "
            f"seeded={idempotency['conversations_seeded']} convs, "
            f"memories_p1={idempotency['memories_pass1']} "
            f"p2={idempotency['memories_pass2']} "
            f"evidence_p1={idempotency['evidence_pass1']} "
            f"p2={idempotency['evidence_pass2']}"
        )
        print(f"idempotent: {idempotency['idempotent']}")
    return 0


def _run_memory_curate(database: Path, args: argparse.Namespace) -> int:
    """Run bounded, resumable memory curation; print an aggregate-only report.

    Every write goes through the policy-gated ``propose_memory`` path; the
    report is content-free. ``--dry-run`` analyzes the window without writing
    or creating any rows. ``--resume`` completes exactly the unfinished units
    of the newest run that is still incomplete (status ``running`` or
    ``failed``) for the requested source and extraction mode — a completed
    run is never resumed, even when a newer run finished first.
    """
    config = CurationConfig(
        source_type=args.source,
        extraction=args.extraction,
        limit=args.limit,
        offset=args.offset,
        max_messages=args.max_messages,
        max_retries=args.max_retries,
        max_model_calls=args.max_model_calls,
        min_signal=args.min_signal,
        sample=args.sample,
        unit_timeout_seconds=args.unit_timeout_seconds,
        dry_run=args.dry_run,
        resume=args.resume,
        model_name=args.model,
    )
    try:
        config.validate()
    except CurationError as exc:
        raise SystemExit(str(exc)) from exc

    connection = connect_database(database)
    memory_connection, memory_store = open_memory_store(database)
    try:
        curation_store = CurationStore(connection)
        service = MemoryService(memory_store)
        manager = _NullContext(None)
        if config.extraction is CurationExtractionMode.LLM:
            manager = OllamaClient(model=args.model or MODEL)
        with manager as client:
            adapter = _build_curation_registry(config, connection, client)
            report = _run_curation(curation_store, adapter, service, config)
    finally:
        memory_connection.close()
        connection.close()

    _print_curation_report(report, as_json=args.json)
    return 0


class _NullContext:
    """Context manager that yields a fixed value (for the deterministic path)."""

    def __init__(self, value: object | None) -> None:
        self._value = value

    def __enter__(self) -> object | None:
        return self._value

    def __exit__(self, *exc: object) -> None:
        return None


def _build_curation_registry(
    config: CurationConfig, connection: sqlite3.Connection, client: object | None
) -> CurationAdapterRegistry:
    """Build the adapter registry for the requested corpus source."""
    adapters: list[object] = []
    if config.source_type in CONVERSATION_SOURCE_TYPES:
        adapters.append(
            ConversationCurationAdapter(ConversationStore(connection), client=client)
        )
    if config.source_type in (EMAIL, FINANCIAL, GENERIC_DOCUMENT_SOURCE):
        adapters.append(
            DocumentCurationAdapter(
                DocumentStore(connection),
                ChunkStore(connection),
                client=client,
            )
        )
    if config.source_type == WORKOUTS:
        workout_store = WorkoutStore(connection)
        adapters.append(WorkoutCurationAdapter(WorkoutQueryService(workout_store)))
    if config.source_type in (ACTIVITY, CHROME_HISTORY_EVENT_SOURCE):
        adapters.append(EventCurationAdapter(EventStore(connection)))
    return CurationAdapterRegistry(*adapters)  # type: ignore[arg-type]


def _run_curation(
    curation_store: CurationStore,
    adapter: object,
    service: MemoryService,
    config: CurationConfig,
) -> CurationReport:
    runner = MemoryCurationRunner(
        store=curation_store,
        adapter=adapter,
        memory_service=service,  # type: ignore[arg-type]
    )
    try:
        return runner.run(config)
    except CurationError as exc:
        raise SystemExit(str(exc)) from exc


def _run_memory_runs(database: Path, args: argparse.Namespace) -> int:
    """List durable curation runs (content-free checkpoint metadata)."""
    connection = connect_database(database)
    try:
        runs = CurationStore(connection).list_runs(
            source_type=args.source, limit=args.limit
        )
    finally:
        connection.close()
    if args.json:
        _print_json({"runs": [dict(run) for run in runs]})
        return 0
    if not runs:
        print("No curation runs.")
        return 0
    for run in runs:
        print(
            f"{run['run_id']}  {run['source_type']}  {run['extraction']}  "
            f"{run['status']}  {run['started_at']}"
        )
    return 0


def _run_memory_review(database: Path, args: argparse.Namespace) -> int:
    """Exception-only review of escalated candidates (Phase 18).

    By default only aggregate counts are shown (content-free).
    Use ``--show`` to display the stored statement per row.
    Use ``--approve N`` / ``--reject N`` to decide a pending review item.
    """
    from personal_ai.memory.review import (
        MemoryReviewService,
        _AdjudicationConnection,
    )

    connection = connect_database(database)
    # Wrap the shared connection so the approval/rejection decision commits the
    # memory write, audit event, and review transition in one atomic unit
    # (Phase 27).
    review_connection = _AdjudicationConnection(connection)
    try:
        store = CurationStore(review_connection)
        service = MemoryService(MemoryStore(review_connection))
        review = MemoryReviewService(store, service, connection=review_connection)

        # Audit observability (Phase 28/30): privacy-safe, read-only views of the
        # durable review audit trail. Handled first so they never touch the
        # pending queue or write anything.
        if args.audit:
            try:
                return _emit_review_audit(
                    review,
                    args.limit,
                    json_mode=args.json,
                    since=args.since,
                    until=args.until,
                )
            except ValueError as exc:
                raise SystemExit(f"review audit: {exc}") from exc

        if args.audit_counts:
            try:
                return _emit_review_audit_counts(
                    review, json_mode=args.json, since=args.since, until=args.until
                )
            except ValueError as exc:
                raise SystemExit(f"review audit: {exc}") from exc

        # Approve / reject first (mutually exclusive)
        if args.approve is not None:
            result = review.approve(args.approve, note=args.note)
            outcome = result.get("outcome", "unknown")
            sid = result.get("memory_id", "")
            sr = result.get("superseded_id", "")
            ea = result.get("evidence_added", 0)
            status = result.get("status", "")
            recorded = result.get("audit_recorded", None)
            print(f"review approve {args.approve}: outcome={outcome}", end="")
            if sid:
                print(f", memory_id={sid}", end="")
            if sr:
                print(f", superseded={sr}", end="")
            if isinstance(ea, int):
                print(f", evidence_added={ea}", end="")
            if status:
                print(f", status={status}", end="")
            if recorded is not None:
                print(f", audit_recorded={str(recorded).lower()}", end="")
            print()
            return 0

        if args.reject is not None:
            result = review.reject(args.reject, note=args.note)
            outcome = result.get("outcome", "unknown")
            recorded = result.get("audit_recorded", None)
            print(f"review reject {args.reject}: outcome={outcome}", end="")
            if recorded is not None:
                print(f", audit_recorded={str(recorded).lower()}", end="")
            print()
            return 0

        # List rows — respect --show, --category
        category_filter = args.category
        rows = review.list_pending(category=category_filter, limit=args.limit)

        if args.show:
            # content-bearing mode
            if not rows:
                print("No pending memory reviews.")
                return 0
            for row in rows:
                stmt = row.get("statement", "")
                print(f"{row['id']}: {stmt}")
                # print metadata below on next lines
                print(
                    f"  kind={row['kind']} temporal_scope={row['temporal_scope']} "
                    f"confidence={row['confidence']} importance={row['importance']} "
                    f"status={row['status']}"
                )
                print(
                    f"  run={row['run_id']} unit={row['unit_id']} "
                    f"created_at={row['created_at']}"
                )
                print(f"  category={row['category']} reason={row['reason']}")
            return 0

        # aggregate-only default
        if args.json:
            _print_json(
                {
                    "pending": len(rows),
                    "by_category": {
                        c: n
                        for c, n in review.pending_counts()
                        .get("by_category", {})
                        .items()
                    }
                    if hasattr(review.pending_counts(), "get")
                    else {},
                    "total_pending": len(rows),
                }
            )
            return 0

        pending = review.pending_counts()
        print(f"Pending reviews: {pending.get('pending', 0)} total")
        by_cat = pending.get("by_category", {})
        if by_cat:
            for cat, cnt in sorted(by_cat.items()):
                print(f"  {cat}: {cnt}")
        return 0

    finally:
        connection.close()


def _emit_review_audit(
    review: object,
    limit: int,
    *,
    json_mode: bool,
    since: str | None = None,
    until: str | None = None,
) -> int:
    """Print a privacy-safe recent view of the durable review audit trail.

    ``review`` exposes ``audit_counts()`` and ``audit(limit=...)``. Only
    operational metadata is emitted (review_id, action, outcome, actor,
    policy_category, memory_id, created_at) — never statements, evidence, the
    internal row id, or the statement digest.

    Optional time-window filtering:
    - ``since``: inclusive lower bound on ``created_at`` (ISO-8601, normalized to UTC)
    - ``until``: inclusive upper bound on ``created_at`` (ISO-8601, normalized to UTC)
    """
    counts = review.audit_counts(since=since, until=until)  # type: ignore[attr-defined]
    events = int(counts.get("events", 0))
    if json_mode:
        _print_json(
            {
                "events": events,
                "recent_events": [
                    _audit_public_row(row)
                    for row in review.audit(limit=limit, since=since, until=until)  # type: ignore[attr-defined]
                ],
                "actions": counts.get("actions", {}),
                "outcomes": counts.get("outcomes", {}),
                "policy_categories": counts.get("policy_categories", {}),
                "actors": counts.get("actors", {}),
            }
        )
        return 0

    print("Review audit")
    print("============")
    print(f"events: {events}")
    print("recent events (metadata only, newest first):")
    recent = review.audit(limit=limit, since=since, until=until)  # type: ignore[attr-defined]
    if not recent:
        print("  (none)")
    for row in recent:
        print(
            f"  {row['created_at']}  {row['action']:<8} {row['outcome']:<10} "
            f"{row['policy_category']:<16} actor={row['actor']} "
            f"review_id={row['review_id']} "
            + (f"memory_id={row['memory_id']}" if row.get("memory_id") else "")
        )
    return 0


def _emit_review_audit_counts(
    review: object,
    *,
    json_mode: bool,
    since: str | None = None,
    until: str | None = None,
) -> int:
    """Print aggregate-only review audit counts (never content).

    Optional time-window filtering:
    - ``since``: inclusive lower bound on ``created_at`` (ISO-8601, normalized to UTC)
    - ``until``: inclusive upper bound on ``created_at`` (ISO-8601, normalized to UTC)
    """
    counts = review.audit_counts(since=since, until=until)  # type: ignore[attr-defined]
    if json_mode:
        _print_json(
            {
                "events": counts.get("events", 0),
                "actions": counts.get("actions", {}),
                "outcomes": counts.get("outcomes", {}),
                "policy_categories": counts.get("policy_categories", {}),
                "actors": counts.get("actors", {}),
            }
        )
        return 0

    events = int(counts.get("events", 0))
    print("Review audit counts")
    print("===================")
    print(f"events: {events}")
    outcomes = counts.get("outcomes", {}) or {}
    print(f"approved: {outcomes.get('approved', 0)}")
    print(f"rejected: {outcomes.get('rejected', 0)}")
    print(f"expired: {outcomes.get('expired', 0)}")
    actions = counts.get("actions", {}) or {}
    if actions:
        print("actions:")
        for key in sorted(actions):
            print(f"  {key}: {actions[key]}")
    categories = counts.get("policy_categories", {}) or {}
    if categories:
        print("policy categories:")
        for key in sorted(categories):
            print(f"  {key}: {categories[key]}")
    actors = counts.get("actors", {}) or {}
    if actors:
        print("actors:")
        for key in sorted(actors):
            print(f"  {key}: {actors[key]}")
    return 0


def _audit_public_row(row: dict[str, object]) -> dict[str, object]:
    """Mapper that exposes only safe operational audit metadata."""
    return {
        "review_id": row.get("review_id"),
        "action": row.get("action"),
        "outcome": row.get("outcome"),
        "actor": row.get("actor"),
        "policy_category": row.get("policy_category"),
        "memory_id": row.get("memory_id"),
        "created_at": row.get("created_at"),
    }


def _run_memory_curate_all(database: Path, args: argparse.Namespace) -> int:
    """Run bounded curation over all corpus sources sequentially (Phase 18).

    All writes flow through the same policy-gated write path as per-source curation.
    The report is aggregate-only (totals, decision counts, versions, memory stats,
    review queue counts). ``--dry-run`` analyzes without writing. ``--resume``
    completes unfinished units from the newest incomplete run (``running`` or
    ``failed``) per source; completed runs are never resumed.
    """
    from personal_ai.memory.orchestration import (
        CorpusCurationConfig,
        CorpusCurationOrchestrator,
    )

    config = CorpusCurationConfig(
        sources=args.sources,
        mode=args.mode,
        limit=args.limit,
        offset=args.offset,
        max_messages=args.max_messages,
        max_retries=args.max_retries,
        max_model_calls=args.max_model_calls,
        min_signal=args.min_signal,
        sample=args.sample,
        unit_timeout_seconds=args.unit_timeout_seconds,
        dry_run=args.dry_run,
        resume=args.resume,
        model_name=args.model,
    )
    # CorpusCurationConfig.__post_init__ validates modes and the
    # dry_run/resume exclusivity at construction (no separate validate()).

    connection = connect_database(database)
    memory_connection, memory_store = open_memory_store(database)
    try:
        curation_store = CurationStore(connection)
        service = MemoryService(memory_store)
        registry = _build_curation_registry_generic(connection, args.model)

        orchestrator = CorpusCurationOrchestrator(
            curation_store=curation_store,
            registry=registry,
            memory_service=service,
        )

        report = orchestrator.run(config)
    finally:
        memory_connection.close()
        connection.close()

    _print_curation_report(report, as_json=args.json)
    return 0


def _build_curation_registry_generic(
    connection: sqlite3.Connection, model: str | None
) -> CurationAdapterRegistry:
    """Build a generic adapter registry over all sources (used by orchestration)."""
    # We don't need a client for deterministic-only sources; pass None and the
    # runner will handle graceful degradation per-unit.
    from personal_ai.memory.adapters import (
        EventCurationAdapter,
        WorkoutCurationAdapter,
    )
    from personal_ai.storage.events import EventStore
    from personal_ai.workouts.store import WorkoutStore

    # Email and document adapters need a client only for LLM mode; orchestration
    # will create one on demand per source. For now pass None.
    adapters: list[object] = []

    # Conversation sources (chatgpt/gemini) always need a client for llm mode
    # but orchestration creates one lazily; we'll register empty adapters and
    # let the runner raise early if LLM-mode is requested without a client.
    # For deterministic mode we skip them (the CLI default is adaptive).
    # The runner resolves adapters from registry per source.
    # To keep this simple, we only register the non-LLM adapters.
    # LLM sources (chatgpt/gemini/email/document) are conditionally registered
    # when --mode deterministic: they run deterministic only.

    # Deterministic adapters only:
    adapters.append(WorkoutCurationAdapter(WorkoutStore(connection)))
    adapters.append(EventCurationAdapter(EventStore(connection)))

    return CurationAdapterRegistry(*adapters)  # type: ignore[arg-type]


def _print_curation_report(report: CurationReport, *, as_json: bool) -> None:
    """Print an aggregate-only curation report (JSON or human text)."""
    summary = report.summary()
    if as_json:
        _print_json(summary)
        return
    for key, value in summary.items():
        if isinstance(value, dict):
            print(f"{key}:")
            for sub_key, sub_value in sorted(value.items()):
                print(f"  {sub_key}: {sub_value}")
        else:
            print(f"{key}: {value}")


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, default=str))


def _workout_summary_dict(workout: object) -> dict[str, object]:
    return {
        "id": workout.workout_id,
        "name": workout.name,
        "started_at": workout.started_at,
        "ended_at": workout.ended_at,
        "activity_type": workout.activity_type,
        "duration_seconds": workout.duration_seconds,
        "program_id": workout.program_id,
        "exercise_count": workout.exercise_count,
        "set_count": workout.set_count,
        "completed_set_count": workout.completed_set_count,
        "total_volume_kg": workout.total_volume_kg,
    }


def _workout_dict(workout: object) -> dict[str, object]:
    return {
        "id": workout.workout_id,
        "source_type": workout.source_type,
        "source_file": workout.source_file,
        "source_id": workout.source_id,
        "content_hash": workout.content_hash,
        "created_at": workout.created_at,
        "updated_at": workout.updated_at,
        "started_at": workout.started_at,
        "ended_at": workout.ended_at,
        "name": workout.name,
        "activity_type": workout.activity_type,
        "week": workout.week,
        "day": workout.day,
        "program_id": workout.program_id,
        "program_log_id": workout.program_log_id,
        "duration_seconds": workout.duration_seconds,
        "notes": workout.notes,
        "finished_v2_at": workout.finished_v2_at,
        "exercise_count": workout.exercise_count(),
        "set_count": workout.set_count(),
        "completed_set_count": workout.completed_set_count(),
        "total_volume_kg": workout.total_volume_kg(),
        "exercises": [
            {
                "id": exercise.exercise_id,
                "name": exercise.name,
                "normalized_name": exercise.normalized_name,
                "order_index": exercise.order_index,
                "source_exercise_id": exercise.source_exercise_id,
                "equipment_type": exercise.equipment_type,
                "target_type": exercise.target_type,
                "notes": exercise.notes,
                "sets": [
                    {
                        "id": workout_set.set_id,
                        "set_index": workout_set.set_index,
                        "value_raw": workout_set.value_raw,
                        "amount_raw": workout_set.amount_raw,
                        "weight": workout_set.weight,
                        "weight_unit": workout_set.weight_unit,
                        "reps": workout_set.reps,
                        "reps_open_ended": workout_set.reps_open_ended,
                        "target_type": workout_set.target_type,
                        "intensity": workout_set.intensity,
                        "intensity_unit": workout_set.intensity_unit,
                        "completed": workout_set.completed,
                        "custom": workout_set.custom,
                        "source": workout_set.source,
                    }
                    for workout_set in exercise.sets
                ],
            }
            for exercise in workout.exercises
        ],
    }


def _exercise_summary_dict(exercise: object) -> dict[str, object]:
    return {
        "id": exercise.exercise_id,
        "workout_id": exercise.workout_id,
        "name": exercise.name,
        "normalized_name": exercise.normalized_name,
        "order_index": exercise.order_index,
        "equipment_type": exercise.equipment_type,
        "target_type": exercise.target_type,
        "set_count": exercise.set_count,
        "completed_set_count": exercise.completed_set_count,
        "max_weight_kg": exercise.max_weight_kg,
    }


def run_workouts(args: argparse.Namespace) -> int:
    """Dispatch a ``workouts`` subcommand and return a process exit code."""
    verb = args.verb
    database = args.database
    if verb == "import":
        return _run_workouts_import(args.path, database, args.json)
    if verb == "list":
        return _run_workouts_list(database, args)
    if verb == "show":
        return _run_workouts_show(args.workout_id, database, args.json)
    if verb == "exercises":
        return _run_workouts_exercises(database, args)
    raise SystemExit(f"Unknown workouts verb: {verb}")


def _run_workouts_import(path: Path, database: Path, as_json: bool) -> int:
    if not path.exists():
        raise SystemExit(f"Workout path does not exist: {path}")
    if not path.is_dir() and path.is_file() and path.suffix.lower() != ".csv":
        raise SystemExit(f"Unsupported workout file: {path}")

    connection, store = open_workout_store(database)
    try:
        if path.is_dir():
            result = import_workout_directory(path, store)
        else:
            raw = path.read_bytes()
            source = path.name
            from personal_ai.workouts import parse_workout_file

            records, warnings = parse_workout_file(
                raw,
                source_file=source,
                source_checksum=_sha256_hex(raw),
            )
            result = store.import_records(
                records,
                source_file=source,
                checksum=_sha256_hex(raw),
                size_bytes=len(raw),
            )
            result.files_seen.append(source)
            result.files_imported.append(source)
            result.warnings.extend(warnings)
    finally:
        connection.close()

    if as_json:
        _print_json(result.to_dict())
        return 0
    print(f"workouts import: {', '.join(result.files_imported) or '(none)'}")
    print(f"  files_seen: {len(result.files_seen)}")
    print(f"  files_imported: {len(result.files_imported)}")
    print(f"  files_skipped: {len(result.files_skipped)}")
    print(f"  workouts_created: {result.workouts_created}")
    print(f"  workouts_updated: {result.workouts_updated}")
    print(f"  workouts_skipped: {result.workouts_skipped}")
    print(f"  records_with_warnings: {result.records_with_warnings}")
    print(f"  errors: {len(result.errors)}")
    for warning in result.warnings:
        print(f"  warning {warning.source_file}:{warning.index}: {warning.message}")
    for error in result.errors:
        print(f"  error: {error}")
    return 0


def _run_workouts_list(database: Path, args: argparse.Namespace) -> int:
    connection, store = open_workout_store(database)
    try:
        service = WorkoutQueryService(store)
        workouts = service.list_workouts(
            date_from=args.date_from,
            date_to=args.date_to,
            program_id=getattr(args, "program_id", None),
            limit=args.limit,
        )
    finally:
        connection.close()
    if args.json:
        _print_json(
            {"workouts": [_workout_summary_dict(workout) for workout in workouts]}
        )
        return 0
    if not workouts:
        print("No workouts.")
        return 0
    for workout in workouts:
        print(
            f"{workout.workout_id}  {workout.started_at}  {workout.name or '-':<22}"
            f"  {workout.exercise_count} ex, {workout.set_count} sets, "
            f"{workout.total_volume_kg:.1f} kg"
        )
    return 0


def _run_workouts_show(workout_id: str, database: Path, as_json: bool) -> int:
    connection, store = open_workout_store(database)
    try:
        workout = WorkoutQueryService(store).get_workout(workout_id)
    finally:
        connection.close()
    if workout is None:
        raise SystemExit(f"No such workout: {workout_id}")
    if as_json:
        _print_json({"workout": _workout_dict(workout)})
        return 0
    print(f"id: {workout.workout_id}")
    print(f"source: {workout.source_type} ({workout.source_file}, {workout.source_id})")
    print(f"started_at: {workout.started_at}")
    print(f"ended_at: {workout.ended_at}")
    print(f"name: {workout.name or '-'}")
    print(f"activity_type: {workout.activity_type}")
    if workout.week is not None or workout.day is not None:
        print(f"program_position: week {workout.week} day {workout.day}")
    if workout.program_id:
        print(f"program_id: {workout.program_id}")
    if workout.duration_seconds is not None:
        print(f"duration_seconds: {workout.duration_seconds:.1f}")
    if workout.notes:
        print(f"notes: {workout.notes}")
    print(f"sets: {workout.set_count()} ({workout.completed_set_count()} completed)")
    print(f"total_volume_kg: {workout.total_volume_kg():.1f}")
    for exercise in workout.exercises:
        sets = ", ".join(
            f"{workout_set.weight or workout_set.value_raw or '-'}x{workout_set.reps or workout_set.amount_raw or '-'}"
            for workout_set in exercise.sets
        )
        print(
            f"  {exercise.order_index}. {exercise.name}"
            + (f"  [{sets}]" if sets else "")
        )
    return 0


def _run_workouts_exercises(database: Path, args: argparse.Namespace) -> int:
    connection, store = open_workout_store(database)
    try:
        service = WorkoutQueryService(store)
        exercises = service.list_exercises(
            workout_id=getattr(args, "workout_id", None),
            name=getattr(args, "name", None),
            limit=args.limit,
        )
    finally:
        connection.close()
    if args.json:
        _print_json(
            {"exercises": [_exercise_summary_dict(exercise) for exercise in exercises]}
        )
        return 0
    if not exercises:
        print("No exercises.")
        return 0
    for exercise in exercises:
        print(
            f"{exercise.exercise_id}  {exercise.name}"
            + (f"  [{exercise.equipment_type}]" if exercise.equipment_type else "")
            + f"  {exercise.set_count} sets"
            + (
                f", max {exercise.max_weight_kg:.1f} kg"
                if exercise.max_weight_kg is not None
                else ""
            )
        )
    return 0


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _connect_agent_registry(
    workspace: Path,
    database: Path,
    workout_service: object | None = None,
    memory_service: object | None = None,
    memory_proposal_approver: object | None = None,
) -> tuple[ToolRegistry, sqlite3.Connection]:
    """Build the agent tool registry backed by the knowledge and event stores.

    Opens the workspace knowledge database and wires the existing stores into
    the default tool registry so the agent can use ``search_knowledge``
    (via :class:`~personal_ai.retrieval.RetrievalService`) and ``query_events``
    (via :class:`~personal_ai.storage.events.EventStore`). When a workout query
    service is provided, the policy-gated ``search_workouts`` chat tool is
    registered as well. When a memory service is provided, the read-only
    ``personal_context`` overview reports durable-memory availability and,
    when a ``memory_proposal_approver`` is given, the policy-gated
    ``propose_memory`` chat tool is registered too. The read-only
    ``get_document`` (needs the document store alongside the chunk store) and
    ``get_memory`` (needs the memory service) fetch tools are registered
    through the same policy-gated chat path.

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
        person_store = PersonStore(connection)
        retrieval_service = RetrievalService(
            chunk_store,
            extraction_store,
            document_store,
            conversation_store,
        )
        personal_context_service = PersonalContextService(
            memory=memory_service,
            workout=workout_service,
            document=document_store,
            event=event_store,
        )
        registry = create_default_registry(
            workspace,
            chunk_store=chunk_store,
            retrieval_service=retrieval_service,
            event_store=event_store,
            workout_service=workout_service,
            personal_context_service=personal_context_service,
            memory_service=memory_service,
            memory_proposal_approver=memory_proposal_approver,
            document_store=document_store,
            person_store=person_store,
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
    base_url: str | None = None,
    observer: AgentObserver | None = None,
    workout_service: object | None = None,
    memory_service: object | None = None,
    memory_proposal_approver: object | None = None,
) -> BuiltAgent:
    """Construct the production Agent from workspace and database settings.

    This is the only place a production Agent is assembled. The ToolRegistry
    is wired from the workspace-database stores (knowledge retrieval + event
    queries) through the default registry, and the Agent is built around a
    caller-supplied ``OllamaClient``. The client is entered as a context
    manager (as the CLI has always done) and the returned
    :class:`BuiltAgent` holds the open resources, closed via ``close()`` or
    ``with``.

    When ``workout_service`` is given, the policy-gated ``search_workouts``
    chat tool is registered so conversational chat can answer movement-based
    questions about the user's workout history through the policy engine.
    When ``memory_service`` is given, the read-only ``personal_context``
    overview reports durable-memory availability (counts + provenance only,
    never memory content). When ``memory_proposal_approver`` is ALSO given,
    the policy-gated ``propose_memory`` chat tool is registered so the model
    can propose durable memories that the user approves; without it the chat
    build is default-deny for memory writes.

    The Ollama endpoint defaults to the local daemon and is overridable with
    ``base_url``, which falls back to the ``OLLAMA_BASE_URL`` environment
    variable — this is how the Dockerized gateway reaches a containerized
    Ollama without changing any client code.
    """
    connection: sqlite3.Connection | None = None
    if database is not None:
        registry, connection = _connect_agent_registry(
            workspace,
            database,
            workout_service=workout_service,
            memory_service=memory_service,
            memory_proposal_approver=memory_proposal_approver,
        )
    else:
        registry = create_default_registry(workspace)

    client = OllamaClient(
        model=model,
        base_url=base_url or load_ollama_settings().base_url,
    )
    enter_client = getattr(client, "__enter__", None)
    if callable(enter_client):
        enter_client()
    agent = Agent(client, registry, observer=observer)
    return BuiltAgent(agent=agent, client=client, connection=connection)


@dataclass
class BuiltChatMemory:
    """A constructed :class:`ChatMemory` together with its database resource.

    ``build_chat_memory`` is the application-layer construction path for
    automatic chat recall. Persistence lives here (SQLite), so the HTTP layer
    never touches the database or :class:`MemoryStore` directly.
    """

    chat: ChatMemory
    connection: sqlite3.Connection | None = None

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()


def build_chat_memory(database: Path | None) -> BuiltChatMemory | None:
    """Construct bounded automatic chat memory recall, or ``None``.

    Called by the application layer (server ``main``) only — never by the
    HTTP handlers. When ``database`` is ``None`` no memory is wired and chat
    works exactly as before. Memory and orchestration tables co-locate in the
    same database file (:func:`open_memory_store`), so memories written by the
    CLI and executions persist across restarts.
    """
    if database is None:
        return None
    connection, store = open_memory_store(database)
    service = MemoryService(store)
    return BuiltChatMemory(chat=ChatMemory(service), connection=connection)


@dataclass
class BuiltChatPeople:
    """A constructed :class:`ChatPeople` together with its database resource.

    ``build_chat_people`` is the application-layer construction path for
    automatic people grounding. Persistence lives here (SQLite), so the HTTP
    layer never touches the database or :class:`PersonStore` directly.
    """

    chat: ChatPeople
    connection: sqlite3.Connection | None = None

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()


def build_chat_people(database: Path | None) -> BuiltChatPeople | None:
    """Construct bounded automatic people grounding, or ``None``.

    Called by the application layer (server ``main``) only — never by the
    HTTP handlers. When ``database`` is ``None`` no people context is wired
    and chat works exactly as before. People tables co-locate in the shared
    database file, so identities derived by ``people index`` are immediately
    groundable by chat without re-exporting a source.
    """
    if database is None:
        return None
    connection = connect_database(database)
    return BuiltChatPeople(
        chat=ChatPeople(PersonStore(connection)), connection=connection
    )


def _make_interactive_memory_approver() -> Callable[[object, object, object], bool]:
    """Return an approver that asks the user on the terminal for memory writes.

    Memory writes in chat are never auto-granted: the proposal runs only when
    the user explicitly approves it on ``stdin``. ``y``/``yes`` approves;
    anything else (including EOF) declines. The prompt is the only place the
    proposal is surfaced to the user for confirmation.
    """

    def approve(agent_id: object, tool_name: object, permission: object) -> bool:
        print(
            f"[memory] tool '{tool_name}' requests permission {permission!r}",
            file=sys.stderr,
        )
        try:
            answer = input("[memory] Approve writing this durable memory? [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}

    return approve


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


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if getattr(args, "command", None) == "workouts":
        run_workouts(args)
        return

    if getattr(args, "command", None) == "memory":
        run_memory(args)
        return

    if getattr(args, "command", None) == "people":
        run_people(args)
        return

    if args.search_query is not None:
        run_search(args.search_query, args.database, args.limit)
        return

    if args.ingest_source_args is not None:
        source_type, source_path = args.ingest_source_args
        if source_type in EVENT_SOURCE_TYPES:
            run_event_ingest(source_type, Path(source_path), args.database)
        elif is_conversation_source(source_type):
            run_conversation_ingest(
                source_type,
                Path(source_path),
                args.database,
                memory=args.memory,
            )
        else:
            run_ingest(source_type, Path(source_path), args.database)
        return

    workspace = args.workspace.resolve()

    if not workspace.is_dir():
        raise SystemExit(f"Workspace is not a directory: {workspace}")

    observer = _make_agent_observer() if args.verbose else None

    chat_memory = build_chat_memory(args.database)
    memory_service: object | None = (
        chat_memory.chat.service if chat_memory is not None else None
    )
    chat_people = build_chat_people(args.database)
    interactive = bool(
        sys.stdin is not None and hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    )
    approver = _make_interactive_memory_approver() if interactive else None

    built = build_agent(
        workspace,
        args.database,
        model=MODEL,
        observer=observer,
        memory_service=memory_service,
        memory_proposal_approver=approver,
    )
    try:
        messages: list[ChatMessage] = [ChatMessage(role="user", content=args.prompt)]
        if chat_memory is not None:
            result = chat_memory.chat.build_context_messages(messages)
            messages = list(result.messages)
        if chat_people is not None:
            result = chat_people.chat.build_context_messages(messages)
            messages = list(result.messages)
        response = built.agent.run(messages)
    finally:
        built.close()
        if chat_memory is not None:
            chat_memory.close()
        if chat_people is not None:
            chat_people.close()

    print(response)


if __name__ == "__main__":
    main()
