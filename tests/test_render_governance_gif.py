"""Tests for scripts/render_governance_gif.py.

The transcript is produced by a real ControlPlane run, so these tests pin the
facts the GIF states: the action does not run before approval, runs once after
a human approves, and the audit log records the approval.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "render_governance_gif.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("render_governance_gif", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their own module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load_script()


def test_action_is_blocked_before_approval_and_runs_once_after(script) -> None:
    scenario = script.build_scenario()
    assert scenario.handler_runs_before_approval == 0
    assert scenario.handler_runs_after_approval == 1
    assert scenario.plan_status_blocked == "needs_approval"
    assert scenario.plan_status_final == "completed"


def test_audit_log_records_the_request_and_the_human_decision(script) -> None:
    scenario = script.build_scenario()
    events = scenario.audit_event_types
    assert events.index("approval.requested") < events.index("approval.granted")
    assert events.index("approval.granted") < events.index("plan.completed")
    assert scenario.audit_details["approval.granted"] == (
        "permission=network  by=human"
    )
    assert "permission=network" in scenario.audit_details["approval.gate_required"]


def test_transcript_states_the_measured_values(script) -> None:
    text = script.transcript(script.build_scenario())
    assert "handler runs   0" in text
    assert "handler runs   1" in text
    assert "needs_approval" in text
    assert "by=human" in text


def test_scenario_is_deterministic(script) -> None:
    first = script.transcript(script.build_scenario())
    second = script.transcript(script.build_scenario())
    assert first == second


def test_text_mode_needs_no_pillow(script, capsys) -> None:
    assert script.main(["--text"]) == 0
    assert "AUDIT LOG RECORDS THE DECISION" in capsys.readouterr().out


def test_gif_runs_thirty_seconds_with_one_frame_per_line(
    script, tmp_path: Path
) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    output = tmp_path / "demo.gif"
    assert script.main(["--output", str(output)]) == 0
    with Image.open(output) as image:
        durations = []
        for index in range(image.n_frames):
            image.seek(index)
            durations.append(int(image.info["duration"]))
    scenario = script.build_scenario()
    assert len(durations) == sum(len(scene.lines) for scene in scenario.scenes)
    assert sum(durations) == script.TOTAL_MS
