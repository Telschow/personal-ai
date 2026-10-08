"""Explicit environment-driven configuration for optional model backends.

Configuration here is deliberately narrow: only backends that are not yet
wired elsewhere read environment variables through this module. Ollama's HTTP
endpoint is configurable via ``OLLAMA_BASE_URL`` for containerized deployments.
There is no fallback chain — an unset embedding model simply means embedding
work is unavailable, and a chat model can never become an embedding model.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

EMBEDDING_MODEL_ENV = "PERSONAL_AI_EMBEDDING_MODEL"

RETRIEVAL_MODE_ENV = "PERSONAL_AI_RETRIEVAL_MODE"

VISION_MODEL_ENV = "PERSONAL_AI_VISION_MODEL"
VISION_PROMPT_VERSION_ENV = "PERSONAL_AI_VISION_PROMPT_VERSION"
DEFAULT_VISION_PROMPT_VERSION = "v2"

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


class RetrievalMode(Enum):
    """Search backend for chunk retrieval.

    ``KEYWORD`` is the production default: the SQLite FTS5 keyword index.
    ``SEMANTIC`` and ``HYBRID`` are opt-in backends enabled via
    :data:`RETRIEVAL_MODE_ENV`; both require an embedding model to be
    configured (see :data:`EMBEDDING_MODEL_ENV`).
    """

    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class RetrievalSettings:
    """Chunk retrieval backend configuration.

    ``mode`` selects which :class:`~personal_ai.retrieval.ChunkIndex`
    implementation the application constructs. It is never changed by the
    presence of an embedding model: configuring
    :data:`EMBEDDING_MODEL_ENV` alone leaves retrieval on ``KEYWORD``.
    """

    mode: RetrievalMode = RetrievalMode.KEYWORD


def load_retrieval_settings(
    environ: Mapping[str, str] | None = None,
) -> RetrievalSettings:
    """Read retrieval configuration from the given (or real) environment.

    An absent or blank :data:`RETRIEVAL_MODE_ENV` yields the ``KEYWORD``
    default; an unknown value raises ``ValueError`` rather than degrading
    silently, so an operator typo is surfaced instead of silently switching
    backends.
    """
    source = os.environ if environ is None else environ
    raw = source.get(RETRIEVAL_MODE_ENV, "").strip()
    if not raw:
        return RetrievalSettings()
    try:
        mode = RetrievalMode(raw.lower())
    except ValueError as exc:
        valid = ", ".join(m.value for m in RetrievalMode)
        msg = f"{RETRIEVAL_MODE_ENV} must be one of {valid!r}, got {raw!r}"
        raise ValueError(msg) from exc
    return RetrievalSettings(mode=mode)


@dataclass(frozen=True, slots=True)
class VisionSettings:
    """Vision extraction configuration.

    ``model`` is ``None`` when :data:`VISION_MODEL_ENV` is absent or blank,
    which disables page-level vision extraction entirely: image-heavy
    documents keep their existing store-without-chunks behavior. The chat
    model never becomes a vision model implicitly.
    """

    model: str | None
    prompt_version: str = DEFAULT_VISION_PROMPT_VERSION


def load_vision_settings(
    environ: Mapping[str, str] | None = None,
) -> VisionSettings:
    """Read vision configuration from the given (or real) environment."""
    source = os.environ if environ is None else environ
    raw_model = source.get(VISION_MODEL_ENV, "").strip()
    raw_version = source.get(VISION_PROMPT_VERSION_ENV, "").strip()
    prompt_version = raw_version or DEFAULT_VISION_PROMPT_VERSION
    return VisionSettings(model=raw_model or None, prompt_version=prompt_version)


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


OLLAMA_BASE_URL_ENV = "OLLAMA_BASE_URL"

#: The default Ollama endpoint matches the local CLI runtime. Override with
#: ``OLLAMA_BASE_URL`` — for example ``http://ollama:11434`` when the gateway
#: runs in Docker on the same network as an Ollama container.
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


@dataclass(frozen=True, slots=True)
class OllamaSettings:
    """Ollama backend configuration.

    ``base_url`` is the HTTP endpoint the chat agent talks to; it defaults to
    the local Ollama daemon and can be pointed at a container over a Docker
    network without changing application code.
    """

    base_url: str


def load_ollama_settings(
    environ: Mapping[str, str] | None = None,
) -> OllamaSettings:
    """Read Ollama endpoint configuration from the given (or real) environment."""
    source = os.environ if environ is None else environ
    raw = source.get(OLLAMA_BASE_URL_ENV, "").strip()
    return OllamaSettings(base_url=raw or DEFAULT_OLLAMA_BASE_URL)
