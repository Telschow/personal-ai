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
