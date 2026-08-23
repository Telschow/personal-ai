"""Ollama-backed implementation of the embedding contract."""

from personal_ai.documents.embedding import Embedding, parse_embedding
from personal_ai.ollama_client import OllamaClient


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

    def embed(self, text: str) -> Embedding:
        if not text:
            msg = "Cannot embed empty text"
            raise ValueError(msg)
        response = self._client.embed(text)
        return parse_embedding(response.model, response.embeddings)
