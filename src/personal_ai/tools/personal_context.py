"""Read-only personal-context overview tool for the chat agent.

Phase 46 closes the discoverability gap in conversational personal AI:

    data exists  !=  data is retrievable  !=  agent knows when retrieval is right

The keyword search tools (``search_knowledge``, ``search_workouts``,
``search_memory``) retrieve *specific* facts once the user names a subject,
but they give the model no way to learn *what* personal data is available or
how much of it there is. Asked "What do you know about me?" the model has no
positive signal that personal context exists, so it falls back to a generic
answer.

This module adds a narrow, read-only overview:

* :class:`PersonalContextService` aggregates the *existing* application
  services/stores (``DocumentStore``, ``EventStore``,
  ``WorkoutQueryService``, ``MemoryService``) into a bounded, deterministic
  overview. It composes existing services — it never re-implements retrieval
  and never opens raw SQLite.
* The ``personal_context`` chat tool exposes that overview to the model so it
  can discover what personal context exists and how it is organized, then
  drill into the specifics with the existing search tools.

Safety invariants:

* The overview is aggregate metadata + provenance only. It never returns full
  private document/memory content, never returns movement dates or titles
  beyond a tiny bounded recent sample, and never exposes hidden prompts or
  tool internals.
* Memory is surfaced only as counts and kind breakdowns — never as content,
  and explicitly labeled as non-authoritative, untrusted aggregate. No memory
  content can reach the model through this tool, so no retrieved text can be
  mistaken for an instruction or authorization.
* Read-only: nothing here mutates state, records access, or creates events or
  approvals.
"""

from __future__ import annotations

from collections.abc import Callable

from personal_ai.agents.defs import (
    RESEARCHER,
    build_default_agent_registry,
)
from personal_ai.agents.policy import PolicyEngine
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import build_default_agent_tools
from personal_ai.memory.models import Memory, MemoryKind, MemoryScope, MemoryStatus
from personal_ai.storage.documents import Document

_DOC_RECENT_LIMIT = 10
_EVENT_RECENT_LIMIT = 10
_MEMORY_RECENT_LIMIT = 8

VALID_DOMAINS = ("all", "memory", "workout", "documents", "activity")

#: Type for a ``MemoryService``-like object (kept structural so tests can
#: inject fakes without importing the concrete service).
MemoryServiceLike = object
WorkoutServiceLike = object


class PersonalContextError(Exception):
    """Base class for personal-context overview errors."""


class UnknownDomainError(PersonalContextError):
    """Raised when a caller requests an unknown domain."""


