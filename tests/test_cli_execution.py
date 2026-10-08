"""Tests for the execution control-plane CLI.

The CLI exercises the real :class:`ControlPlane` over a temporary SQLite
orchestration database and (for ``research``) a temporary empty corpus. The
researcher -> verifier workflow is fully deterministic and offline: with an
empty corpus the research task gathers no evidence and verification fails, so
every assertion below is stable.

These are not unit mocks of the CLI: they run the real argument parser, real
store, and real workflow through :func:`main` and assert on stdout/SystemExit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_ai.agents.defs import build_default_agent_registry
from personal_ai.agents.models import Permission
from personal_ai.agents.policy import (
    ApprovalRequiredError,
    PolicyDenialError,
)
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import build_default_agent_tools
from personal_ai.execution import (
    ControlPlane,
    PlanStatus,
    TaskStatus,
    open_orchestration_store,
)
from personal_ai.execution.cli import main, parse_args
from personal_ai.execution.models import AgentResult, Plan, Task
from personal_ai.execution.planner import Planner, TaskSpec
from personal_ai.execution.workflows import default_work_dispatch
from personal_ai.storage import connect_database


def _tmp_corpus(tmp_path: Path) -> Path:
    db = tmp_path / "corpus.db"
    connection = connect_database(db)
    connection.close()
    return db


def _create_execution(tmp_path: Path, capsys, objective: str = "career goals") -> str:
    corpus = _tmp_corpus(tmp_path)
    orch = tmp_path / "orch.db"
    main(
        [
            "--database",
            str(orch),
            "research",
            objective,
            "--corpus",
            str(corpus),
            "--json",
        ]
    )
    data = json.loads(capsys.readouterr().out)
    return data["execution_id"]


class _GatedPlanner(Planner):
    """Deterministic planner for a single approval-gated engineer task."""

    def deterministic_research_plan(
        self, plan_id: str, objective: str, created_at: str
    ) -> Plan:
        spec = TaskSpec(
            task_id=f"{plan_id}-code",
            title="Write a note",
            description=objective,
            agent="engineer",
            skill="corpus-research",
            tools=("filesystem.write",),
        )
        return Plan(
            plan_id=plan_id,
            objective=objective,
            status=PlanStatus.PLANNED,
            task_ids=(spec.task_id,),
            risk="medium",
            assumptions=(),
            constraints=("approval-gated",),
            created_at=created_at,
            updated_at=created_at,
        )

    def build_tasks(self, plan: Plan, created_at: str) -> tuple[Task, ...]:
        return (
            Task(
                task_id=f"{plan.plan_id}-code",
                plan_id=plan.plan_id,
                title="Write a note",
                description=plan.objective,
                status=TaskStatus.READY,
                assigned_agent="engineer",
                skill="corpus-research",
                tools=("filesystem.write",),
                created_at=created_at,
                updated_at=created_at,
            ),
        )


def _gated_work(agent, task, policy, ctx):
    """Real work: try the gated tool through the actual PolicyEngine."""
    try:
        policy.execute(
            agent, "filesystem.write", {"path": "note.txt", "content": "hello"}
        )
    except ApprovalRequiredError as exc:
        return AgentResult(
            status="needs_approval",
            summary="paused pending approval",
            metrics={"permission": "filesystem.write", "tool": "filesystem.write"},
            error=str(exc),
        )
    except PolicyDenialError as exc:
        return AgentResult(status="failed", summary="denied", error=str(exc))
    return AgentResult(
        status="completed",
        summary="note written",
        metrics={"summary": "note written", "path": "note.txt"},
    )


def _gated_control_plane(
    tmp_path: Path, workdir: str = "workspace"
) -> tuple[object, object, ControlPlane, Path]:
    """ControlPlane over a temp store, paused at an approval gate on run."""
    conn, store = open_orchestration_store(tmp_path / "orch.db")
    managers = build_default_agent_registry()
    skills = build_default_skill_registry()
    workspace = tmp_path / workdir
    workspace.mkdir(exist_ok=True)

    def dispatch(agent, task, policy, ctx):
        if task.assigned_agent == "engineer":
            return _gated_work(agent, task, policy, ctx)
        return default_work_dispatch()(agent, task, policy, ctx)

    cp = ControlPlane(
        store,
        agents=managers,
        skills=skills,
        tools=build_default_agent_tools(workspace=workspace),
        planner=_GatedPlanner(managers, skills),
        work_dispatch=dispatch,
        now=lambda: "2026-01-01T00:00:00+00:00",
    )
    return conn, store, cp, workspace


def test_cli_requires_database(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        parse_args(["research", "goal"])


def test_cli_research_reports_plan_and_events(tmp_path: Path, capsys) -> None:
    corpus = _tmp_corpus(tmp_path)
    main(
        [
            "--database",
            str(tmp_path / "orch.db"),
            "research",
            "career goals",
            "--corpus",
            str(corpus),
        ]
    )
    out = capsys.readouterr().out
    assert "status=" in out
    assert "objective: career goals" in out
    assert "events:" in out


def test_cli_research_json_contract(tmp_path: Path, capsys) -> None:
    corpus = _tmp_corpus(tmp_path)
    main(
        [
            "--database",
            str(tmp_path / "orch.db"),
            "research",
            "what are my goals?",
            "--corpus",
            str(corpus),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution_id"].startswith("exec-")
    assert "status" in payload
    assert "objective" in payload
    assert "task_count" in payload
    assert "artifact_count" in payload
    # An empty corpus cannot produce verified evidence.
    assert payload["status"] == "failed"


def test_cli_execution_list_show_events_board(tmp_path: Path, capsys) -> None:
    execution_id = _create_execution(tmp_path, capsys)
    base = ["--database", str(tmp_path / "orch.db")]

    main([*base, "execution", "list", "--json"])
    listed = json.loads(capsys.readouterr().out)
    assert [item["execution_id"] for item in listed] == [execution_id]
    assert listed[0]["status"] == "failed"

    main([*base, "execution", "show", execution_id, "--json"])
    shown = json.loads(capsys.readouterr().out)
    assert shown["plan"]["execution_id"] == execution_id
    assert shown["tasks"], "research plan must create at least one task"

    main([*base, "execution", "events", execution_id, "--json"])
    events = json.loads(capsys.readouterr().out)
    assert events, "research must emit events"
    kinds = [event["event_type"] for event in events]
    assert "plan.created" in kinds
    assert "plan.failed" in kinds
    seqs = [event["seq"] for event in events]
    assert seqs == sorted(seqs)
    # A cursor at/after the stream boundary returns nothing.
    main([*base, "execution", "events", execution_id, "--cursor", str(len(events))])
    assert "No events" in capsys.readouterr().out

    main([*base, "execution", "board", execution_id, "--json"])
    board = json.loads(capsys.readouterr().out)
    assert board["status"] == "failed"
    assert board["columns"]["failed"], "failed research task lands in the failed column"


def test_cli_execution_retry_resume_cancel(tmp_path: Path, capsys) -> None:
    execution_id = _create_execution(tmp_path, capsys)
    base = ["--database", str(tmp_path / "orch.db")]

    main([*base, "execution", "show", execution_id, "--json"])
    shown = json.loads(capsys.readouterr().out)
    failed_task = next(
        task["task_id"] for task in shown["tasks"] if task["status"] == "failed"
    )

    main([*base, "execution", "retry", execution_id, failed_task, "--json"])
    retried = json.loads(capsys.readouterr().out)
    assert retried["task_id"] == failed_task
    assert retried["status"] == "ready"
    assert (
        json.loads(
            _run_json(capsys, [*base, "execution", "board", execution_id, "--json"])
        )["status"]
        == "running"
    )

    main([*base, "execution", "resume", execution_id, "--json"])
    resumed = json.loads(capsys.readouterr().out)
    # Re-running against the empty corpus fails verification again.
    assert resumed["status"] == "failed"

    # Recover once more so the plan is RUNNING, where cancel is meaningful.
    main([*base, "execution", "retry", execution_id, failed_task])
    main([*base, "execution", "cancel", execution_id])
    assert "status=cancelled" in capsys.readouterr().out

    # Resume on a cancelled (terminal) execution is a no-op.
    main([*base, "execution", "resume", execution_id, "--json"])
    assert json.loads(capsys.readouterr().out)["status"] == "cancelled"
    main([*base, "execution", "board", execution_id, "--json"])
    assert json.loads(capsys.readouterr().out)["status"] == "cancelled"


def _run_json(capsys, argv) -> str:
    main(argv)
    return capsys.readouterr().out


def test_cli_approve_reject_on_unknown_request_exits(tmp_path: Path, capsys) -> None:
    execution_id = _create_execution(tmp_path, capsys)
    base = ["--database", str(tmp_path / "orch.db")]

    with pytest.raises(SystemExit) as excinfo:
        main([*base, "execution", "approve", execution_id, "no-such-task", "shell.run"])
    assert "No pending approval" in str(excinfo.value)

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                *base,
                "execution",
                "reject",
                execution_id,
                "no-such-task",
                "filesystem.write",
            ]
        )
    assert "No pending approval" in str(excinfo.value)


def _paused_execution(cp: ControlPlane, execution_id: str) -> tuple[str, str]:
    """Create + run a gated execution, returning ``(plan_id, task_id)``."""
    plan = cp.create_execution("Write a note", execution_id=execution_id)
    task_id = cp.tasks(plan.plan_id)[0].task_id
    paused = cp.run_execution(plan.plan_id)
    assert paused.status is PlanStatus.NEEDS_APPROVAL
    assert cp.approvals(plan.plan_id)[0]["status"] == "pending"
    return plan.plan_id, task_id


def test_cli_approve_and_reject_success_paths(tmp_path: Path, capsys) -> None:
    """The CLI approve/reject decision lines print the scoped triple and the
    decision is durable — the same paused execution then completes (approve)
    or fails (reject) through the real engine."""
    conn, _, cp, workspace = _gated_control_plane(tmp_path)
    db = ["--database", str(tmp_path / "orch.db")]
    write = Permission.FILESYSTEM_WRITE.value

    plan_id, task_id = _paused_execution(cp, "exec-cli-approve")
    main([*db, "execution", "approve", plan_id, task_id, write])
    out = capsys.readouterr().out
    assert out == (f"approved: execution={plan_id} task={task_id} permission={write}\n")
    assert "status=" not in out
    assert cp.approvals(plan_id)[0]["status"] == "approved"

    # The CLI-approved decision is honored by the real engine on resume.
    final = cp.resume_execution(plan_id)
    assert final.status is PlanStatus.COMPLETED
    assert (workspace / "note.txt").read_text() == "hello"

    # Rejection: durable and scoped the same way; the engine honours it in a
    # separate workspace so the earlier approved write cannot mask it.
    conn2, _, cp2, reject_ws = _gated_control_plane(tmp_path, workdir="ws_reject")
    reject_plan, reject_task = _paused_execution(cp2, "exec-cli-reject")
    main([*db, "execution", "reject", reject_plan, reject_task, write])
    out = capsys.readouterr().out
    assert out == (
        f"rejected: execution={reject_plan} task={reject_task} permission={write}\n"
    )
    assert "status=" not in out
    assert cp2.approvals(reject_plan)[0]["status"] == "rejected"

    failed = cp2.resume_execution(reject_plan)
    assert failed.status is PlanStatus.FAILED
    assert list(reject_ws.iterdir()) == [], "rejected task must not execute the write"
    board = cp2.board(reject_plan)
    assert board["columns"]["failed"], "rejected task cannot complete"

    conn.close()
    conn2.close()


def test_cli_unknown_execution_exits_cleanly(tmp_path: Path, capsys) -> None:
    _create_execution(tmp_path, capsys)
    base = ["--database", str(tmp_path / "orch.db")]

    with pytest.raises(SystemExit) as excinfo:
        main([*base, "execution", "show", "exec-does-not-exist"])
    assert "Unknown execution" in str(excinfo.value)
