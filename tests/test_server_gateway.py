"""Tests for the Personal AI gateway HTTP surface (Phase 44).

Covers the OpenAI-compatible endpoints (``/v1/models``, streaming chat) plus
the read-only control-plane / memory / workout endpoints under ``/api/*``, and
two acceptance tests that run real :class:`ControlPlane` and
:class:`WorkoutQueryService` services behind the HTTP app.

Everything is offline and deterministic: agents are fakes, SQLite lives in
``tmp_path``, no Ollama, no network, no real personal documents. Because SQLite
connections are bound to one thread, every request harness builds its app and
runs the ASGI app inside a single ``asyncio.run`` scope (the event-loop thread
is the connection-creating thread).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx

from personal_ai.agent import Agent
from personal_ai.agents.defs import build_default_agent_registry
from personal_ai.agents.policy import ApprovalRequiredError, PolicyDenialError
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import build_default_agent_tools
from personal_ai.execution import (
    ControlPlane,
    EventType,
    Plan,
    PlanStatus,
    Task,
    TaskStatus,
    default_work_dispatch,
    open_orchestration_store,
)
from personal_ai.execution.models import AgentResult
from personal_ai.execution.planner import Planner, TaskSpec
from personal_ai.memory import MemoryService, MemoryStore
from personal_ai.ollama_client import (
    ChatMessage,
    ChatResponse,
    ToolCall,
)
from personal_ai.server import MODEL_ID, create_app
from personal_ai.tools import create_default_registry
from personal_ai.workouts import WorkoutQueryService, WorkoutStore
from personal_ai.workouts.parser import parse_workout_file
from tests.workouts_fixtures import FIXTURE_CSV

DEFAULT_MODEL = "qwen3.5:9b"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeAgent:
    def __init__(self, answer: str = "fake answer"):
        self.answer = answer
        self.calls: list[object] = []

    def run(self, messages: object) -> str:
        self.calls.append(messages)
        return self.answer


class _FakeBuilt:
    def __init__(self, agent: object):
        self.agent = agent

    def close(self) -> None:
        pass


class _WorkoutChatClient:
    """Two-round scripted client: emits ``search_workouts``, then answers.

    The second response derives its text from the real tool result the agent
    injected back, proving the policy-gated tool executed against the workout
    service.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[list[ChatMessage], object]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        tools: object = None,
    ) -> ChatResponse:
        self.calls.append((list(messages), tools))
        if len(self.calls) == 1:
            return ChatResponse(
                content="",
                model="test-model",
                done=True,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="search_workouts",
                        arguments={"query": "bench press"},
                    )
                ],
            )
        tool_message = messages[-1]
        content = str(getattr(tool_message, "content", ""))
        return ChatResponse(
            content=f"Your training log says: {content}",
            model="test-model",
            done=True,
        )


# ---------------------------------------------------------------------------
# Request harness (single-threaded ASGI)
# ---------------------------------------------------------------------------


def _run(coro) -> object:
    return asyncio.run(coro)


def run_gateway(
    tmp_path: Path,
    scenario,
    *,
    seed_workouts: bool = True,
    agent_factory=None,
) -> object:
    async def main() -> object:
        connection, store = open_orchestration_store(tmp_path / "gateway.db")
        memory = MemoryService(MemoryStore(connection))
        workout_store = WorkoutStore(connection)
        if seed_workouts:
            records, _ = parse_workout_file(
                FIXTURE_CSV.encode("utf-8"), source_file="boostcamp.csv"
            )
            workout_store.import_records(records, source_file="boostcamp.csv")
        workout = WorkoutQueryService(workout_store)
        plane = ControlPlane(store, memory=memory, workout=workout)
        app = create_app(
            tmp_path,
            tmp_path / "gateway.db",
            model=DEFAULT_MODEL,
            agent_factory=agent_factory or (lambda: _FakeBuilt(_FakeAgent())),
            control_plane=plane,
            workout_service=workout,
        )
        try:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client,
            ):
                return await scenario(client, plane=plane, workout=workout)
        finally:
            connection.close()

    return _run(main())


