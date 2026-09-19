"""OpenAI-compatible HTTP API exposing the Personal AI Agent and gateway.

The API is a single local endpoint surface (Phase 44 gateway):

* ``GET /v1/models`` — exposes the single served model id ``personal-ai``;
  internal Ollama model names stay hidden behind the endpoint.
* ``POST /v1/chat/completions`` — grounded personal chat; non-streaming by
  default, OpenAI-compatible SSE when ``stream: true``. The agent runs
  synchronously; its final answer is streamed as one content chunk so HTTP
  error status stay truthful. No prompts, chain-of-thought, tool arguments,
  or private data ever appear in the SSE stream.
* ``/api/executions`` + ``/api/executions/{id}`` + ``board`` / ``events`` /
  ``approvals`` — the execution control plane (run/resume/pause/cancel/retry)
  exposed 1:1 onto :class:`ControlPlane`, with the same JSON shapes the
  ``--json`` CLI emits. Approvals accept the exact body
  ``{task_id, permission}`` and are scoped to one execution/task/permission.
* ``/api/memory`` — read-only durable memory (list / search / show). Nothing
  here can write or build memories; memory is untrusted data everywhere.
* ``/api/workouts`` — read-only workout activity (list / one / exercises /
  history / stats).

Every request is answered by the configured ``PERSONAL_AI_CHAT_MODEL``
(default ``qwen3.5:9b``); the incoming ``model`` field is ignored because this
is a personal-AI endpoint, not a generic model proxy. The control plane,
memory, and workout endpoints require a configured ``--database``; without one
they answer ``503`` rather than silently returning empty state.

It does not re-implement the retrieval, orchestration, or workout logic. It
constructs the same production Agent as the CLI via
:func:`personal_ai.cli.build_agent` plus an optional
:class:`~personal_ai.execution.ControlPlane` over the same local SQLite
database, and simply bridges HTTP <-> those services.

Run directly::

    python -m personal_ai.server --workspace PATH --database path/to/orch.db
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from personal_ai import cli, config
from personal_ai.agent import AgentError, MaxToolRoundsError
from personal_ai.execution import (
    ControlPlane,
    ExecutionNotFoundError,
    PlanTransitionError,
    TaskTransitionError,
    open_orchestration_store,
)
from personal_ai.execution.orchestrator import PlanNotCompleteError
from personal_ai.memory import (
    ChatMemory,
    MemoryNotFoundError,
    MemoryScope,
    MemoryStatus,
    ScopeFilter,
)
from personal_ai.memory.service import MemoryNotConfiguredError, MemoryService
from personal_ai.memory.store import MemoryStore
from personal_ai.ollama_client import (
    ChatMessage,
    OllamaConnectionError,
    OllamaError,
)
from personal_ai.people import ChatPeople
from personal_ai.workouts import WorkoutQueryService, WorkoutStore

# M3: Job-Agent imports
try:
    from job_agent import db as job_db

    JOB_AGENT_AVAILABLE = True
except ImportError:
    JOB_AGENT_AVAILABLE = False

DEFAULT_WORKSPACE_ENV = "PERSONAL_AI_WORKSPACE"
DEFAULT_DATABASE_ENV = "PERSONAL_AI_DATABASE"
API_TOKEN_ENV = "PERSONAL_AI_API_TOKEN"

#: The single model id exposed to clients; internal Ollama model names never
#: leak through the API surface.
MODEL_ID = "personal-ai"

_MEMORY_SCOPE_VALUES = {scope.value for scope in MemoryScope}


class ApiError(Exception):
    """An HTTP error with an OpenAI-style payload."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        error_type: str = "invalid_request_error",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="personal-ai-server",
        description="Serve the Personal AI Agent over an OpenAI-compatible HTTP API.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=config_env_str(DEFAULT_WORKSPACE_ENV) or Path.cwd(),
        help=f"Workspace directory (default: ${DEFAULT_WORKSPACE_ENV} or current dir)",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=config_env_str(DEFAULT_DATABASE_ENV),
        help=f"Path to the personal corpus SQLite database (default: ${DEFAULT_DATABASE_ENV})",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind address (default: from config, usually 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: from config, usually 8000)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Optional bearer token required for every request",
    )
    parser.add_argument(
        "--job-db",
        type=Path,
        default=None,
        help="Path to Job-Agent SQLite database (default: $JOB_AGENT_DB or job_agent/output/jobs.sqlite3)",
    )
    return parser.parse_args(argv)


def config_env_str(name: str) -> str | None:
    import os

    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class RequestConfig:
    """Resolved runtime configuration for a server process."""

    workspace: Path
    database: Path | None
    host: str
    port: int
    model: str
    token: str | None
    job_db: Path | None


def resolve_config(
    args: argparse.Namespace | None = None,
    environ: dict[str, str] | None = None,
) -> RequestConfig:
    """Combine CLI args and configuration into a single :class:`RequestConfig`.

    ``environ`` (defaulting to the real process environment) supplies model,
    host and port via :func:`personal_ai.config.load_api_settings`, which the
    CLI ``--host``/``--port`` flags may override. The optional API token also
    comes from the same ``environ`` (``API_TOKEN_ENV``) unless a ``--token``
    CLI argument is given, which takes precedence.
    """
    args = args or parse_args([])
    settings = config.load_api_settings(environ)
    source = os.environ if environ is None else environ
    env_token = source.get(API_TOKEN_ENV, "").strip() or None
    env_job_db = source.get("JOB_AGENT_DB", "").strip() or None
    host = args.host or settings.host
    port = args.port or settings.port
    token = args.token or env_token
    job_db = (
        args.job_db.resolve()
        if args.job_db is not None
        else (Path(env_job_db) if env_job_db else None)
    )
    return RequestConfig(
        workspace=args.workspace.resolve(),
        database=args.database.resolve() if args.database is not None else None,
        host=host,
        port=port,
        model=settings.model,
        token=token,
        job_db=job_db,
    )


