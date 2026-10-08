"""Phase 31 — read-only ``get_document`` and ``get_memory`` agent tools.

Adds the two read-only fetch tools to the agent ToolRegistry, closing the
agent-facing retrieval gap: the model can now retrieve one indexed document
(metadata + bounded, deterministic chunk window) and one durable memory
(canonical statement + content-free provenance aggregation) by id.

* Registration: ``get_document`` is registered only when BOTH a
  ``DocumentStore`` and a ``ChunkStore`` are wired in; ``get_memory`` is
  registered only when a ``MemoryService`` is wired in. Both are absent from a
  dependency-free build.
* Profiles: ``risk=READ``, ``mutates_state=False``, ``accesses_network=False``,
  ``deterministic=True``; ``get_document`` is gated on the existing read-only
  ``corpus.search`` permission and ``get_memory`` on the existing read-only
  ``memory.read`` permission — no new permission or policy change is needed.
* Contracts: unknown ids return a ``not_found`` status, never a fallback or a
  raised error; malformed ids (non-string/empty) are rejected loudly; the
  chunk window is bounded (default 20, hard cap 100) in deterministic
  ``chunk_index, chunk_id`` order.
* Provenance privacy: ``get_memory`` returns aggregate-only provenance
  (evidence count, distinct evidence kinds, first/last evidence timestamps).
  Evidence identifiers, evidence bodies, prompts, model output, candidate
  JSON, and statement hashes never appear; sentinel markers are proven absent.
* Read-only guarantee: neither tool mutates state, creates memory events, or
  touches any write surface (a recording proxy proves only read methods run).
* Integration: the real ``PolicyEngine`` + ``AgentToolRegistry`` path lets the
  researcher execute both tools and bars agents that lack the permission.

Everything is offline: ``tmp_path``/``:memory:`` SQLite, no Ollama, no network.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from personal_ai.agents.defs import (
    CURATOR,
    ENGINEER,
    ORCHESTRATOR,
    RESEARCHER,
    REVIEWER,
    build_default_agent_registry,
)
from personal_ai.agents.models import Permission, PolicyDecision, RiskLevel
from personal_ai.agents.policy import PolicyDenialError, PolicyEngine
from personal_ai.agents.registry import AgentToolRegistry
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import (
    GET_DOCUMENT,
    GET_MEMORY,
    build_default_agent_tools,
)
from personal_ai.documents.models import Document, DocumentChunk
from personal_ai.memory.models import MemoryDraft, MemoryEvidenceRef
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryStore
from personal_ai.storage import ChunkStore, DocumentStore

EVIDENCE_SOURCE_SENTINEL = "EVIDENCE_SOURCE_SENTINEL_conv_id"
EVIDENCE_MSG_SENTINEL = "EVIDENCE_MSG_SENTINEL_message_id"
STATEMENT_SENTINEL = "STATEMENT_SENTINEL_user_prefers_flat_white"


class _Bundle:
    """One shared SQLite connection with document storage and memory."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.document_store = DocumentStore(self.connection)
        self.chunk_store = ChunkStore(self.connection)
        self.memory_service = MemoryService(MemoryStore(self.connection))

    def seed_document(
        self,
        doc_id: str,
        *,
        chunks: int = 3,
        with_index: bool = True,
    ) -> None:
        self.document_store.add(
            Document(
                id=doc_id,
                source="notes/private.md",
                source_type="file",
                content_hash=f"hash-{doc_id}",
                created_at="2026-01-01T00:00:00+00:00",
                modified_at="2026-01-02T00:00:00+00:00",
                path="notes/private.md",
                filename="private.md",
                mime_type="text/markdown",
                metadata={"title": "Private notes"},
            )
        )
        for index in range(chunks):
            self.chunk_store.add(
                DocumentChunk(
                    id=f"chunk-{doc_id}-{index:03d}",
                    document_id=doc_id,
                    text=f"private document chunk text number {index}",
                    metadata={"chunk_index": index} if with_index else {},
                )
            )

    def seed_memory(
        self,
        content: str = STATEMENT_SENTINEL,
        *,
        evidence: tuple[tuple[str, str], ...] = (
            ("chatgpt", "2026-03-01T10:00:00+00:00"),
            ("gemini", "2026-03-02T09:00:00+00:00"),
        ),
        refs: tuple[MemoryEvidenceRef, ...] | None = None,
    ) -> str:
        memory_id = self.memory_service.create(
            MemoryDraft(
                kind="preference",
                content=content,
                summary="synthetic summary",
                source_type="user",
                confidence=0.8,
                importance=0.7,
            )
        ).memory_id
        if refs is not None:
            self.memory_service.add_evidence(memory_id, refs)
        elif evidence:
            self.memory_service.add_evidence(
                memory_id,
                tuple(
                    MemoryEvidenceRef(
                        source_type=source_type,
                        source_id=EVIDENCE_SOURCE_SENTINEL,
                        source_document_id=EVIDENCE_MSG_SENTINEL,
                        source_timestamp=stamp,
                    )
                    for source_type, stamp in evidence
                ),
            )
        return memory_id

    def close(self) -> None:
        self.connection.close()


