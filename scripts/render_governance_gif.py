"""Render the 30-second governance demo GIF from a real control-plane run.

The GIF shows one story: an agent proposes a network action, the policy engine
blocks it, a human approves, and the audit log records the decision. Nothing on
screen is a hand-drawn status. ``build_scenario`` drives the real
``ControlPlane`` (durable approvals, policy engine, event stream) against a
synthetic task and a probe tool. Every status, count, id and event name in the
transcript is read from what that run returned; only the short labels around
them are fixed text.

The probe tool stands in for an outward-facing action such as sending mail. It
only counts how often its handler ran, so the frames can state "handler runs: 0"
before approval and "handler runs: 1" after, as measured values.

Usage (Pillow is only needed to draw the frames, so it is not a project
dependency):

    uv run --with pillow python scripts/render_governance_gif.py
    uv run python scripts/render_governance_gif.py --text   # transcript only

Writes ``docs/architecture/governance-demo.gif`` by default.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from personal_ai.agents.defs import build_default_agent_registry
from personal_ai.agents.models import (
    AccessPolicy,
    AgentTool,
    AutonomyLevel,
    Permission,
    RiskLevel,
)
from personal_ai.agents.models import Agent as PolicyAgent
from personal_ai.agents.policy import ApprovalRequiredError, PolicyDenialError
from personal_ai.agents.registry import AgentToolRegistry
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.execution import ControlPlane, open_orchestration_store
from personal_ai.execution.models import AgentResult, Plan, PlanStatus, Task, TaskStatus
from personal_ai.execution.planner import Planner

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "docs" / "architecture" / "governance-demo.gif"

TOTAL_MS = 30_000
FIXED_NOW = "2026-01-01T00:00:00+00:00"
AGENT_ID = "outreach"
TOOL_NAME = "send_message"
OBJECTIVE = "Send the synthetic status update to the synthetic recipient"
PROPOSAL = {"to": "synthetic-recipient", "body": "synthetic status update"}

# Event types shown in the audit log scene, in the order the run emitted them.
AUDIT_EVENT_TYPES = (
    "tool.approval_required",
    "approval.requested",
    "approval.gate_required",
    "task.waiting_approval",
    "approval.granted",
    "task.approved",
    "execution.resumed",
    "tool.completed",
    "task.completed",
    "plan.completed",
)


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    kind: str = "text"  # text | dim | propose | block | human | ok


@dataclass(frozen=True, slots=True)
class Scene:
    title: str
    lines: tuple[Line, ...]


@dataclass(frozen=True, slots=True)
class Scenario:
    scenes: tuple[Scene, ...]
    handler_runs_before_approval: int
    handler_runs_after_approval: int
    plan_status_blocked: str
    plan_status_final: str
    audit_event_types: tuple[str, ...]
    audit_details: dict[str, str]


class _OutreachPlanner(Planner):
    """One deterministic task for an agent whose network access needs approval."""

    def deterministic_research_plan(
        self, plan_id: str, objective: str, created_at: str
    ) -> Plan:
        return Plan(
            plan_id=plan_id,
            objective=objective,
            status=PlanStatus.PLANNED,
            task_ids=(f"{plan_id}-send",),
            risk="medium",
            assumptions=(),
            constraints=("approval-gated",),
            created_at=created_at,
            updated_at=created_at,
        )

    def build_tasks(self, plan: Plan, created_at: str) -> tuple[Task, ...]:
        return (
            Task(
                task_id=f"{plan.plan_id}-send",
                plan_id=plan.plan_id,
                title="Send status update",
                description=plan.objective,
                status=TaskStatus.READY,
                assigned_agent=AGENT_ID,
                skill="corpus-research",
                tools=(TOOL_NAME,),
                created_at=created_at,
                updated_at=created_at,
            ),
        )


def _outreach_agent() -> PolicyAgent:
    return PolicyAgent(
        id=AGENT_ID,
        role="Synthetic outreach agent",
        system_instructions="Synthetic. Never act outward without human approval.",
        skills=frozenset({"corpus-research"}),
        policy=AccessPolicy(
            name="outreach-policy",
            allowed=frozenset({Permission.CORPUS_SEARCH}),
            approval_required=frozenset({Permission.NETWORK}),
            autonomy=AutonomyLevel.READ_ONLY_RESEARCH,
        ),
    )


def _audit_detail(event) -> str:  # type: ignore[no-untyped-def]
    """The permission and approver an approval event records, from its payload."""
    payload = event.payload
    if event.event_type == "approval.granted":
        return f"permission={payload.get('permission')}  by={payload.get('agent')}"
    if event.event_type == "approval.gate_required":
        return f"permission={payload.get('permission')}  risk={payload.get('risk')}"
    return ""


def build_scenario() -> Scenario:
    """Run the real approval lifecycle once and format it as four scenes."""
    handler_runs: list[dict[str, object]] = []

    def handler(arguments: dict[str, object]) -> object:
        handler_runs.append(dict(arguments))
        return "sent"

    tools = AgentToolRegistry()
    tools.register(
        AgentTool(
            name=TOOL_NAME,
            description="Synthetic outward-facing action.",
            permissions=(Permission.NETWORK,),
            risk=RiskLevel.NETWORK,
            mutates_state=True,
            accesses_network=True,
        ),
        handler,
    )
    agents = build_default_agent_registry()
    agents.register(_outreach_agent())
    skills = build_default_skill_registry()

    def dispatch(agent, task, policy, ctx):  # type: ignore[no-untyped-def]
        try:
            policy.execute(agent, TOOL_NAME, dict(PROPOSAL))
        except ApprovalRequiredError as exc:
            permission = policy.check_tool(agent, TOOL_NAME).permission
            return AgentResult(
                status="needs_approval",
                summary="paused pending approval",
                metrics={
                    "permission": permission.value if permission else "unknown",
                    "tool": TOOL_NAME,
                    "risk": RiskLevel.NETWORK.value,
                    "reason": str(exc),
                },
                error=str(exc),
            )
        except PolicyDenialError as exc:
            return AgentResult(status="failed", summary="denied", error=str(exc))
        return AgentResult(
            status="completed", summary="message sent", metrics={"summary": "sent"}
        )

    with tempfile.TemporaryDirectory() as scratch:
        connection, store = open_orchestration_store(Path(scratch) / "demo.db")
        try:
            plane = ControlPlane(
                store,
                agents=agents,
                skills=skills,
                tools=tools,
                planner=_OutreachPlanner(agents, skills),
                work_dispatch=dispatch,
                now=lambda: FIXED_NOW,
            )
            plan = plane.create_execution(OBJECTIVE, execution_id="exec-demo")
            task = plane.tasks(plan.plan_id)[0]

            blocked = plane.run_execution(plan.plan_id)
            request = plane.approvals(plan.plan_id)[0]
            runs_before = len(handler_runs)
            task_blocked = plane.tasks(plan.plan_id)[0]

            plane.approve(
                plan.plan_id, task.task_id, str(request["permission"]), approver="human"
            )
            approved = plane.approvals(plan.plan_id)[0]
            final = plane.resume_execution(plan.plan_id)
            runs_after = len(handler_runs)
            events = plane.events(plan.plan_id)
        finally:
            connection.close()

    audit = tuple(
        (e.seq, e.event_type, _audit_detail(e))
        for e in events
        if e.event_type in AUDIT_EVENT_TYPES
    )
    permission = str(request["permission"])
    scenes = (
        Scene(
            "1  AGENT PROPOSES",
            (
                Line(f"agent      {AGENT_ID}", "dim"),
                Line(f"objective  {OBJECTIVE}", "dim"),
                Line(
                    f"proposes   {TOOL_NAME}(to={PROPOSAL['to']!r})",
                    "propose",
                ),
                Line(
                    f"needs      permission '{permission}'  (outward-facing)", "propose"
                ),
            ),
        ),
        Scene(
            "2  POLICY BLOCKS",
            (
                Line("policy engine: approval required for this permission", "block"),
                Line(f"plan status    {blocked.status.value}", "block"),
                Line(f"task status    {task_blocked.status.value}", "block"),
                Line(
                    f"approval       {request['status']}   tool={request['tool']}"
                    f"   permission={request['permission']}",
                    "block",
                ),
                Line(f"handler runs   {runs_before}   (nothing was sent)", "block"),
            ),
        ),
        Scene(
            "3  HUMAN APPROVES",
            (
                Line(
                    f"approve {plan.plan_id} {task.task_id} {permission}  --approver human",
                    "human",
                ),
                Line(f"approval       {approved['status']}", "human"),
                Line("resume execution", "human"),
                Line(f"plan status    {final.status.value}", "ok"),
                Line(
                    f"handler runs   {runs_after}   (sent once, after approval)", "ok"
                ),
            ),
        ),
        Scene(
            "4  AUDIT LOG RECORDS THE DECISION",
            tuple(
                Line(
                    f"{seq:>3}  {event_type:<24}{detail}", "human" if detail else "text"
                )
                for seq, event_type, detail in audit
            ),
        ),
    )
    return Scenario(
        scenes=scenes,
        handler_runs_before_approval=runs_before,
        handler_runs_after_approval=runs_after,
        plan_status_blocked=blocked.status.value,
        plan_status_final=final.status.value,
        audit_event_types=tuple(event_type for _seq, event_type, _detail in audit),
        audit_details={event_type: detail for _seq, event_type, detail in audit},
    )


def transcript(scenario: Scenario) -> str:
    out = []
    for scene in scenario.scenes:
        out.append(f"== {scene.title}")
        out.extend(f"   {line.text}" for line in scene.lines)
    return "\n".join(out)


# --- Drawing (needs Pillow) ----------------------------------------------------

WIDTH, HEIGHT = 1000, 560
BG = (15, 20, 25)
PANEL = (24, 31, 40)
TEXT = (214, 222, 235)
DIM = (107, 122, 144)
COLORS = {
    "text": TEXT,
    "dim": DIM,
    "propose": (122, 162, 247),
    "block": (247, 118, 142),
    "human": (224, 175, 104),
    "ok": (158, 206, 106),
}
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/Library/Fonts/Menlo.ttc",
    "C:/Windows/Fonts/consola.ttf",
)
LINE_MS = 800
SCENE_HOLD_MS = 1500


def _load_font(size: int):  # type: ignore[no-untyped-def]
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    raise SystemExit("no monospaced TrueType font found; edit FONT_CANDIDATES")


def render_frames(scenario: Scenario):  # type: ignore[no-untyped-def]
    """Return ``(images, durations_ms)`` totalling ``TOTAL_MS``."""
    from PIL import Image, ImageDraw

    body = _load_font(20)
    small = _load_font(15)
    frames: list[tuple[object, int]] = []
    steps = [scene.title for scene in scenario.scenes]

    for index, scene in enumerate(scenario.scenes):
        for shown in range(1, len(scene.lines) + 1):
            image = Image.new("RGB", (WIDTH, HEIGHT), BG)
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, WIDTH, 56), fill=PANEL)
            x = 24
            for step_index, title in enumerate(steps):
                label = title.split("  ")[0]
                active = step_index == index
                color = TEXT if active else DIM
                draw.text((x, 18), label, font=body, fill=color)
                if active:
                    draw.rectangle((x, 46, x + 14, 49), fill=COLORS["propose"])
                x += 70
            draw.text((x + 20, 22), "personal-ai governance demo", font=small, fill=DIM)
            draw.text((24, 82), scene.title, font=body, fill=TEXT)
            y = 130
            for line in scene.lines[:shown]:
                draw.text((40, y), line.text, font=body, fill=COLORS[line.kind])
                y += 34
            draw.text(
                (24, HEIGHT - 34),
                "synthetic data   scripted agent   real policy engine and audit log",
                font=small,
                fill=DIM,
            )
            frames.append((image, LINE_MS))
        last_image, _ = frames[-1]
        frames[-1] = (last_image, LINE_MS + SCENE_HOLD_MS)

    used = sum(duration for _image, duration in frames)
    image, duration = frames[-1]
    frames[-1] = (image, max(LINE_MS, duration + (TOTAL_MS - used)))
    return [f for f, _d in frames], [d for _f, d in frames]


def write_gif(scenario: Scenario, output: Path) -> int:
    images, durations = render_frames(scenario)
    output.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        output,
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    return sum(durations)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--text", action="store_true", help="print the transcript only")
    args = parser.parse_args(argv)

    scenario = build_scenario()
    if args.text:
        print(transcript(scenario))
        return 0
    try:
        import PIL  # noqa: F401
    except ImportError:
        print(
            "Pillow is required to draw the frames: "
            "uv run --with pillow python scripts/render_governance_gif.py",
            file=sys.stderr,
        )
        return 1
    total = write_gif(scenario, args.output)
    print(f"wrote {args.output} ({total / 1000:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
