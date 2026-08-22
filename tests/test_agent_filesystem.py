"""Integration tests for the agent and default filesystem tools."""

from pathlib import Path

from personal_ai.agent import Agent
from personal_ai.ollama_client import ChatMessage, ChatResponse, ToolCall
from personal_ai.tools import create_default_registry


class FakeOllamaClient:
    """Deterministic Ollama substitute for agent integration tests."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self.responses = iter(responses)
        self.conversations: list[list[ChatMessage]] = []
        self.tool_schemas: list[list[dict[str, object]] | None] = []

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, object]] | None = None,
    ) -> ChatResponse:
        self.tool_schemas.append(tools)
        self.conversations.append(list(messages))
        return next(self.responses)


def test_agent_can_execute_default_list_directory_tool(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").write_text("alpha")
    (tmp_path / "beta.txt").write_text("beta")
    (tmp_path / "documents").mkdir()

    client = FakeOllamaClient(
        [
            ChatResponse(
                content="",
                model="test-model",
                done=True,
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="list_directory",
                        arguments={},
                    ),
                ),
            ),
            ChatResponse(
                content="The workspace contains alpha.txt, beta.txt, and documents.",
                model="test-model",
                done=True,
            ),
        ]
    )

    agent = Agent(client, create_default_registry(tmp_path))

    result = agent.run([ChatMessage(role="user", content="What is in the workspace?")])

    assert result == "The workspace contains alpha.txt, beta.txt, and documents."
    assert len(client.conversations) == 2

    second_request = client.conversations[1]
    assert second_request[-1] == ChatMessage(
        role="tool",
        content=(
            '[{"name": "alpha.txt", "type": "file"}, '
            '{"name": "beta.txt", "type": "file"}, '
            '{"name": "documents", "type": "directory"}]'
        ),
    )


def test_agent_passes_default_tool_schema_to_model(tmp_path: Path) -> None:
    client = FakeOllamaClient(
        [
            ChatResponse(
                content="No files.",
                model="test-model",
                done=True,
            )
        ]
    )

    agent = Agent(client, create_default_registry(tmp_path))

    agent.run([ChatMessage(role="user", content="List the files.")])

    assert client.tool_schemas == [
        [
            {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "List files and directories in the workspace.",
                    "parameters": {
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
                },
            }
        ]
    ]


def test_filesystem_tool_cannot_escape_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")

    client = FakeOllamaClient(
        [
            ChatResponse(
                content="",
                model="test-model",
                done=True,
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="list_directory",
                        arguments={"path": ".."},
                    ),
                ),
            ),
            ChatResponse(
                content="I could not access that directory.",
                model="test-model",
                done=True,
            ),
        ]
    )

    agent = Agent(client, create_default_registry(tmp_path))

    result = agent.run([ChatMessage(role="user", content="List the parent directory.")])

    assert result == "I could not access that directory."
    assert "Path escapes workspace" in client.conversations[1][-1].content
    assert "secret" not in client.conversations[1][-1].content
