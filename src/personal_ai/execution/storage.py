"""SQLite-backed durable storage for orchestration plans, tasks, events, and artifacts.

Follows the project's existing storage conventions: store classes create
their tables via ``CREATE TABLE IF NOT EXISTS``, metadata/JSON columns are
dumped on write, and a shared :func:`personal_ai.storage.documents.connect_database`
connection is reused. No distributed stack is introduced — this is a single-user
local monolith.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from personal_ai.execution.events import OrchestrationEvent
from personal_ai.execution.models import (
    Artifact,
    Evidence,
    Plan,
    PlanStatus,
    Task,
    TaskStatus,
    now_iso,
)
from personal_ai.storage.documents import connect_database

_PLANS_SCHEMA = """
CREATE TABLE IF NOT EXISTS orchestration_plans (
    plan_id TEXT PRIMARY KEY,
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    task_ids TEXT NOT NULL,
    risk TEXT NOT NULL,
    assumptions TEXT NOT NULL,
    constraints TEXT NOT NULL,
    final_outcome TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_TASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS orchestration_tasks (
    task_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL,
    dependencies TEXT NOT NULL,
    assigned_agent TEXT,
    selected_model TEXT,
    skill TEXT,
    tools TEXT NOT NULL,
    policy TEXT NOT NULL,
    inputs TEXT NOT NULL,
    outputs TEXT NOT NULL,
    retry_count INTEGER NOT NULL,
    max_retries INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    approval_state TEXT NOT NULL
)
"""

_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS orchestration_events (
    id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    task_id TEXT,
    agent_id TEXT,
    tool TEXT,
    timestamp TEXT NOT NULL,
    status TEXT,
    payload TEXT NOT NULL
)
"""

_ARTIFACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS orchestration_artifacts (
    artifact_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    producing_task_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    reference TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_EVIDENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS orchestration_evidence (
    evidence_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    source_type TEXT,
    source TEXT,
    document_id TEXT,
    chunk_id TEXT,
    relevance REAL NOT NULL,
    excerpt TEXT NOT NULL,
    metadata TEXT NOT NULL
)
"""

_APPROVALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS orchestration_approvals (
    execution_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    tool TEXT,
    risk TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL,
    requested_at TEXT,
    decided_at TEXT,
    approver TEXT,
    PRIMARY KEY (execution_id, task_id, permission)
)
"""


class OrchestrationStore:
    """Durable storage for plans, tasks, events, and artifacts."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        for schema in (
            _PLANS_SCHEMA,
            _TASKS_SCHEMA,
            _EVENTS_SCHEMA,
            _ARTIFACTS_SCHEMA,
            _EVIDENCE_SCHEMA,
            _APPROVALS_SCHEMA,
        ):
            self._connection.execute(schema)
        self._connection.commit()

    # ---- Plans ----
    def save_plan(self, plan: Plan) -> None:
        self._connection.execute(
            """
            INSERT INTO orchestration_plans (
                plan_id, objective, status, task_ids, risk, assumptions,
                constraints, final_outcome, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                objective=excluded.objective, status=excluded.status,
                task_ids=excluded.task_ids, risk=excluded.risk,
                assumptions=excluded.assumptions, constraints=excluded.constraints,
                final_outcome=excluded.final_outcome, updated_at=excluded.updated_at
            """,
            (
                plan.plan_id,
                plan.objective,
                plan.status.value,
                json.dumps(list(plan.task_ids)),
                plan.risk,
                json.dumps(list(plan.assumptions)),
                json.dumps(list(plan.constraints)),
                plan.final_outcome,
                plan.created_at,
                plan.updated_at,
            ),
        )
        self._connection.commit()

    def get_plan(self, plan_id: str) -> Plan | None:
        row = self._connection.execute(
            "SELECT * FROM orchestration_plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            return None
        return Plan(
            plan_id=row[0],
            objective=row[1],
            status=PlanStatus(row[2]),
            task_ids=tuple(json.loads(row[3])),
            risk=row[4],
            assumptions=tuple(json.loads(row[5])),
            constraints=tuple(json.loads(row[6])),
            final_outcome=row[7],
            created_at=row[8],
            updated_at=row[9],
        )

    def list_plan_ids(self) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT plan_id FROM orchestration_plans ORDER BY updated_at"
        ).fetchall()
        return tuple(r[0] for r in rows)

    # ---- Tasks ----
    def save_task(self, task: Task) -> None:
        self._connection.execute(
            """
            INSERT INTO orchestration_tasks (
                task_id, plan_id, title, description, status, priority,
                dependencies, assigned_agent, selected_model, skill, tools,
                policy, inputs, outputs, retry_count, max_retries, created_at,
                updated_at, completed_at, error, approval_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                plan_id=excluded.plan_id, title=excluded.title,
                description=excluded.description, status=excluded.status,
                priority=excluded.priority, dependencies=excluded.dependencies,
                assigned_agent=excluded.assigned_agent,
                selected_model=excluded.selected_model, skill=excluded.skill,
                tools=excluded.tools, policy=excluded.policy,
                inputs=excluded.inputs, outputs=excluded.outputs,
                retry_count=excluded.retry_count, max_retries=excluded.max_retries,
                updated_at=excluded.updated_at, completed_at=excluded.completed_at,
                error=excluded.error, approval_state=excluded.approval_state
            """,
            (
                task.task_id,
                task.plan_id,
                task.title,
                task.description,
                task.status.value,
                task.priority,
                json.dumps(list(task.dependencies)),
                task.assigned_agent,
                task.selected_model,
                task.skill,
                json.dumps(list(task.tools)),
                task.policy,
                json.dumps(task.inputs),
                json.dumps(task.outputs),
                task.retry_count,
                task.max_retries,
                task.created_at,
                task.updated_at,
                task.completed_at,
                task.error,
                task.approval_state,
            ),
        )
        if task.evidence:
            for evidence in task.evidence:
                self._connection.execute(
                    """
                    INSERT INTO orchestration_evidence (
                        evidence_id, task_id, plan_id, source_type, source,
                        document_id, chunk_id, relevance, excerpt, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(evidence_id) DO UPDATE SET
                        task_id=excluded.task_id, plan_id=excluded.plan_id,
                        source_type=excluded.source_type, source=excluded.source,
                        document_id=excluded.document_id, chunk_id=excluded.chunk_id,
                        relevance=excluded.relevance, excerpt=excluded.excerpt,
                        metadata=excluded.metadata
                    """,
                    (
                        evidence.evidence_id,
                        task.task_id,
                        task.plan_id,
                        evidence.source_type,
                        evidence.source,
                        evidence.document_id,
                        evidence.chunk_id,
                        float(evidence.relevance),
                        evidence.excerpt,
                        json.dumps(evidence.metadata),
                    ),
                )
        self._connection.commit()

    def get_task(self, task_id: str) -> Task | None:
        row = self._connection.execute(
            "SELECT * FROM orchestration_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return Task(
            task_id=row[0],
            plan_id=row[1],
            title=row[2],
            description=row[3],
            status=TaskStatus(row[4]),
            priority=row[5],
            dependencies=tuple(json.loads(row[6])),
            assigned_agent=row[7],
            selected_model=row[8],
            skill=row[9],
            tools=tuple(json.loads(row[10])),
            policy=row[11],
            inputs=json.loads(row[12]),
            outputs=json.loads(row[13]),
            retry_count=row[14],
            max_retries=row[15],
            created_at=row[16],
            updated_at=row[17],
            completed_at=row[18],
            error=row[19],
            approval_state=row[20],
            evidence=self.evidence_for_task(row[0]),
        )

    def tasks_for_plan(self, plan_id: str) -> tuple[Task, ...]:
        rows = self._connection.execute(
            "SELECT task_id FROM orchestration_tasks WHERE plan_id = ? ORDER BY priority, task_id",
            (plan_id,),
        ).fetchall()
        return tuple(
            self.get_task(r[0]) for r in rows if self.get_task(r[0]) is not None
        )

    # ---- Events ----
    def append_event(self, event: OrchestrationEvent) -> None:
        self._connection.execute(
            """
            INSERT INTO orchestration_events (
                id, seq, event_type, plan_id, task_id, agent_id, tool,
                timestamp, status, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.seq,
                event.event_type,
                event.plan_id,
                event.task_id,
                event.agent_id,
                event.tool,
                event.timestamp,
                event.status,
                json.dumps(event.payload),
            ),
        )
        self._connection.commit()

    def events_for_plan(self, plan_id: str) -> tuple[OrchestrationEvent, ...]:
        rows = self._connection.execute(
            "SELECT * FROM orchestration_events WHERE plan_id = ? ORDER BY seq",
            (plan_id,),
        ).fetchall()
        out = []
        for row in rows:
            out.append(
                OrchestrationEvent(
                    id=row[0],
                    seq=row[1],
                    event_type=row[2],
                    plan_id=row[3],
                    task_id=row[4],
                    agent_id=row[5],
                    tool=row[6],
                    timestamp=row[7],
                    status=row[8],
                    payload=json.loads(row[9]),
                )
            )
        return tuple(out)

    def event_count(self) -> int:
        return self._connection.execute(
            "SELECT COUNT(*) FROM orchestration_events"
        ).fetchone()[0]

    def events_after_seq(
        self, plan_id: str, after_seq: int
    ) -> tuple[OrchestrationEvent, ...]:
        """Return events for an execution with ``seq`` strictly greater than ``after_seq``.

        Used for cursor-style incremental reads ("what changed since I last
        looked") without a full rescan.
        """
        rows = self._connection.execute(
            "SELECT * FROM orchestration_events WHERE plan_id = ? AND seq > ? ORDER BY seq",
            (plan_id, after_seq),
        ).fetchall()
        out = []
        for row in rows:
            out.append(
                OrchestrationEvent(
                    id=row[0],
                    seq=row[1],
                    event_type=row[2],
                    plan_id=row[3],
                    task_id=row[4],
                    agent_id=row[5],
                    tool=row[6],
                    timestamp=row[7],
                    status=row[8],
                    payload=json.loads(row[9]),
                )
            )
        return tuple(out)

    # ---- Artifacts ----
    def save_artifact(self, artifact: Artifact) -> None:
        self._connection.execute(
            """
            INSERT INTO orchestration_artifacts (
                artifact_id, type, title, producing_task_id, content_hash,
                reference, metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                type=excluded.type, title=excluded.title,
                producing_task_id=excluded.producing_task_id,
                content_hash=excluded.content_hash, reference=excluded.reference,
                metadata=excluded.metadata
            """,
            (
                artifact.artifact_id,
                artifact.type,
                artifact.title,
                artifact.producing_task_id,
                artifact.content_hash,
                artifact.reference,
                json.dumps(artifact.metadata),
                artifact.created_at,
            ),
        )
        self._connection.commit()

    def artifacts_for_task(self, task_id: str) -> tuple[Artifact, ...]:
        rows = self._connection.execute(
            "SELECT * FROM orchestration_artifacts WHERE producing_task_id = ? ORDER BY artifact_id",
            (task_id,),
        ).fetchall()
        out = []
        for row in rows:
            out.append(
                Artifact(
                    artifact_id=row[0],
                    type=row[1],
                    title=row[2],
                    producing_task_id=row[3],
                    content_hash=row[4],
                    reference=row[5],
                    metadata=json.loads(row[6]),
                    created_at=row[7],
                )
            )
        return tuple(out)

    def evidence_for_task(self, task_id: str) -> tuple[Evidence, ...]:
        rows = self._connection.execute(
            "SELECT * FROM orchestration_evidence WHERE task_id = ? ORDER BY evidence_id",
            (task_id,),
        ).fetchall()
        out = []
        for row in rows:
            out.append(
                Evidence(
                    evidence_id=row[0],
                    source_type=row[3],
                    source=row[4],
                    document_id=row[5],
                    chunk_id=row[6],
                    relevance=row[7],
                    excerpt=row[8],
                    metadata=json.loads(row[9]),
                )
            )
        return tuple(out)

    # ---- Approvals ----
    def save_approval_request(
        self,
        execution_id: str,
        task_id: str,
        permission: str,
        *,
        tool: str | None = None,
        risk: str = "unknown",
        reason: str | None = None,
        requested_at: str = "",
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO orchestration_approvals (
                execution_id, task_id, permission, tool, risk, reason, status,
                requested_at, decided_at, approver
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(execution_id, task_id, permission) DO UPDATE SET
                tool=excluded.tool, risk=excluded.risk, reason=excluded.reason,
                status=excluded.status, requested_at=excluded.requested_at
            """,
            (
                execution_id,
                task_id,
                permission,
                tool,
                risk,
                reason,
                "pending",
                requested_at,
                None,
                None,
            ),
        )
        self._connection.commit()

    def approval_status(self, execution_id: str, task_id: str, permission: str) -> str:
        """Return the durable approval state for one task+permission.

        ``"pending"`` means no decision has been recorded yet; ``"approved"``
        and ``"rejected"`` are the two terminal decisions. Absent rows read as
        ``"pending"`` so a missing record can never be (mis)read as granted.
        """
        row = self._connection.execute(
            "SELECT status FROM orchestration_approvals "
            "WHERE execution_id = ? AND task_id = ? AND permission = ? LIMIT 1",
            (execution_id, task_id, permission),
        ).fetchone()
        if row is None:
            return "pending"
        return row[0]

    def max_event_seq(self) -> int:
        """The highest event sequence persisted so far (0 for an empty stream).

        Lets a freshly constructed orchestrator resume sequencing from durable
        state so cursor reads remain monotonic across process restarts.
        """
        row = self._connection.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM orchestration_events"
        ).fetchone()
        return int(row[0])

    def approval_requests(self, execution_id: str) -> tuple[dict[str, object], ...]:
        rows = self._connection.execute(
            "SELECT task_id, permission, tool, risk, reason, status, "
            "requested_at, decided_at, approver FROM orchestration_approvals "
            "WHERE execution_id = ? ORDER BY task_id",
            (execution_id,),
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "task_id": r[0],
                    "permission": r[1],
                    "tool": r[2],
                    "risk": r[3],
                    "reason": r[4],
                    "status": r[5],
                    "requested_at": r[6],
                    "decided_at": r[7],
                    "approver": r[8],
                }
            )
        return tuple(out)

    def decide_approval(
        self,
        execution_id: str,
        task_id: str,
        permission: str,
        status: str,
        *,
        approver: str = "user",
    ) -> None:
        decided_at = now_iso()
        self._connection.execute(
            "UPDATE orchestration_approvals SET status = ?, decided_at = ?, "
            "approver = ? WHERE execution_id = ? AND task_id = ? AND permission = ?",
            (status, decided_at, approver, execution_id, task_id, permission),
        )
        self._connection.commit()

    def counts(self) -> dict[str, int]:
        return {
            "plans": self._connection.execute(
                "SELECT COUNT(*) FROM orchestration_plans"
            ).fetchone()[0],
            "tasks": self._connection.execute(
                "SELECT COUNT(*) FROM orchestration_tasks"
            ).fetchone()[0],
            "events": self.event_count(),
            "artifacts": self._connection.execute(
                "SELECT COUNT(*) FROM orchestration_artifacts"
            ).fetchone()[0],
            "approvals": self._connection.execute(
                "SELECT COUNT(*) FROM orchestration_approvals"
            ).fetchone()[0],
        }


def open_orchestration_store(
    path: str | Path,
) -> tuple[sqlite3.Connection, OrchestrationStore]:
    connection = connect_database(path)
    return connection, OrchestrationStore(connection)
