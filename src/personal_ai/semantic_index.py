"""Semantic (embedding-backed) retrieval behind the ``ChunkIndex`` boundary.

This module adds the semantic sibling to the FTS5-backed keyword index. It is
the retrieval-time consumer of the durable vectors produced by
:class:`~personal_ai.embeddings.EmbeddingBackfiller`: a query is embedded once
through an :class:`~personal_ai.documents.embedding.EmbeddingProvider`, the
stored chunk vectors applicable to the query are compared with cosine
similarity, and the closest chunks are returned as the same typed
:class:`~personal_ai.storage.chunks.ChunkSearchResult` hits the keyword
backend produces.

Deliberate boundaries:

- The provider seam (``EmbeddingProvider``) keeps model/backend details out of
  the index; ``OllamaEmbedder`` is one possible provider, but nothing here
  imports Ollama or configuration.
- Search is observationally read-only: it issues only SELECT queries against
  the shared connection (plus optional query-time embedding inference through
  the provider). It never inserts, updates, or deletes chunk, document, or
  embedding rows, and it never triggers backfilling.
- Vectors are only comparable when produced by the provider's current model.
  Stored vectors from another model are never compared — they are excluded
  from candidates (run ``EmbeddingBackfiller`` to renew them). A stored
  vector from the same model but with a different dimension is a corpus
  inconsistency and fails deterministically rather than producing a
  meaningless score.
- Backend selection is construction-injection only. Keyword retrieval remains
  the default in every production wiring path; there is no setting that
  silently switches retrieval semantics. This index therefore takes a
  connection and a provider explicitly.

Ranking semantics differ from the keyword backend on purpose: ``rank`` here is
the cosine similarity in ``[0, 1]`` (higher = more similar; candidates whose
similarity is negative are not matches), ordered by score descending and chunk
id ascending. The two backends are not directly comparable numerically —
merging/renormalizing their scales belongs to a later hybrid-fusion phase.
"""

import math
import sqlite3
from collections.abc import Sequence

from personal_ai.documents.embedding import Embedding, EmbeddingProvider
from personal_ai.storage.chunks import (
    ChunkSearchResult,
    DocumentFilter,
    _document_constraints,
    _document_provenance,
)
from personal_ai.storage.embeddings import EmbeddingStore

DEFAULT_SEARCH_LIMIT = 10

_CANDIDATES_SQL = """
SELECT chunk_id, document_id, chunk_index, text
FROM document_chunks
ORDER BY chunk_id
"""

