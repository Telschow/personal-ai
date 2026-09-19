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

from .evidence import CareerEvidence, VerificationLevel

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


class NullCareerKnowledge:
    """Knowledge provider that serves nothing (default for offline running)."""

    def health(self) -> bool:
        return False

    def memory_search(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        return ()

    def corpus_search(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
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
    ) -> None:
        self._path = database_path
        self._kinds: tuple[str, ...] = tuple(memory_kinds) if memory_kinds else DEFAULT_MEMORY_KINDS
        self._corpus_limit = corpus_limit
        self._memory_limit = memory_limit
        self._conn: sqlite3.Connection | None = None
        self._chunk_index: Any = None
        self._memory_retriever: Any = None
        self._parent: Any = None

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
        self._load()
        where = limit if limit and limit < self._memory_limit else self._memory_limit
        hits = self._memory_retriever.search(query, scopes=(), limit=max(1, where))
        out: list[CareerEvidence] = []
        for hit in hits:
            memory = hit.memory
            if memory.kind.value not in self._kinds:
                continue
            out.append(
                CareerEvidence(
                    evidence_id=self._evidence_id(memory.content, "personal_ai_memory"),
                    claim=f"{memory.content}",
                    level=VerificationLevel.DOCUMENTED,
                    source="personal_ai_memory",
                    source_type=memory.source_type.value,
                    categories=[],
                    keywords=[],
                    confidence=float(memory.confidence),
                    memory_id=memory.memory_id,
                    memory_kind=memory.kind.value,
                    observed_at=memory.created_at or memory.updated_at,
                    raw={"summary": memory.summary},
                )
            )
        return tuple(out)

    def corpus_search(self, query: str, limit: int = 10) -> tuple[CareerEvidence, ...]:
        self._load()
        where = limit if limit and limit < self._corpus_limit else self._corpus_limit
        hits = self._chunk_index.search(query, limit=max(1, where))
        out: list[CareerEvidence] = []
        for hit in hits:
            out.append(
                CareerEvidence(
                    evidence_id=self._evidence_id(hit.text, "personal_ai_corpus"),
                    claim=hit.text,
                    level=VerificationLevel.DOCUMENTED,
                    source="personal_ai_corpus",
                    source_type=hit.source_type,
                    confidence=0.6,
                    document_id=hit.document_id,
                    chunk_id=hit.chunk_id,
                    raw={"rank": hit.rank, "source": hit.source},
                )
            )
        return tuple(out)

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
            self._conn = None

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