class CompletionRequest:
    """Small typed view over an OpenAI-style chat completions payload.

    Kept framework-free so parsing is unit-testable without a server. Both the
    non-streaming and the streaming (SSE) paths are supported; ``stream`` is
    exposed as a validated property so the router can branch before parsing
    the whole message list.
    """

    def __init__(self, payload: object) -> None:
        self._payload = payload

    @property
    def stream(self) -> bool:
        if not isinstance(self._payload, dict):
            raise ApiError(400, "request body must be a JSON object")
        value = self._payload.get("stream", False)
        if not isinstance(value, bool):
            raise ApiError(400, "stream must be a boolean")
        return value

    def validate(self) -> list[ChatMessage]:
        if not isinstance(self._payload, dict):
            raise ApiError(400, "request body must be a JSON object")

        messages = self._payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ApiError(400, "messages must be a non-empty array")

        converted: list[ChatMessage] = []
        for raw in messages:
            if not isinstance(raw, dict):
                raise ApiError(400, "each message must be an object")
            role = raw.get("role")
            content = raw.get("content")
            if role not in ("system", "user", "assistant"):
                raise ApiError(400, f"unsupported message role: {role!r}")
            if not isinstance(content, str) or not content.strip():
                raise ApiError(
                    400,
                    f"message content must be a non-empty string (role {role!r}); "
                    "multimodal/image content is not supported",
                )
            converted.append(ChatMessage(role=role, content=content))

        if not any(m.role == "user" for m in converted):
            raise ApiError(400, "messages must include at least one user message")
        return converted


def error_payload(
    message: str,
    error_type: str,
    *,
    status_code: int,
) -> dict:
    request_id = uuid.uuid4().hex
    return {
        "id": request_id,
        "object": "error",
        "created": int(time.time()),
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": status_code,
        },
    }


def build_response(
    answer: str,
    model: str,
    memory_used: Sequence[dict[str, object]] | None = None,
    people_used: Sequence[dict[str, object]] | None = None,
) -> dict:
    response = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "system_fingerprint": "personal-ai",
    }
    if memory_used:
        response["memory_used"] = [
            {
                "memory_id": item["memory_id"],
                "kind": item["kind"],
                "score": item["score"],
                "rank": item["rank"],
            }
            for item in memory_used
        ]
    if people_used:
        response["people_used"] = [
            {
                "person_id": item["person_id"],
                "display_name": item["display_name"],
                "evidence_count": item["evidence_count"],
            }
            for item in people_used
        ]
    return response


def complete_chat(
    agent: object,
    request: CompletionRequest,
    *,
    model: str,
    chat_memory: ChatMemory | None = None,
    chat_people: ChatPeople | None = None,
) -> dict:
    """Run one chat completion through the Agent and return an OpenAI response.

    ``agent`` only needs a ``run(messages) -> str`` method, so tests can inject
    a fake. When ``chat_memory`` is wired, a small deterministic automatic
    recall runs first and its untrusted block is appended below the user
    request; safe provenance (ids + kinds only) is attached to the response.
    When ``chat_people`` is wired, a bounded deterministic people overview is
    likewise appended as an explicitly-untrusted reference-data block, with
    id/name/count-safe provenance attached. All model/tool/connection failures
    are translated into :class:`ApiError`.
    """
    messages = request.validate()
    memory_used: tuple[dict[str, object], ...] = ()
    if chat_memory is not None:
        result = chat_memory.build_context_messages(messages)
        messages = result.messages
        memory_used = result.provenance
    people_used: tuple[dict[str, object], ...] = ()
    if chat_people is not None:
        result = chat_people.build_context_messages(messages)
        messages = result.messages
        people_used = result.provenance
    try:
        answer = agent.run(messages)
    except MaxToolRoundsError as exc:
        raise ApiError(
            500,
            "The agent stopped after exceeding its maximum tool rounds.",
            error_type="server_error",
        ) from exc
    except OllamaConnectionError as exc:
        raise ApiError(
            502,
            "Could not reach the local model service.",
            error_type="model_unavailable",
        ) from exc
    except OllamaError as exc:
        raise ApiError(
            502,
            "The local model service returned an error.",
            error_type="model_error",
        ) from exc
    except AgentError as exc:
        raise ApiError(
            500,
            "The agent could not complete the request.",
            error_type="server_error",
        ) from exc
    if not answer.strip():
        raise ApiError(
            500,
            "The agent returned an empty response.",
            error_type="server_error",
        )
    return build_response(
        answer,
        model=MODEL_ID,
        memory_used=memory_used or None,
        people_used=people_used or None,
    )


