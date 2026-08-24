"""Application-facing keyword search over persisted knowledge.

This module is the stable entry point for searching ingested documents.
It owns no retrieval logic of its own: every query, including all
sanitization, ranking, and filtering semantics, is delegated to
:class:`~personal_ai.storage.chunks.ChunkStore.search`.
"""

from dataclasses import dataclass

from personal_ai.storage.chunks import (
    DEFAULT_SEARCH_LIMIT,
    ChunkSearchResult,
    ChunkStore,
    DocumentFilter,
)

__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "SearchDocumentsRequest",
    "search_documents",
]


@dataclass(frozen=True, slots=True)
class SearchDocumentsRequest:
    """A fully typed search request against the persisted knowledge base.

    ``query`` is free text; it is sanitized into literal keyword terms
    downstream by the chunk store. ``document_filter`` is optional and must
    be an already-validated :class:`~personal_ai.storage.chunks.DocumentFilter`.
    """

    query: str
    limit: int = DEFAULT_SEARCH_LIMIT
    document_filter: DocumentFilter | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query, str):
            msg = f"query must be a string, got {type(self.query).__name__}"
            raise TypeError(msg)
        if not isinstance(self.limit, int) or isinstance(self.limit, bool):
            msg = f"limit must be an integer, got {type(self.limit).__name__}"
            raise TypeError(msg)
        if self.limit < 0:
            msg = f"limit must be non-negative, got {self.limit}"
            raise ValueError(msg)


def search_documents(
    chunk_store: ChunkStore, request: SearchDocumentsRequest
) -> tuple[ChunkSearchResult, ...]:
    """Run one search request and return deterministic ranked hits."""
    return chunk_store.search(
        request.query, limit=request.limit, filters=request.document_filter
    )
