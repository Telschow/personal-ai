"""Tests for the offline synthetic mode of scripts/evaluate_agent.py."""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path
from types import ModuleType

import pytest

from personal_ai.agents import policy as policy_module
from personal_ai.tools import filesystem as filesystem_module

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "evaluate_agent.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_agent", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their own module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load_script()


def test_every_synthetic_score_meets_the_baseline(script) -> None:
    assert script.main(["--synthetic", "--check"]) == 0


def test_task_set_is_well_formed(script) -> None:
    ids = [task.id for task in script.TASKS]
    assert len(ids) == len(set(ids))
    assert {task.category for task in script.TASKS} == set(script.CATEGORIES)
    baseline = json.loads(script.BASELINE.read_text("utf-8"))
    report = script.run_synthetic()
    assert report.task_counts == baseline["min_task_counts"]


def test_run_is_deterministic_and_writes_scores(script, tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert script.main(["--synthetic", "--output", str(first)]) == 0
    assert script.main(["--synthetic", "--output", str(second)]) == 0
    assert first.read_text("utf-8") == second.read_text("utf-8")
    data = json.loads(first.read_text("utf-8"))
    assert set(data["scores"]) == set(script.CATEGORIES)
    assert data["unexpected_side_effects"] == 0


def test_run_makes_no_network_connection(script, monkeypatch) -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("the synthetic evaluation opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    assert script.run_synthetic().unexpected_side_effects == 0


def test_scripted_client_replays_steps_then_exhausts(script) -> None:
    client = script.ScriptedClient(
        [script.Step(calls=(script.call("a"),)), script.Step(answer="done")]
    )
    assert client.chat([]).tool_calls[0].name == "a"
    assert client.chat([]).content == "done"
    assert client.chat([]).content == "(script exhausted)"
    repeating = script.ScriptedClient([script.Step(answer="x")], repeat_last=True)
    assert [repeating.chat([]).content for _ in range(3)] == ["x", "x", "x"]


def test_check_requires_cli_database_without_synthetic(script) -> None:
    with pytest.raises(SystemExit):
        script.main([])
    with pytest.raises(SystemExit):
        script.main(["--check"])


# --- The gate must actually catch regressions --------------------------------


def _scores(script) -> dict[str, float]:
    return script.run_synthetic().scores


def test_gate_catches_a_policy_bypass(script, monkeypatch) -> None:
    def bypass(self, agent, tool_name, arguments):
        _tool, handler = self._tools.get(tool_name)
        return handler(arguments)

    monkeypatch.setattr(policy_module.PolicyEngine, "execute", bypass)
    report = script.run_synthetic()
    assert report.scores["refusal"] < 1.0
    assert report.scores["approval"] < 1.0
    assert report.unexpected_side_effects > 0
    assert script.main(["--synthetic", "--check"]) == 1


def test_gate_catches_approval_that_is_always_granted(script, monkeypatch) -> None:
    monkeypatch.setattr(
        policy_module.PolicyEngine,
        "_approve",
        lambda self, agent, tool, permission: True,
    )
    report = script.run_synthetic()
    assert report.scores["approval"] < 1.0
    assert report.scores["refusal"] == 1.0


def test_gate_catches_a_disabled_sandbox(script, monkeypatch) -> None:
    monkeypatch.setattr(
        filesystem_module.FilesystemTool,
        "resolve",
        lambda self, path=".": (self.workspace / path).resolve(),
    )
    assert _scores(script)["refusal"] < 1.0


def test_gate_catches_unbounded_tool_results(script, monkeypatch) -> None:
    import personal_ai.agent as agent_module

    monkeypatch.setattr(agent_module, "_bound_result", lambda text: text)
    assert _scores(script)["tool_call_correctness"] < 1.0


def test_gate_catches_a_removed_task(script) -> None:
    baseline = json.loads(script.BASELINE.read_text("utf-8"))
    report = script.run_synthetic(script.TASKS[:-1])
    problems = script.baseline_failures(report, baseline)
    assert any("tasks, baseline requires" in problem for problem in problems)
