"""Idempotent population of durable embeddings for persisted chunks."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from personal_ai.documents.embedding import Embedding, EmbeddingProvider
from personal_ai.documents.models import DocumentChunk
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

    def backfill_corpus(
        self,
        *,
        batch_size: int = 32,
        resume: bool = True,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> CorpusBackfillSummary:
        """Ensure every persisted chunk has a current vector, in chunk order.

        Chunks are visited exactly once in deterministic chunk-id order, in
        bounded ``batch_size`` windows. With ``resume`` (the default), a
        stored embedding whose model matches the provider is skipped without
        a provider call or write, so an interrupted run is resumed by simply
        starting again; ``resume=False`` re-embeds every chunk and overwrites
        existing current-model vectors. Both modes follow the identical
        model-match semantics and fail-fast persistence of
        :meth:`ensure_document`: each successful batch commits immediately
        and provider errors propagate unchanged, leaving earlier chunks
        valid and the remainder repairable on the next run.

        ``progress_callback`` is invoked after each batch with the
        cumulative ``(chunks_scanned, embeddings_created)`` counts.
        """
        if batch_size < 1:
            msg = f"batch_size must be >= 1, got {batch_size}"
            raise ValueError(msg)
        total = self._chunk_store.count()
        scanned = 0
        created = 0
        offset = 0
        overwrite = not resume
        while True:
            chunks = self._chunk_store.list_chunks(limit=batch_size, offset=offset)
            if not chunks:
                break
            scanned += len(chunks)
            created += self._embed_batch(chunks, overwrite=overwrite)
            offset += len(chunks)
            if progress_callback is not None:
                progress_callback(scanned, created)
        return CorpusBackfillSummary(
            total_chunks=total, chunks_scanned=scanned, embeddings_created=created
        )

    def _embed_batch(self, chunks: Iterable[DocumentChunk], *, overwrite: bool) -> int:
        """Embed one batch and persist it in one transaction.

        Returns the number of embeddings produced by the provider this
        call (each provider call yields exactly one persisted vector).
        """
        chunk_list = list(chunks)
        current = self._embedding_store.list_for_chunks(
            chunk.id for chunk in chunk_list
        )
        produced: list[tuple[Embedding, str]] = []
        for chunk in chunk_list:
            stored = current.get(chunk.id)
            if (
                not overwrite
                and stored is not None
                and stored.model == self._embedding_provider.model
            ):
                continue
            produced.append((self._embedding_provider.embed(chunk.text), chunk.id))
        if not produced:
            return 0
        self._embedding_store.add_many(produced)
        return len(produced)


@dataclass(frozen=True, slots=True)
class CorpusBackfillSummary:
    """Outcome of a corpus-wide :meth:`EmbeddingBackfiller.backfill_corpus` run.

    ``total_chunks`` is the authoritative chunk count at the start of the
    run; ``chunks_scanned`` and ``embeddings_created`` are cumulative for
    this invocation only.
    """

    total_chunks: int
    chunks_scanned: int
    embeddings_created: int


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    """Outcome of running the backfiller over a set of documents."""

    documents: int
    embeddings_created: int


def backfill_documents(
    backfiller: EmbeddingBackfiller, document_ids: Iterable[str]
) -> BackfillSummary:
    """Ensure vectors for many documents in deterministic document-id order.

    Purely an iteration wrapper around :meth:`EmbeddingBackfiller.ensure_document`:
    identical idempotency, immediate persistence, and fail-fast error
    propagation are inherited unchanged; duplicates in the input simply
    reuse their now-current vectors.
    """
    ordered = sorted(document_ids)
    created = 0
    for document_id in ordered:
        created += backfiller.ensure_document(document_id)
    return BackfillSummary(documents=len(ordered), embeddings_created=created)