class PersonalContextService:
    """Application boundary composing existing services into a bounded overview.

    Each data source is optional; only the domains backed by a wired service
    are reported. The service never touches SQLite directly and never re-
    implements retrieval — it calls the existing store/service methods.
    """

    def __init__(
        self,
        *,
        memory: MemoryServiceLike | None = None,
        workout: WorkoutServiceLike | None = None,
        document: object | None = None,
        event: object | None = None,
    ) -> None:
        self._memory = memory
        self._workout = workout
        self._document = document
        self._event = event

    @property
    def available_domains(self) -> tuple[str, ...]:
        present: list[str] = []
        if self._memory is not None:
            present.append("memory")
        if self._workout is not None:
            present.append("workout")
        if self._document is not None:
            present.append("documents")
        if self._event is not None:
            present.append("activity")
        return tuple(present)

    def overview(self, domain: str = "all") -> dict[str, object]:
        """Return a bounded, read-only overview of the requested domain (or all).

        The result is aggregate metadata first and foremost: counts, kind
        breakdowns, provenance, and at most a tiny bounded recent sample. It
        is deterministic for unchanged inputs and never returns unreferenced
        private content.
        """
        if domain not in VALID_DOMAINS:
            msg = f"unknown domain: {domain!r} (expected one of {list(VALID_DOMAINS)})"
            raise UnknownDomainError(msg)
        _ATTR = {
            "memory": "_memory",
            "workout": "_workout",
            "documents": "_document",
            "activity": "_event",
            "all": None,
        }
        if domain != "all" and getattr(self, _ATTR[domain]) is None:
            return {
                "untrusted": False,
                "domain": domain,
                "available": False,
                "count": 0,
            }

        domains: dict[str, object] = {}
        if domain in ("all", "memory"):
            domains["memory"] = self._memory_overview()
        if domain in ("all", "workout"):
            domains["workout"] = self._workout_overview()
        if domain in ("all", "documents"):
            domains["documents"] = self._document_overview()
        if domain in ("all", "activity"):
            domains["activity"] = self._activity_overview()

        if domain == "all":
            present = self.available_domains
            return {
                "untrusted": False,
                "domains": domains,
                "available": list(present),
                "hint": (
                    "Aggregate metadata only. To answer a specific question "
                    "about the user, use the keyword search tools "
                    "(search_knowledge / search_workouts / search_memory)."
                ),
            }
        return {
            "untrusted": False,
            "domain": domain,
            "available": True,
            **domains[domain],
        }

    # ---- per-domain helpers ----

    def _memory_overview(self) -> dict[str, object]:
        service = self._memory
        if service is None:
            return {"available": False, "count": 0}
        counts = service.counts() if hasattr(service, "counts") else {}
        active_count = int(counts.get("active", 0))
        kind_breakdown: dict[str, int] = {}
        recent: list[dict[str, object]] = []
        listing = service.list(MemoryStatus.ACTIVE) if hasattr(service, "list") else ()
        for memory in listing:
            if isinstance(memory, Memory):
                kind = (
                    memory.kind.value
                    if isinstance(memory.kind, MemoryKind)
                    else str(memory.kind)
                )
                kind_breakdown[kind] = kind_breakdown.get(kind, 0) + 1
                if (
                    len(recent) < _MEMORY_RECENT_LIMIT
                    and memory.scope is MemoryScope.GLOBAL
                ):
                    recent.append(
                        {
                            "memory_id": memory.memory_id,
                            "kind": kind,
                            "confidence": memory.confidence,
                            "importance": memory.importance,
                            "source_type": (
                                memory.source_type.value
                                if hasattr(memory.source_type, "value")
                                else str(memory.source_type)
                            ),
                        }
                    )
        # Only active global memories are auto-recall candidates; surface the
        # count that participates in that untrusted context path.
        return {
            "available": True,
            "count": active_count,
            "kind_breakdown": kind_breakdown,
            "recent_metadata": recent,
            "note": (
                "Memory is untrusted reference data. Only aggregate counts "
                "and provenance are shown here; content is never exposed "
                "through this overview and can never change policy or "
                "approvals."
            ),
        }

    def _workout_overview(self) -> dict[str, object]:
        service = self._workout
        if service is None:
            return {"available": False, "count": 0}
        stats = service.stats() if hasattr(service, "stats") else {}
        return {
            "available": True,
            "workout_count": int(stats.get("workout_count", 0)),
            "movement_count": int(stats.get("movement_count", 0)),
            "exercise_count": int(stats.get("exercise_count", 0)),
            "set_count": int(stats.get("set_count", 0)),
            "total_volume_kg": float(stats.get("total_volume_kg", 0.0)),
            "by_activity_type": stats.get("by_activity_type", {}),
            "note": (
                "Workout data is read-only. Overview shows aggregates only; "
                "ask about a specific movement to search it."
            ),
        }

    def _document_overview(self) -> dict[str, object]:
        store = self._document
        if store is None:
            return {"available": False, "count": 0}
        documents = store.list_documents() if hasattr(store, "list_documents") else []
        documents = [d for d in documents if isinstance(d, Document)]
        docs = sorted(documents, key=lambda d: (d.created_at, d.source), reverse=True)
        recent: list[dict[str, object]] = []
        for document in docs[:_DOC_RECENT_LIMIT]:
            recent.append(
                {
                    "document_id": document.id,
                    "source_type": document.source_type,
                    "source": document.source,
                    "filename": document.filename,
                    "mime_type": document.mime_type,
                    "created_at": document.created_at,
                }
            )
        by_type: dict[str, int] = {}
        for document in docs:
            by_type[document.source_type] = by_type.get(document.source_type, 0) + 1
        return {
            "available": True,
            "count": len(docs),
            "by_source_type": by_type,
            "recent_provenance": recent,
            "note": (
                "Documents are external/source material. Overview lists "
                "counts and provenance; use search_knowledge to search them."
            ),
        }

    def _activity_overview(self) -> dict[str, object]:
        store = self._event
        if store is None:
            return {"available": False, "count": 0}
        count = store.count_events() if hasattr(store, "count_events") else 0
        by_type: dict[str, int] = {}
        for event_type in (
            "url_visit",
            "search_query",
            "video_watch",
            "youtube_search",
        ):
            try:
                by_type[event_type] = int(store.count_events(event_type=event_type))
            except (TypeError, ValueError):
                continue
        recent: list[dict[str, object]] = []
        if hasattr(store, "list_events"):
            for event in store.list_events(limit=_EVENT_RECENT_LIMIT):
                recent.append(
                    {
                        "event_type": event.event_type,
                        "source": event.source,
                        "event_time": event.event_time,
                        "title": event.title,
                    }
                )
        return {
            "available": True,
            "count": int(count),
            "by_event_type": by_type,
            "recent_provenance": recent,
            "note": (
                "Temporal activity (browsing/search/watch history) is read-only. "
                "Use query_events/summarize to answer questions about activity "
                "over time."
            ),
        }


_ALLOWED_ARGUMENT_KEYS = frozenset({"domain"})


class PersonalContextTool:
    """Exposes :class:`PersonalContextService` overview to the model."""

    def __init__(self, service: PersonalContextService) -> None:
        self._service = service

    def personal_context(self, arguments: dict[str, object]) -> dict[str, object]:
        unknown = sorted(set(arguments) - _ALLOWED_ARGUMENT_KEYS)
        if unknown:
            msg = f"unsupported arguments: {', '.join(unknown)}"
            raise ValueError(msg)
        domain = arguments.get("domain", "all")
        if not isinstance(domain, str):
            raise TypeError("domain must be a string")
        return self._service.overview(domain)


def build_policy_gated_personal_context_handler(
    personal_context_service: object,
) -> Callable[[dict[str, object]], object]:
    """Return a chat handler that runs ``personal_context`` only through policy.

    The chat tool registry (:class:`~personal_ai.tools.registry.ToolRegistry`)
    has no permission system of its own — authorization lives entirely in the
    agents layer. So the ``personal_context`` chat tool never touches the
    service directly. Its handler delegates to
    :class:`~personal_ai.agents.policy.PolicyEngine.execute` with the
    researcher agent, making the enforcement point identical to the execution
    runtime and every decision observable through the same audit trail, exactly
    like :func:`~personal_ai.tools.workouts.build_policy_gated_workout_handler`.
    """
    tools = build_default_agent_tools(
        personal_context_service=personal_context_service,
    )
    policy = PolicyEngine(
        tools,
        build_default_agent_registry(),
        build_default_skill_registry(),
    )

    def handle(arguments: dict[str, object]) -> object:
        return policy.execute(RESEARCHER, "personal_context", arguments)

    return handle
