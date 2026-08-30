"""Registries for agents, skills, and agent-facing tools.

These are explicit, inspectable collections: registration is idempotency- and
duplicate-checked, lookups are deterministic, and the effective capabilities
for an agent are computed as the intersection of its profile with the
registry (never inferred from natural language).
"""

from __future__ import annotations

from personal_ai.agents.models import Agent, AgentTool, Permission, Skill


class RegistryError(Exception):
    """Base class for agent/skill/tool registry errors."""


class DuplicateEntryError(RegistryError):
    """Raised when registering a name already present."""


class UnknownEntryError(RegistryError):
    """Raised when looking up a name not present."""


class AgentRegistry:
    """Registry of :class:`Agent` definitions."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        if agent.id in self._agents:
            raise DuplicateEntryError(f"Agent already registered: {agent.id}")
        self._agents[agent.id] = agent

    def get(self, agent_id: str) -> Agent:
        try:
            return self._agents[agent_id]
        except KeyError:
            raise UnknownEntryError(f"Unknown agent: {agent_id}") from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._agents)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._agents


class SkillRegistry:
    """Registry of :class:`Skill` definitions."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise DuplicateEntryError(f"Skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError:
            raise UnknownEntryError(f"Unknown skill: {name}") from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills


class AgentToolRegistry:
    """Registry of :class:`AgentTool` definitions with a handler callable.

    The handler is the executable bound to the tool; the registry also keeps
    the tool's permission/risk profile for the policy engine.
    """

    def __init__(self) -> None:
        self._tools: dict[str, tuple[AgentTool, object]] = {}

    def register(self, tool: AgentTool, handler: object) -> None:
        if tool.name in self._tools:
            raise DuplicateEntryError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = (tool, handler)

    def get(self, name: str) -> tuple[AgentTool, object]:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownEntryError(f"Unknown tool: {name}") from None

    def tool(self, name: str) -> AgentTool:
        return self.get(name)[0]

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


def effective_permissions(
    agent: Agent, skill_registry: SkillRegistry
) -> frozenset[Permission]:
    """The union of an agent's own permissions and its skills' required ones."""
    perms: set[Permission] = set(agent.permissions)
    for skill_name in agent.skills:
        try:
            skill = skill_registry.get(skill_name)
        except UnknownEntryError:
            continue
        perms.update(skill.required_permissions)
    return frozenset(perms)


__all__ = [
    "AgentRegistry",
    "AgentToolRegistry",
    "DuplicateEntryError",
    "RegistryError",
    "SkillRegistry",
    "UnknownEntryError",
    "effective_permissions",
]