_FILTERED_CANDIDATES_SQL = """
SELECT document_chunks.chunk_id,
       document_chunks.document_id,
       document_chunks.chunk_index,
       document_chunks.text
FROM document_chunks
JOIN documents ON documents.id = document_chunks.document_id
WHERE 1 = 1{constraints}
ORDER BY chunk_id
"""


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return the cosine similarity between two dense vectors.

    Deterministic and dependency-free. Dimension mismatch, empty input, and
    non-finite components raise ``ValueError``; a zero-norm vector has no
    direction and is defined to have similarity ``0.0``.
    """
    if len(left) != len(right):
        msg = f"Cannot compare vectors of dimension {len(left)} and {len(right)}"
        raise ValueError(msg)
    if not left or not right:
        msg = "Cannot compute similarity for an empty vector"
        raise ValueError(msg)

    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for left_component, right_component in zip(left, right):
        left_float = float(left_component)
        right_float = float(right_component)
        if not math.isfinite(left_float) or not math.isfinite(right_float):
            msg = "Similarity requires finite vector components"
            raise ValueError(msg)
        dot += left_float * right_float
        left_norm += left_float * left_float
        right_norm += right_float * right_float

    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


class SemanticChunkIndex:
    """Concrete embedding-backed implementation of the ``ChunkIndex`` contract.

    Brute-force deterministic similarity over the caller's persisted vectors:
    nearest-neighbour retrieval is intentionally not a production-scale vector
    engine, matching the local single-user deployment. Candidate selection,
    model identity checks, and similarity ranking are exactly as documented in
    this module's docstring.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._connection = connection
        self._embedding_provider = embedding_provider
        self._embedding_store = EmbeddingStore(connection)

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        filters: DocumentFilter | None = None,
    ) -> tuple[ChunkSearchResult, ...]:
        """Return semantic hits over stored chunk embeddings, closest first.

        Empty or whitespace-only queries return no results without touching
        the provider. ``limit`` must be non-negative. ``filters`` constrain
        candidate chunks by owning-document metadata before any embedding is
        loaded (never after similarity).

        Provider and store errors propagate unchanged and are never reported
        as a legitimate zero-result query. Stored vectors produced by a
        different model are excluded from candidates; when no candidate carries
        a vector for the provider's current model, the result is ``()`` without
        any provider call. Corrupt rows and stale-but-reported-valid rows are
        governed by the same rules as the storage layer.
        """
        if limit < 0:
            msg = f"Search limit must be non-negative, got {limit}"
            raise ValueError(msg)
        if not query.strip():
            return ()

        candidates = self._candidate_rows(filters)
        if not candidates:
            return ()
        stored = self._embedding_store.list_for_chunks(
            chunk_id for chunk_id, *_ in candidates
        )
        current_model_vectors = {
            chunk_id: embedding
            for chunk_id, embedding in stored.items()
            if embedding.model == self._embedding_provider.model
        }
        if not current_model_vectors:
            # No candidate is semantically indexable for the current model;
            # backfill/reindex first. This is a legitimate "no semantically
            # matching chunks" result, not a provider failure.
            return ()

        query_embedding = self._embedding_provider.embed(query)

        def similarity(chunk_id: str, embedding: Embedding) -> float | None:
            if embedding.dimensions != query_embedding.dimensions:
                msg = (
                    f"Stored embedding for chunk {chunk_id!r} has "
                    f"{embedding.dimensions} dimensions but the query embedding "
                    f"for model {self._embedding_provider.model!r} has "
                    f"{query_embedding.dimensions}"
                )
                raise ValueError(msg)
            score = cosine_similarity(query_embedding.vector, embedding.vector)
            return score if score >= 0.0 else None

        hits: list[tuple[float, str, ChunkSearchResult]] = []
        candidate_lookup = {
            row[0]: (
                str(row[1]),
                row[2] if row[2] is None else int(row[2]),
                str(row[3]),
            )
            for row in candidates
        }
        for chunk_id, embedding in current_model_vectors.items():
            document_id, chunk_index, text = candidate_lookup[chunk_id]
            score = similarity(chunk_id, embedding)
            if score is None:
                continue
            hits.append(
                (
                    score,
                    chunk_id,
                    ChunkSearchResult(
                        chunk_id=chunk_id,
                        document_id=document_id,
                        chunk_index=chunk_index,
                        text=text,
                        rank=score,
                    ),
                )
            )
        hits.sort(key=lambda item: (-item[0], item[1]))
        results = tuple(hit for _, _, hit in hits[:limit])
        if not results:
            return ()
        provenance = _document_provenance(
            self._connection, [("", hit.document_id) for hit in results]
        )
        return tuple(
            ChunkSearchResult(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                chunk_index=hit.chunk_index,
                text=hit.text,
                rank=hit.rank,
                source_type=provenance[index][0],
                source=provenance[index][1],
            )
            for index, hit in enumerate(results)
        )

    def _candidate_rows(
        self, filters: DocumentFilter | None
    ) -> list[tuple[object, ...]]:
        if filters is None or filters.is_empty:
            return self._connection.execute(_CANDIDATES_SQL).fetchall()
        constraints, parameters = _document_constraints(filters)
        sql = _FILTERED_CANDIDATES_SQL.format(constraints=constraints)
        return self._connection.execute(sql, tuple(parameters)).fetchall()