@pytest.fixture
def bundle(tmp_path: Path) -> _Bundle:
    b = _Bundle(tmp_path / "bundle.sqlite")
    try:
        yield b
    finally:
        b.close()


def _tools(bundle: _Bundle) -> AgentToolRegistry:
    return build_default_agent_tools(
        document_store=bundle.document_store,
        chunk_store=bundle.chunk_store,
        memory_service=bundle.memory_service,
    )


def _policy(tools: AgentToolRegistry) -> PolicyEngine:
    return PolicyEngine(
        tools,
        build_default_agent_registry(),
        build_default_skill_registry(),
    )


def _invoke(
    policy: PolicyEngine,
    tool: str,
    arguments: dict[str, object],
    *,
    agent=RESEARCHER,
) -> dict[str, object]:
    result = policy.execute(agent, tool, arguments)
    assert isinstance(result, dict)
    return result


def _blob(*objects: object) -> str:
    return json.dumps(objects, default=str)


# =====================================================================
# Registration
# =====================================================================


def test_not_registered_without_dependencies() -> None:
    registry = build_default_agent_tools()
    assert "get_document" not in registry.names()
    assert "get_memory" not in registry.names()
    # A chunk store alone is not enough for get_document (needs metadata too).
    chunk_only = build_default_agent_tools(chunk_store=object())
    assert "get_document" not in chunk_only.names()
    assert "get_memory" not in chunk_only.names()
    # A memory service alone enables get_memory but not get_document.
    memory_only = build_default_agent_tools(memory_service=object())
    assert "get_memory" in memory_only.names()
    assert "get_document" not in memory_only.names()


def test_registered_when_dependencies_wired(bundle: _Bundle) -> None:
    registry = _tools(bundle)
    assert "get_document" in registry.names()
    assert "get_memory" in registry.names()
    assert registry.tool("get_document") is GET_DOCUMENT
    assert registry.tool("get_memory") is GET_MEMORY


def test_get_document_needs_document_store_and_chunk_store(bundle: _Bundle) -> None:
    only_docs = build_default_agent_tools(document_store=bundle.document_store)
    assert "get_document" not in only_docs.names()
    only_chunks = build_default_agent_tools(chunk_store=bundle.chunk_store)
    assert "get_document" not in only_chunks.names()
    both = build_default_agent_tools(
        document_store=bundle.document_store, chunk_store=bundle.chunk_store
    )
    assert "get_document" in both.names()


def test_profiles_are_read_only() -> None:
    for tool, permission in (
        (GET_DOCUMENT, Permission.CORPUS_SEARCH),
        (GET_MEMORY, Permission.MEMORY_READ),
    ):
        assert tool.risk is RiskLevel.READ
        assert tool.mutates_state is False
        assert tool.accesses_network is False
        assert tool.reads_private_data is True
        assert tool.deterministic is True
        assert tool.timeout_seconds is None
        assert tool.permissions == (permission,)


def test_researcher_declares_both_tools() -> None:
    assert "get_document" in RESEARCHER.tools
    assert "get_memory" in RESEARCHER.tools
    assert RESEARCHER.policy.is_allowed(Permission.CORPUS_SEARCH)
    assert RESEARCHER.policy.is_allowed(Permission.MEMORY_READ)


# =====================================================================
# Permission model
# =====================================================================


def test_get_memory_requires_memory_read(bundle: _Bundle) -> None:
    policy = _policy(_tools(bundle))
    for agent in (ENGINEER, ORCHESTRATOR, REVIEWER):
        check = policy.check_tool(agent, "get_memory")
        assert check.decision is PolicyDecision.DENIED, agent.id
        assert check.permission is Permission.MEMORY_READ
        with pytest.raises(PolicyDenialError, match="memory.read"):
            policy.execute(agent, "get_memory", {"memory_id": "mem-any"})


