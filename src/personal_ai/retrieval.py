"""Application-facing search over persisted knowledge.

This module is the stable entry point for searching ingested documents.
It owns no retrieval logic of its own: every query, including all
sanitization, ranking, and filtering semantics, is delegated to a
:class:`ChunkIndex` implementation. Three implementations exist:
:class:`~personal_ai.storage.chunks.SQLiteChunkIndex` (FTS5 keyword search,
the production default), :class:`~personal_ai.semantic_index.SemanticChunkIndex`
(cosine-similarity retrieval over persisted embeddings), and
:class:`~personal_ai.hybrid_index.HybridChunkIndex` (both backends fused with
Reciprocal Rank Fusion). All produce the same typed
:class:`~personal_ai.storage.chunks.ChunkSearchResult` hits, and consumers —
the tools and the agent — never know which backend is in use. The keyword and
semantic ranking scales differ on purpose and are not directly comparable;
the hybrid backend merges them by rank position (RRF), never by raw value.

The :class:`RetrievalService` provides a unified search across document
chunks, structured extractions, and conversation messages, returning a
single ranked result set.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from personal_ai.documents.models import Document
from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
    Event,
)
from personal_ai.storage.chunks import (
    DEFAULT_SEARCH_LIMIT,
    ChunkSearchResult,
    DocumentFilter,
)
from personal_ai.storage.conversations import (
    ConversationSearchResult,
    ConversationStore,
)
from personal_ai.storage.documents import DocumentStore
from personal_ai.storage.events import (
    ActivityCount,
    BucketCount,
    ChannelCount,
    CountedQuery,
    EventQuery,
    EventStore,
    VideoCount,
)
from personal_ai.storage.extractions import ExtractionSearchResult, ExtractionStore

# Event types treated as "search" events by the temporal aggregation layer.
DEFAULT_SEARCH_EVENT_TYPES = (EVENT_TYPE_SEARCH_QUERY, EVENT_TYPE_YOUTUBE_SEARCH)

# Event types treated as "video" (watchable) events by temporal aggregation.
DEFAULT_VIDEO_EVENT_TYPES = (EVENT_TYPE_VIDEO_WATCH,)

# Canonical retrieval status values. These are the safety-relevant states the
# agent layer must be able to distinguish:
#
# * ``RESULTS_AVAILABLE``  -- a query ran and returned one or more matches.
# * ``NO_MATCHES``          -- a query ran cleanly but nothing matched. This is
#                              a real, verified outcome and is the ONLY state in
#                              which the model may reason that no relevant data
#                              exists.
# * ``RETRIEVAL_ERROR``     -- the query could not be executed (for example the
#                              underlying store was unavailable). The model must
#                              treat this as an operational failure, never as
#                              "no documents exist".
RETRIEVAL_STATUS_RESULTS = "results"
RETRIEVAL_STATUS_NO_MATCHES = "no_matches"
RETRIEVAL_STATUS_ERROR = "error"

# Canonical retrieval error category. A deliberately generic, leak-free signal.
RETRIEVAL_ERROR_UNAVAILABLE = "retrieval_unavailable"

# Hard bounds on model-supplied retrieval input. Kept conservative so that a
# single query never allocates unbounded work, and so observability never has
# to log or evaluate arbitrarily large query text.
MAX_SEARCH_QUERY_CHARS = 500
MAX_SEARCH_LIMIT = 50


@runtime_checkable
class ChunkIndex(Protocol):
    """Typed retrieval boundary over the document/chunk corpus.

    Search consumers depend on this interface instead of a concrete
    backend, so the agent, tool, service, and storage layers never see how a
    query is executed. Implementations today are SQLite FTS5
    (:class:`~personal_ai.storage.chunks.SQLiteChunkIndex`, exposed through
    the same connection by :class:`~personal_ai.storage.chunks.ChunkStore`)
    and the embedding-backed semantic index
    (:class:`~personal_ai.semantic_index.SemanticChunkIndex`), or a hybrid of
    the two (:class:`~personal_ai.hybrid_index.HybridChunkIndex`), selected by
    construction injection.

    ``search`` returns typed :class:`ChunkSearchResult` hits, best-ranked
    first, with a deterministic chunk-id tie-break. Rank semantics are
    backend-specific: the keyword index ranks lexically (FTS5 BM25, where a
    smaller rank is better), while the semantic index ranks by cosine
    similarity in ``[0, 1]`` (higher is better). The two scales are not
    directly comparable; only an implementation can interpret its own rank.
    Queries are validated and interpreted by the implementation; callers
    never write or influence FTS5 syntax.
    """

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        filters: DocumentFilter | None = None,
    ) -> tuple[ChunkSearchResult, ...]:
        """Return hits for this query, best-ranked first.

        ``query`` is free text; the implementation defines its exact
        semantics (literal keyword terms for the keyword index, an embedded
        query vector for the semantic index). ``limit`` bounds the results
        returned; ``filters`` optionally constrains hits by owning document
        metadata.
        """
        ...


__all__ = [
    "DEFAULT_SEARCH_EVENT_TYPES",
    "DEFAULT_SEARCH_LIMIT",
    "DEFAULT_VIDEO_EVENT_TYPES",
    "MAX_SEARCH_LIMIT",
    "MAX_SEARCH_QUERY_CHARS",
    "RETRIEVAL_ERROR_UNAVAILABLE",
    "RETRIEVAL_STATUS_ERROR",
    "RETRIEVAL_STATUS_NO_MATCHES",
    "RETRIEVAL_STATUS_RESULTS",
    "ActivityBucketsRequest",
    "ActivitySummaryRequest",
    "ChannelTrendsRequest",
    "ChunkIndex",
    "EventQueryRequest",
    "RetrievalOutcome",
    "RetrievalService",
    "SearchDocumentsRequest",
    "SearchResult",
    "SearchTrendsRequest",
    "VideoTrendsRequest",
    "activity_by_bucket",
    "activity_summary",
    "build_retrieval_outcome",
    "query_events",
    "search_documents",
    "top_channels",
    "top_searches",
    "top_videos",
]


def _validate_model_query(query: str) -> None:
    """Validate a model-supplied query against canonical length bounds.

    Raises ``ValueError`` when the query exceeds
    :data:`MAX_SEARCH_QUERY_CHARS`. This is a validation concern (the model
    supplied an out-of-bounds argument), distinct from an operational
    retrieval failure.
    """
    if len(query) > MAX_SEARCH_QUERY_CHARS:
        msg = (
            f"query exceeds the {MAX_SEARCH_QUERY_CHARS}-character limit"
            f" ({len(query)} characters)"
        )
        raise ValueError(msg)


def _validate_model_limit(limit: int) -> None:
    """Validate a model-supplied result limit cap.

    Raises ``ValueError`` when ``limit`` exceeds :data:`MAX_SEARCH_LIMIT`.
    """
    if limit > MAX_SEARCH_LIMIT:
        msg = f"limit exceeds the maximum of {MAX_SEARCH_LIMIT}"
        raise ValueError(msg)


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
    chunk_index: ChunkIndex, request: SearchDocumentsRequest
) -> tuple[ChunkSearchResult, ...]:
    """Run one search request and return deterministic ranked hits.

    ``chunk_index`` is any :class:`ChunkIndex` implementation; today that is
    the SQLite FTS5-backed store/index shared by the document pipeline.
    """
    return chunk_index.search(
        request.query, limit=request.limit, filters=request.document_filter
    )


def build_retrieval_outcome(
    query: str,
    results: Sequence[dict[str, object]],
    *,
    limit: int,
    truncated_any: bool = False,
    error: bool = False,
) -> RetrievalOutcome:
    """Build a canonical :class:`RetrievalOutcome` from a result set.

    ``error`` forces :data:`RETRIEVAL_STATUS_ERROR` (used when the underlying
    store could not be reached) so that an operational failure is never
    surfaced as an empty, successful result. ``truncated_any`` is True when the
    caller knows additional matches were dropped even though the returned count
    is below ``limit``; otherwise ``truncated`` is True only when the count
    equals or exceeds the limit.
    """
    if error:
        return RetrievalOutcome(
            query=query,
            status=RETRIEVAL_STATUS_ERROR,
            results=[],
            total_returned=0,
            truncated=False,
            query_length=len(query),
            error=RETRIEVAL_ERROR_UNAVAILABLE,
        )
    if not results:
        return RetrievalOutcome(
            query=query,
            status=RETRIEVAL_STATUS_NO_MATCHES,
            results=[],
            total_returned=0,
            truncated=False,
            query_length=len(query),
        )
    truncated = truncated_any or len(results) >= limit
    return RetrievalOutcome(
        query=query,
        status=RETRIEVAL_STATUS_RESULTS,
        results=list(results),
        total_returned=len(results),
        truncated=truncated,
        query_length=len(query),
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

    ``source_type`` identifies the owning document's source for ``chunk``
    and ``structured_extraction`` results (for example ``"file"``,
    ``"email"``) and is ``None`` for conversation results.
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
    source_type: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalOutcome:
    """The canonical, status-aware outcome of a retrieval operation.

    This is the explicit contract between the retrieval layer and the agent.
    It decouples *content* (``results``) from *status* so that a successful
    query with no matches is never confused with an operational failure.

    ``status`` is one of :data:`RETRIEVAL_STATUS_RESULTS`,
    :data:`RETRIEVAL_STATUS_NO_MATCHES`, or :data:`RETRIEVAL_STATUS_ERROR`.

    ``total_returned`` is the number of results actually returned (==
    ``len(results)``). ``truncated`` is True when more matches existed than
    were returned and the result set was cut at the limit. For
    ``RETRIEVAL_STATUS_ERROR``, ``results`` is empty and ``error`` carries a
    single safe, generic :data:`RETRIEVAL_ERROR_UNAVAILABLE` category.

    ``query`` echoes the validated query text (the model's own input) so the
    agent can ground its answer on exactly what was searched. ``query_length``
    is a privacy-safe scalar usable for observability without logging content.
    """

    query: str
    status: str
    results: list[dict[str, object]]
    total_returned: int = 0
    truncated: bool = False
    query_length: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in (
            RETRIEVAL_STATUS_RESULTS,
            RETRIEVAL_STATUS_NO_MATCHES,
            RETRIEVAL_STATUS_ERROR,
        ):
            msg = f"invalid retrieval status: {self.status!r}"
            raise ValueError(msg)
        if self.status == RETRIEVAL_STATUS_ERROR and self.error is None:
            msg = "an error status requires a non-None error category"
            raise ValueError(msg)
        if self.status != RETRIEVAL_STATUS_ERROR and self.error is not None:
            msg = "only an error status may carry an error category"
            raise ValueError(msg)


def _chunk_to_search_result(
    hit: ChunkSearchResult, title: str, source_type: str | None
) -> SearchResult:
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
        source_type=source_type,
    )


def _extraction_to_search_result(
    hit: ExtractionSearchResult, title: str, source_type: str | None
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
        source_type=source_type,
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

    - :class:`ChunkIndex` (keyword search over document chunks; today the
      SQLite FTS5-backed ``SQLiteChunkIndex`` via ``ChunkStore``)
    - :class:`~personal_ai.storage.extractions.ExtractionStore` (structured knowledge)
    - :class:`~personal_ai.storage.conversations.ConversationStore` (conversation messages)

    into a single ranked result list.
    """

    def __init__(
        self,
        chunk_index: ChunkIndex,
        extraction_store: ExtractionStore,
        document_store: DocumentStore,
        conversation_store: ConversationStore | None = None,
    ) -> None:
        self._chunk_index = chunk_index
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

        chunk_hits = self._chunk_index.search(query, limit=limit, filters=filters)
        extraction_hits = self._extraction_store.search(query, limit=limit)
        conversation_hits: tuple[ConversationSearchResult, ...] = ()
        if self._conversation_store is not None:
            created_after = filters.created_after if filters is not None else None
            created_before = filters.created_before if filters is not None else None
            conversation_hits = self._conversation_store.search(
                query,
                limit=limit,
                created_after=created_after,
                created_before=created_before,
            )

        # Cache document rows to avoid repeated lookups. Titles prefer the
        # email subject so mail results read naturally; the source key is
        # the fallback identity. source_type travels with the same row.
        document_cache: dict[str, Document | None] = {}

        def _resolve_document(document_id: str) -> Document | None:
            if document_id not in document_cache:
                document_cache[document_id] = self._document_store.get(document_id)
            return document_cache[document_id]

        def _document_provenance(document_id: str) -> tuple[str, str | None]:
            document = _resolve_document(document_id)
            if document is None:
                return document_id, None
            subject = document.metadata.get("subject")
            if isinstance(subject, str) and subject.strip():
                return subject.strip(), document.source_type
            return document.source, document.source_type

        results: list[SearchResult] = []
        for hit in chunk_hits:
            title, source_type = _document_provenance(hit.document_id)
            results.append(_chunk_to_search_result(hit, title, source_type))
        for hit in extraction_hits:
            title, source_type = _document_provenance(hit.document_id)
            results.append(_extraction_to_search_result(hit, title, source_type))
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
    keyword: str | None = None
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
            keyword=request.keyword,
            limit=request.limit,
        )
    )


