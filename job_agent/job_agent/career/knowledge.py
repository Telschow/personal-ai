"""Read-only career knowledge sources.

The fit layer consumes knowledge through the :class:`CareerKnowledge` seam.
``NullCareerKnowledge`` (the default) serves no external knowledge; the
``personal_ai`` provider lazily opens a read-only connection to the parent
knowledge base and searches its durable memory and keyword chunk index.
Nothing here ever writes, and a failed provider degrades to an explicit
unavailable state rather than a silent empty result.
"""

from __future__ import annotations

import contextlib
import sqlite3
import urllib.parse
from collections.abc import Sequence
from typing import Any, Protocol

from .evidence import CareerEvidence, CareerEvidenceType, VerificationLevel
from .evidence_cache import EvidenceQueryCache, get_global_cache

# Canonical memory kinds the fit layer will consider (subset of the parent's
# MemoryKind vocabulary). Others are ignored to keep narrative claims bounded.
DEFAULT_MEMORY_KINDS: tuple[str, ...] = (
    "fact",
    "goal",
    "habit",
    "skill",
    "work",
    "education",
    "identity",
    "relationship",
    "preference",
    "biography",
    "personal_fact",
    "long_term_context",
)


class CareerKnowledgeUnavailable(Exception):
    """The configured knowledge provider could not be reached.

    Raised only when the provider is *configured* but unusable, so callers
    distinguish "provider down" from "provider not configured".
    """


