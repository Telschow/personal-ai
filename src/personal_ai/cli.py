"""Command-line interface for the personal AI agent."""

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from personal_ai.agent import Agent, AgentObserver
from personal_ai.config import load_ollama_settings, load_vision_settings
from personal_ai.ingestion import DocumentIngestor
from personal_ai.memory import (
    ChatMemory,
    MemoryService,
    open_memory_store,
)
from personal_ai.ollama_client import ChatMessage, OllamaClient
from personal_ai.ollama_structured import OllamaStructuredExtractor
from personal_ai.ollama_vision import OllamaVisionExtractor
from personal_ai.orchestration import ingest_source
from personal_ai.retrieval import (
    RetrievalService,
    SearchDocumentsRequest,
    search_documents,
)
from personal_ai.sources.base import SourceError
from personal_ai.sources.email import SOURCE_TYPE as EMAIL_SOURCE_TYPE
from personal_ai.sources.financial import SOURCE_TYPE as FINANCIAL_SOURCE_TYPE
from personal_ai.sources.registry import known_source_types, resolve_source_adapter
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
from personal_ai.workouts import (
    WorkoutQueryService,
    import_workout_directory,
    open_workout_store,
)

MODEL = "qwen3.5:9b"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "workouts":
        args = _build_workouts_parser().parse_args(argv[1:])
        args.command = "workouts"
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
) -> tuple[ToolRegistry, sqlite3.Connection]:
    """Build the agent tool registry backed by the knowledge and event stores.

    Opens the workspace knowledge database and wires the existing stores into
    the default tool registry so the agent can use ``search_knowledge``
    (via :class:`~personal_ai.retrieval.RetrievalService`) and ``query_events``
    (via :class:`~personal_ai.storage.events.EventStore`). When a workout query
    service is provided, the policy-gated ``search_workouts`` chat tool is
    registered as well.

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
            workout_service=workout_service,
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

    The Ollama endpoint defaults to the local daemon and is overridable with
    ``base_url``, which falls back to the ``OLLAMA_BASE_URL`` environment
    variable — this is how the Dockerized gateway reaches a containerized
    Ollama without changing any client code.
    """
    connection: sqlite3.Connection | None = None
    if database is not None:
        registry, connection = _connect_agent_registry(
            workspace, database, workout_service=workout_service
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
