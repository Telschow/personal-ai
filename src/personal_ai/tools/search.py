"""Agent tool exposing keyword search over persisted knowledge.

The result of a query is wrapped in the canonical retrieval envelope so the
model can always distinguish ``results``, ``no_matches``, and ``error``. A
store-level failure is reported as a safe ``error`` outcome, never as an empty
successful result.
"""

from dataclasses import asdict

from personal_ai.retrieval import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    MAX_SEARCH_QUERY_CHARS,
    ChunkIndex,
    SearchDocumentsRequest,
    build_retrieval_outcome,
    search_documents,
)
from personal_ai.storage.chunks import DocumentFilter

_ALLOWED_ARGUMENT_KEYS = frozenset({"query", "limit", "filter"})
_ALLOWED_FILTER_KEYS = frozenset(
    {
        "source_types",
        "mime_types",
        "created_after",
        "created_before",
        "modified_after",
        "modified_before",
    }
)


def _parse_query(arguments: dict[str, object]) -> str:
    query = arguments.get("query")
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if len(query) > MAX_SEARCH_QUERY_CHARS:
        raise ValueError(f"query exceeds the {MAX_SEARCH_QUERY_CHARS}-character limit")
    return query


def _parse_limit(arguments: dict[str, object]) -> int:
    limit = arguments.get("limit", DEFAULT_SEARCH_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit must be an integer")
    if limit > MAX_SEARCH_LIMIT:
        raise ValueError(f"limit exceeds the maximum of {MAX_SEARCH_LIMIT}")
    return limit


def _parse_document_filter(arguments: dict[str, object]) -> DocumentFilter | None:
    raw_filter = arguments.get("filter")
    if raw_filter is None:
        return None
    if not isinstance(raw_filter, dict):
        raise TypeError("filter must be an object")

    unknown_keys = sorted(set(raw_filter) - _ALLOWED_FILTER_KEYS)
    if unknown_keys:
        msg = f"filter has unsupported fields: {', '.join(unknown_keys)}"
        raise ValueError(msg)

    kwargs: dict[str, object] = {}
    source_types = raw_filter.get("source_types")
    if source_types is not None:
        if not isinstance(source_types, list) or not all(
            isinstance(value, str) for value in source_types
        ):
            raise ValueError("filter.source_types must be a list of strings")
        kwargs["source_types"] = tuple(source_types)
    mime_types = raw_filter.get("mime_types")
    if mime_types is not None:
        if not isinstance(mime_types, list) or not all(
            isinstance(value, str) for value in mime_types
        ):
            raise ValueError("filter.mime_types must be a list of strings")
        kwargs["mime_types"] = tuple(mime_types)
    for key in (
        "created_after",
        "created_before",
        "modified_after",
        "modified_before",
    ):
        value = raw_filter.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise TypeError(f"filter.{key} must be a string")
        kwargs[key] = value
    return DocumentFilter(**kwargs)  # type: ignore[arg-type]


class SearchTool:
    """Keyword search over the ingested knowledge base.

    The handler accepts only explicitly allow-listed JSON arguments and
    converts them into a typed request; the model can never influence SQL,
    FTS MATCH expressions, or anything beyond query text, a result limit,
    and validated document filters.
    """

    def __init__(self, chunk_index: ChunkIndex) -> None:
        self._chunk_index = chunk_index

    def search_documents(self, arguments: dict[str, object]) -> dict[str, object]:
        """Run one search request from model-supplied JSON arguments.

        Returns the canonical retrieval envelope. Argument validation errors
        (unknown keys, non-string query, out-of-bounds limit) still raise and
        are surfaced as tool execution errors; only a failure to *execute* the
        query against the index is converted into a safe ``error`` outcome.
        """
        unknown_keys = sorted(set(arguments) - _ALLOWED_ARGUMENT_KEYS)
        if unknown_keys:
            msg = f"unsupported arguments: {', '.join(unknown_keys)}"
            raise ValueError(msg)

        request = SearchDocumentsRequest(
            query=_parse_query(arguments),
            limit=_parse_limit(arguments),
            document_filter=_parse_document_filter(arguments),
        )
        try:
            results = search_documents(self._chunk_index, request)
            hits = tuple(
                {
                    "chunk_id": hit.chunk_id,
                    "document_id": hit.document_id,
                    "chunk_index": hit.chunk_index,
                    "text": hit.text,
                    "rank": hit.rank,
                    "source_type": hit.source_type,
                    "source": hit.source,
                }
                for hit in results
            )
        except Exception:  # noqa: BLE001 - convert operational failure to safe error status
            outcome = build_retrieval_outcome(
                request.query, (), limit=request.limit, error=True
            )
            return asdict(outcome)

        outcome = build_retrieval_outcome(request.query, hits, limit=request.limit)
        return asdict(outcome)