def test_get_document_requires_corpus_read(bundle: _Bundle) -> None:
    policy = _policy(_tools(bundle))
    # CURATOR denies corpus.search outright and can never fetch a document.
    check = policy.check_tool(CURATOR, "get_document")
    assert check.decision is PolicyDecision.DENIED
    with pytest.raises(PolicyDenialError, match="corpus.search"):
        policy.execute(CURATOR, "get_document", {"document_id": "doc-any"})
    # Research-aligned agents holding corpus.search may fetch.
    for agent in (RESEARCHER, ENGINEER, ORCHESTRATOR, REVIEWER):
        assert (
            policy.check_tool(agent, "get_document").decision is PolicyDecision.ALLOWED
        )


# =====================================================================
# get_document
# =====================================================================


def test_get_document_returns_metadata_and_chunks(bundle: _Bundle) -> None:
    bundle.seed_document("doc-alpha", chunks=3)
    policy = _policy(_tools(bundle))
    data = _invoke(policy, "get_document", {"document_id": "doc-alpha"})
    assert data["status"] == "ok"
    doc = data["document"]
    assert doc["document_id"] == "doc-alpha"
    assert doc["filename"] == "private.md"
    assert doc["mime_type"] == "text/markdown"
    assert doc["source_type"] == "file"
    assert doc["metadata"] == {"title": "Private notes"}
    assert data["chunk_count"] == 3
    assert [c["chunk_id"] for c in data["chunks"]] == [
        "chunk-doc-alpha-000",
        "chunk-doc-alpha-001",
        "chunk-doc-alpha-002",
    ]
    assert "private document chunk text" in data["chunks"][0]["text"]
    assert json.dumps(data)  # JSON-serializable contract


def test_get_document_chunks_in_deterministic_order(bundle: _Bundle) -> None:
    bundle.document_store.add(
        Document(
            id="doc-order",
            source="s",
            source_type="file",
            content_hash="h",
            created_at="2026-01-01T00:00:00+00:00",
            modified_at="2026-01-01T00:00:00+00:00",
        )
    )
    for index in (4, 1, 7, 3):
        bundle.chunk_store.add(
            DocumentChunk(
                id=f"chunk-order-{index}",
                document_id="doc-order",
                text=f"text {index}",
                metadata={"chunk_index": index},
            )
        )
    policy = _policy(_tools(bundle))
    data = _invoke(
        policy, "get_document", {"document_id": "doc-order", "chunk_limit": 100}
    )
    assert [c["chunk_id"] for c in data["chunks"]] == [
        "chunk-order-1",
        "chunk-order-3",
        "chunk-order-4",
        "chunk-order-7",
    ]


def test_get_document_unknown_id_is_not_found(bundle: _Bundle) -> None:
    bundle.seed_document("doc-known")
    policy = _policy(_tools(bundle))
    data = _invoke(policy, "get_document", {"document_id": "doc-missing"})
    assert data == {"status": "not_found", "document_id": "doc-missing"}


def test_get_document_invalid_id_rejected(bundle: _Bundle) -> None:
    policy = _policy(_tools(bundle))
    with pytest.raises(TypeError):
        _invoke(policy, "get_document", {"document_id": 42})
    with pytest.raises(TypeError):
        _invoke(policy, "get_document", {})
    with pytest.raises(ValueError):
        _invoke(policy, "get_document", {"document_id": "   "})


def test_get_document_chunk_limit_bounds(bundle: _Bundle) -> None:
    bundle.seed_document("doc-many", chunks=105)
    policy = _policy(_tools(bundle))
    default = _invoke(policy, "get_document", {"document_id": "doc-many"})
    assert len(default["chunks"]) == 20  # repository-default window
    explicit = _invoke(
        policy, "get_document", {"document_id": "doc-many", "chunk_limit": 5}
    )
    assert len(explicit["chunks"]) == 5
    clamped = _invoke(
        policy, "get_document", {"document_id": "doc-many", "chunk_limit": 99999}
    )
    assert len(clamped["chunks"]) == 100  # hard cap, never fetch-all verbatim
    assert clamped["status"] == "ok"


def test_get_document_invalid_chunk_limit_rejected(bundle: _Bundle) -> None:
    bundle.seed_document("doc-limits")
    policy = _policy(_tools(bundle))
    for bad in (0, -1, "20", 2.5, True, None):
        with pytest.raises((TypeError, ValueError)):
            _invoke(
                policy,
                "get_document",
                {"document_id": "doc-limits", "chunk_limit": bad},
            )


