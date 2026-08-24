"""Command-line interface for the personal AI agent."""

import argparse
from pathlib import Path

from personal_ai.agent import Agent
from personal_ai.ollama_client import ChatMessage, OllamaClient
from personal_ai.retrieval import SearchDocumentsRequest, search_documents
from personal_ai.storage import ChunkStore, connect_database
from personal_ai.tools import create_default_registry

MODEL = "qwen3.5:9b"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the personal AI agent.")
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Directory the agent is allowed to inspect.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="SQLite database holding the ingested knowledge base.",
    )
    parser.add_argument(
        "--search",
        dest="search_query",
        metavar="QUERY",
        help=(
            "Search the knowledge base instead of running the agent; "
            "requires --database."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of search results (default: 10).",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Question or instruction for the agent.",
    )

    args = parser.parse_args()
    if args.search_query is None and args.workspace is None:
        parser.error("the following arguments are required: --workspace")
    if args.search_query is None and args.prompt is None:
        parser.error("the following arguments are required: prompt")
    if args.search_query is not None and args.database is None:
        parser.error("--search requires --database")
    return args


def run_search(query: str, database: Path, limit: int) -> None:
    """Print keyword-search results from the knowledge database."""
    connection = connect_database(database)
    try:
        chunk_store = ChunkStore(connection)
        results = search_documents(
            chunk_store, SearchDocumentsRequest(query=query, limit=limit)
        )
    finally:
        connection.close()

    if not results:
        print("No matching documents.")
        return

    print(f"{len(results)} hits")
    for position, hit in enumerate(results, start=1):
        print(
            f"{position}. rank={hit.rank:.4f} "
            f"chunk={hit.chunk_id} document={hit.document_id}"
        )
        print(f"   {hit.text}")


def main() -> None:
    args = parse_args()

    if args.search_query is not None:
        run_search(args.search_query, args.database, args.limit)
        return

    workspace = args.workspace.resolve()

    if not workspace.is_dir():
        raise SystemExit(f"Workspace is not a directory: {workspace}")

    registry = create_default_registry(workspace)

    with OllamaClient(model=MODEL) as client:
        agent = Agent(client, registry)
        response = agent.run(
            [
                ChatMessage(
                    role="user",
                    content=args.prompt,
                )
            ]
        )

    print(response)


if __name__ == "__main__":
    main()
