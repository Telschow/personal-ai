"""Idempotent population of durable embeddings for persisted chunks."""

from personal_ai.documents.embedding import EmbeddingProvider
from personal_ai.storage.chunks import ChunkStore
from personal_ai.storage.embeddings import EmbeddingStore


class EmbeddingBackfiller:
    """Populates embeddings for already-ingested documents.

    Ingestion persists documents, chunks, and structured extraction
    without any model availability requirement; this class is the explicit
    boundary that turns those durable chunks into durable vectors. It
    operates purely over persisted state — chunk rows and embedding rows —
    never raw sources, and never fabricates or skips vectors.

    A stored embedding is reused only when its recorded model matches the
    provider's current model identity; anything missing or produced by
    another model is re-embedded and overwritten under the same chunk id,
    so a model change invalidates old vectors instead of silently treating
    them as equivalent. Chunks are processed in chunk order and each
    embedding persists immediately, so a provider failure mid-document
    leaves a partial but consistent state: earlier chunks keep valid
    embeddings, the failing chunk propagates its error, and later chunks
    stay absent. Rerunning :meth:`ensure_document` repairs exactly the
    remainder and reuses everything already current, so retries after any
    interruption never repeat successful work. Provider errors propagate
    unchanged; nothing here logs or swallows chunk text.
    """

    def __init__(
        self,
        chunk_store: ChunkStore,
        embedding_store: EmbeddingStore,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._chunk_store = chunk_store
        self._embedding_store = embedding_store
        self._embedding_provider = embedding_provider

    def ensure_document(self, document_id: str) -> int:
        """Ensure every persisted chunk of one document has a current vector.

        Returns the number of embeddings generated during this call;
        chunks whose stored embedding already matches the provider's model
        are reused without provider calls or writes.
        """
        embedded = 0
        for chunk in self._chunk_store.list_for_document(document_id):
            stored = self._embedding_store.get(chunk.id)
            if stored is not None and stored.model == self._embedding_provider.model:
                continue
            embedding = self._embedding_provider.embed(chunk.text)
            self._embedding_store.add(embedding, chunk.id)
            embedded += 1
        return embedded