def stream_chat(
    agent: object,
    request: CompletionRequest,
    *,
    model: str,
    chat_memory: ChatMemory | None = None,
    chat_people: ChatPeople | None = None,
) -> StreamingResponse:
    """Run one chat completion and stream it as OpenAI-compatible SSE.

    Streaming TESTS only status truthfulness: the agent runs synchronously and
    its final answer is delivered as a single content chunk, preceded by a
    role delimiter and followed by ``finish_reason: stop`` and ``[DONE]``.
    Because the agent completes *before* any bytes are written, failures are
    translated into proper HTTP error statuses instead of a broken stream.
    The stream carries only the assistant's answer text — never prompts,
    chain-of-thought, tool arguments, or private data. ``chat_memory`` and
    ``chat_people`` behave exactly as in :func:`complete_chat`.
    """
    messages = request.validate()
    memory_used: tuple[dict[str, object], ...] = ()
    if chat_memory is not None:
        result = chat_memory.build_context_messages(messages)
        messages = result.messages
        memory_used = result.provenance
    people_used: tuple[dict[str, object], ...] = ()
    if chat_people is not None:
        result = chat_people.build_context_messages(messages)
        messages = result.messages
        people_used = result.provenance
    try:
        answer = agent.run(messages)
    except MaxToolRoundsError as exc:
        raise ApiError(
            500,
            "The agent stopped after exceeding its maximum tool rounds.",
            error_type="server_error",
        ) from exc
    except OllamaConnectionError as exc:
        raise ApiError(
            502,
            "Could not reach the local model service.",
            error_type="model_unavailable",
        ) from exc
    except OllamaError as exc:
        raise ApiError(
            502,
            "The local model service returned an error.",
            error_type="model_error",
        ) from exc
    except AgentError as exc:
        raise ApiError(
            500,
            "The agent could not complete the request.",
            error_type="server_error",
        ) from exc
    if not answer.strip():
        raise ApiError(
            500,
            "The agent returned an empty response.",
            error_type="server_error",
        )
    return StreamingResponse(
        _sse_completion(
            answer,
            model=MODEL_ID,
            memory_used=memory_used or None,
            people_used=people_used or None,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_completion(
    answer: str,
    model: str,
    memory_used: Sequence[dict[str, object]] | None = None,
    people_used: Sequence[dict[str, object]] | None = None,
):
    """Yield OpenAI-compatible ``chat.completion.chunk`` SSE frames.

    The first frame announces the assistant role, the second carries the whole
    answer, and the stream terminates with a ``finish_reason: stop`` frame and
    the canonical ``data: [DONE]`` sentinel. Safe memory provenance (ids +
    kinds only) and people provenance (id/name/count only) are attached to the
    final frame.
    """
    base = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "system_fingerprint": "personal-ai",
    }

    def frame(*, delta: dict, finish_reason: str | None = None) -> str:
        payload = {
            **base,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload)}\n\n"

    def finish() -> str:
        payload = {
            **base,
            "memory_used": (
                [
                    {
                        "memory_id": item["memory_id"],
                        "kind": item["kind"],
                        "score": item["score"],
                        "rank": item["rank"],
                    }
                    for item in memory_used
                ]
                if memory_used
                else None
            ),
            "people_used": (
                [
                    {
                        "person_id": item["person_id"],
                        "display_name": item["display_name"],
                        "evidence_count": item["evidence_count"],
                    }
                    for item in people_used
                ]
                if people_used
                else None
            ),
        }
        return f"data: {json.dumps(payload)}\n\n"

    yield frame(delta={"role": "assistant", "content": ""})
    yield frame(delta={"content": answer})
    yield finish()
    yield frame(delta={}, finish_reason="stop")
    yield "data: [DONE]\n\n"