class CareerKnowledge(Protocol):
    def health(self) -> bool:
        """True when a bounded read can run; never raises exceptions."""

    def memory_search(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Search durable memories. Returns attrs-limited evidence."""

    def corpus_search(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Search document chunks. Returns attrs-limited evidence."""

    # Job-aware retrieval methods
    def find_skills(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Find skill evidence matching the query."""
        ...

    def find_projects(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Find project evidence matching the query."""
        ...

    def find_achievements(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Find achievement evidence matching the query."""
        ...

    def find_leadership_experience(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Find leadership evidence matching the query."""
        ...

    def find_domain_experience(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Find domain/industry experience evidence matching the query."""
        ...

    def find_education(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Find education evidence matching the query."""
        ...

    def find_certifications(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Find certification evidence matching the query."""
        ...

    def find_technologies(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Find technology evidence matching the query."""
        ...

    def find_languages(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Find language proficiency evidence matching the query."""
        ...

    def find_career_experience(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Find career experience evidence matching the query."""
        ...

    def find_evidence_by_type(self, evidence_type: CareerEvidenceType, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        """Generic evidence retrieval filtered by evidence type."""
        ...


class NullCareerKnowledge:
    """Knowledge provider that serves nothing (default for offline running)."""

    def health(self) -> bool:
        return False

    def memory_search(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def corpus_search(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    # Job-aware retrieval methods (no-op implementations)
    def find_skills(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def find_projects(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def find_achievements(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def find_leadership_experience(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def find_domain_experience(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def find_education(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def find_certifications(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def find_technologies(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def find_languages(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def find_career_experience(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def find_evidence_by_type(self, evidence_type: CareerEvidenceType, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()


class PersonalAiCareerKnowledge:
    """Lazy, strictly read-only adapter over the parent ``personal_ai`` database.

    Opened with ``?mode=ro`` so a write handle is structurally impossible.
    The parent package is imported lazily (never imported at job_agent import
    time) and only the parent's public store/index surfaces are used.
    """

    def __init__(
        self,
        database_path: str,
        *,
        memory_kinds: Sequence[str] | None = None,
        corpus_limit: int = 12,
        memory_limit: int = 8,
        cache_ttl: float = 300.0,
        cache: EvidenceQueryCache | None = None,
    ) -> None:
        self._path = database_path
        self._kinds: tuple[str, ...] = tuple(memory_kinds) if memory_kinds else DEFAULT_MEMORY_KINDS
        self._corpus_limit = corpus_limit
        self._memory_limit = memory_limit
        self._conn: sqlite3.Connection | None = None
        self._chunk_index: Any = None
        self._memory_retriever: Any = None
        self._parent: Any = None
        self._cache = cache or get_global_cache()
        self._cache_ttl = cache_ttl

    # -- construction ------------------------------------------------------

    def _load(self) -> None:
        if self._conn is not None:
            return
        try:
            import personal_ai.memory.retriever  # noqa: F401
            from personal_ai.memory.retriever import MemoryRetriever
            from personal_ai.memory.store import MemoryStore
            from personal_ai.storage.chunks import SQLiteChunkIndex

            self._parent = True
            quoted = urllib.parse.quote(self._path)
            conn = sqlite3.connect(f"file:{quoted}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            self._conn = conn
            self._chunk_index = SQLiteChunkIndex(conn)
            self._memory_store = MemoryStore(conn)
            self._memory_retriever = MemoryRetriever(self._memory_store)
        except Exception as exc:  # noqa: BLE001
            raise CareerKnowledgeUnavailable(str(exc)) from exc

    # -- CareerKnowledge ----------------------------------------------------

    def health(self) -> bool:
        try:
            self._load()
            assert self._conn is not None
            self._conn.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    def memory_search(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        # Check cache first
        cached = self._cache.get("memory_search", query, limit)
        if cached is not None:
            return cached
        self._load()
        where = limit if limit and limit < self._memory_limit else self._memory_limit
        hits = self._memory_retriever.search(query, scopes=(), limit=max(1, where))
        out: list[CareerEvidence] = []
        for hit in hits:
            memory = hit.memory
            if memory.kind.value not in self._kinds:
                continue
            # Infer evidence type from memory kind
            ev_type = self._infer_evidence_type_from_kind(memory.kind.value, memory.content)
            out.append(
                CareerEvidence(
                    evidence_id=self._evidence_id(memory.content, "personal_ai_memory"),
                    claim=f"{memory.content}",
                    level=VerificationLevel.DOCUMENTED,
                    source="personal_ai_memory",
                    source_type=memory.source_type.value,
                    evidence_type=ev_type,
                    source_location=f"memory:{memory.kind.value}:{memory.memory_id[:8]}",
                    categories=[],
                    keywords=[],
                    confidence=float(memory.confidence),
                    memory_id=memory.memory_id,
                    memory_kind=memory.kind.value,
                    observed_at=memory.created_at or memory.updated_at,
                    raw={"summary": memory.summary},
                )
            )
        result = tuple(out)
        self._cache.set("memory_search", query, limit, result, ttl=self._cache_ttl)
        return result

    def corpus_search(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        # Check cache first
        cached = self._cache.get("corpus_search", query, limit)
        if cached is not None:
            return cached
        self._load()
        where = limit if limit and limit < self._corpus_limit else self._corpus_limit
        hits = self._chunk_index.search(query, limit=max(1, where))
        out: list[CareerEvidence] = []
        for hit in hits:
            # Infer evidence type from source_type and content
            ev_type = self._infer_evidence_type_from_source(hit.source_type, hit.text)
            source_loc = f"corpus:{hit.document_id[:8]}:chunk{hit.chunk_index}"
            out.append(
                CareerEvidence(
                    evidence_id=self._evidence_id(hit.text, "personal_ai_corpus"),
                    claim=hit.text,
                    level=VerificationLevel.DOCUMENTED,
                    source="personal_ai_corpus",
                    source_type=hit.source_type,
                    evidence_type=ev_type,
                    source_location=source_loc,
                    confidence=0.6,
                    document_id=hit.document_id,
                    chunk_id=hit.chunk_id,
                    raw={"rank": hit.rank, "source": hit.source},
                )
            )
        result = tuple(out)
        self._cache.set("corpus_search", query, limit, result, ttl=self._cache_ttl)
        return result

    def _infer_evidence_type_from_kind(self, kind: str, content: str) -> CareerEvidenceType | None:
        """Infer evidence type from memory kind and content."""
        content_l = content.casefold()
        if kind == "skill":
            return CareerEvidenceType.SKILL
        if kind == "work":
            return CareerEvidenceType.CAREER_EXPERIENCE
        if kind == "education":
            return CareerEvidenceType.EDUCATION
        if kind == "goal":
            return None  # goals are not evidence of current capabilities
        if kind == "habit":
            return CareerEvidenceType.SKILL
        if kind == "identity":
            return None
        if kind == "preference":
            return None
        if kind == "relationship":
            return None
        if kind == "personal_fact":
            return None
        if kind == "biography":
            return CareerEvidenceType.CAREER_EXPERIENCE
        if kind == "long_term_context":
            return None
        # Fallback: infer from content
        if any(k in content_l for k in ("skill", "proficient", "expertise", "experience with")):
            return CareerEvidenceType.SKILL
        if any(k in content_l for k in ("project", "built", "developed", "launched")):
            return CareerEvidenceType.PROJECT
        if any(k in content_l for k in ("led", "lead", "managed", "spearheaded")):
            return CareerEvidenceType.LEADERSHIP
        if any(k in content_l for k in ("python", "c++", "java", "kubernetes", "docker", "aws", "sql", "kafka", "spark")):
            return CareerEvidenceType.TECHNOLOGY
        return None

    def _infer_evidence_type_from_source(self, source_type: str, content: str) -> CareerEvidenceType | None:
        """Infer evidence type from corpus source type and content."""
        content_l = content.casefold()
        if source_type == "financial":
            return None
        if source_type == "email":
            return None
        if source_type == "chat":
            return None
        if source_type == "web" and any(k in content_l for k in ("skill", "project", "experience", "led", "built")):
            return CareerEvidenceType.SKILL
        return None

    # ---- Job-aware retrieval methods ----
    # These filter the base search results by evidence_type

    def _filter_by_type(self, results: tuple[CareerEvidence, ...], ev_type: CareerEvidenceType) -> tuple[CareerEvidence, ...]:
        return tuple(r for r in results if r.evidence_type == ev_type)

    def find_skills(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        mem = self.memory_search(query, limit=limit)
        corp = self.corpus_search(query, limit=limit)
        combined = mem + corp
        return self._filter_by_type(combined, CareerEvidenceType.SKILL)[:limit]

    def find_projects(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        mem = self.memory_search(query, limit=limit)
        corp = self.corpus_search(query, limit=limit)
        combined = mem + corp
        return self._filter_by_type(combined, CareerEvidenceType.PROJECT)[:limit]

    def find_achievements(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        mem = self.memory_search(query, limit=limit)
        corp = self.corpus_search(query, limit=limit)
        combined = mem + corp
        return self._filter_by_type(combined, CareerEvidenceType.ACHIEVEMENT)[:limit]

    def find_leadership_experience(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        mem = self.memory_search(query, limit=limit)
        corp = self.corpus_search(query, limit=limit)
        combined = mem + corp
        return self._filter_by_type(combined, CareerEvidenceType.LEADERSHIP)[:limit]

    def find_domain_experience(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        mem = self.memory_search(query, limit=limit)
        corp = self.corpus_search(query, limit=limit)
        combined = mem + corp
        return self._filter_by_type(combined, CareerEvidenceType.DOMAIN_EXPERIENCE)[:limit]

    def find_education(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        mem = self.memory_search(query, limit=limit)
        corp = self.corpus_search(query, limit=limit)
        combined = mem + corp
        return self._filter_by_type(combined, CareerEvidenceType.EDUCATION)[:limit]

    def find_certifications(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        mem = self.memory_search(query, limit=limit)
        corp = self.corpus_search(query, limit=limit)
        combined = mem + corp
        return self._filter_by_type(combined, CareerEvidenceType.CERTIFICATION)[:limit]

    def find_technologies(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        mem = self.memory_search(query, limit=limit)
        corp = self.corpus_search(query, limit=limit)
        combined = mem + corp
        return self._filter_by_type(combined, CareerEvidenceType.TECHNOLOGY)[:limit]

    def find_languages(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        mem = self.memory_search(query, limit=limit)
        corp = self.corpus_search(query, limit=limit)
        combined = mem + corp
        return self._filter_by_type(combined, CareerEvidenceType.LANGUAGE)[:limit]

    def find_career_experience(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        mem = self.memory_search(query, limit=limit)
        corp = self.corpus_search(query, limit=limit)
        combined = mem + corp
        return self._filter_by_type(combined, CareerEvidenceType.CAREER_EXPERIENCE)[:limit]

    def find_evidence_by_type(self, evidence_type: CareerEvidenceType, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        mem = self.memory_search(query, limit=limit)
        corp = self.corpus_search(query, limit=limit)
        combined = mem + corp
        return self._filter_by_type(combined, evidence_type)[:limit]

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
            self._conn = None
        # Clear cache on close to avoid stale data
        self._cache.clear()

    def clear_cache(self) -> None:
        """Explicitly clear the query cache."""
        self._cache.clear()

    def cache_stats(self) -> dict[str, Any]:
        """Return cache statistics for monitoring."""
        return self._cache.stats()

    @staticmethod
    def _evidence_id(claim: str, source: str) -> str:
        import hashlib

        return hashlib.sha256(f"{claim}\x00{source}".encode()).hexdigest()[:16]


def build_knowledge(
    provider: str,
    *,
    database_path: str = "",
    memory_kinds: Sequence[str] | None = None,
) -> CareerKnowledge:
    """Construct the knowledge provider named by ``provider``.

    ``"none"`` (default) returns :class:`NullCareerKnowledge`; ``"personal_ai"``
    returns the lazy read-only adapter, raising :class:`CareerKnowledgeUnavailable`
    immediately only if the parent import or read-only open fails at build time.
    """
    if provider != "personal_ai":
        return NullCareerKnowledge()
    knowledge = PersonalAiCareerKnowledge(database_path, memory_kinds=memory_kinds)
    if not knowledge.health():
        knowledge.close()
        raise CareerKnowledgeUnavailable(f"personal_ai knowledge base unavailable at {database_path}")
    return knowledge
