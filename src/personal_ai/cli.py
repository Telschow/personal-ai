"""Command-line interface for the personal AI agent."""

import argparse
from pathlib import Path

from personal_ai.agent import Agent
from personal_ai.ollama_client import ChatMessage, OllamaClient
from personal_ai.tools import create_default_registry

MODEL = "qwen3.5:9b"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the personal AI agent.")
    parser.add_argument(
        "workspace",
        type=Path,
        help="Directory the agent is allowed to inspect.",
    )
    parser.add_argument(
        "prompt",
        help="Question or instruction for the agent.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
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