def create_app(
    workspace: Path,
    database: Path | None,
    *,
    model: str,
    token: str | None = None,
    agent_factory=None,
    chat_memory: ChatMemory | None = None,
    chat_people: ChatPeople | None = None,
    control_plane: ControlPlane | None = None,
    workout_service: WorkoutQueryService | None = None,
    memory_service: object | None = None,
    job_db_path: Path | None = None,
) -> FastAPI:
    """Build the FastAPI application bound to a Personal AI Agent.

    ``agent_factory`` is an optional callable returning an agent plus a
    ``close()``; defaults to constructing the production Agent through
    :func:`personal_ai.cli.build_agent` (including a policy-gated
    ``search_workouts`` chat tool when ``workout_service`` is wired). Tests
    inject a fake factory.

    ``chat_memory`` is an optional :class:`ChatMemory` (auto-built from the
    database by the application layer) that adds bounded, untrusted memory
    context to chat requests. ``chat_people`` is an optional
    :class:`~personal_ai.people.ChatPeople` (auto-built the same way) that
    adds bounded, deterministic people grounding to chat requests.

    ``control_plane`` and ``workout_service`` are optional application
    services. When present, the read-only control-plane / memory / workout
    endpoints are served from them; when absent those endpoints answer
    ``503``. ``memory_service`` is threaded into the default agent build so
    the read-only ``personal_context`` overview reports durable-memory
    availability. HTTP handlers never touch SQLite or the stores directly —
    they only call :class:`ControlPlane` / :class:`WorkoutQueryService`.
    """
    agent_factory = agent_factory or (
        lambda: cli.build_agent(
            workspace,
            database,
            model=model,
            workout_service=workout_service,
            memory_service=memory_service,
        )
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        built = agent_factory()
        _app.state.agent = built.agent if hasattr(built, "agent") else built
        closable: list[object] = [built]
        _app.state.chat_memory = None
        if chat_memory is not None:
            _app.state.chat_memory = (
                chat_memory.chat if hasattr(chat_memory, "chat") else chat_memory
            )
            closable.append(chat_memory)
        _app.state.chat_people = None
        if chat_people is not None:
            _app.state.chat_people = (
                chat_people.chat if hasattr(chat_people, "chat") else chat_people
            )
            closable.append(chat_people)
        _app.state.control_plane = control_plane
        _app.state.workout_service = workout_service
        # Job-Agent database connection
        job_db_conn: sqlite3.Connection | None = None
        if job_db_path is not None and JOB_AGENT_AVAILABLE:
            job_db_conn = job_db.connect(str(job_db_path))
            _app.state.job_db = job_db_conn
            _app.state.job_db_path = str(Path(job_db_path).resolve())
            closable.append(job_db_conn)
        try:
            yield
        finally:
            for resource in closable:
                if hasattr(resource, "close"):
                    resource.close()
            if job_db_conn is not None:
                job_db_conn.close()

    app = FastAPI(title="Personal AI", version="0.1.0", lifespan=lifespan)

    def _require_auth(request: Request) -> None:
        if token is None:
            return
        header = request.headers.get("authorization", "")
        expected = f"Bearer {token}"
        if not secrets.compare_digest(header, expected):
            raise ApiError(
                401,
                "Incorrect or missing API token.",
                error_type="authentication_error",
            )

    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(
                exc.message,
                exc.error_type,
                status_code=exc.status_code,
            ),
        )

    for _exc_type, _status, _etype, _message in (
        (
            ExecutionNotFoundError,
            404,
            "not_found",
            lambda exc: f"Unknown execution: {exc}",
        ),
        (
            MemoryNotFoundError,
            404,
            "not_found",
            lambda exc: f"Unknown memory: {exc}",
        ),
        (
            PlanNotCompleteError,
            409,
            "not_complete",
            lambda exc: str(exc),
        ),
        (
            PlanTransitionError,
            409,
            "transition_error",
            lambda exc: str(exc),
        ),
        (
            TaskTransitionError,
            409,
            "transition_error",
            lambda exc: str(exc),
        ),
        (
            MemoryNotConfiguredError,
            503,
            "service_unavailable",
            lambda exc: str(exc),
        ),
    ):

        @app.exception_handler(_exc_type)
        async def _domain_error_handler(
            request: Request,
            exc: Exception,
            _status: int = _status,
            _etype: str = _etype,
            _message: Callable[[Exception], str] = _message,
        ) -> JSONResponse:
            return JSONResponse(
                status_code=_status,
                content=error_payload(
                    _message(exc),
                    _etype,
                    status_code=_status,
                ),
            )

    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        try:
            _require_auth(request)
        except ApiError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content=error_payload(
                    exc.message,
                    exc.error_type,
                    status_code=exc.status_code,
                ),
            )
        return await call_next(request)

    @app.get("/v1/models")
    async def list_models() -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={
                "object": "list",
                "data": [
                    {
                        "id": MODEL_ID,
                        "object": "model",
                        "created": 0,
                        "owned_by": "personal-ai",
                    }
                ],
            },
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            raise ApiError(400, "request body is not valid JSON")
        resolved = CompletionRequest(payload)
        if resolved.stream:
            return stream_chat(
                app.state.agent,
                resolved,
                model=model,
                chat_memory=app.state.chat_memory,
                chat_people=app.state.chat_people,
            )
        result = complete_chat(
            app.state.agent,
            resolved,
            model=model,
            chat_memory=app.state.chat_memory,
            chat_people=app.state.chat_people,
        )
        return JSONResponse(status_code=200, content=result)

    _mount_gateway_endpoints(app)
    _mount_job_agent(app)

    _static_dir = Path(__file__).parent / "static"
    if _static_dir.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(_static_dir), html=True),
            name="static",
        )

    return app


# ---- gateway serializers (mirror the ``--json`` CLI shapes) ----


def _plan_dict(plan: object) -> dict[str, object]:
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


def _task_dict(task: object) -> dict[str, object]:
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


def _event_dict(event: object) -> dict[str, object]:
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


def _memory_dict(memory: object) -> dict[str, object]:
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


