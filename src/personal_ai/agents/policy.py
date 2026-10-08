"""Software-enforced policy engine for agent tool execution.

Permissions are enforced in code, not by model obedience. The engine asks:
*tool + task + agent + policy -> allowed / denied / approval required*.

A tool may only run when every permission it requires is ALLOWED by the
effective policy. Any DENIED permission blocks execution outright; any
APPROVAL_REQUIRED permission pauses execution until a separate approval is
granted. Approval is never inferred from natural language.
"""

from __future__ import annotations

from dataclasses import dataclass

from personal_ai.agents.models import (
    Agent,
    AgentTool,
    Permission,
    PolicyDecision,
)
from personal_ai.agents.registry import (
    AgentRegistry,
    AgentToolRegistry,
    SkillRegistry,
    effective_permissions,
)


class PolicyEnforcementError(Exception):
    """Base class for policy-enforcement failures."""


class PolicyDenialError(PolicyEnforcementError):
    """Raised when a requested tool is denied by policy."""


class ApprovalRequiredError(PolicyEnforcementError):
    """Raised when a requested tool requires approval before execution."""


@dataclass(frozen=True, slots=True)
class PolicyCheck:
    """Result of evaluating one tool request against policy."""

    allowed: bool
    decision: PolicyDecision
    permission: Permission | None = None
    tool: str | None = None
    agent: str | None = None
    detail: str = ""


class PolicyEngine:
    """Evaluates and (optionally) gates tool execution for an agent.

    ``registry`` supplies the :class:`AgentTool` security profiles and the
    handlers. ``approver`` is a callable that, given the agent id, tool name,
    and permission, returns True to grant approval. If ``None``, any
    approval-required permission is recorded as pending and never executes.
    """

    def __init__(
        self,
        registry: AgentToolRegistry,
        agent_registry: AgentRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        approver: object | None = None,
    ) -> None:
        self._tools = registry
        self._agents = agent_registry or AgentRegistry()
        self._skills = skill_registry or SkillRegistry()
        self._approver = approver
        self._decisions: list[PolicyCheck] = []

    @property
    def decisions(self) -> tuple[PolicyCheck, ...]:
        return tuple(self._decisions)

    def check_tool(self, agent: Agent, tool_name: str) -> PolicyCheck:
        """Evaluate whether ``agent`` may execute ``tool_name``.

        Records the decision for auditability. Raises on unknown tool names;
        tool existence is checked separately so a typo is surfaced rather
        than silently treated as a policy decision.
        """
        tool: AgentTool
        tool, _handler = self._tools.get(tool_name)
        perms = self._permissions_for(agent)
        for permission in tool.permissions:
            decision = perms.decision_for(permission)
            check = PolicyCheck(
                allowed=decision is PolicyDecision.ALLOWED,
                decision=decision,
                permission=permission,
                tool=tool_name,
                agent=agent.id,
            )
            self._decisions.append(check)
            if decision is PolicyDecision.APPROVAL_REQUIRED:
                if self._approve(agent, tool_name, permission):
                    continue
                return check
            if decision is PolicyDecision.DENIED:
                return check
        allowed_check = PolicyCheck(
            allowed=True,
            decision=PolicyDecision.ALLOWED,
            tool=tool_name,
            agent=agent.id,
        )
        self._decisions.append(allowed_check)
        return allowed_check

    def evaluate(self, agent: Agent, tool_name: str) -> PolicyDecision:
        return self.check_tool(agent, tool_name).decision

    def execute(
        self, agent: Agent, tool_name: str, arguments: dict[str, object]
    ) -> object:
        """Run ``tool_name`` only after the policy fully allows it.

        This is the only route by which a tool handler runs; callers must go
        through here so denial/approval is enforced in a single place.
        """
        check = self.check_tool(agent, tool_name)
        if check.decision is not PolicyDecision.ALLOWED:
            if check.decision is PolicyDecision.DENIED:
                detail = check.detail or f"permission {check.permission.value} denied"
                raise PolicyDenialError(detail)
            msg = f"tool {tool_name!r} requires approval"
            raise ApprovalRequiredError(msg)
        _tool, handler = self._tools.get(tool_name)
        return handler(arguments)

    def _permissions_for(self, agent: Agent) -> object:
        merged = effective_permissions(agent, self._skills)
        base = agent.policy.allowed | merged
        allowed = frozenset(base)
        denied = agent.policy.denied
        approval = agent.policy.approval_required
        allow_all = agent.policy.allow_all
        # Rebuild the policy with merged allowed set so skill requirements are
        # taken into account alongside the agent's own policy.
        from personal_ai.agents.models import AccessPolicy

        return AccessPolicy(
            name=agent.policy.name,
            allowed=allowed,
            denied=denied,
            approval_required=approval,
            allow_all=allow_all,
            autonomy=agent.policy.autonomy,
        )

    def _approve(self, agent: Agent, tool_name: str, permission: Permission) -> bool:
        if self._approver is None:
            return False
        return bool(self._approver(agent.id, tool_name, permission.value))