@dataclass(frozen=True, slots=True)
class ActivitySummaryRequest:
    """A typed request for the event activity summary."""

    start_time: str | None = None
    end_time: str | None = None
    source: str | None = None
    event_type: str | None = None
    keyword: str | None = None


@dataclass(frozen=True, slots=True)
class SearchTrendsRequest:
    """A typed request for the top-searches aggregation."""

    start_time: str | None = None
    end_time: str | None = None
    source: str | None = None
    event_type: str | None = None
    keyword: str | None = None
    limit: int = 10


@dataclass(frozen=True, slots=True)
class ChannelTrendsRequest:
    """A typed request for the top-channels aggregation."""

    start_time: str | None = None
    end_time: str | None = None
    source: str | None = None
    event_type: str | None = None
    keyword: str | None = None
    limit: int = 10


@dataclass(frozen=True, slots=True)
class VideoTrendsRequest:
    """A typed request for the top-videos aggregation."""

    start_time: str | None = None
    end_time: str | None = None
    source: str | None = None
    event_type: str | None = None
    keyword: str | None = None
    limit: int = 10


@dataclass(frozen=True, slots=True)
class ActivityBucketsRequest:
    """A typed request for the time-bucket activity aggregation."""

    start_time: str | None = None
    end_time: str | None = None
    source: str | None = None
    event_type: str | None = None
    keyword: str | None = None
    bucket: str = "month"
    limit: int = 100


