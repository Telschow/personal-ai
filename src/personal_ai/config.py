"""Explicit environment-driven configuration for optional model backends.

Configuration here is deliberately narrow: only backends that are not yet
wired elsewhere read environment variables through this module. There is
no fallback chain — an unset embedding model simply means embedding work
is unavailable, and a chat model can never become an embedding model.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

EMBEDDING_MODEL_ENV = "PERSONAL_AI_EMBEDDING_MODEL"

CHAT_MODEL_ENV = "PERSONAL_AI_CHAT_MODEL"
API_HOST_ENV = "PERSONAL_AI_API_HOST"
API_PORT_ENV = "PERSONAL_AI_API_PORT"

# The single production chat model. Keep in sync with ``cli.MODEL``.
DEFAULT_CHAT_MODEL = "qwen3.5:9b"
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000


@dataclass(frozen=True, slots=True)
class EmbeddingSettings:
    """Embedding backend configuration.

    ``model`` is ``None`` when :data:`EMBEDDING_MODEL_ENV` is absent or
    blank; document ingestion remains fully functional in that state.
    """

    model: str | None


def load_embedding_settings(
    environ: Mapping[str, str] | None = None,
) -> EmbeddingSettings:
    """Read embedding configuration from the given (or real) environment."""
    source = os.environ if environ is None else environ
    raw = source.get(EMBEDDING_MODEL_ENV, "").strip()
    return EmbeddingSettings(model=raw or None)


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """HTTP API configuration.

    ``model`` is the personal-AI chat model used for every request; the API
    is not a generic model proxy, so incoming ``model`` values are ignored.
    ``host``/``port`` control the local bind address (localhost by default).
    """

    model: str
    host: str
    port: int


def load_api_settings(
    environ: Mapping[str, str] | None = None,
) -> ApiSettings:
    """Read HTTP API configuration from the given (or real) environment."""
    source = os.environ if environ is None else environ
    model = source.get(CHAT_MODEL_ENV, "").strip() or DEFAULT_CHAT_MODEL
    host = source.get(API_HOST_ENV, "").strip() or DEFAULT_API_HOST
    raw_port = source.get(API_PORT_ENV, "").strip()
    try:
        port = int(raw_port) if raw_port else DEFAULT_API_PORT
    except ValueError as exc:
        msg = f"{API_PORT_ENV} must be an integer, got {raw_port!r}"
        raise ValueError(msg) from exc
    if not 0 < port < 65536:
        msg = f"{API_PORT_ENV} must be a valid port in (0, 65536), got {port}"
        raise ValueError(msg)
    return ApiSettings(model=model, host=host, port=port)