def run_neutral_gateway(tmp_path: Path, scenario) -> object:
    async def main() -> object:
        app = create_app(
            tmp_path,
            None,
            model=DEFAULT_MODEL,
            agent_factory=lambda: _FakeBuilt(_FakeAgent()),
        )
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            return await scenario(client, plane=None, workout=None)

    return _run(main())


def run_approval_gateway(tmp_path: Path, scenario) -> object:
    async def main() -> object:
        connection, store = open_orchestration_store(tmp_path / "cp.db")
        managers = build_default_agent_registry()
        skills = build_default_skill_registry()
        workspace = tmp_path / "workspace"
        workspace.mkdir(exist_ok=True)

        def dispatch(agent, task, policy, ctx):
            if task.assigned_agent == "engineer":
                return _gated_work(agent, task, policy, ctx)
            return default_work_dispatch()(agent, task, policy, ctx)

        plane = ControlPlane(
            store,
            agents=managers,
            skills=skills,
            tools=build_default_agent_tools(workspace=workspace),
            planner=_EngineerPlanner(managers, skills),
            work_dispatch=dispatch,
            now=lambda: "2026-01-01T00:00:00+00:00",
        )
        app = create_app(
            tmp_path,
            tmp_path / "cp.db",
            model=DEFAULT_MODEL,
            agent_factory=lambda: _FakeBuilt(_FakeAgent()),
            control_plane=plane,
        )
        try:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client,
            ):
                return await scenario(client, plane=plane, workspace=workspace)
        finally:
            connection.close()

    return _run(main())


def _gated_work(agent, task, policy, ctx) -> AgentResult:
    """Real work function: tries a gated tool through the actual PolicyEngine."""
    try:
        policy.execute(
            agent, "filesystem.write", {"path": "note.txt", "content": "hello"}
        )
    except ApprovalRequiredError as exc:
        permission = policy.check_tool(agent, "filesystem.write").permission
        return AgentResult(
            status="needs_approval",
            summary="paused pending approval",
            metrics={
                "permission": permission.value if permission is not None else "unknown",
                "tool": "filesystem.write",
                "risk": "write",
                "reason": str(exc),
            },
            error=str(exc),
        )
    except PolicyDenialError as exc:
        return AgentResult(status="failed", summary="denied", error=str(exc))
    return AgentResult(
        status="completed",
        summary="note written",
        metrics={"summary": "note written", "path": "note.txt"},
    )


class _EngineerPlanner(Planner):
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


# ---------------------------------------------------------------------------
# /v1 streaming chat
# ---------------------------------------------------------------------------


def test_route_models_exposes_only_served_id(tmp_path: Path) -> None:
    async def scenario(client, **kwargs):
        return await client.get("/v1/models")

    res = run_neutral_gateway(tmp_path, scenario)
    assert res.status_code == 200
    data = res.json()["data"]
    assert [m["id"] for m in data] == [MODEL_ID]


def test_openwebui_chat_can_use_personal_ai_workout_capability(
    tmp_path: Path,
) -> None:
    """Acceptance: Open WebUI-style chat drives the real policy-gated tool."""

    # The workout service and agent are built inside the single-thread scope.
    async def run_chat() -> dict[str, object]:
        connection = sqlite3.connect(tmp_path / "chat.db")
        workout_store = WorkoutStore(connection)
        records, _ = parse_workout_file(
            FIXTURE_CSV.encode("utf-8"), source_file="boostcamp.csv"
        )
        workout_store.import_records(records, source_file="boostcamp.csv")
        workout = WorkoutQueryService(workout_store)
        registry = create_default_registry(workspace=tmp_path, workout_service=workout)
        client_fake = _WorkoutChatClient()
        agent = Agent(client_fake, registry)
        app = create_app(
            tmp_path,
            None,
            model=DEFAULT_MODEL,
            agent_factory=lambda: _FakeBuilt(agent),
            workout_service=workout,
        )
        try:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client,
            ):
                res = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "anything",
                        "messages": [
                            {
                                "role": "user",
                                "content": "How often do I bench press?",
                            }
                        ],
                    },
                )
                return {"res": res, "client_fake": client_fake}
        finally:
            connection.close()

    outcome = _run(run_chat())
    res: httpx.Response = outcome["res"]
    client_fake: _WorkoutChatClient = outcome["client_fake"]

    assert res.status_code == 200
    body = res.json()
    content = body["choices"][0]["message"]["content"]
    # The answer is derived from the real tool result returned by the workout
    # service — the policy-gated tool truly executed during chat.
    assert "Bench Press (Barbell)" in content
    assert body["model"] == MODEL_ID

    # The model was offered the tool and used exactly one tool round.
    assert len(client_fake.calls) == 2
    first_tools = client_fake.calls[0][1]
    assert first_tools is not None
    names = [
        schema["function"]["name"]
        for schema in first_tools  # type: ignore[union-attr]
    ]
    assert "search_workouts" in names


