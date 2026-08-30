"""Command-line interface for the execution control plane.

Thin, flat argparse surface over :class:`~personal_ai.execution.ControlPlane`.
One entry point, subcommands for the execution lifecycle (research,
approve/reject, resume/pause/cancel/retry), read projections
(list/show/events/board), and durable memory (add/list/show/search/archive/
delete/purge), with ``--json`` for machine consumers (a future HTTP API or
Kanban/Open WebUI client).

Stays fully local: orchestration + memory state live in one SQLite database
(``--database``) and optional corpus-backed research connects to the existing
personal-knowledge database (``--corpus``). No network, no Ollama.

Usage::

    uv run python -m personal_ai.execution.cli --database orch.db \\
        research "What are my goals?" --corpus knowledge.db
    uv run python -m personal_ai.execution.cli --database orch.db \\
        execution list --json
    uv run python -m personal_ai.execution.cli --database orch.db \\
        execution show exec-1234567800abcdef
    uv run python -m personal_ai.execution.cli --database orch.db \\
        memory add preference "Prefers concise explanations."
    uv run python -m personal_ai.execution.cli --database orch.db \\
        memory search "concise" --json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from personal_ai.agents.tools import build_default_agent_tools
from personal_ai.execution import ControlPlane, open_orchestration_store
from personal_ai.execution.control_plane import ExecutionNotFoundError
from personal_ai.execution.models import Plan, PlanTransitionError, TaskTransitionError
from personal_ai.execution.orchestrator import PlanNotCompleteError
from personal_ai.memory import (
    Memory,
    MemoryKind,
    MemoryNotFoundError,
    MemoryStore,
    ScopeFilter,
)
from personal_ai.memory.service import (
    MemoryNotConfiguredError,
    MemoryService,
)
from personal_ai.retrieval import RetrievalService
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentStore,
    ExtractionStore,
    connect_database,
)


def _plan_dict(plan: Plan) -> dict[str, Any]:
    return {
        "execution_id": plan.plan_id,
        "status": plan.status.value,
        "objective": plan.objective,
        "risk": plan.risk,
        "final_outcome": plan.final_outcome,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "task_ids": list(plan.task_ids),
    }


def _event_dict(event: Any) -> dict[str, Any]:
    return {
        "id": event.id,
        "seq": event.seq,
        "event_type": event.event_type,
        "execution_id": event.plan_id,
        "task_id": event.task_id,
        "agent_id": event.agent_id,
        "tool": event.tool,
        "timestamp": event.timestamp,
        "status": event.status,
        "payload": event.payload,
    }


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))


def _build_corpora(args: argparse.Namespace) -> tuple[Any, ControlPlane, Any]:
    """Build a ControlPlane wired to memory (and the corpus when configured).

    Returns ``(corpus_connection | None, ControlPlane, orb_connection)``.
    Both database connections must stay open for the whole invocation and are
    closed by the caller. Memory shares the orchestration database, so the
    orchestration connection doubles as the memory store connection.
    """
    orb_conn, store = open_orchestration_store(args.database)
    memory_service = MemoryService(MemoryStore(orb_conn))
    corpus: Any = None
    if getattr(args, "corpus", None) is not None:
        corpus = connect_database(args.corpus)
        try:
            retrieval_service = RetrievalService(
                ChunkStore(corpus),
                ExtractionStore(corpus),
                DocumentStore(corpus),
                ConversationStore(corpus),
            )
        except BaseException:
            corpus.close()
            orb_conn.close()
            raise
        plan_ = ControlPlane(
            store,
            tools=build_default_agent_tools(retrieval_service=retrieval_service),
            memory=memory_service,
        )
    else:
        plan_ = ControlPlane(store, memory=memory_service)
    return corpus, plan_, orb_conn


def run_research(plane: ControlPlane, args: argparse.Namespace) -> None:
    plan = plane.create_research_execution(args.objective)
    final = plane.resume_execution(plan.plan_id)
    if args.as_json:
        _print_json(plane.result(plan.plan_id))
        return
    print(f"{plan.plan_id}  status={final.status.value}")
    print(f"objective: {plan.objective}")
    for column in (
        "ready",
        "running",
        "waiting_approval",
        "verifying",
        "done",
        "failed",
    ):
        for card in plane.board(plan.plan_id)["columns"][column]:
            print(f"  [{column}] {card['task_id']}  {card.get('title', '')}")
    if final.final_outcome:
        print(f"outcome: {final.final_outcome}")
    print(f"events: {len(plane.events(plan.plan_id))}")


def run_list(plane: ControlPlane, args: argparse.Namespace) -> None:
    plans = [
        plan
        for plan_id in plane.list_executions()
        if (plan := plane.get_execution(plan_id)) is not None
    ]
    if args.as_json:
        _print_json([_plan_dict(plan) for plan in plans])
        return
    for plan in plans:
        print(f"{plan.plan_id}  status={plan.status.value}  objective={plan.objective}")


def run_show(plane: ControlPlane, args: argparse.Namespace) -> None:
    plan = plane.get_execution(args.execution_id)
    tasks = plane.tasks(args.execution_id)
    if args.as_json:
        _print_json({"plan": _plan_dict(plan), "tasks": [_task_dict(t) for t in tasks]})
        return
    print(f"{plan.plan_id}  status={plan.status.value}  objective={plan.objective}")
    for task in tasks:
        print(
            f"  {task.task_id:<28} {task.status.value:<12} "
            f"agent={task.assigned_agent}  skill={task.skill or '-'}"
        )


def run_events(plane: ControlPlane, args: argparse.Namespace) -> None:
    events = plane.events_after(args.execution_id, args.cursor)
    if args.as_json:
        _print_json([_event_dict(event) for event in events])
        return
    if not events:
        print(f"No events after seq={args.cursor}.")
        return
    for event in events:
        print(
            f"{event.seq:>3} {event.event_type:<24} "
            f"task={event.task_id or '-'}  agent={event.agent_id or '-'}  "
            f"tool={event.tool or '-'}"
        )


def run_board(plane: ControlPlane, args: argparse.Namespace) -> None:
    board = plane.board(args.execution_id)
    if args.as_json:
        _print_json(board)
        return
    print(f"status: {board['status']}")
    for column in (
        "ready",
        "running",
        "waiting_approval",
        "verifying",
        "done",
        "failed",
    ):
        cards = board["columns"][column]
        if cards:
            print(f"  {column}: {', '.join(c['task_id'] for c in cards)}")


def run_approve(plane: ControlPlane, args: argparse.Namespace) -> None:
    decision = plane.approve(
        args.execution_id, args.task_id, args.permission, approver=args.approver
    )
    print(
        f"approved: execution={decision['execution_id']} "
        f"task={decision['task_id']} permission={decision['permission']}"
    )


def run_reject(plane: ControlPlane, args: argparse.Namespace) -> None:
    decision = plane.reject(
        args.execution_id, args.task_id, args.permission, approver=args.approver
    )
    print(
        f"rejected: execution={decision['execution_id']} "
        f"task={decision['task_id']} permission={decision['permission']}"
    )


def run_resume(plane: ControlPlane, args: argparse.Namespace) -> None:
    _print_plan(args, plane.resume_execution(args.execution_id))


def run_pause(plane: ControlPlane, args: argparse.Namespace) -> None:
    _print_plan(args, plane.pause_execution(args.execution_id))


def run_cancel(plane: ControlPlane, args: argparse.Namespace) -> None:
    _print_plan(args, plane.cancel_execution(args.execution_id))


def run_retry(plane: ControlPlane, args: argparse.Namespace) -> None:
    task = plane.retry_task(args.execution_id, args.task_id)
    if args.as_json:
        _print_json(_task_dict(task))
        return
    print(f"{task.task_id}  status={task.status.value}  plan={task.plan_id}")


def _print_plan(args: argparse.Namespace, plan: Plan) -> None:
    if args.as_json:
        _print_json(_plan_dict(plan))
        return
    print(f"{plan.plan_id}  status={plan.status.value}  objective={plan.objective}")


def _task_dict(task: Any) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "title": task.title,
        "status": task.status.value,
        "assigned_agent": task.assigned_agent,
        "skill": task.skill,
        "selected_model": task.selected_model,
        "retry_count": task.retry_count,
        "approval_state": task.approval_state,
        "error": task.error,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "completed_at": task.completed_at,
        "tools": list(task.tools),
    }


def _memory_dict(memory: Memory) -> dict[str, Any]:
    return {
        "memory_id": memory.memory_id,
        "kind": memory.kind.value,
        "content": memory.content,
        "summary": memory.summary,
        "scope": memory.scope.value,
        "scope_id": memory.scope_id,
        "source_type": memory.source_type.value,
        "source_id": memory.source_id,
        "confidence": memory.confidence,
        "importance": memory.importance,
        "status": memory.status.value,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
        "last_accessed_at": memory.last_accessed_at,
        "expires_at": memory.expires_at,
    }


def _memory_line(memory: Memory) -> str:
    return (
        f"{memory.memory_id}  kind={memory.kind.value}  "
        f"scope={memory.scope.value}"
        f"{f'/{memory.scope_id}' if memory.scope_id else ''}  "
        f"{memory.summary or memory.content}"
    )


def run_memory_add(plane: ControlPlane, args: argparse.Namespace) -> None:
    memory = plane.memory_create_user(
        args.content,
        kind=args.kind,
        summary=args.summary,
        scope=args.scope,
        scope_id=args.scope_id,
        source_id=args.source_id,
        confidence=args.confidence,
        importance=args.importance,
        expires_at=args.expires_at,
    )
    if args.as_json:
        _print_json(_memory_dict(memory))
        return
    print(_memory_line(memory))


def run_memory_list(plane: ControlPlane, args: argparse.Namespace) -> None:
    memories = plane.memory_list(args.status)
    if args.as_json:
        _print_json([_memory_dict(m) for m in memories])
        return
    for memory in memories:
        print(_memory_line(memory))


def run_memory_show(plane: ControlPlane, args: argparse.Namespace) -> None:
    memory = plane.memory_get(args.memory_id)
    events = plane.memory_events(args.memory_id)
    if args.as_json:
        _print_json({"memory": _memory_dict(memory), "events": list(events)})
        return
    print(memory.content)
    if memory.summary:
        print(f"summary: {memory.summary}")
    print(
        f"kind={memory.kind.value} scope={memory.scope.value} "
        f"status={memory.status.value}"
    )
    print(f"confidence={memory.confidence} importance={memory.importance}")
    print(f"events: {len(events)}")


def run_memory_search(plane: ControlPlane, args: argparse.Namespace) -> None:
    scopes: tuple[ScopeFilter, ...]
    if args.scope:
        scopes = (ScopeFilter(args.scope, args.scope_id),)
    else:
        scopes = ()
    hits = plane.memory_search(
        args.query,
        scopes=scopes,
        limit=args.limit,
        include_expired=args.include_expired,
    )
    if args.as_json:
        _print_json([hit.to_dict() for hit in hits])
        return
    for hit in hits:
        print(
            f"#{hit.rank} score={hit.score:.3f} {hit.memory.memory_id}  "
            f"{hit.memory.content}"
        )


def run_memory_archive(plane: ControlPlane, args: argparse.Namespace) -> None:
    memory = plane.memory_archive(args.memory_id)
    if args.as_json:
        _print_json(_memory_dict(memory))
        return
    print(f"{memory.memory_id}  status={memory.status.value}  archived")


def run_memory_delete(plane: ControlPlane, args: argparse.Namespace) -> None:
    memory = plane.memory_delete(args.memory_id)
    if args.as_json:
        _print_json(_memory_dict(memory))
        return
    print(f"{memory.memory_id}  status={memory.status.value}  deleted")


def run_memory_purge(plane: ControlPlane, args: argparse.Namespace) -> None:
    plane.memory_purge(args.memory_id)
    if args.as_json:
        _print_json({"purged": args.memory_id})
        return
    print(f"purged: {args.memory_id}")


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", dest="as_json", action="store_true")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="personal-ai-exec",
        description=__doc__,
    )
    parser.add_argument(
        "--database",
        required=True,
        type=Path,
        help="Path to the orchestration SQLite database (created if absent).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    research = subparsers.add_parser(
        "research", help="Create and run a research execution against a corpus."
    )
    research.add_argument(
        "--corpus",
        type=Path,
        required=True,
        help="Path to the personal-knowledge SQLite database (search corpus).",
    )
    _add_common(research)
    research.add_argument("objective")
    research.set_defaults(handler=run_research)

    execution = subparsers.add_parser(
        "execution", help="Control-plane operations on executions."
    )
    exec_sub = execution.add_subparsers(dest="exec_action", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        cmd = exec_sub.add_parser(name, help=help_text)
        _add_common(cmd)
        return cmd

    list_parser = add("list", "List executions.")
    list_parser.set_defaults(handler=run_list)

    show_parser = add("show", "Show an execution and its tasks.")
    show_parser.add_argument("execution_id")
    show_parser.set_defaults(handler=run_show)

    events_parser = add("events", "Read the event stream (cursor-based).")
    events_parser.add_argument("--cursor", type=int, default=0)
    events_parser.add_argument("execution_id")
    events_parser.set_defaults(handler=run_events)

    board_parser = add("board", "Show the Kanban projection.")
    board_parser.add_argument("execution_id")
    board_parser.set_defaults(handler=run_board)

    approve_parser = add("approve", "Approve a permission request.")
    approve_parser.add_argument("execution_id")
    approve_parser.add_argument("task_id")
    approve_parser.add_argument("permission")
    approve_parser.add_argument("--approver", default="user")
    approve_parser.set_defaults(handler=run_approve)

    reject_parser = add("reject", "Reject a permission request.")
    reject_parser.add_argument("execution_id")
    reject_parser.add_argument("task_id")
    reject_parser.add_argument("permission")
    reject_parser.add_argument("--approver", default="user")
    reject_parser.set_defaults(handler=run_reject)

    resume_parser = add("resume", "Resume a paused or gated execution.")
    resume_parser.add_argument("execution_id")
    resume_parser.set_defaults(handler=run_resume)

    pause_parser = add("pause", "Pause an execution (idempotent).")
    pause_parser.add_argument("execution_id")
    pause_parser.set_defaults(handler=run_pause)

    cancel_parser = add("cancel", "Cancel an execution.")
    cancel_parser.add_argument("execution_id")
    cancel_parser.set_defaults(handler=run_cancel)

    retry_parser = add("retry", "Retry a failed task (recovery).")
    retry_parser.add_argument("execution_id")
    retry_parser.add_argument("task_id")
    retry_parser.set_defaults(handler=run_retry)

    memory = subparsers.add_parser(
        "memory", help="Control-plane operations on durable memory."
    )
    mem_sub = memory.add_subparsers(dest="mem_action", required=True)

    def add_mem(name: str, help_text: str) -> argparse.ArgumentParser:
        cmd = mem_sub.add_parser(name, help=help_text)
        _add_common(cmd)
        return cmd

    add_mem_parser = add_mem("add", "Create an explicitly-provided memory.")
    add_mem_parser.add_argument(
        "--kind",
        choices=[k.value for k in MemoryKind],
        default="preference",
        help="Kind of memory (default: preference).",
    )
    add_mem_parser.add_argument(
        "--scope",
        choices=["global", "agent", "project", "execution"],
        default="global",
    )
    add_mem_parser.add_argument("--scope-id", default=None)
    add_mem_parser.add_argument("--summary", default="")
    add_mem_parser.add_argument("--source-id", default="")
    add_mem_parser.add_argument(
        "--confidence", type=float, default=0.5, help="0.0-1.0 (default 0.5)."
    )
    add_mem_parser.add_argument(
        "--importance", type=float, default=0.5, help="0.0-1.0 (default 0.5)."
    )
    add_mem_parser.add_argument(
        "--expires-at",
        default=None,
        help="ISO-8601 datetime; retrieve stops after this point.",
    )
    add_mem_parser.add_argument("content")
    add_mem_parser.set_defaults(handler=run_memory_add)

    list_mem = add_mem("list", "List memories (active by default).")
    list_mem.add_argument(
        "--status",
        choices=["active", "archived", "deleted"],
        default="active",
    )
    list_mem.set_defaults(handler=run_memory_list)

    show_mem = add_mem("show", "Show a memory and its safe audit events.")
    show_mem.add_argument("memory_id")
    show_mem.set_defaults(handler=run_memory_show)

    search_mem = add_mem(
        "search", "Deterministic scope-aware retrieval over active memories."
    )
    search_mem.add_argument(
        "--scope",
        choices=["global", "agent", "project", "execution"],
        default=None,
    )
    search_mem.add_argument("--scope-id", default=None)
    search_mem.add_argument(
        "--limit", type=int, default=10, help="Max hits (default 10)."
    )
    search_mem.add_argument(
        "--include-expired", action="store_true", dest="include_expired"
    )
    search_mem.add_argument("query")
    search_mem.set_defaults(handler=run_memory_search)

    archive_mem = add_mem("archive", "Retire a memory (kept, no longer retrieved).")
    archive_mem.add_argument("memory_id")
    archive_mem.set_defaults(handler=run_memory_archive)

    delete_mem = add_mem("delete", "Logically delete a memory.")
    delete_mem.add_argument("memory_id")
    delete_mem.set_defaults(handler=run_memory_delete)

    purge_mem = add_mem(
        "purge", "Physically purge a memory record (privacy-sensitive)."
    )
    purge_mem.add_argument("memory_id")
    purge_mem.set_defaults(handler=run_memory_purge)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    corpus, plane, orb_conn = _build_corpora(args)
    try:
        args.handler(plane, args)
    except PlanNotCompleteError as exc:
        raise SystemExit(str(exc)) from exc
    except ExecutionNotFoundError as exc:
        raise SystemExit(f"Unknown execution: {exc}") from exc
    except MemoryNotFoundError as exc:
        raise SystemExit(f"Unknown memory: {exc}") from exc
    except MemoryNotConfiguredError as exc:
        raise SystemExit(str(exc)) from exc
    except (PlanTransitionError, TaskTransitionError) as exc:
        raise SystemExit(str(exc)) from exc
    except Exception as exc:  # CLI boundary converts everything to an exit code
        raise SystemExit(f"execution failed: {exc}") from exc
    finally:
        if corpus is not None:
            corpus.close()
        orb_conn.close()


if __name__ == "__main__":
    main()