def _event_types(request_event_type: str | None, defaults: tuple[str, ...]) -> tuple:
    """Return the concrete event-type set for a trends query.

    When ``request_event_type`` is ``None`` the ``defaults`` set is used;
    otherwise a single-element set containing the requested type.
    """
    if request_event_type is None:
        return defaults
    return (request_event_type,)


def activity_summary(
    event_store: EventStore, request: ActivitySummaryRequest
) -> tuple[ActivityCount, ...]:
    """Return an event activity summary grouped by event_type and source."""
    return event_store.activity_summary(
        start_time=request.start_time,
        end_time=request.end_time,
        source=request.source,
        event_type=request.event_type,
        keyword=request.keyword,
    )


def top_searches(
    event_store: EventStore, request: SearchTrendsRequest
) -> tuple[CountedQuery, ...]:
    """Return the most frequent search queries.

    Restricted to search event types by default (or the requested one).
    ``keyword`` additionally restricts to matching events.
    """
    return event_store.top_search_queries(
        start_time=request.start_time,
        end_time=request.end_time,
        source=request.source,
        event_types=_event_types(request.event_type, DEFAULT_SEARCH_EVENT_TYPES),
        keyword=request.keyword,
        limit=request.limit,
    )


def top_channels(
    event_store: EventStore, request: ChannelTrendsRequest
) -> tuple[ChannelCount, ...]:
    """Return the most frequently watched channels.

    ``keyword`` additionally restricts to matching events.
    """
    return event_store.top_channels(
        start_time=request.start_time,
        end_time=request.end_time,
        source=request.source,
        event_types=_event_types(request.event_type, DEFAULT_VIDEO_EVENT_TYPES),
        keyword=request.keyword,
        limit=request.limit,
    )


def top_videos(
    event_store: EventStore, request: VideoTrendsRequest
) -> tuple[VideoCount, ...]:
    """Return the most frequently watched videos.

    ``keyword`` additionally restricts to matching events.
    """
    return event_store.top_videos(
        start_time=request.start_time,
        end_time=request.end_time,
        source=request.source,
        event_types=_event_types(request.event_type, DEFAULT_VIDEO_EVENT_TYPES),
        keyword=request.keyword,
        limit=request.limit,
    )


def activity_by_bucket(
    event_store: EventStore, request: ActivityBucketsRequest
) -> tuple[BucketCount, ...]:
    """Return event activity grouped by a UTC time bucket."""
    return event_store.activity_by_bucket(
        start_time=request.start_time,
        end_time=request.end_time,
        source=request.source,
        event_type=request.event_type,
        keyword=request.keyword,
        bucket=request.bucket,
        limit=request.limit,
    )