# ---------------------------------------------------------------------------
# Control plane over HTTP
# ---------------------------------------------------------------------------


def test_openwebui_approval_flow_is_real_control_plane(tmp_path: Path) -> None:
    """Acceptance: approve/resume over HTTP drives the durable gate end-to-end."""

    WRITE = "filesystem.write"

    async def scenario(client, plane, workspace) -> dict[str, object]:
        listing_before = (await client.get("/api/executions")).json()
        plan = plane.create_execution("Write a note", execution_id="webui-gate")
        task_id = plane.tasks(plan.plan_id)[0].task_id

        # Run from the UI: the real policy gate pauses on approval.
        paused = await client.post(f"/api/executions/{plan.plan_id}/resume")
        assert paused.status_code == 200
        assert paused.json()["status"] == "needs_approval"

        approvals = (
            await client.get(f"/api/executions/{plan.plan_id}/approvals")
        ).json()
        assert approvals["approvals"][0]["status"] == "pending"
        assert approvals["approvals"][0]["permission"] == WRITE
        # Approval requests never carry tool arguments or file content.
        assert "hello" not in str(approvals)

        # The UI cannot widen approval scope: extra fields are rejected.
        widened = await client.post(
            f"/api/executions/{plan.plan_id}/approve",
            json={
                "task_id": task_id,
                "permission": WRITE,
                "approved": True,
            },
        )
        assert widened.status_code == 400
        assert (await client.get(f"/api/executions/{plan.plan_id}/approvals")).json()[
            "approvals"
        ][0]["status"] == "pending"

        # The exact, scoped decision body approves exactly one request. The
        # decision echoes only the scoped triple — no widened scope fields.
        decided = await client.post(
            f"/api/executions/{plan.plan_id}/approve",
            json={"task_id": task_id, "permission": WRITE},
        )
        assert decided.status_code == 200
        assert set(decided.json()) == {"execution_id", "task_id", "permission"}
        assert (await client.get(f"/api/executions/{plan.plan_id}/approvals")).json()[
            "approvals"
        ][0]["status"] == "approved"

        final = await client.post(f"/api/executions/{plan.plan_id}/resume")
        assert final.status_code == 200
        assert final.json()["status"] == "completed"

        return {
            "plan_id": plan.plan_id,
            "task_id": task_id,
            "listing_before": listing_before,
            "final": final.json(),
        }

    outcome = run_approval_gateway(tmp_path, scenario)
    assert outcome["listing_before"] == {"executions": []}

    # The approved tool handler actually wrote to the workspace.
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hello"

    def verify_events() -> dict[str, object]:
        async def scenario2(client, plane, workspace) -> dict[str, object]:
            events = await client.get(
                f"/api/executions/{outcome['plan_id']}/events", params={"after_seq": 0}
            )
            board = await client.get(f"/api/executions/{outcome['plan_id']}/board")
            listing = await client.get("/api/executions")
            return {
                "events": events.json(),
                "board": board.json(),
                "listing": listing.json(),
            }

        return run_approval_gateway(tmp_path, scenario2)

    after = verify_events()
    kinds = [e["event_type"] for e in after["events"]["events"]]
    assert kinds.count(EventType.APPROVAL_REQUESTED) == 1
    assert kinds.count(EventType.APPROVAL_GRANTED) == 1
    assert EventType.EXECUTION_RESUMED in kinds
    assert EventType.PLAN_COMPLETED in kinds
    assert after["events"]["next_seq"] == max(
        e["seq"] for e in after["events"]["events"]
    )

    board = after["board"]
    assert board["status"] == "completed"
    assert [c["task_id"] for c in board["columns"]["done"]] == [outcome["task_id"]]

    assert after["listing"] == {
        "executions": [
            {
                "execution_id": outcome["plan_id"],
                "status": "completed",
                "objective": "Write a note",
                "risk": "medium",
                "final_outcome": "note written",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "task_ids": [outcome["task_id"]],
            }
        ]
    }


