"""Ollama-backed implementation of the embedding contract."""

from dataclasses import dataclass

import httpx

from personal_ai.config import EMBEDDING_MODEL_ENV, EmbeddingSettings
from personal_ai.documents.embedding import (
    Embedding,
    MalformedEmbeddingError,
    parse_embedding,
)
from personal_ai.ollama_client import (
    DEFAULT_BASE_URL,
    OllamaClient,
    OllamaConnectionError,
    OllamaHTTPStatusError,
    OllamaResponseError,
)


class OllamaEmbedder:
    """Embedding provider over :class:`OllamaClient`.

    Lives outside the pure document domain: it only translates between
    the embed primitive and the typed embedding contract. Provider errors
    from the client propagate unchanged; malformed responses raise
    :class:`~personal_ai.documents.embedding.MalformedEmbeddingError`
    without leaking contents.
    """

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    @property
    def model(self) -> str:
        """Model whose vectors this embedder produces."""
        return self._client.model

    def embed(self, text: str) -> Embedding:
        if not text:
            msg = "Cannot embed empty text"
            raise ValueError(msg)
        response = self._client.embed(text)
        return parse_embedding(response.model, response.embeddings)


class EmbeddingModelNotConfiguredError(Exception):
    """Raised when embedding work is requested but no model is configured.

    Deliberately not part of the Ollama error taxonomy: this is a local
    configuration gap, not a provider failure.
    """


def create_embedder(
    settings: EmbeddingSettings,
    *,
    base_url: str = DEFAULT_BASE_URL,
    transport: httpx.BaseTransport | None = None,
) -> OllamaEmbedder:
    """Build an embedder for the configured model, refusing to guess.

    There is no default embedding model and chat models are never
    substituted; an unset configuration raises
    :class:`EmbeddingModelNotConfiguredError` instead.
    """
    if settings.model is None:
        msg = (
            f"{EMBEDDING_MODEL_ENV} is not configured; "
            "embedding operations are unavailable"
        )
        raise EmbeddingModelNotConfiguredError(msg)
    client = OllamaClient(model=settings.model, base_url=base_url, transport=transport)
    return OllamaEmbedder(client)


@dataclass(frozen=True, slots=True)
class EmbeddingBackendReport:
    """Outcome of one on-demand readiness probe.

    ``dimensions`` is populated only when a probe vector satisfied the
    full embedding contract. ``detail`` carries a short stable reason and
    never echoes probe input or document content.
    """

    reachable: bool
    supported: bool
    model_ready: bool
    dimensions: int | None
    detail: str


PROBE_TEXT = "readiness probe"


def _classify_http_error(exc: OllamaHTTPStatusError) -> str:
    text = str(exc)
    if "does not support embeddings" in text:
        return "server does not support embeddings"
    if exc.status_code == 404:
        return "embedding model not available"
    return f"embedding request failed with HTTP {exc.status_code}"


def probe_embedding_backend(
    model: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> EmbeddingBackendReport:
    """Check endpoint reachability, embeddings support, and model readiness.

    A single fixed probe sentence is embedded once; nothing else is sent
    and no state is changed. This is an explicit pre-flight check for
    operators, not a health-monitoring system.
    """
    try:
        with OllamaClient(
            model=model, base_url=base_url, timeout=timeout, transport=transport
        ) as client:
            embedding = OllamaEmbedder(client).embed(PROBE_TEXT)
    except OllamaConnectionError:
        return EmbeddingBackendReport(False, False, False, None, "endpoint unreachable")
    except OllamaHTTPStatusError as exc:
        return EmbeddingBackendReport(
            True, False, False, None, _classify_http_error(exc)
        )
    except OllamaResponseError, MalformedEmbeddingError:
        return EmbeddingBackendReport(
            True, False, False, None, "embedding response violated the contract"
        )
    return EmbeddingBackendReport(True, True, True, embedding.dimensions, "ready")
