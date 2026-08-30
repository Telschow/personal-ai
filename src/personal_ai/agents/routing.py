"""Model-provider abstraction and capability routing.

The orchestrator never hard-codes model names. It requests a *capability*
(simple classification, normal research, complex reasoning, vision, coding,
independent verification) and a complexity hint; the :class:`ModelRouter`
resolves that into a concrete configured model via a :class:`ModelProvider`.

``OllamaProvider`` is one implementation; it only records the configured
model per capability and never downloads or installs anything. Future
providers implement the same small protocol. No cloud provider is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class ModelCapability(Enum):
    """The kind of task a model is selected for."""

    SIMPLE = "simple"  # classification, small decisions
    RESEARCH = "research"  # general-purpose research
    REASONING = "reasoning"  # complex reasoning
    VISION = "vision"  # image understanding
    CODING = "coding"  # software engineering
    VERIFICATION = "verification"  # independent review


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """A request to select a model for a task."""

    capability: ModelCapability
    complexity: str = "normal"  # low | normal | high
    context_size: int | None = None
    requires_tools: bool = True
    local_only: bool = True


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """A resolved model choice."""

    capability: ModelCapability
    provider: str
    model: str
    fallback: bool = False


@runtime_checkable
class ModelProvider(Protocol):
    """A backend that can name a model for a requested capability."""

    def name(self) -> str: ...

    def select(self, request: ModelRequest) -> str: ...


class ModelRoutingError(Exception):
    """Base class for model-routing failures."""


class NoModelAvailableError(ModelRoutingError):
    """Raised when no provider can satisfy a requested capability."""


@dataclass(frozen=True, slots=True)
class CapabilityModel:
    """One provider assignment for one capability."""

    capability: ModelCapability
    model: str
    complexity: str | None = None


class OllamaProvider:
    """A local Ollama backend mapping capabilities to configured models.

    ``capability_models`` is an explicit mapping (capability -> model name).
    The chat model provided at construction is the default fallback for any
    capability not otherwise configured, so behavior stays deterministic and
    never fabricates a model name.
    """

    def __init__(
        self,
        chat_model: str,
        capability_models: dict[ModelCapability, str] | None = None,
    ) -> None:
        self._chat_model = chat_model
        self._capability_models = dict(capability_models or {})
        self._requests: list[ModelRequest] = []

    def name(self) -> str:
        return "ollama"

    @property
    def last_requests(self) -> tuple[ModelRequest, ...]:
        return tuple(self._requests)

    def select(self, request: ModelRequest) -> str:
        self._requests.append(request)
        if not request.local_only:
            msg = "OllamaProvider refuses non-local model selection"
            raise ModelRoutingError(msg)
        return self._capability_models.get(request.capability, self._chat_model)


class ModelRouter:
    """Resolves a :class:`ModelRequest` to a :class:`ModelSelection`.

    Providers are consulted in registration order; the first provider that
    returns a model for the requested capability wins. If no provider can
    satisfy the capability, ``fallback_model`` (a fully qualified
    ``provider:model`` string) is used and ``fallback=True`` is set.
    """

    def __init__(
        self, fallback_model: str, providers: dict[str, ModelProvider] | None = None
    ) -> None:
        self._fallback = fallback_model
        self._providers = dict(providers or {})

    def providers(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def add_provider(self, name: str, provider: ModelProvider) -> None:
        if name in self._providers:
            raise ModelRoutingError(f"Provider already registered: {name}")
        self._providers[name] = provider

    def resolve(self, request: ModelRequest) -> ModelSelection:
        for provider_name, provider in self._providers.items():
            try:
                model = provider.select(request)
            except ModelRoutingError:
                continue
            if model:
                return ModelSelection(
                    capability=request.capability,
                    provider=provider_name,
                    model=model,
                )
        provider, model = self._parse_fallback()
        return ModelSelection(
            capability=request.capability,
            provider=provider,
            model=model,
            fallback=True,
        )

    def available_capabilities(self) -> frozenset[ModelCapability]:
        caps: set[ModelCapability] = set()
        for provider in self._providers.values():
            if isinstance(provider, OllamaProvider):
                caps.update(provider._capability_models)
        return frozenset(caps)

    def _parse_fallback(self) -> tuple[str, str]:
        if ":" in self._fallback:
            provider, model = self._fallback.split(":", 1)
            return provider, model
        return "default", self._fallback
