"""Manual researcher -> verifier orchestration demo against a local corpus.

This is a manual demonstration of the Phase 39A/39B agent-orchestration
framework. It wires an existing (already-indexed) personal corpus SQLite
database through the new :class:`ControlPlane` service boundary (researcher ->
verifier workflow) and prints the resulting plan, tasks, events, board, and
final verdict.

It is deliberately NOT part of the automated pytest suite:
* it reads a local corpus database (privacy-sensitive), and
* it expects ``--database`` to contain documents/chunks/extractions to search.

The retrieval used by the researcher tool is keyword/provenance search over
the corpus, so this demo runs fully offline with no Ollama and no network.

Usage:

    uv run python scripts/demo_researcher_verifier.py \\
        --database /tmp/knowledge.db \\
        --objective "What are my long-term career goals?"
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from personal_ai.agents.tools import build_default_agent_tools
from personal_ai.execution import (
    ControlPlane,
    open_orchestration_store,
)
from personal_ai.execution.models import PlanStatus
from personal_ai.retrieval import RetrievalService
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentStore,
    ExtractionStore,
    connect_database,
)


def run_demo(database: Path, objective: str) -> None:
    corpus_conn = connect_database(database)
    retrieval_service = RetrievalService(
        ChunkStore(corpus_conn),
        ExtractionStore(corpus_conn),
        DocumentStore(corpus_conn),
        ConversationStore(corpus_conn) if _has_conversations(corpus_conn) else None,
    )

    orch_path = Path(tempfile.mkdtemp(prefix="orch-demo-")) / "orch.db"
    _, store = open_orchestration_store(orch_path)
    control = ControlPlane(
        store,
        tools=build_default_agent_tools(retrieval_service=retrieval_service),
    )

    plan = control.create_research_execution(objective)
    print(f"\n[plan] {plan.plan_id}  status={plan.status.value}")
    for task in control.tasks(plan.plan_id):
        print(
            f"  - task {task.task_id}  agent={task.assigned_agent} skill={task.skill}"
        )

    final = control.run_execution(plan.plan_id)
    print(f"\n[result] plan status = {final.status.value}")
    if final.status is PlanStatus.COMPLETED:
        print(f"[outcome] {final.final_outcome}")
    else:
        print("[outcome] research/verification did not complete.")

    print("\n[events]")
    for event in control.events(plan.plan_id):
        print(
            f"  {event.seq:>2} {event.event_type:<22} agent={event.agent_id or '-'} "
            f"tool={event.tool or '-'}"
        )

    print("\n[board]")
    board = control.board(plan.plan_id)
    print(f"  status={board['status']}")
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


def _has_conversations(connection) -> bool:
    try:
        row = connection.execute("SELECT COUNT(*) FROM conversations").fetchone()
        return bool(row and row[0] > 0)
    except Exception:  # noqa: BLE001 - table may not exist
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        required=True,
        type=Path,
        help="Path to the corpus SQLite database.",
    )
    parser.add_argument(
        "--objective",
        default="What are my long-term goals?",
        help="Research objective to run through the researcher -> verifier workflow.",
    )
    args = parser.parse_args()
    run_demo(args.database, args.objective)


if __name__ == "__main__":
    main()
