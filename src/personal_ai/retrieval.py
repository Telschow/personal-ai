"""Application-facing keyword search over persisted knowledge.

This module is the stable entry point for searching ingested documents.
It owns no retrieval logic of its own: every query, including all
sanitization, ranking, and filtering semantics, is delegated to
:class:`~personal_ai.storage.chunks.ChunkStore.search`.

The :class:`RetrievalService` provides a unified search across document
chunks, structured extractions, and conversation messages, returning a
single ranked result set.
"""

from dataclasses import dataclass

from personal_ai.events.models import Event
from personal_ai.storage.chunks import (
    DEFAULT_SEARCH_LIMIT,
    ChunkSearchResult,
    ChunkStore,
    DocumentFilter,
)
from personal_ai.storage.conversations import (
    ConversationSearchResult,
    ConversationStore,
)
from personal_ai.storage.documents import DocumentStore
from personal_ai.storage.events import EventQuery, EventStore
from personal_ai.storage.extractions import ExtractionSearchResult, ExtractionStore

__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "EventQueryRequest",
    "RetrievalService",
    "SearchDocumentsRequest",
    "SearchResult",
    "query_events",
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


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A unified search result from chunk, extraction, or conversation search.

    ``result_type`` is ``"chunk"``, ``"structured_extraction"``, or
    ``"conversation"``.

    ``score`` is a normalized relevance value in [0, 1] (higher is better).

    For chunk results it is derived from BM25 rank; for extraction results
    it is computed from field match quality; for conversation results it
    is based on content/title match quality.

    ``matched_fields`` is non-empty for structured extraction and
    conversation results.

    Conversation-specific fields (``conversation_id``, ``message_id``,
    ``message_index``, ``role``, ``speaker``) are populated only for
    ``result_type="conversation"`` and are ``None`` otherwise.
    """

    result_type: str
    document_id: str | None
    score: float
    title: str
    text: str
    page_number: int | None
    matched_fields: tuple[str, ...]
    conversation_id: str | None = None
    message_id: str | None = None
    message_index: int | None = None
    role: str | None = None
    speaker: str | None = None
    timestamp: str | None = None
    is_active_branch: bool | None = None


def _chunk_to_search_result(hit: ChunkSearchResult, title: str) -> SearchResult:
    """Convert a chunk search hit to the unified result format."""
    # BM25 rank is negative (closer to 0 = better).  Normalize to [0, 1].
    # Clamp very negative ranks to 0.
    score = max(0.0, min(1.0, 1.0 + hit.rank))
    return SearchResult(
        result_type="chunk",
        document_id=hit.document_id,
        score=score,
        title=title,
        text=hit.text,
        page_number=None,
        matched_fields=(),
    )


def _extraction_to_search_result(
    hit: ExtractionSearchResult, title: str
) -> SearchResult:
    """Convert an extraction search hit to the unified result format."""
    return SearchResult(
        result_type="structured_extraction",
        document_id=hit.document_id,
        score=hit.score,
        title=title,
        text=hit.summary,
        page_number=None,
        matched_fields=hit.matched_fields,
    )


def _conversation_to_search_result(
    hit: ConversationSearchResult,
) -> SearchResult:
    """Convert a conversation search hit to the unified result format."""
    return SearchResult(
        result_type="conversation",
        document_id=None,
        score=hit.score,
        title=hit.conversation_title,
        text=hit.content_text,
        page_number=None,
        matched_fields=("content_text",) if hit.score > 0 else (),
        conversation_id=hit.conversation_id,
        message_id=hit.message_id,
        message_index=hit.message_index,
        role=hit.role,
        speaker=hit.speaker,
        timestamp=hit.timestamp,
        is_active_branch=hit.is_active_branch,
    )


class RetrievalService:
    """Unified search across document chunks, structured extractions, and conversations.

    Combines results from:

    - :class:`~personal_ai.storage.chunks.ChunkStore` (BM25 over document text)
    - :class:`~personal_ai.storage.extractions.ExtractionStore` (structured knowledge)
    - :class:`~personal_ai.storage.conversations.ConversationStore` (conversation messages)

    into a single ranked result list.
    """

    def __init__(
        self,
        chunk_store: ChunkStore,
        extraction_store: ExtractionStore,
        document_store: DocumentStore,
        conversation_store: ConversationStore | None = None,
    ) -> None:
        self._chunk_store = chunk_store
        self._extraction_store = extraction_store
        self._document_store = document_store
        self._conversation_store = conversation_store

    def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        filters: DocumentFilter | None = None,
    ) -> tuple[SearchResult, ...]:
        """Search across chunks, extractions, and conversations.

        Returns at most ``limit`` results, ordered by score descending.
        """
        if not isinstance(query, str) or not query.strip():
            return ()

        chunk_hits = self._chunk_store.search(query, limit=limit, filters=filters)
        extraction_hits = self._extraction_store.search(query, limit=limit)
        conversation_hits: tuple[ConversationSearchResult, ...] = ()
        if self._conversation_store is not None:
            conversation_hits = self._conversation_store.search(query, limit=limit)

        # Cache document titles to avoid repeated lookups
        title_cache: dict[str, str] = {}

        def _get_title(document_id: str) -> str:
            if document_id not in title_cache:
                doc = self._document_store.get(document_id)
                title_cache[document_id] = doc.source if doc else document_id
            return title_cache[document_id]

        results: list[SearchResult] = []
        for hit in chunk_hits:
            results.append(_chunk_to_search_result(hit, _get_title(hit.document_id)))
        for hit in extraction_hits:
            results.append(
                _extraction_to_search_result(hit, _get_title(hit.document_id))
            )
        for hit in conversation_hits:
            results.append(_conversation_to_search_result(hit))

        results.sort(key=lambda r: (-r.score, r.document_id or "", r.result_type))
        return tuple(results[:limit])


@dataclass(frozen=True, slots=True)
class EventQueryRequest:
    """A fully typed structural query against temporal events.

    All bounds are inclusive ISO-8601 strings. ``source`` and ``event_type``
    are optional exact-match filters. Temporal events are not documents and
    are never searched semantically: this request maps directly onto indexed
    time-range and filter predicates.
    """

    start_time: str | None = None
    end_time: str | None = None
    source: str | None = None
    event_type: str | None = None
    limit: int = 100


def query_events(
    event_store: EventStore, request: EventQueryRequest
) -> tuple[Event, ...]:
    """Run one structural temporal-event query.

    Delegates to :class:`~personal_ai.storage.events.EventStore` and returns
    events ordered by event_time then id.
    """
    return event_store.search(
        EventQuery(
            start_time=request.start_time,
            end_time=request.end_time,
            source=request.source,
            event_type=request.event_type,
            limit=request.limit,
        )
    )