def _set_summary_dict(workout_set: object) -> dict[str, object]:
    return {
        "id": workout_set.set_id,
        "exercise_id": workout_set.exercise_id,
        "workout_id": workout_set.workout_id,
        "set_index": workout_set.set_index,
        "value_raw": workout_set.value_raw,
        "amount_raw": workout_set.amount_raw,
        "weight": workout_set.weight,
        "weight_unit": workout_set.weight_unit,
        "reps": workout_set.reps,
        "reps_open_ended": workout_set.reps_open_ended,
        "target_type": workout_set.target_type,
        "completed": workout_set.completed,
        "started_at": workout_set.started_at,
        "exercise_name": workout_set.exercise_name,
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


def _coerce_int(
    value: str | int | None, name: str, *, minimum: int | None = None
) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except TypeError, ValueError:
        raise ApiError(
            400, f"{name} must be an integer", error_type="invalid_request_error"
        )
    if minimum is not None and parsed < minimum:
        raise ApiError(
            400, f"{name} must be >= {minimum}", error_type="invalid_request_error"
        )
    return parsed


def _require_object(body: object) -> dict[str, object]:
    if not isinstance(body, dict):
        raise ApiError(400, "request body must be a JSON object")
    return body


def _require_key(body: dict[str, object], key: str, expected: type) -> str:
    value = body.get(key)
    if not isinstance(value, expected):
        raise ApiError(
            400,
            f"{key} must be a {expected.__name__}",
            error_type="invalid_request_error",
        )
    return value


def _enforce_body_keys(body: dict[str, object], allowed: set[str]) -> None:
    extra = set(body) - allowed
    if extra:
        names = ", ".join(sorted(extra))
        raise ApiError(
            400, f"unexpected field(s): {names}", error_type="invalid_request_error"
        )


def _gateway_plane(app: FastAPI) -> ControlPlane:
    plane = getattr(app.state, "control_plane", None)
    if plane is None:
        raise ApiError(
            503,
            "The control plane is not configured (start the server with --database).",
            error_type="service_unavailable",
        )
    return plane


def _gateway_workouts(app: FastAPI) -> WorkoutQueryService:
    service = getattr(app.state, "workout_service", None)
    if service is None:
        raise ApiError(
            503,
            "Workout data is not configured (start the server with --database).",
            error_type="service_unavailable",
        )
    return service


def _mount_gateway_endpoints(app: FastAPI) -> None:
    """Mount the read-only control-plane / memory / workout HTTP endpoints.

    Every handler is ``async def`` so blocking service calls run inline on the
    single event-loop thread — the same thread the SQLite connections are bound
    to. Handlers only call :class:`ControlPlane` / :class:`WorkoutQueryService`;
    they never touch stores or SQLite directly.
    """

    # ---- control plane: lifecycle ----
    @app.get("/api/executions")
    async def list_executions() -> JSONResponse:
        plane = _gateway_plane(app)
        plans = [
            plane.get_execution(execution_id)
            for execution_id in plane.list_executions()
        ]
        return JSONResponse(
            status_code=200,
            content={"executions": [_plan_dict(plan) for plan in plans]},
        )

    @app.get("/api/executions/{execution_id}")
    async def show_execution(execution_id: str) -> JSONResponse:
        plane = _gateway_plane(app)
        plan = plane.get_execution(execution_id)
        tasks = plane.tasks(execution_id)
        return JSONResponse(
            status_code=200,
            content={"plan": _plan_dict(plan), "tasks": [_task_dict(t) for t in tasks]},
        )

    @app.get("/api/executions/{execution_id}/board")
    async def execution_board(execution_id: str) -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content=_gateway_plane(app).board(execution_id),
        )

    @app.get("/api/executions/{execution_id}/events")
    async def execution_events(execution_id: str, after_seq: int = 0) -> JSONResponse:
        events = _gateway_plane(app).events_after(execution_id, after_seq)
        next_seq = max((event.seq for event in events), default=after_seq)
        return JSONResponse(
            status_code=200,
            content={
                "events": [_event_dict(event) for event in events],
                "next_seq": int(next_seq),
            },
        )

    @app.get("/api/executions/{execution_id}/approvals")
    async def execution_approvals(execution_id: str) -> JSONResponse:
        approvals: tuple[dict[str, object], ...] = _gateway_plane(app).approvals(
            execution_id
        )
        return JSONResponse(
            status_code=200,
            content={"approvals": list(approvals)},
        )

    @app.post("/api/executions/{execution_id}/resume")
    async def resume_execution(execution_id: str) -> JSONResponse:
        plan = _gateway_plane(app).resume_execution(execution_id)
        return JSONResponse(status_code=200, content=_plan_dict(plan))

    @app.post("/api/executions/{execution_id}/pause")
    async def pause_execution(execution_id: str) -> JSONResponse:
        plan = _gateway_plane(app).pause_execution(execution_id)
        return JSONResponse(status_code=200, content=_plan_dict(plan))

    @app.post("/api/executions/{execution_id}/cancel")
    async def cancel_execution(execution_id: str) -> JSONResponse:
        plan = _gateway_plane(app).cancel_execution(execution_id)
        return JSONResponse(status_code=200, content=_plan_dict(plan))

    @app.post("/api/executions/{execution_id}/retry")
    async def retry_task(execution_id: str, request: Request) -> JSONResponse:
        body = _require_object(await request.json())
        _enforce_body_keys(body, {"task_id"})
        task_id = _require_key(body, "task_id", str)
        task = _gateway_plane(app).retry_task(execution_id, task_id)
        return JSONResponse(status_code=200, content=_task_dict(task))

    @app.post("/api/executions/{execution_id}/approve")
    async def approve_task(execution_id: str, request: Request) -> JSONResponse:
        body = _require_object(await request.json())
        _enforce_body_keys(body, {"task_id", "permission"})
        task_id = _require_key(body, "task_id", str)
        permission = _require_key(body, "permission", str)
        decision = _gateway_plane(app).approve(execution_id, task_id, permission)
        return JSONResponse(status_code=200, content=decision)

    @app.post("/api/executions/{execution_id}/reject")
    async def reject_task(execution_id: str, request: Request) -> JSONResponse:
        body = _require_object(await request.json())
        _enforce_body_keys(body, {"task_id", "permission"})
        task_id = _require_key(body, "task_id", str)
        permission = _require_key(body, "permission", str)
        decision = _gateway_plane(app).reject(execution_id, task_id, permission)
        return JSONResponse(status_code=200, content=decision)

    # ---- memory (read-only) ----
    @app.get("/api/memory")
    async def memory_list(status: str = "active") -> JSONResponse:
        plane = _gateway_plane(app)
        if status not in {item.value for item in MemoryStatus}:
            raise ApiError(
                400,
                "status must be one of: active, archived, deleted",
                error_type="invalid_request_error",
            )
        memories = plane.memory_list(MemoryStatus(status))
        return JSONResponse(
            status_code=200,
            content={"memories": [_memory_dict(memory) for memory in memories]},
        )

    @app.get("/api/memory/search")
    async def memory_search(
        q: str = "",
        limit: int = 10,
        scope: str = "global",
        scope_id: str | None = None,
    ) -> JSONResponse:
        plane = _gateway_plane(app)
        if not q.strip():
            raise ApiError(
                400,
                "q must be a non-empty query string",
                error_type="invalid_request_error",
            )
        if limit < 1:
            raise ApiError(
                400, "limit must be >= 1", error_type="invalid_request_error"
            )
        if scope not in _MEMORY_SCOPE_VALUES:
            raise ApiError(
                400,
                f"unknown scope: {scope!r}",
                error_type="invalid_request_error",
            )
        scopes: tuple[ScopeFilter, ...] = ()
        if scope != "global":
            scopes = (ScopeFilter(MemoryScope(scope), scope_id),)
        hits = plane.memory_search(q, scopes=scopes, limit=limit)
        return JSONResponse(
            status_code=200,
            content={"hits": [hit.to_dict() for hit in hits]},
        )

    @app.get("/api/memory/{memory_id}")
    async def memory_show(memory_id: str) -> JSONResponse:
        plane = _gateway_plane(app)
        memory = plane.memory_get(memory_id)
        events = plane.memory_events(memory_id)
        return JSONResponse(
            status_code=200,
            content={
                "memory": _memory_dict(memory),
                "events": list(events),
            },
        )

    # ---- workouts (read-only) ----
    @app.get("/api/workouts")
    async def workouts(
        date_from: str | None = None,
        date_to: str | None = None,
        program_id: str | None = None,
        limit: str | int | None = None,
    ) -> JSONResponse:
        service = _gateway_workouts(app)
        parsed_limit = _coerce_int(limit, "limit", minimum=1)
        rows = service.list_workouts(
            date_from=date_from or None,
            date_to=date_to or None,
            program_id=program_id or None,
            limit=parsed_limit,
        )
        return JSONResponse(
            status_code=200,
            content={"workouts": [_workout_summary_dict(row) for row in rows]},
        )

    @app.get("/api/workouts/stats")
    async def workout_stats() -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content=_gateway_workouts(app).stats(),
        )

    @app.get("/api/workouts/history")
    async def workout_history(limit: str | int | None = None) -> JSONResponse:
        service = _gateway_workouts(app)
        rows = service.list_sets(limit=_coerce_int(limit, "limit", minimum=1))
        return JSONResponse(
            status_code=200,
            content={"sets": [_set_summary_dict(row) for row in rows]},
        )

    @app.get("/api/workouts/exercises")
    async def workout_exercises(
        workout_id: str | None = None,
        name: str | None = None,
        limit: str | int | None = None,
    ) -> JSONResponse:
        service = _gateway_workouts(app)
        rows = service.list_exercises(
            workout_id=workout_id or None,
            name=name or None,
            limit=_coerce_int(limit, "limit", minimum=1),
        )
        return JSONResponse(
            status_code=200,
            content={"exercises": [_exercise_summary_dict(row) for row in rows]},
        )

    @app.get("/api/workouts/{workout_id}")
    async def workout_detail(workout_id: str) -> JSONResponse:
        workout = _gateway_workouts(app).get_workout(workout_id)
        if workout is None:
            raise ApiError(
                404,
                f"Unknown workout: {workout_id}",
                error_type="not_found",
            )
        return JSONResponse(status_code=200, content=_workout_dict(workout))