def test_gateway_error_mapping_over_http(tmp_path: Path) -> None:
    async def scenario(client, plane) -> dict[str, object]:
        res = {}
        res["unknown_execution"] = await client.get("/api/executions/does-not-exist")
        res["unknown_resume"] = await client.post(
            "/api/executions/does-not-exist/resume"
        )
        plan = plane.create_execution("Whatever", execution_id="webui-map")
        res["plan_id"] = plan.plan_id
        res["task_id"] = plane.tasks(plan.plan_id)[0].task_id
        res["bad_body"] = await client.post(
            f"/api/executions/{plan.plan_id}/approve",
            json={"task_id": "x", "permission": "filesystem.write", "extra": 1},
        )
        res["unknown_memory"] = await client.get("/api/memory/does-not-exist")
        return res

    async def main() -> dict[str, object]:
        connection, store = open_orchestration_store(tmp_path / "map.db")
        memory = MemoryService(MemoryStore(connection))
        plane = ControlPlane(store, memory=memory)
        app = create_app(
            tmp_path,
            tmp_path / "map.db",
            model=DEFAULT_MODEL,
            agent_factory=lambda: _FakeBuilt(_FakeAgent()),
            control_plane=plane,
        )
        try:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client,
            ):
                return await scenario(client, plane)
        finally:
            connection.close()

    res = _run(main())
    assert res["unknown_execution"].status_code == 404
    assert res["unknown_execution"].json()["error"]["type"] == "not_found"
    assert res["unknown_resume"].status_code == 409
    assert res["bad_body"].status_code == 400
    assert res["unknown_memory"].status_code == 404


def test_memory_endpoints_are_read_only_over_http(tmp_path: Path) -> None:
    async def scenario(client, plane, **kw) -> dict[str, object]:
        created = plane.memory_create_user(
            "I prefer strength training in the morning.",
            summary="morning strength preference",
            scope="global",
            kind="preference",
        )
        archived = plane.memory_create_user(
            "The 2024 project shipped in June.",
            scope="global",
        )
        plane.memory_archive(archived.memory_id)

        listing = await client.get("/api/memory")
        archived_listing = await client.get(
            "/api/memory", params={"status": "archived"}
        )
        search = await client.get("/api/memory/search", params={"q": "strength"})
        show = await client.get(f"/api/memory/{created.memory_id}")

        memory_ids = [m["memory_id"] for m in listing.json()["memories"]]
        archived_ids = [m["memory_id"] for m in archived_listing.json()["memories"]]
        hits = search.json()["hits"]
        return {
            "created_id": created.memory_id,
            "archived_id": archived.memory_id,
            "memory_ids": memory_ids,
            "archived_ids": archived_ids,
            "hits": hits,
            "show": show.json(),
        }

    outcome = run_gateway(tmp_path, scenario)
    assert outcome["created_id"] in outcome["memory_ids"]
    assert outcome["archived_id"] not in outcome["memory_ids"]
    assert outcome["archived_id"] in outcome["archived_ids"]
    assert [h["memory"]["memory_id"] for h in outcome["hits"]] == [
        outcome["created_id"]
    ]
    assert outcome["show"]["memory"]["memory_id"] == outcome["created_id"]
    assert "events" in outcome["show"]


