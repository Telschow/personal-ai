"""Small OpenAI-compatible HTTP API exposing the existing Personal AI Agent.

The API is intentionally narrow for Phase 30:

* ``POST /v1/chat/completions`` (non-streaming only)
* local bind (:const:`DEFAULT_API_HOST` ``127.0.0.1``) by default
* every request is answered by the configured ``PERSONAL_AI_CHAT_MODEL``
  (default ``qwen3.5:9b``); the incoming ``model`` field is ignored because
  this is a personal-AI endpoint, not a generic model proxy

It does not re-implement the retrieval or agent logic. It constructs the same
production Agent as the CLI via :func:`personal_ai.cli.build_agent` and simply
bridges HTTP <-> ``Agent.run``.

Run directly::

    python -m personal_ai.server --workspace PATH --database path/to/corpus.db
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from personal_ai import cli, config
from personal_ai.agent import AgentError, MaxToolRoundsError
from personal_ai.ollama_client import (
    ChatMessage,
    OllamaConnectionError,
    OllamaError,
)

DEFAULT_WORKSPACE_ENV = "PERSONAL_AI_WORKSPACE"
DEFAULT_DATABASE_ENV = "PERSONAL_AI_DATABASE"
API_TOKEN_ENV = "PERSONAL_AI_API_TOKEN"


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
        default=None,
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
    host = args.host or settings.host
    port = args.port or settings.port
    token = args.token or env_token
    return RequestConfig(
        workspace=args.workspace.resolve(),
        database=args.database.resolve() if args.database is not None else None,
        host=host,
        port=port,
        model=settings.model,
        token=token,
    )


class CompletionRequest:
    """Small typed view over an OpenAI-style chat completions payload.

    Kept framework-free so parsing is unit-testable without a server. Only the
    non-streaming path is supported in Phase 30.
    """

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def validate(self) -> list[ChatMessage]:
        if not isinstance(self._payload, dict):
            raise ApiError(400, "request body must be a JSON object")

        messages = self._payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ApiError(400, "messages must be a non-empty array")

        stream = self._payload.get("stream", False)
        if stream:
            raise ApiError(
                400,
                "streaming is not supported in this version",
                error_type="unsupported_parameter",
            )

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


def build_response(answer: str, model: str) -> dict:
    return {
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


def complete_chat(
    agent: object,
    request: CompletionRequest,
    *,
    model: str,
) -> dict:
    """Run one chat completion through the Agent and return an OpenAI response.

    ``agent`` only needs a ``run(messages) -> str`` method, so tests can inject
    a fake. All model/tool/connection failures are translated into
    :class:`ApiError`.
    """
    messages = request.validate()
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
    return build_response(answer, model=model)


def create_app(
    workspace: Path,
    database: Path | None,
    *,
    model: str,
    token: str | None = None,
    agent_factory=None,
) -> FastAPI:
    """Build the FastAPI application bound to a Personal AI Agent.

    ``agent_factory`` is an optional callable returning an agent plus a
    ``close()``; defaults to constructing the production Agent through
    :func:`personal_ai.cli.build_agent`. Tests inject a fake factory.
    """
    agent_factory = agent_factory or (
        lambda: cli.build_agent(workspace, database, model=model)
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        built = agent_factory()
        _app.state.agent = built.agent if hasattr(built, "agent") else built
        try:
            yield
        finally:
            if hasattr(built, "close"):
                built.close()

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
                        "id": model,
                        "object": "model",
                        "created": 0,
                        "owned_by": "personal-ai",
                    }
                ],
            },
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            raise ApiError(400, "request body is not valid JSON")
        resolved = CompletionRequest(payload)
        result = complete_chat(app.state.agent, resolved, model=model)
        return JSONResponse(status_code=200, content=result)

    return app


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

    app = create_app(
        cfg.workspace,
        cfg.database,
        model=cfg.model,
        token=cfg.token,
    )
    if not run:
        return app

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
    return app


if __name__ == "__main__":
    main()
