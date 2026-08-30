"""Manual/private evaluation of the real Personal AI agent.

This is a manual evaluation harness (mirroring the other scripts/validate_*
helpers). It is deliberately NOT part of the automated pytest suite: it
requires a live, locally hosted Ollama model and the real personal corpus,
so it must never be a mandatory test dependency.

It drives the real Agent against a locally built corpus database (events +
conversations) and records each model round's requested tool calls and the
model's final answer, so one can judge tool selection, temporal bounds,
cross-domain chaining, and synthesis quality on the actual model.

This script changes no production code. It only subclasses OllamaClient to
observe calls (delegating everything to the parent) and replicates
cli._connect_agent_registry to wire the same tool registry the CLI uses.

Usage:

    uv run python scripts/evaluate_agent.py --database /tmp/ph18_corpus.db

It prints a per-question transcript: each model round (requested tool-call
names + arguments, and any narration) followed by the final answer.
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path

from personal_ai.agent import Agent
from personal_ai.ollama_client import ChatMessage, ChatResponse, OllamaClient
from personal_ai.retrieval import RetrievalService
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentStore,
    EventStore,
    ExtractionStore,
    connect_database,
)
from personal_ai.tools import ToolRegistry, create_default_registry

MODEL = "qwen3.5:9b"

QUESTIONS: tuple[str, ...] = (
    (
        "What did I watch about career interviews, and what do my notes say "
        "about career interviews?"
    ),
    "What was I researching about career in January 2026?",
    (
        "What was I interested in during January 2026, and did I have notes "
        "about those topics?"
    ),
    (
        "What was I researching around BCG in January 2026, and what did I "
        "watch about it?"
    ),
    (
        "Summarize my January 2026 activity: what I searched, what I watched, "
        "and what I was working on."
    ),
    "What did I know about BCG versus what was I doing online about BCG?",
)


class TraceClient(OllamaClient):
    """OllamaClient that records, but never alters, each model round."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.rounds: list[dict[str, object]] = []

    def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, object]] | None = None,
        *,
        think: bool | None = None,
        format: dict[str, object] | None = None,
    ) -> ChatResponse:
        response = super().chat(
            messages,
            tools,
            think=think,
            format=format,
        )
        self.rounds.append(
            {
                "tool_calls": [
                    {"name": call.name, "arguments": call.arguments}
                    for call in response.tool_calls
                ],
                "content": response.content,
            }
        )
        return response


def _build_registry(workspace: Path, database: Path) -> tuple[ToolRegistry, sqlite3.Connection]:
    connection = connect_database(database)
    document_store = DocumentStore(connection)
    chunk_store = ChunkStore(connection)
    extraction_store = ExtractionStore(connection)
    conversation_store = ConversationStore(connection)
    event_store = EventStore(connection)
    retrieval_service = RetrievalService(
        chunk_store,
        extraction_store,
        document_store,
        conversation_store,
    )
    registry = create_default_registry(
        workspace,
        chunk_store=chunk_store,
        retrieval_service=retrieval_service,
        event_store=event_store,
    )
    return registry, connection


def run_question(client: TraceClient, registry, prompt: str, workspace: Path) -> str:
    agent = Agent(client, registry)
    started = time.monotonic()
    answer = agent.run(
        [ChatMessage(role="user", content=prompt)]
    )
    elapsed = time.monotonic() - started
    print(f"\n{'='*78}")
    print(f"QUESTION: {prompt}")
    print(f"latency: {elapsed:.1f}s  | rounds: {len(client.rounds)}")
    for index, round_ in enumerate(client.rounds, start=1):
        calls = round_["tool_calls"]
        content = round_["content"]
        if content:
            preview = content.replace("\n", " ")
            print(f"  [round {index}] narration: {preview[:160]}")
        if calls:
            for call in calls:
                print(f"  [round {index}] -> {call['name']}({call['arguments']})")
        elif index == len(client.rounds):
            print(f"  [round {index}] (final answer) {answer[:200].replace(chr(10),' ')}...")
    print(f"\n--- FINAL ANSWER ---\n{answer}")
    return answer


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manually evaluate the real agent on the real corpus."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path("/tmp/ph18_ws"))
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--question", type=int, default=None)
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    registry, connection = _build_registry(workspace, args.database.resolve())
    try:
        with TraceClient(model=args.model, timeout=300.0) as client:
            indexes = [args.question] if args.question is not None else range(1, len(QUESTIONS) + 1)
            for idx in indexes:
                client.rounds = []
                run_question(client, registry, QUESTIONS[idx - 1], workspace)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
