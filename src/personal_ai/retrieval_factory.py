"""Construction seam for the production chunk-retrieval backend.

This module owns the only place the application decides *which*
:class:`~personal_ai.retrieval.ChunkIndex` implementation is used. Every
wiring path (CLI ``--search``/chat registry, HTTP/MCP server) builds its
index through :func:`build_chunk_index`, so keyword is the default everywhere
until an operator explicitly opts into ``semantic`` or ``hybrid`` via
``PERSONAL_AI_RETRIEVAL_MODE``.

Rules enforced here:

- ``keyword`` needs no model backend and constructs the plain FTS5 index.
- ``semantic``/``hybrid`` require an embedding provider. Configuring an
  embedding model alone never changes retrieval — only an explicit
  retrieval-mode setting does. A requested backend without a provider is a
  configuration error and raises ``ValueError`` (fail loudly, never degrade
  silently to keyword).
- The factory is deterministic and network-free: it never touches Ollama,
  never embeds anything, and never writes rows. Provider wiring happens in
  the call site.
"""

import sqlite3
from collections.abc import Callable, Mapping

from personal_ai.config import (
    EmbeddingSettings,
    RetrievalMode,
    RetrievalSettings,
    load_embedding_settings,
    load_retrieval_settings,
)
from personal_ai.documents.embedding import EmbeddingProvider
from personal_ai.hybrid_index import HybridChunkIndex
from personal_ai.retrieval import ChunkIndex
from personal_ai.semantic_index import SemanticChunkIndex
from personal_ai.storage.chunks import SQLiteChunkIndex


def _require_embedding_provider(
    mode: RetrievalMode, embedding_provider: EmbeddingProvider | None
) -> EmbeddingProvider:
    """Return the required provider or fail loudly for opt-in backends."""
    if embedding_provider is None:
        valid = ", ".join(m.value for m in RetrievalMode)
        msg = (
            f"{mode.value!r} retrieval requires an embedding provider; "
            "configure PERSONAL_AI_EMBEDDING_MODEL (one of "
            f"PERSONAL_AI_RETRIEVAL_MODE values: {valid!r}) "
            "before selecting a non-keyword backend"
        )
        raise ValueError(msg)
    return embedding_provider


def build_chunk_index(
    connection: sqlite3.Connection,
    settings: RetrievalSettings,
    embedding_provider: EmbeddingProvider | None = None,
) -> ChunkIndex:
    """Construct the :class:`~personal_ai.retrieval.ChunkIndex` for ``settings``.

    ``connection`` is the shared SQLite connection owned by the caller; the
    returned index is read-only at query time and never opens its own
    database handle.
    """
    if settings.mode is RetrievalMode.SEMANTIC:
        provider = _require_embedding_provider(settings.mode, embedding_provider)
        return SemanticChunkIndex(connection, provider)
    if settings.mode is RetrievalMode.HYBRID:
        provider = _require_embedding_provider(settings.mode, embedding_provider)
        keyword = SQLiteChunkIndex(connection)
        semantic = SemanticChunkIndex(connection, provider)
        return HybridChunkIndex(keyword, semantic)
    return SQLiteChunkIndex(connection)


def runtime_chunk_index(
    connection: sqlite3.Connection,
    *,
    embedding_provider_factory: (
        Callable[[EmbeddingSettings], EmbeddingProvider] | None
    ) = None,
    environ: Mapping[str, str] | None = None,
) -> ChunkIndex:
    """Assemble the runtime retrieval backend from operator configuration.

    Keyword is the default unless ``PERSONAL_AI_RETRIEVAL_MODE`` opts into
    ``semantic``/``hybrid``; those backends additionally require an embedding
    model, and the provider is constructed through
    ``embedding_provider_factory`` — the call site supplies the Ollama-backed
    default (``create_embedder``), keeping this factory provider-independent.

    This function never touches the network and never embeds anything:
    provider construction is deferred until a backend that needs it is
    actually selected, and an unsupported selection fails loudly instead of
    degrading silently to keyword.
    """
    settings = load_retrieval_settings(environ)
    provider = None
    if settings.mode is not RetrievalMode.KEYWORD:
        embedding_settings = load_embedding_settings(environ)
        if (
            embedding_settings.model is not None
            and embedding_provider_factory is not None
        ):
            provider = embedding_provider_factory(embedding_settings)
    return build_chunk_index(connection, settings, provider)
