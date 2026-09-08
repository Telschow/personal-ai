"""Phase 32 — policy-gated interactive chat access to get_document/get_memory.

Phase 31 added the read-only ``get_document`` and ``get_memory`` tools to the
agent execution runtime. Phase 32 exposes them to the interactive chat
ToolRegistry (``create_default_registry``) behind the existing permission
architecture, with zero change to the Phase 31 tool semantics.

* Registration: ``get_document`` appears in the chat registry only when BOTH
  a ``DocumentStore`` and a ``ChunkStore`` are wired in; ``get_memory`` only
  when a ``MemoryService`` is wired in. Parameter surfaces are the canonical
  ``document_id`` + optional ``chunk_limit`` and ``memory_id`` shapes.
* Authorization: chat tools run only through the policy engine impersonating
  the researcher — the existing ``corpus.search`` (documents) and
  ``memory.read`` (memory) read permissions, no new permission. A denied agent
  is refused BEFORE any store/service receives a call (recording proxies prove
  zero reads on denial).
* Differentiated access preserved: only the researcher identity is reachable
  from chat; the curator can never fetch documents through the policy path and
  corpus-enabled engineers cannot fetch memories.
* End-to-end: the real ``ToolRegistry`` built by ``create_default_registry``
  executes both tools against hermetic SQLite-backed stores, honoring
  ``document_id``/``chunk_limit``/``memory_id`` passthrough, unknown-id
  ``not_found`` contracts, chunk window bounding, and deterministic results.
* No-write regression: invoking both tools through the chat registry leaves
  the SQLite file byte-identical (no memory rows, no events, no evidence, no
  review/curation rows), and ``propose_memory`` stays unwired without an
  approver.
* Privacy regression: ``get_memory`` exposes only the canonical memory plus
  content-free provenance (evidence ids/bodies, candidate JSON, statement
  hashes, review notes, prompts, model output, and secrets never appear).

Everything is offline: ``tmp_path`` SQLite, no Ollama, no network.
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
from personal_ai.tools import ToolRegistry, create_default_registry
from personal_ai.tools.fetch import build_policy_gated_get_handler

STATEMENT_SENTINEL = "STATEMENT_SENTINEL_user_prefers_flat_white"
EVIDENCE_SOURCE_SENTINEL = "EVIDENCE_SOURCE_SENTINEL_conv_id"
EVIDENCE_MSG_SENTINEL = "EVIDENCE_MSG_SENTINEL_message_id"


class _Bundle:
    """One shared SQLite connection with document storage and memory."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.document_store = DocumentStore(self.connection)
        self.chunk_store = ChunkStore(self.connection)
        self.memory_service = MemoryService(MemoryStore(self.connection))

    def seed_document(
        self, doc_id: str, *, chunks: int = 3, with_index: bool = True
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
        else:
            self.memory_service.add_evidence(
                memory_id,
                tuple(
                    MemoryEvidenceRef(
                        source_type=source_type,
                        source_id=EVIDENCE_SOURCE_SENTINEL,
                        source_document_id=EVIDENCE_MSG_SENTINEL,
                        source_timestamp=stamp,
                    )
                    for source_type, stamp in (
                        ("chatgpt", "2026-03-01T10:00:00+00:00"),
                        ("gemini", "2026-03-02T09:00:00+00:00"),
                    )
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


def _chat_registry(bundle: _Bundle, workspace: Path) -> ToolRegistry:
    return create_default_registry(
        workspace,
        document_store=bundle.document_store,
        chunk_store=bundle.chunk_store,
        memory_service=bundle.memory_service,
    )


def _schema_names(registry: ToolRegistry) -> set[str]:
    return {schema["function"]["name"] for schema in registry.schemas()}


def _tool_schema(registry: ToolRegistry, name: str) -> dict[str, object]:
    for schema in registry.schemas():
        if schema["function"]["name"] == name:
            return schema["function"]
    raise AssertionError(f"tool {name!r} absent from chat registry")


def _agent_policy() -> PolicyEngine:
    return PolicyEngine(
        build_default_agent_tools(
            document_store=object(),
            chunk_store=object(),
            memory_service=object(),
        ),
        build_default_agent_registry(),
        build_default_skill_registry(),
    )


def _blob(*objects: object) -> str:
    return json.dumps(objects, default=str)


# =====================================================================
# Registration in the interactive chat registry
# =====================================================================


def test_chat_registry_exposes_get_tools_when_wired(
    bundle: _Bundle, tmp_path: Path
) -> None:
    registry = _chat_registry(bundle, tmp_path)
    names = _schema_names(registry)
    assert "get_document" in names
    assert "get_memory" in names

    document = _tool_schema(registry, "get_document")
    assert document["name"] == "get_document"
    assert document["parameters"]["required"] == ["document_id"]
    assert document["parameters"]["properties"]["chunk_limit"]["type"] == "integer"

    memory = _tool_schema(registry, "get_memory")
    assert memory["name"] == "get_memory"
    assert memory["parameters"]["required"] == ["memory_id"]


def test_chat_registry_omits_get_tools_without_dependencies(tmp_path: Path) -> None:
    bare = create_default_registry(tmp_path)
    assert "get_document" not in _schema_names(bare)
    assert "get_memory" not in _schema_names(bare)

    bundle = _Bundle(tmp_path / "docs-only.sqlite")
    try:
        docs_only = create_default_registry(
            tmp_path,
            document_store=bundle.document_store,
            chunk_store=bundle.chunk_store,
        )
        assert "get_document" in _schema_names(docs_only)
        assert "get_memory" not in _schema_names(docs_only)
    finally:
        bundle.close()

    bundle = _Bundle(tmp_path / "memory-only.sqlite")
    try:
        memory_only = create_default_registry(
            tmp_path,
            memory_service=bundle.memory_service,
        )
        assert "get_memory" in _schema_names(memory_only)
        assert "get_document" not in _schema_names(memory_only)
    finally:
        bundle.close()


def test_chat_get_tools_keep_read_only_agent_profiles() -> None:
    for tool, permission in (
        (GET_DOCUMENT, Permission.CORPUS_SEARCH),
        (GET_MEMORY, Permission.MEMORY_READ),
    ):
        assert tool.risk is RiskLevel.READ
        assert tool.mutates_state is False
        assert tool.accesses_network is False
        assert tool.reads_private_data is True
        assert tool.deterministic is True
        assert tool.permissions == (permission,)


def test_chat_registry_leaves_write_tool_unwired_without_approver(
    bundle: _Bundle, tmp_path: Path
) -> None:
    registry = _chat_registry(bundle, tmp_path)
    names = _schema_names(registry)
    assert "get_document" in names
    assert "get_memory" in names
    # The read fetch tools must not open the memory.write surface: without an
    # approver, propose_memory stays absent (Phase 11/12 default-deny).
    assert "propose_memory" not in names


# =====================================================================
# Authorization through the real policy path
# =====================================================================


def _graded_tools(
    document_store: object, chunk_store: object, memory_service: object
) -> PolicyEngine:
    return PolicyEngine(
        build_default_agent_tools(
            document_store=document_store,
            chunk_store=chunk_store,
            memory_service=memory_service,
        ),
        build_default_agent_registry(),
        build_default_skill_registry(),
    )


class _RecordingDocuments:
    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def get(self, document_id: str) -> object:
        self.calls.append("documents.get")
        return self._inner.get(document_id)  # type: ignore[attr-defined]


class _RecordingChunks:
    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def list_for_document(self, document_id: str, limit: int | None) -> object:
        self.calls.append("chunks.list_for_document")
        return self._inner.list_for_document(document_id, limit=limit)  # type: ignore[attr-defined]


class _RecordingMemory:
    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def get(self, memory_id: str) -> object:
        self.calls.append("memory.get")
        return self._inner.get(memory_id)  # type: ignore[attr-defined]

    def provenance_for(self, memory_id: str) -> object:
        self.calls.append("memory.provenance_for")
        return self._inner.provenance_for(memory_id)  # type: ignore[attr-defined]


def test_chat_handler_runs_get_document_under_researcher(bundle: _Bundle) -> None:
    bundle.seed_document("doc-chat")
    handler = build_policy_gated_get_handler(
        document_store=bundle.document_store,
        chunk_store=bundle.chunk_store,
        memory_service=bundle.memory_service,
    )
    data = handler.get_document({"document_id": "doc-chat"})
    assert isinstance(data, dict)
    assert data["status"] == "ok"
    assert data["document"]["document_id"] == "doc-chat"


def test_chat_handler_runs_get_memory_under_researcher(bundle: _Bundle) -> None:
    memory_id = bundle.seed_memory()
    handler = build_policy_gated_get_handler(
        document_store=bundle.document_store,
        chunk_store=bundle.chunk_store,
        memory_service=bundle.memory_service,
    )
    data = handler.get_memory({"memory_id": memory_id})
    assert isinstance(data, dict)
    assert data["status"] == "ok"
    assert data["memory"]["memory_id"] == memory_id


def test_chat_denied_get_document_never_touches_stores(bundle: _Bundle) -> None:
    bundle.seed_document("doc-curator")
    documents = _RecordingDocuments(bundle.document_store)
    chunks = _RecordingChunks(bundle.chunk_store)
    policy = _graded_tools(documents, chunks, object())

    check = policy.check_tool(CURATOR, "get_document")
    assert check.decision is PolicyDecision.DENIED
    assert check.permission is Permission.CORPUS_SEARCH
    with pytest.raises(PolicyDenialError, match="corpus.search"):
        policy.execute(CURATOR, "get_document", {"document_id": "doc-curator"})
    # Permission failure precedes execution: the stores saw zero calls.
    assert documents.calls == []
    assert chunks.calls == []


def test_chat_denied_get_memory_never_touches_service(
    bundle: _Bundle,
) -> None:
    for agent in (ENGINEER, ORCHESTRATOR, REVIEWER):
        memory = _RecordingMemory(bundle.memory_service)
        policy = _graded_tools(object(), object(), memory)
        check = policy.check_tool(agent, "get_memory")
        assert check.decision is PolicyDecision.DENIED, agent.id
        assert check.permission is Permission.MEMORY_READ
        with pytest.raises(PolicyDenialError, match="memory.read"):
            policy.execute(agent, "get_memory", {"memory_id": "mem-any"})
        assert memory.calls == [], f"{agent.id} reached the memory service"


def test_chat_researcher_authorized_for_both() -> None:
    policy = _graded_tools(object(), object(), object())
    for tool in ("get_document", "get_memory"):
        assert policy.check_tool(RESEARCHER, tool).decision is PolicyDecision.ALLOWED


def test_chat_agent_policy_needs_no_new_permission() -> None:
    existing = {permission.value for permission in Permission}
    assert "corpus.search" in existing
    assert "memory.read" in existing
    assert "document.read" not in existing
    # The architecture deliberately expresses the read distinction with the
    # existing corpus.search (documents) + memory.read (memory) permissions.
    policy = _agent_policy()
    for agent, tool in ((CURATOR, "get_document"), (ENGINEER, "get_memory")):
        assert policy.check_tool(agent, tool).decision is PolicyDecision.DENIED


# =====================================================================
# End-to-end through create_default_registry
# =====================================================================


def test_chat_registry_get_document_end_to_end(bundle: _Bundle, tmp_path: Path) -> None:
    bundle.seed_document("doc-e2e", chunks=3)
    registry = _chat_registry(bundle, tmp_path)
    data = registry.execute("get_document", {"document_id": "doc-e2e"})
    assert isinstance(data, dict)
    assert data["status"] == "ok"
    assert data["document"]["filename"] == "private.md"
    assert data["document"]["mime_type"] == "text/markdown"
    assert data["chunk_count"] == 3
    assert [c["chunk_id"] for c in data["chunks"]] == [
        "chunk-doc-e2e-000",
        "chunk-doc-e2e-001",
        "chunk-doc-e2e-002",
    ]


def test_chat_registry_get_memory_end_to_end(bundle: _Bundle, tmp_path: Path) -> None:
    memory_id = bundle.seed_memory()
    registry = _chat_registry(bundle, tmp_path)
    data = registry.execute("get_memory", {"memory_id": memory_id})
    assert isinstance(data, dict)
    assert data["status"] == "ok"
    assert data["memory"]["content"] == STATEMENT_SENTINEL
    assert data["provenance"]["evidence_count"] == 2
    assert data["provenance"]["evidence_kinds"] == ["chatgpt", "gemini"]
    assert data["provenance"]["first_evidence_at"] == "2026-03-01T10:00:00+00:00"
    assert data["provenance"]["last_evidence_at"] == "2026-03-02T09:00:00+00:00"


def test_chat_registry_unknown_ids_are_not_found(
    bundle: _Bundle, tmp_path: Path
) -> None:
    bundle.seed_document("doc-present")
    bundle.seed_memory()
    registry = _chat_registry(bundle, tmp_path)
    assert registry.execute("get_document", {"document_id": "doc-missing"}) == {
        "status": "not_found",
        "document_id": "doc-missing",
    }
    assert registry.execute("get_memory", {"memory_id": "mem-missing"}) == {
        "status": "not_found",
        "memory_id": "mem-missing",
    }


def test_chat_registry_chunk_limit_passthrough(bundle: _Bundle, tmp_path: Path) -> None:
    bundle.seed_document("doc-many", chunks=105)
    registry = _chat_registry(bundle, tmp_path)
    default = registry.execute("get_document", {"document_id": "doc-many"})
    assert isinstance(default, dict)
    assert len(default["chunks"]) == 20
    explicit = registry.execute(
        "get_document", {"document_id": "doc-many", "chunk_limit": 5}
    )
    assert isinstance(explicit, dict)
    assert len(explicit["chunks"]) == 5
    clamped = registry.execute(
        "get_document", {"document_id": "doc-many", "chunk_limit": 99999}
    )
    assert isinstance(clamped, dict)
    assert len(clamped["chunks"]) == 100


def test_chat_registry_parameter_passthrough(bundle: _Bundle, tmp_path: Path) -> None:
    bundle.seed_document("doc-pt", chunks=4)
    memory_id = bundle.seed_memory(refs=())
    registry = _chat_registry(bundle, tmp_path)
    limited = registry.execute(
        "get_document", {"document_id": "doc-pt", "chunk_limit": 2}
    )
    assert isinstance(limited, dict)
    assert [c["chunk_id"] for c in limited["chunks"]] == [
        "chunk-doc-pt-000",
        "chunk-doc-pt-001",
    ]
    fetched = registry.execute("get_memory", {"memory_id": memory_id})
    assert isinstance(fetched, dict)
    assert fetched["memory"]["memory_id"] == memory_id
    assert fetched["provenance"]["evidence_count"] == 0


def test_chat_registry_deterministic(bundle: _Bundle, tmp_path: Path) -> None:
    bundle.seed_document("doc-det", chunks=4)
    memory_id = bundle.seed_memory()
    registry = _chat_registry(bundle, tmp_path)
    first_doc = registry.execute("get_document", {"document_id": "doc-det"})
    second_doc = registry.execute("get_document", {"document_id": "doc-det"})
    assert first_doc == second_doc
    first_mem = registry.execute("get_memory", {"memory_id": memory_id})
    second_mem = registry.execute("get_memory", {"memory_id": memory_id})
    assert first_mem == second_mem


def test_chat_registry_unknown_parameters_ignored_not_honored(
    bundle: _Bundle, tmp_path: Path
) -> None:
    bundle.seed_document("doc-ignore")
    memory_id = bundle.seed_memory()
    before = dict(bundle.memory_service.statistics())
    registry = _chat_registry(bundle, tmp_path)
    assert (
        registry.execute(
            "get_document",
            {
                "document_id": "doc-ignore",
                "approve": True,
                "review_id": 9,
                "sql": "DROP TABLE document_chunks",
                "raw": True,
            },
        )["status"]
        == "ok"
    )
    assert (
        registry.execute(
            "get_memory",
            {
                "memory_id": memory_id,
                "approve": True,
                "apply_candidate": True,
                "review_id": "1",
                "curate": "run",
            },
        )["status"]
        == "ok"
    )
    assert bundle.chunk_store.count() == 3
    assert bundle.memory_service.statistics() == before


# =====================================================================
# No-write regression through the chat registry
# =====================================================================


def test_chat_registry_writes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "bundle.sqlite"
    bundle = _Bundle(db)
    try:
        bundle.seed_document("doc-ro", chunks=2)
        memory_id = bundle.seed_memory()
        registry = _chat_registry(bundle, tmp_path)
        before_bytes = db.read_bytes()
        before_stats = dict(bundle.memory_service.statistics())
        before_events = tuple(bundle.memory_service.events(memory_id))

        for _ in range(2):
            assert (
                registry.execute("get_document", {"document_id": "doc-ro"})["status"]
                == "ok"
            )
            assert (
                registry.execute("get_document", {"document_id": "doc-missing"})[
                    "status"
                ]
                == "not_found"
            )
            assert (
                registry.execute("get_memory", {"memory_id": memory_id})["status"]
                == "ok"
            )
            assert (
                registry.execute("get_memory", {"memory_id": "mem-missing"})["status"]
                == "not_found"
            )

        assert db.read_bytes() == before_bytes
        assert bundle.memory_service.statistics() == before_stats
        assert tuple(bundle.memory_service.events(memory_id)) == before_events
        assert {e["event_type"] for e in before_events} == {"memory.created"}
        memory = bundle.memory_service.get(memory_id)
        assert memory.last_accessed_at is None
    finally:
        bundle.close()


# =====================================================================
# Privacy regression at the registry level
# =====================================================================


def test_chat_registry_get_memory_privacy(bundle: _Bundle, tmp_path: Path) -> None:
    memory_id = bundle.seed_memory(
        refs=(
            MemoryEvidenceRef(
                source_type="chatgpt",
                source_id=EVIDENCE_SOURCE_SENTINEL,
                source_document_id=EVIDENCE_MSG_SENTINEL,
                source_timestamp="2026-03-01T10:00:00+00:00",
            ),
        )
    )
    registry = _chat_registry(bundle, tmp_path)
    data = registry.execute("get_memory", {"memory_id": memory_id})
    assert isinstance(data, dict)
    assert set(data) == {"status", "memory", "provenance"}
    assert set(data["provenance"]) == {
        "evidence_count",
        "evidence_kinds",
        "first_evidence_at",
        "last_evidence_at",
    }
    blob = _blob(data)
    assert EVIDENCE_SOURCE_SENTINEL not in blob
    assert EVIDENCE_MSG_SENTINEL not in blob
    assert "source_document_id" not in blob
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
    assert STATEMENT_SENTINEL in data["memory"]["content"]


def test_chat_registry_get_document_privacy(bundle: _Bundle, tmp_path: Path) -> None:
    bundle.seed_document("doc-priv", chunks=1)
    registry = _chat_registry(bundle, tmp_path)
    data = registry.execute("get_document", {"document_id": "doc-priv"})
    assert isinstance(data, dict)
    assert set(data) == {"status", "document", "chunk_count", "chunks"}
    assert set(data["chunks"][0]) == {
        "chunk_id",
        "document_id",
        "page_number",
        "text",
        "metadata",
    }
    for marker in (
        "evidence",
        "candidate_json",
        "prompt",
        "model_output",
        "statement_hash",
    ):
        assert marker not in _blob(data), f"privacy marker leaked: {marker}"