def test_get_document_deterministic(bundle: _Bundle) -> None:
    bundle.seed_document("doc-det", chunks=4)
    policy = _policy(_tools(bundle))
    first = _invoke(policy, "get_document", {"document_id": "doc-det"})
    second = _invoke(policy, "get_document", {"document_id": "doc-det"})
    assert first == second


def test_get_document_is_read_only(tmp_path: Path) -> None:
    db = tmp_path / "bundle.sqlite"
    bundle = _Bundle(db)
    try:
        bundle.seed_document("doc-ro")
        policy = _policy(_tools(bundle))
        before = db.read_bytes()
        _invoke(policy, "get_document", {"document_id": "doc-ro"})
        _invoke(policy, "get_document", {"document_id": "doc-missing"})
        assert db.read_bytes() == before
    finally:
        bundle.close()


# =====================================================================
# get_memory
# =====================================================================


def test_get_memory_returns_canonical_memory_and_aggregate_provenance(
    bundle: _Bundle,
) -> None:
    memory_id = bundle.seed_memory()
    policy = _policy(_tools(bundle))
    data = _invoke(policy, "get_memory", {"memory_id": memory_id})
    assert data["status"] == "ok"
    memory = data["memory"]
    assert memory["memory_id"] == memory_id
    assert memory["content"] == STATEMENT_SENTINEL
    assert memory["kind"] == "preference"
    assert memory["summary"] == "synthetic summary"
    assert memory["scope"] == "global"
    assert memory["status"] == "active"
    assert memory["temporal_scope"] == "unknown"
    provenance = data["provenance"]
    assert provenance["evidence_count"] == 2
    assert provenance["evidence_kinds"] == ["chatgpt", "gemini"]
    assert provenance["first_evidence_at"] == "2026-03-01T10:00:00+00:00"
    assert provenance["last_evidence_at"] == "2026-03-02T09:00:00+00:00"
    assert json.dumps(data)  # JSON-serializable contract


def test_get_memory_zero_evidence_empty_projection(bundle: _Bundle) -> None:
    memory_id = bundle.seed_memory(evidence=())
    policy = _policy(_tools(bundle))
    data = _invoke(policy, "get_memory", {"memory_id": memory_id})
    assert data["status"] == "ok"
    assert data["provenance"] == {
        "evidence_count": 0,
        "evidence_kinds": [],
        "first_evidence_at": None,
        "last_evidence_at": None,
    }
    assert json.dumps(data)  # None fields stay JSON-null, never invented


def test_get_memory_evidence_kinds_sorted_and_deduped(bundle: _Bundle) -> None:
    memory_id = bundle.seed_memory(
        refs=(
            MemoryEvidenceRef(
                source_type="gemini",
                source_id="src-2",
                source_timestamp="2026-03-02T09:00:00+00:00",
            ),
            MemoryEvidenceRef(
                source_type="chatgpt",
                source_id="src-1",
                source_timestamp="2026-03-01T10:00:00+00:00",
            ),
            MemoryEvidenceRef(
                source_type="gemini",
                source_id="src-3",
                source_timestamp="2026-03-03T08:00:00+00:00",
            ),
            MemoryEvidenceRef(
                source_type="gemini",
                source_id="src-3",
                source_timestamp="2026-03-04T08:00:00+00:00",
            ),
        )
    )
    policy = _policy(_tools(bundle))
    data = _invoke(policy, "get_memory", {"memory_id": memory_id})
    # Ref (gemini, src-3) is a duplicate identity and was deduped by the store.
    assert data["provenance"]["evidence_count"] == 3
    assert data["provenance"]["evidence_kinds"] == ["chatgpt", "gemini"]
    assert data["provenance"]["first_evidence_at"] == "2026-03-01T10:00:00+00:00"
    assert data["provenance"]["last_evidence_at"] == "2026-03-03T08:00:00+00:00"


def test_get_memory_unknown_id_is_not_found(bundle: _Bundle) -> None:
    bundle.seed_memory()
    policy = _policy(_tools(bundle))
    data = _invoke(policy, "get_memory", {"memory_id": "mem-missing"})
    assert data == {"status": "not_found", "memory_id": "mem-missing"}


def test_get_memory_invalid_id_rejected(bundle: _Bundle) -> None:
    policy = _policy(_tools(bundle))
    with pytest.raises(TypeError):
        _invoke(policy, "get_memory", {"memory_id": None})
    with pytest.raises(ValueError):
        _invoke(policy, "get_memory", {"memory_id": ""})