# ---- job-agent (M3 MVP) ----
def _gateway_job_db(app: FastAPI) -> sqlite3.Connection:
    """Get the Job-Agent database connection from app state."""
    db = getattr(app.state, "job_db", None)
    if db is None:
        raise ApiError(
            503,
            "Job-Agent database not configured (start with --job-db or set JOB_AGENT_DB).",
            error_type="service_unavailable",
        )
    return db


def _job_dict(job) -> dict[str, object]:
    """Serialize a Job for the API."""
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "url": job.url,
        "apply_url": job.apply_url,
        "source": job.source,
        "source_type": job.source_type,
        "canonical_url": job.canonical_url,
        "location": job.location,
        "country": job.country,
        "normalized_location": job.normalized_location,
        "remote_mode": job.remote_mode,
        "employment_type": job.employment_type,
        "date_posted": job.date_posted.isoformat() if job.date_posted else None,
        "description": job.description,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "salary_min_eur": job.salary_min_eur,
        "salary_max_eur": job.salary_max_eur,
        "canonical_key": job.canonical_key,
        "status": job.status,
        "closed_at": job.closed_at,
        "discovered_at": job.discovered_at,
        "last_seen": job.last_seen,
        "last_checked": job.last_checked,
        "missing_scans": job.missing_scans,
        "user_status": job.user_status,
        "user_status_updated_at": job.user_status_updated_at,
    }


def _job_with_score_dict(job, score=None) -> dict[str, object]:
    """Serialize a Job with optional score for the API.

    Fit scores are exposed on the 0..1 scale (divided by 100): the stored
    ``evaluations.total`` is 0..100 but the API contract — and every UI
    consumer — uses 0..1.
    """
    base = _job_dict(job)
    if score:
        base["fit_score"] = score.total / 100.0
        base["fit_decision"] = score.decision
        base["fit_reasons"] = score.reasons
        base["fit_gaps"] = score.gaps
    return base


