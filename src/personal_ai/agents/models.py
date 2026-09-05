"""Domain models for the agent-orchestration layer.

These are the four foundational concepts the orchestration runtime is built
on. They are deliberately distinct and never collapsed into one object:

* :class:`Agent` — WHO performs work (role, system instructions, skills,
  allowed tools, model and permission policy).
* :class:`Skill` — WHAT an agent knows how to do (a declarative, inspectable
  capability description).
* :class:`AgentTool` — WHAT an agent can actually execute, with an explicit
  risk and mutation profile (distinct from the raw handler registry).
* :class:`Permission` / :class:`AccessPolicy` — WHAT an agent is ALLOWED to do,
  enforced in software.

The model-provider abstraction (:class:`ModelRequest`,
:class:`ModelProvider`, :class:`ModelRouter`) lives here too so the
orchestrator requests *capabilities* (reasoning, vision, ...) instead of
hard-coding model names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AutonomyLevel(Enum):
    """Configurable degree of self-direction.

    Defaults stay conservative (home-user mode). Higher levels opt into
    progressively broader local execution; destructive actions always still
    require explicit approval.
    """

    ANSWER_ONLY = 0
    PLAN_AND_ASK = 1
    READ_ONLY_RESEARCH = 2
    WORKSPACE_EXECUTION = 3
    BROADER_AUTOMATION = 4


class RiskLevel(Enum):
    """Risk classification of a tool or action."""

    NONE = "none"
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    NETWORK = "network"
    PRIVILEGED = "privileged"


class Permission(Enum):
    """A discrete permission that can be granted, denied, or gated on approval."""

    # read-only corpus / local reads
    CORPUS_SEARCH = "corpus.search"
    CORPUS_FETCH = "corpus.fetch"
    FILESYSTEM_READ = "filesystem.read"
    # read-only durable memory retrieval
    MEMORY_READ = "memory.read"
    # durable-memory writing (gated on explicit user approval)
    MEMORY_WRITE = "memory.write"
    # read-only workout activity retrieval
    WORKOUT_READ = "workout.read"
    # read-only personal-context overview retrieval (aggregate metadata)
    PERSONAL_CONTEXT_READ = "personal_context.read"
    # workspace mutation
    FILESYSTEM_WRITE = "filesystem.write"
    # execution
    SHELL = "shell.run"
    PYTHON = "python.run"
    # git
    GIT_READ = "git.read"
    GIT_WRITE = "git.write"
    # network
    NETWORK = "network"
    WEB_SEARCH = "web.search"
    # destructive / system
    DESTRUCTIVE = "destructive"
    SYSTEM_CONFIG = "system.config"


class PolicyDecision(Enum):
    """Outcome of evaluating a requested action against a policy."""

    ALLOWED = "allowed"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"


@dataclass(frozen=True, slots=True)
class AgentTool:
    """A tool an agent may execute, with an explicit security profile.

    ``permissions`` lists the :class:`Permission` values this tool requires;
    the policy engine compares a requested tool's permissions against the
    agent's policy. ``risk``, ``mutates_state``, ``accesses_network`` and
    ``reads_private_data`` are metadata for observability and approval
    heuristics and are never a substitute for :class:`AccessPolicy`.
    """

    name: str
    description: str
    permissions: tuple[Permission, ...] = ()
    risk: RiskLevel = RiskLevel.READ
    mutates_state: bool = False
    accesses_network: bool = False
    reads_private_data: bool = False
    deterministic: bool = True
    timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """What an agent is allowed to do.

    A permission is allowed when it is not denied and at least one of the
    wildcard defaults, the granted set, or ``allow_all`` admits it; it is
    gated for approval when ``approval_required`` mentions it. Denied always
    wins (least privilege). ``approval_gate`` is the callable consulted for
    approval decisions; it defaults to a strict deny so a policy without a
    configured approval mechanism never silently approves.
    """

    name: str
    allowed: frozenset[Permission] = frozenset()
    denied: frozenset[Permission] = frozenset()
    approval_required: frozenset[Permission] = frozenset()
    allow_all: bool = False
    autonomy: AutonomyLevel = AutonomyLevel.READ_ONLY_RESEARCH

    def decision_for(self, permission: Permission) -> PolicyDecision:
        if permission in self.denied:
            return PolicyDecision.DENIED
        allowed_anywhere = (
            self.allow_all
            or permission in self.allowed
            or permission in self.approval_required
        )
        if not allowed_anywhere:
            return PolicyDecision.DENIED
        if permission in self.approval_required:
            return PolicyDecision.APPROVAL_REQUIRED
        return PolicyDecision.ALLOWED

    def is_allowed(self, permission: Permission) -> bool:
        return self.decision_for(permission) is PolicyDecision.ALLOWED


@dataclass(frozen=True, slots=True)
class Skill:
    """A declarative capability an agent can perform.

    Skills are plain data so they can be version-controlled and inspected.
    ``tools`` lists the tool names the skill normally relies on; the policy
    engine still independently restricts whether those tools may run for a
    given agent.
    """

    name: str
    purpose: str
    instructions: str = ""
    required_tools: tuple[str, ...] = ()
    required_permissions: tuple[Permission, ...] = ()
    preferred_model_capability: str | None = None
    risk: RiskLevel = RiskLevel.READ


@dataclass(frozen=True, slots=True)
class Agent:
    """WHO performs work. Identity, role, skills, tools, and policy.

    ``skills``/``tools`` name capabilities/permissions; the effective set is
    the intersection with the global policy at execution time. ``model``
    names a configured model (or ``None`` to let the router choose by
    capability).
    """

    id: str
    role: str
    system_instructions: str
    skills: frozenset[str] = frozenset()
    tools: frozenset[str] = frozenset()
    permissions: frozenset[Permission] = frozenset()
    policy: AccessPolicy = field(default_factory=lambda: AccessPolicy(name="default"))
    model: str | None = None
    max_retries: int = 2
    max_tool_calls: int = 50
    timeout_seconds: float = 300.0