def test_get_memory_deterministic(bundle: _Bundle) -> None:
    memory_id = bundle.seed_memory()
    policy = _policy(_tools(bundle))
    first = _invoke(policy, "get_memory", {"memory_id": memory_id})
    second = _invoke(policy, "get_memory", {"memory_id": memory_id})
    assert first == second


def test_get_memory_is_read_only(bundle: _Bundle) -> None:
    memory_id = bundle.seed_memory()
    before_counts = dict(bundle.memory_service.counts())
    before_events = tuple(bundle.memory_service.events(memory_id))
    policy = _policy(_tools(bundle))

    _invoke(policy, "get_memory", {"memory_id": memory_id})
    _invoke(policy, "get_memory", {"memory_id": memory_id})

    assert bundle.memory_service.counts() == before_counts
    events_after = tuple(bundle.memory_service.events(memory_id))
    assert events_after == before_events
    assert {e["event_type"] for e in events_after} == {"memory.created"}
    memory = bundle.memory_service.get(memory_id)
    assert memory.last_accessed_at is None


def test_get_memory_fetch_never_writes_via_recording_proxy(bundle: _Bundle) -> None:
    memory_id = bundle.seed_memory()

    recorded: list[str] = []

    class _RecordingService:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        def get(self, mid: str) -> object:
            recorded.append("get")
            return self._inner.get(mid)  # type: ignore[attr-defined]

        def provenance_for(self, mid: str) -> object:
            recorded.append("provenance_for")
            return self._inner.provenance_for(mid)  # type: ignore[attr-defined]

    tools = build_default_agent_tools(
        memory_service=_RecordingService(bundle.memory_service)
    )
    policy = _policy(tools)
    result = policy.execute(RESEARCHER, "get_memory", {"memory_id": memory_id})
    result2 = policy.execute(
        RESEARCHER, "get_memory", {"memory_id": memory_id, "approve": True}
    )
    assert result["status"] == "ok"  # type: ignore[index]
    assert result2["status"] == "ok"  # type: ignore[index]
    assert recorded == ["get", "provenance_for", "get", "provenance_for"]
    # No write surface was even consulted: get/provenance reads only.


def test_get_document_fetch_never_writes_via_recording_proxy(bundle: _Bundle) -> None:
    bundle.seed_document("doc-rec")
    recorded: list[str] = []

    class _RecordingDocuments:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        def get(self, document_id: str) -> object:
            recorded.append("documents.get")
            return self._inner.get(document_id)  # type: ignore[attr-defined]

    class _RecordingChunks:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        def list_for_document(self, document_id: str, limit: int | None) -> object:
            recorded.append("chunks.list_for_document")
            return self._inner.list_for_document(document_id, limit=limit)  # type: ignore[attr-defined]

    tools = build_default_agent_tools(
        document_store=_RecordingDocuments(bundle.document_store),
        chunk_store=_RecordingChunks(bundle.chunk_store),
    )
    result = policy_for(tools, "get_document", {"document_id": "doc-rec"})
    assert result["status"] == "ok"  # type: ignore[index]
    assert recorded == ["documents.get", "chunks.list_for_document"]


def policy_for(
    tools: AgentToolRegistry, tool: str, arguments: dict[str, object]
) -> object:
    policy = PolicyEngine(
        tools, build_default_agent_registry(), build_default_skill_registry()
    )
    return policy.execute(RESEARCHER, tool, arguments)


# =====================================================================
# Storage/service seams exercised by the tools
# =====================================================================


def test_chunk_store_list_for_document_bounded(bundle: _Bundle) -> None:
    bundle.seed_document("doc-seam", chunks=5)
    all_chunks = bundle.chunk_store.list_for_document("doc-seam")
    assert len(all_chunks) == 5
    limited = bundle.chunk_store.list_for_document("doc-seam", limit=2)
    assert [c.id for c in limited] == [c.id for c in all_chunks[:2]]
    assert bundle.chunk_store.list_for_document("doc-absent") == ()


