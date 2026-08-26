"""Construction of the default tool registry."""

from pathlib import Path

from personal_ai.retrieval import RetrievalService
from personal_ai.storage.chunks import DEFAULT_SEARCH_LIMIT, ChunkStore
from personal_ai.tools.filesystem import FilesystemTool
from personal_ai.tools.knowledge import KnowledgeSearchTool
from personal_ai.tools.registry import ToolDefinition, ToolRegistry
from personal_ai.tools.search import SearchTool


def create_default_registry(
    workspace: Path,
    chunk_store: ChunkStore | None = None,
    retrieval_service: RetrievalService | None = None,
) -> ToolRegistry:
    """Create a registry containing the standard personal-AI tools.

    The ``search_documents`` tool is registered only when a chunk store is
    provided; without a knowledge database there is nothing to search and
    the model is never shown the tool.

    The ``search_knowledge`` tool is registered only when a retrieval
    service is provided; it searches both chunks and structured extractions.
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
                    "Search the ingested personal knowledge base by keyword. "
                    "Returns ranked matching text passages with their "
                    "document identity."
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
                    "Search the personal knowledge base across both document "
                    "text passages and structured extractions (people, "
                    "organizations, projects, goals, topics). Returns ranked "
                    "results with provenance information."
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

    return registry