def _mount_job_agent_endpoints(app: FastAPI) -> None:
    """Mount the Job-Agent HTTP endpoints under /api/job-agent/*."""

    @app.get("/api/job-agent/health")
    async def job_agent_health() -> JSONResponse:
        """Health check for the Job-Agent subsystem."""
        if not JOB_AGENT_AVAILABLE:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unavailable",
                    "reason": "job_agent module not installed",
                },
            )
        job_db_conn = _gateway_job_db(app)
        stats = job_db.stats_counts(job_db_conn)
        user_counts = job_db.get_user_status_counts(job_db_conn)
        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "jobs_total": stats.get("total_jobs", 0),
                "jobs_by_user_status": user_counts,
            },
        )

    @app.get("/api/job-agent/jobs")
    async def job_agent_jobs(
        user_status: str | None = None,
        track: str | None = None,
        location: str | None = None,
        min_fit: float | None = None,
        source: str | None = None,
        analyzed: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> JSONResponse:
        """List jobs with filtering and sorting."""
        job_db_conn = _gateway_job_db(app)

        if user_status:
            jobs = job_db.get_jobs_by_user_status(
                job_db_conn, user_status, limit=limit, offset=offset
            )
        else:
            jobs = job_db.get_jobs(
                job_db_conn, status="active", source=source, limit=limit, offset=offset
            )

        # Apply filters
        filtered = []
        for job in jobs:
            if track:
                from job_agent.career_tracks import classify_track

                t = classify_track(job.title, job.description)
                if not t or t.track_id != track:
                    continue
            if location and location.lower() not in (job.location or "").lower():
                continue
            if min_fit is not None:
                # Check if job has evaluation with sufficient score.
                # ``min_fit`` is on the API 0..1 scale while the stored
                # ``evaluations.total`` is 0..100.
                row = job_db_conn.execute(
                    "SELECT total FROM evaluations WHERE job_id=? AND total >= ?",
                    (job.id, min_fit * 100),
                ).fetchone()
                if not row:
                    continue
            if analyzed is not None:
                has_eval = job_db_conn.execute(
                    "SELECT 1 FROM evaluations WHERE job_id=?", (job.id,)
                ).fetchone()
                if analyzed and not has_eval:
                    continue
                if not analyzed and has_eval:
                    continue
            filtered.append(job)

        # Batch-load evaluation totals and per-job track so the table renders
        # both columns (fit_score on the API 0..1 scale).
        pending = [job.id for job in filtered]
        totals: dict[str, float] = {}
        if pending:
            placeholders = ",".join("?" * len(pending))
            for row in job_db_conn.execute(
                f"SELECT job_id, total FROM evaluations WHERE job_id IN ({placeholders})",
                pending,
            ):
                totals[row["job_id"]] = row["total"] / 100.0

        from job_agent.career_tracks import classify_track

        jobs_out = []
        for job in filtered:
            item = _job_with_score_dict(job)
            item["fit_score"] = totals.get(job.id)
            t = classify_track(job.title, job.description)
            item["track"] = t.track_id if t else None
            jobs_out.append(item)

        return JSONResponse(
            status_code=200,
            content={
                "jobs": jobs_out,
                "count": len(filtered),
                "limit": limit,
                "offset": offset,
            },
        )

    @app.get("/api/job-agent/jobs/{job_id}")
    async def job_agent_job_detail(job_id: str) -> JSONResponse:
        """Get job detail with analysis if available."""
        job_db_conn = _gateway_job_db(app)
        job = job_db.get_job(job_db_conn, job_id)
        if job is None:
            raise ApiError(404, f"Job not found: {job_id}", error_type="not_found")

        # Get evaluation if exists
        eval_row = job_db_conn.execute(
            "SELECT * FROM evaluations WHERE job_id=?", (job_id,)
        ).fetchone()

        # Get career_fit if exists
        fit_row = job_db_conn.execute(
            "SELECT * FROM career_fit WHERE job_id=?", (job_id,)
        ).fetchone()

        # Get provenance
        prov_rows = job_db_conn.execute(
            "SELECT * FROM job_sources WHERE job_id=? ORDER BY source_id", (job_id,)
        ).fetchall()

        response = _job_dict(job)
        if eval_row:
            response["evaluation"] = {
                "total": eval_row["total"] / 100.0,
                "decision": eval_row["decision"],
                "reasons": json.loads(eval_row["reasons_json"] or "[]"),
                "gaps": json.loads(eval_row["gaps_json"] or "[]"),
                "confidence": eval_row["confidence"],
                "scoring_version": eval_row["scoring_version"],
                "profile_version": eval_row["profile_version"],
                "evaluated_at": eval_row["evaluated_at"],
            }
        if fit_row:
            response["career_fit"] = {
                "current_fit": fit_row["current_fit"],
                "career_upside": fit_row["career_upside"],
                "evidence_coverage": fit_row["evidence_coverage"],
                "strengths": json.loads(fit_row["strengths_json"] or "[]"),
                "gaps": json.loads(fit_row["gaps_json"] or "[]"),
                "transferables": json.loads(fit_row["transferable_json"] or "[]"),
                "positioning": json.loads(fit_row["positioning_json"] or "{}"),
                "risks": json.loads(fit_row["risks_json"] or "[]"),
                "evidence_refs": json.loads(fit_row["evidence_refs_json"] or "[]"),
                "knowledge_sources": json.loads(
                    fit_row["knowledge_sources_json"] or "[]"
                ),
                "narrative": fit_row["narrative_json"],
                "llm_used": bool(fit_row["llm_used"]),
                "created_at": fit_row["created_at"],
            }
        if prov_rows:
            response["provenance"] = [
                {
                    "source_id": r["source_id"],
                    "source_name": r["source_name"],
                    "source_url": r["source_url"],
                    "discovery_method": r["discovery_method"],
                    "query": r["query"],
                    "canonical_url": r["canonical_url"],
                    "discovered_at": r["discovered_at"],
                }
                for r in prov_rows
            ]

        return JSONResponse(status_code=200, content=response)

    @app.post("/api/job-agent/jobs/{job_id}/status")
    async def job_agent_update_status(job_id: str, request: Request) -> JSONResponse:
        """Update user status for a job."""
        job_db_conn = _gateway_job_db(app)
        body = await request.json()
        new_status = body.get("user_status")
        valid_statuses = {"NEW", "SAVED", "REJECTED", "APPLIED"}
        if new_status not in valid_statuses:
            raise ApiError(
                400,
                f"Invalid user_status. Must be one of: {', '.join(sorted(valid_statuses))}",
                error_type="invalid_request_error",
            )
        job = job_db.get_job(job_db_conn, job_id)
        if job is None:
            raise ApiError(404, f"Job not found: {job_id}", error_type="not_found")

        success = job_db.update_user_job_status(job_db_conn, job_id, new_status)
        if not success:
            raise ApiError(500, "Failed to update status", error_type="server_error")

        # Return updated job
        updated_job = job_db.get_job(job_db_conn, job_id)
        return JSONResponse(status_code=200, content=_job_dict(updated_job))

    @app.post("/api/job-agent/discover")
    async def job_agent_discover(
        request: Request,
        limit_total: int | None = None,
        limit_per_track: int | None = None,
        max_pages: int | None = None,
        dry_run: bool = False,
    ) -> JSONResponse:
        """Run a bounded discovery cycle."""
        if not JOB_AGENT_AVAILABLE:
            raise ApiError(
                503, "Job-Agent module not available", error_type="service_unavailable"
            )

        # Import job_agent modules lazily so they don't affect startup if not available
        from datetime import UTC, datetime
        from pathlib import Path

        import yaml
        from job_agent.config import load_config as load_job_config
        from job_agent.discovery_search import run_planned_discovery
        from job_agent.pipeline import ingest_global_jobs, run_lifecycle
        from job_agent.scoring import scoring_policy_from_config

        body = (
            await request.json()
            if request.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        limit_total = body.get("limit_total", limit_total)
        limit_per_track = body.get("limit_per_track", limit_per_track)
        max_pages = body.get("max_pages", max_pages)
        dry_run = body.get("dry_run", dry_run)

        # Load config and profile
        cfg = load_job_config()
        profile_path = (
            Path(cfg.profile_path)
            if Path(cfg.profile_path).is_absolute()
            else Path(__file__).parent.parent.parent / "job_agent" / cfg.profile_path
        )
        profile = (
            yaml.safe_load(profile_path.read_text(encoding="utf-8"))
            if profile_path.exists()
            else {}
        )
        policy = scoring_policy_from_config(cfg.model_dump())

        # Use in-memory DB for dry run; otherwise persist into the same
        # database the app/UI reads (the ``--job-db`` connection), never the
        # config-defaulted path.
        app_job_db = getattr(app.state, "job_db_path", None)
        db_path = app_job_db or cfg.database_path
        conn = job_db.connect(":memory:" if dry_run else db_path)

        try:
            run_started_at = datetime.now(UTC).isoformat(timespec="seconds")

            plan, jobs, provenance, errors, pacing_report = run_planned_discovery(
                cfg,
                max_pages=max_pages or cfg.search.max_global_pages,
                max_results=cfg.career.discovery.max_results_per_query,
                limit_total=limit_total,
                limit_per_track=limit_per_track,
            )
            result = ingest_global_jobs(
                conn,
                jobs,
                profile,
                policy,
                provenance=provenance,
                run_started_at=run_started_at,
            )
            lifecycle = run_lifecycle(conn, cfg, result.jobs_seen)

            return JSONResponse(
                status_code=200,
                content={
                    "planned_queries": len(plan.queries()),
                    "candidates_found": len(jobs),
                    "jobs_persisted": sum(result.persisted_by_source.values()),
                    "duplicates": result.total_duplicates,
                    "fetch_errors": len(errors),
                    "fetch_error_details": list(errors),
                    "pacing": pacing_report.to_dict(),
                    "lifecycle": lifecycle,
                    "dry_run": dry_run,
                },
            )
        except Exception as exc:
            raise ApiError(
                500, f"Discovery failed: {exc}", error_type="server_error"
            ) from exc


# Mount the job-agent endpoints
# This will be called from create_app
def _mount_job_agent(app: FastAPI) -> None:
    if JOB_AGENT_AVAILABLE:
        _mount_job_agent_endpoints(app)


def main(
    argv: list[str] | None = None,
    *,
    run: bool = True,
) -> FastAPI | None:
    """CLI entry point: resolve config, build the app, and (by default) serve.

    When ``run=False`` the app is returned without binding the port, which is
    convenient for tests.
    """
    import uvicorn

    args = parse_args(argv)
    cfg = resolve_config(args)
    if not cfg.workspace.is_dir():
        raise SystemExit(f"Workspace is not a directory: {cfg.workspace}")

    chat_memory = cli.build_chat_memory(cfg.database)
    chat_people = cli.build_chat_people(cfg.database)
    orb_connection: sqlite3.Connection | None = None
    control_plane: ControlPlane | None = None
    workout_service: WorkoutQueryService | None = None
    memory_service: MemoryService | None = None
    if cfg.database is not None:
        orb_connection, store = open_orchestration_store(cfg.database)
        try:
            memory_service = MemoryService(MemoryStore(orb_connection))
            workout_service = WorkoutQueryService(WorkoutStore(orb_connection))
            control_plane = ControlPlane(
                store,
                memory=memory_service,
                workout=workout_service,
            )
        except BaseException:
            orb_connection.close()
            raise
    app = create_app(
        cfg.workspace,
        cfg.database,
        model=cfg.model,
        token=cfg.token,
        chat_memory=chat_memory,
        chat_people=chat_people,
        control_plane=control_plane,
        workout_service=workout_service,
        memory_service=memory_service,
        job_db_path=cfg.job_db,
    )
    if not run:
        return app

    try:
        uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
    finally:
        if orb_connection is not None:
            orb_connection.close()
    return app


if __name__ == "__main__":
    main()