def test_provenance_for_is_content_free_and_aggregate(bundle: _Bundle) -> None:
    memory_id = bundle.seed_memory()
    projection = bundle.memory_service.provenance_for(memory_id)
    blob = json.dumps(projection, default=str)
    assert EVIDENCE_SOURCE_SENTINEL not in blob
    assert EVIDENCE_MSG_SENTINEL not in blob
    assert set(projection) == {
        "evidence_count",
        "evidence_kinds",
        "first_evidence_at",
        "last_evidence_at",
    }
    empty = bundle.memory_service.provenance_for(bundle.seed_memory(evidence=()))
    assert empty == {
        "evidence_count": 0,
        "evidence_kinds": [],
        "first_evidence_at": None,
        "last_evidence_at": None,
    }


# =====================================================================
# Provenance privacy
# =====================================================================


def test_get_memory_provenance_never_leaks_evidence_identifiers(
    bundle: _Bundle,
) -> None:
    memory_id = bundle.seed_memory(evidence=(("chatgpt", "2026-03-01T10:00:00+00:00"),))
    policy = _policy(_tools(bundle))
    data = _invoke(policy, "get_memory", {"memory_id": memory_id})
    blob = _blob(data)
    # Evidence identifiers (values) never surface; sentinels are proven absent.
    assert EVIDENCE_SOURCE_SENTINEL not in blob
    assert EVIDENCE_MSG_SENTINEL not in blob
    for marker in (
        "candidate_json",
        "statement_hash",
        "prompt",
        "model_output",
        "review_note",
        "p455w0rd",
        "secret",
    ):
        assert marker not in blob, f"privacy marker leaked: {marker}"
    # The canonical memory statement itself is the intended payload.
    assert "source_document_id" not in blob  # no evidence-level field at all
    assert STATEMENT_SENTINEL in data["memory"]["content"]  # type: ignore[index]


def test_get_memory_unknown_parameters_are_ignored_not_honored(bundle: _Bundle) -> None:
    memory_id = bundle.seed_memory()
    before = dict(bundle.memory_service.counts())
    policy = _policy(_tools(bundle))
    data = _invoke(
        policy,
        "get_memory",
        {
            "memory_id": memory_id,
            "approve": True,
            "apply_candidate": True,
            "review_id": "1",
            "include_candidate": True,
            "sql": "DELETE FROM memories",
        },
    )
    assert data["status"] == "ok"
    assert bundle.memory_service.counts() == before


def test_get_document_unknown_parameters_are_ignored_not_honored(
    bundle: _Bundle,
) -> None:
    bundle.seed_document("doc-ignore")
    policy = _policy(_tools(bundle))
    data = _invoke(
        policy,
        "get_document",
        {"document_id": "doc-ignore", "sql": "DROP TABLE document_chunks", "raw": True},
    )
    assert data["status"] == "ok"
    assert data["chunk_count"] == 3  # type: ignore[index]
    # The store still serves the doc: nothing was mutated.
    assert bundle.chunk_store.count() == 3


# =====================================================================
# Real agent dispatch end-to-end
# =====================================================================


def test_researcher_executes_both_tools_end_to_end(bundle: _Bundle) -> None:
    bundle.seed_document("doc-e2e", chunks=2)
    memory_id = bundle.seed_memory(evidence=(("chatgpt", "2026-03-01T10:00:00+00:00"),))
    policy = _policy(_tools(bundle))

    document = policy.execute(RESEARCHER, "get_document", {"document_id": "doc-e2e"})
    memory = policy.execute(RESEARCHER, "get_memory", {"memory_id": memory_id})

    assert document["status"] == "ok"  # type: ignore[index]
    assert document["document"]["filename"] == "private.md"  # type: ignore[index]
    assert memory["status"] == "ok"  # type: ignore[index]
    assert memory["provenance"]["evidence_count"] == 1  # type: ignore[index]
    assert memory["provenance"]["evidence_kinds"] == ["chatgpt"]  # type: ignore[index]
    blob = _blob(document, memory)
    assert EVIDENCE_SOURCE_SENTINEL not in blob
    assert EVIDENCE_MSG_SENTINEL not in blob


def test_read_only_guarantee_across_both_tools(tmp_path: Path) -> None:
    db = tmp_path / "bundle.sqlite"
    bundle = _Bundle(db)
    try:
        bundle.seed_document("doc-ro2")
        memory_id = bundle.seed_memory()
        policy = _policy(_tools(bundle))
        before_bytes = db.read_bytes()
        before_counts = dict(bundle.memory_service.counts())
        for _ in range(2):
            _invoke(policy, "get_document", {"document_id": "doc-ro2"})
            _invoke(policy, "get_memory", {"memory_id": memory_id})
        assert db.read_bytes() == before_bytes
        assert bundle.memory_service.counts() == before_counts
    finally:
        bundle.close()
