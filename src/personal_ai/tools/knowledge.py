"""Agent tool exposing unified search over document chunks, structured
extractions, and conversation messages.

The result of a query is wrapped in the canonical retrieval envelope so the
model can always distinguish ``results``, ``no_matches``, and ``error``. A
service-level failure is reported as a safe ``error`` outcome, never as an
empty successful result.
"""

from dataclasses import asdict

from personal_ai.retrieval import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    MAX_SEARCH_QUERY_CHARS,
    RetrievalService,
    build_retrieval_outcome,
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


def _format_conversation_result(hit: dict[str, object]) -> dict[str, object]:
    """Format a conversation search result with concise provenance."""
    speaker = hit.get("speaker") or hit.get("role") or "unknown"
    title = hit.get("title") or "untitled"
    msg_index = hit.get("message_index")
    text = hit.get("text") or ""
    timestamp = hit.get("timestamp")
    is_active = hit.get("is_active_branch")

    index_str = f"Message {msg_index}" if msg_index is not None else "Message ?"
    parts = ["[CONVERSATION]", title, f"{index_str} -- {speaker}"]
    if timestamp:
        parts.append(f"Timestamp: {timestamp}")
    if is_active is False:
        parts.append("(inactive branch)")
    provenance = "\n".join(parts)

    return {
        "result_type": hit["result_type"],
        "conversation_id": hit.get("conversation_id"),
        "message_id": hit.get("message_id"),
        "score": hit["score"],
        "provenance": provenance,
        "text": text,
        "role": hit.get("role"),
        "speaker": hit.get("speaker"),
        "message_index": hit.get("message_index"),
        "matched_fields": hit.get("matched_fields", []),
        "timestamp": hit.get("timestamp"),
        "is_active_branch": hit.get("is_active_branch"),
        "source_type": hit.get("source_type"),
    }


class KnowledgeSearchTool:
    """Unified search over document chunks, structured extractions, and conversations.

    Accepts only explicitly allow-listed JSON arguments and converts them
    into a typed request; the model can never influence SQL, FTS MATCH
    expressions, or anything beyond query text, a result limit, and
    validated document filters.
    """

    def __init__(self, retrieval_service: RetrievalService) -> None:
        self._service = retrieval_service

    def search_knowledge(self, arguments: dict[str, object]) -> dict[str, object]:
        """Run one unified search from model-supplied JSON arguments.

        Returns the canonical retrieval envelope. Argument validation errors
        (unknown keys, non-string query, out-of-bounds limit) still raise and
        are surfaced as tool execution errors; only a failure to *execute* the
        query against the service is converted into a safe ``error`` outcome.
        """
        unknown_keys = sorted(set(arguments) - _ALLOWED_ARGUMENT_KEYS)
        if unknown_keys:
            msg = f"unsupported arguments: {', '.join(unknown_keys)}"
            raise ValueError(msg)

        query = _parse_query(arguments)
        limit = _parse_limit(arguments)
        document_filter = _parse_document_filter(arguments)
        try:
            results = self._service.search(
                query=query,
                limit=limit,
                filters=document_filter,
            )
        except Exception:  # noqa: BLE001 - convert operational failure to safe error status
            outcome = build_retrieval_outcome(query, (), limit=limit, error=True)
            return asdict(outcome)

        output: list[dict[str, object]] = []
        for hit in results:
            base: dict[str, object] = {
                "result_type": hit.result_type,
                "document_id": hit.document_id,
                "score": round(hit.score, 4),
                "title": hit.title,
                "text": hit.text,
                "page_number": hit.page_number,
                "matched_fields": list(hit.matched_fields),
                "conversation_id": hit.conversation_id,
                "message_id": hit.message_id,
                "message_index": hit.message_index,
                "role": hit.role,
                "speaker": hit.speaker,
                "timestamp": hit.timestamp,
                "is_active_branch": hit.is_active_branch,
                "source_type": hit.source_type,
            }
            if hit.result_type == "conversation":
                output.append(_format_conversation_result(base))
            else:
                output.append(base)

        outcome = build_retrieval_outcome(query, tuple(output), limit=limit)
        return asdict(outcome)