def test_workout_endpoints_are_read_only_over_http(tmp_path: Path) -> None:
    async def scenario(client, plane, workout) -> dict[str, object]:
        listed = await client.get("/api/workouts")
        stats = await client.get("/api/workouts/stats")
        exercises = await client.get(
            "/api/workouts/exercises", params={"name": "bench press"}
        )
        history = await client.get("/api/workouts/history", params={"limit": 5})
        workouts = listed.json()["workouts"]
        assert workouts
        detail = await client.get(f"/api/workouts/{workouts[0]['id']}")
        missing = await client.get("/api/workouts/nope-unknown")
        return {
            "workouts": workouts,
            "stats": stats.json(),
            "exercises": exercises.json(),
            "history_sets": history.json()["sets"],
            "detail": detail.json(),
            "missing_status": missing.status_code,
        }

    outcome = run_gateway(tmp_path, scenario)
    assert outcome["workouts"][0]["id"].startswith("wkt-")
    assert outcome["stats"]["workout_count"] == len(outcome["workouts"])
    assert "by_activity_type" in outcome["stats"]
    assert any(
        "bench" in e["normalized_name"] for e in outcome["exercises"]["exercises"]
    )
    assert outcome["history_sets"]
    assert outcome["detail"]["id"] == outcome["workouts"][0]["id"]
    assert outcome["detail"]["exercises"]
    assert outcome["missing_status"] == 404


def test_full_gateway_services_copresent_over_http(tmp_path: Path) -> None:
    """Chat + memory + workouts + control plane all live on one gateway app."""

    async def main() -> dict[str, object]:
        connection, store = open_orchestration_store(tmp_path / "all.db")
        memory = MemoryService(MemoryStore(connection))
        workout_store = WorkoutStore(connection)
        records, _ = parse_workout_file(
            FIXTURE_CSV.encode("utf-8"), source_file="boostcamp.csv"
        )
        workout_store.import_records(records, source_file="boostcamp.csv")
        workout = WorkoutQueryService(workout_store)
        plane = ControlPlane(store, memory=memory, workout=workout)
        registry = create_default_registry(workspace=tmp_path, workout_service=workout)
        client_fake = _WorkoutChatClient()
        agent = Agent(client_fake, registry)
        app = create_app(
            tmp_path,
            tmp_path / "all.db",
            model=DEFAULT_MODEL,
            agent_factory=lambda: _FakeBuilt(agent),
            control_plane=plane,
            workout_service=workout,
        )
        try:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client,
            ):
                models = await client.get("/v1/models")
                chat = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "anything",
                        "messages": [{"role": "user", "content": "bench press?"}],
                    },
                )
                plane.memory_create_user("I train in the morning.", scope="global")
                memory_search = await client.get(
                    "/api/memory/search", params={"q": "morning"}
                )
                workout_stats = await client.get("/api/workouts/stats")
                execution = plane.create_execution("Write a note")
                executions = await client.get("/api/executions")
                return {
                    "models": models,
                    "chat": chat,
                    "chat_body": chat.json(),
                    "memory_search": memory_search,
                    "workout_stats": workout_stats,
                    "executions": executions,
                    "execution_id": execution.plan_id,
                }
        finally:
            connection.close()

    outcome = _run(main())
    assert outcome["models"].status_code == 200
    assert [m["id"] for m in outcome["models"].json()["data"]] == [MODEL_ID]
    assert outcome["chat"].status_code == 200
    assert (
        "Bench Press (Barbell)"
        in outcome["chat_body"]["choices"][0]["message"]["content"]
    ), "workout-gated tool executed during chat"
    assert [h["memory"]["memory_id"] for h in outcome["memory_search"].json()["hits"]]
    assert outcome["workout_stats"].json()["workout_count"] >= 1
    assert (
        outcome["executions"].json()["executions"][0]["execution_id"]
        == outcome["execution_id"]
    )


def test_gateway_returns_503_when_services_not_configured(tmp_path: Path) -> None:
    async def scenario(client, **kw) -> dict[str, object]:
        return {
            "executions": await client.get("/api/executions"),
            "memory": await client.get("/api/memory"),
            "memory_search": await client.get("/api/memory/search", params={"q": "x"}),
            "workouts": await client.get("/api/workouts"),
            "workout_stats": await client.get("/api/workouts/stats"),
            "workout_detail": await client.get("/api/workouts/abc"),
        }

    outcome = run_neutral_gateway(tmp_path, scenario)
    for name in (
        "executions",
        "memory",
        "memory_search",
        "workouts",
        "workout_stats",
        "workout_detail",
    ):
        res = outcome[name]
        assert res.status_code == 503, name
        assert res.json()["error"]["type"] == "service_unavailable"
