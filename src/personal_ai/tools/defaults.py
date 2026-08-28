"""Construction of the default tool registry."""

from pathlib import Path

from personal_ai.retrieval import RetrievalService
from personal_ai.storage.chunks import DEFAULT_SEARCH_LIMIT, ChunkStore
from personal_ai.storage.events import EventStore
from personal_ai.tools.events import EventQueryTool
from personal_ai.tools.filesystem import FilesystemTool
from personal_ai.tools.knowledge import KnowledgeSearchTool
from personal_ai.tools.registry import ToolDefinition, ToolRegistry
from personal_ai.tools.search import SearchTool


def create_default_registry(
    workspace: Path,
    chunk_store: ChunkStore | None = None,
    retrieval_service: RetrievalService | None = None,
    event_store: EventStore | None = None,
) -> ToolRegistry:
    """Create a registry containing the standard personal-AI tools.

    The ``search_documents`` tool is registered only when a chunk store is
    provided; without a knowledge database there is nothing to search and
    the model is never shown the tool. It is a narrow chunks-only search
    primitive; ``search_knowledge`` is the broader knowledge search.

    The ``search_knowledge`` tool is registered only when a retrieval
    service is provided; it searches chunks, structured extractions, and
    conversation messages.

    The ``query_events`` tool is registered only when an event store is
    provided; it answers structural temporal-event (browsing/search) queries.
    """
    filesystem = FilesystemTool(workspace)
    registry = ToolRegistry()

    registry.register(
        ToolDefinition(
            name="list_directory",
            description="List files and directories in the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Relative directory path. Defaults to the workspace root."
                        ),
                    }
                },
                "required": [],
            },
            handler=filesystem.list_directory,
        )
    )

    if chunk_store is not None:
        search = SearchTool(chunk_store)
        registry.register(
            ToolDefinition(
                name="search_documents",
                description=(
                    "Narrow search over the indexed document/chunk corpus "
                    "only. Returns ranked document passages (chunk text) "
                    "with their document identity. Use this only when the "
                    "user specifically wants document/chunk content. It does "
                    "NOT search conversations, notes, or structured "
                    "extractions, and it is not the general-purpose "
                    "knowledge search. For broad questions about what the "
                    "user wrote, discussed, researched, or recorded, prefer "
                    "search_knowledge, which also covers documents."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Keyword query. All terms must appear in a match."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                f"Maximum number of results. Defaults to "
                                f"{DEFAULT_SEARCH_LIMIT}."
                            ),
                        },
                        "filter": {
                            "type": "object",
                            "description": ("Optional document metadata constraints."),
                            "properties": {
                                "source_types": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Only include documents of these types."
                                    ),
                                },
                                "created_after": {
                                    "type": "string",
                                    "description": (
                                        "Inclusive ISO-8601 lower bound on "
                                        "document creation time."
                                    ),
                                },
                                "created_before": {
                                    "type": "string",
                                    "description": (
                                        "Inclusive ISO-8601 upper bound on "
                                        "document creation time."
                                    ),
                                },
                                "modified_after": {
                                    "type": "string",
                                    "description": (
                                        "Inclusive ISO-8601 lower bound on "
                                        "document modification time."
                                    ),
                                },
                                "modified_before": {
                                    "type": "string",
                                    "description": (
                                        "Inclusive ISO-8601 upper bound on "
                                        "document modification time."
                                    ),
                                },
                            },
                        },
                    },
                    "required": ["query"],
                },
                handler=search.search_documents,
            )
        )

    if retrieval_service is not None:
        knowledge = KnowledgeSearchTool(retrieval_service)
        registry.register(
            ToolDefinition(
                name="search_knowledge",
                description=(
                    "General-purpose search over durable personal knowledge: "
                    "document/chunk passages, structured extractions (people, "
                    "organizations, projects, goals, topics), and "
                    "conversation messages. Returns ranked results with "
                    "provenance information. This is the preferred knowledge "
                    "tool for questions about what the user knows, wrote, "
                    "discussed, researched, or recorded, including when "
                    "documents are part of the answer. It is broader than "
                    "search_documents (a narrow chunks-only search) and "
                    "complements query_events for activity-history questions. "
                    "The optional 'filter' created_after/created_before "
                    "bounds apply to document results and to conversation "
                    "messages. A single question may require both tools when "
                    "it spans knowledge and activity history."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Search query. Matches against document text "
                                "and structured extraction fields."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                f"Maximum number of results. Defaults to "
                                f"{DEFAULT_SEARCH_LIMIT}."
                            ),
                        },
                        "filter": {
                            "type": "object",
                            "description": ("Optional document metadata constraints."),
                            "properties": {
                                "source_types": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Only include documents of these types."
                                    ),
                                },
                                "created_after": {
                                    "type": "string",
                                    "description": (
                                        "Inclusive ISO-8601 lower bound on "
                                        "document creation time."
                                    ),
                                },
                                "created_before": {
                                    "type": "string",
                                    "description": (
                                        "Inclusive ISO-8601 upper bound on "
                                        "document creation time."
                                    ),
                                },
                                "modified_after": {
                                    "type": "string",
                                    "description": (
                                        "Inclusive ISO-8601 lower bound on "
                                        "document modification time."
                                    ),
                                },
                                "modified_before": {
                                    "type": "string",
                                    "description": (
                                        "Inclusive ISO-8601 upper bound on "
                                        "document modification time."
                                    ),
                                },
                            },
                        },
                    },
                    "required": ["query"],
                },
                handler=knowledge.search_knowledge,
            )
        )

    if event_store is not None:
        events = EventQueryTool(event_store)
        registry.register(
            ToolDefinition(
                name="query_events",
                description=(
                    "Query the temporal event store (browsing, search, and "
                    "YouTube watch/search history). Choose an 'operation': "
                    "'events' returns detailed event rows; 'activity_summary' "
                    "returns counts grouped by event_type and source; "
                    "'top_searches' returns the most frequent search queries; "
                    "'top_channels' and 'top_videos' return the most watched "
                    "YouTube channels/videos; 'activity_by_bucket' returns "
                    "activity counts grouped by a UTC time bucket (day, week, "
                    "or month). All operations accept optional inclusive "
                    "ISO-8601 start_time/end_time, source, event_type, and a "
                    "literal 'keyword' substring filter (against title/URL/"
                    "search query/channel). Use this to answer questions "
                    "about what the user searched for, browsed, or watched, "
                    "or to summarize activity over a time period. When "
                    "summarizing activity, prefer the aggregate operations "
                    "(top_searches, top_videos, top_channels, "
                    "activity_summary) over the raw 'events' operation. A "
                    "single question may also need search_knowledge when it "
                    "spans both activity history and the user's durable "
                    "knowledge/notes."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": [
                                "events",
                                "activity_summary",
                                "top_searches",
                                "top_channels",
                                "top_videos",
                                "activity_by_bucket",
                            ],
                            "description": (
                                "Which query to run. Defaults to 'events'."
                            ),
                        },
                        "start_time": {
                            "type": "string",
                            "description": (
                                "Inclusive ISO-8601 lower bound on the event "
                                "time (e.g. 2026-01-01T00:00:00+00:00)."
                            ),
                        },
                        "end_time": {
                            "type": "string",
                            "description": (
                                "Inclusive ISO-8601 upper bound on the event "
                                "time (e.g. 2026-02-01T00:00:00+00:00)."
                            ),
                        },
                        "event_type": {
                            "type": "string",
                            "description": (
                                "Restrict to an event type: 'search_query', "
                                "'url_visit', 'video_watch', "
                                "or 'youtube_search'. For top_searches only "
                                "search types are allowed; for top_channels / "
                                "top_videos only 'video_watch'."
                            ),
                        },
                        "source": {
                            "type": "string",
                            "description": (
                                "Restrict to a source, e.g. 'chrome_history' "
                                "or 'youtube'."
                            ),
                        },
                        "keyword": {
                            "type": "string",
                            "description": (
                                "Optional literal case-insensitive substring "
                                "filter applied to each event's title, URL, "
                                "search query, and channel name (e.g. "
                                "'career' or 'interview'). It is an exact "
                                "text filter, not semantic or fuzzy search."
                            ),
                        },
                        "bucket": {
                            "type": "string",
                            "enum": ["day", "week", "month"],
                            "description": (
                                "Time bucket for activity_by_bucket. "
                                "Defaults to 'month'."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "Maximum number of results. Defaults to 100 "
                                "for events/activity_by_bucket and 10 for "
                                "the top_* operations."
                            ),
                        },
                    },
                    "required": [],
                },
                handler=events.query_events,
            )
        )

    return registry
