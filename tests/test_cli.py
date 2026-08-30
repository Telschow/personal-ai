"""Tests for the command-line interface."""

from pathlib import Path
from typing import Self

import pytest

from personal_ai import cli


def test_parse_args_requires_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["personal-ai", "What files are here?"],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args()

    assert exc_info.value.code == 2


def test_parse_args_accepts_workspace_and_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--workspace",
            str(tmp_path),
            "What files are here?",
        ],
    )

    args = cli.parse_args()

    assert args.workspace == tmp_path
    assert args.prompt == "What files are here?"


def test_main_rejects_non_directory_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "missing"

    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--workspace",
            str(workspace),
            "What files are here?",
        ],
    )

    with pytest.raises(SystemExit, match="Workspace is not a directory"):
        cli.main()


def test_main_runs_agent_and_prints_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: dict[str, object] = {}

    class FakeRegistry:
        pass

    class FakeClient:
        def __enter__(self) -> Self:
            calls["client_entered"] = True
            return self

        def __exit__(self, *args: object) -> None:
            calls["client_exited"] = True

    class FakeAgent:
        def __init__(
            self, client: object, registry: object, observer: object = None
        ) -> None:
            calls["client"] = client
            calls["registry"] = registry

        def run(self, messages: list[cli.ChatMessage]) -> str:
            calls["messages"] = messages
            return "The answer is here."

    registry = FakeRegistry()
    client = FakeClient()

    monkeypatch.setattr(cli, "create_default_registry", lambda path: registry)
    monkeypatch.setattr(cli, "OllamaClient", lambda model, base_url=None: client)
    monkeypatch.setattr(cli, "Agent", FakeAgent)
    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--workspace",
            str(tmp_path),
            "What files are here?",
        ],
    )

    cli.main()

    assert calls["client_entered"] is True
    assert calls["client_exited"] is True
    assert calls["registry"] is registry
    assert calls["messages"] == [
        cli.ChatMessage(role="user", content="What files are here?")
    ]
    assert capsys.readouterr().out == "The answer is here.\n"
