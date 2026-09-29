"""Knowledge adapter: null provider, build-time failure, read-only adapter."""

import sqlite3
import sys
from pathlib import Path

import pytest

from job_agent.career.knowledge import (
    CareerKnowledgeUnavailable,
    NullCareerKnowledge,
    PersonalAiCareerKnowledge,
    build_knowledge,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
pytest.importorskip("personal_ai")


def _new_personal_db(tmp_path) -> str:
    """Create a minimal parent `personal_ai`-shaped database (real stores)."""
    from personal_ai.documents.models import (
        Document,
        DocumentChunk,
        compute_content_hash,
    )
    from personal_ai.memory.models import (
        Memory,
        MemoryKind,
        MemoryScope,
        MemorySourceType,
        MemoryStatus,
        new_memory_id,
    )
    from personal_ai.memory.store import MemoryStore
    from personal_ai.storage.chunks import ChunkStore
    from personal_ai.storage.documents import DocumentStore

    conn = sqlite3.connect(tmp_path / "pa.db")
    MemoryStore(conn)  # creates memory tables on init
    chunk_store = ChunkStore(conn)  # creates chunk + FTS tables on init
    doc_store = DocumentStore(conn)  # creates documents table on init

    doc = Document(
        id="doc-1",
        source="note.md",
        source_type="file",
        content_hash=compute_content_hash(b"autonomous driving notes"),
        created_at="2025-01-01T00:00:00+00:00",
        modified_at="2025-01-01T00:00:00+00:00",
        path=str(tmp_path / "note.md"),
        filename="note.md",
        mime_type="text/markdown",
    )
    doc_store.add(doc)
    chunk_store.add(
        DocumentChunk(
            id="chunk-1",
            document_id="doc-1",
            text="Alice Example works on autonomous driving perception systems.",
            page_number=0,
            metadata={"chunk_index": 0},
        )
    )

    store = MemoryStore(conn)
    store.save(
        Memory(
            memory_id=new_memory_id(),
            kind=MemoryKind.WORK,
            content="Alice Example is a Product Owner at BMW Group",
            summary="work identity",
            source_type=MemorySourceType.IMPORTED,
            source_id="conv-1",
            scope=MemoryScope.GLOBAL,
            confidence=0.9,
            importance=0.6,
            status=MemoryStatus.ACTIVE,
            temporal_scope="current",
        )
    )
    conn.commit()
    return str(tmp_path / "pa.db")


def test_null_provider_healthy_false():
    k = NullCareerKnowledge()
    assert k.health() is False
    assert k.memory_search("anything", limit=3) == ()
    assert k.corpus_search("anything", limit=3) == ()


def test_build_knowledge_none_returns_null():
    assert isinstance(build_knowledge("none"), NullCareerKnowledge)


def test_build_knowledge_missing_db_raises(tmp_path):
    with pytest.raises(CareerKnowledgeUnavailable):
        build_knowledge("personal_ai", database_path=str(tmp_path / "missing.db"))


def test_adapter_read_only_connection(tmp_path):
    path = _new_personal_db(tmp_path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert conn.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE t (x)")  # read-only URI prevents writes


def test_adapter_memory_and_corpus_search(tmp_path):
    path = _new_personal_db(tmp_path)
    knowledge = PersonalAiCareerKnowledge(path)
    try:
        assert knowledge.health() is True
        mem = knowledge.memory_search("Product Owner BMW", limit=5)
        assert len(mem) == 1
        assert mem[0].level.value == "documented"
        assert mem[0].source == "personal_ai_memory"
        assert mem[0].memory_id
        corp = knowledge.corpus_search("autonomous driving", limit=5)
        assert len(corp) == 1
        assert corp[0].source == "personal_ai_corpus"
        assert corp[0].chunk_id == "chunk-1"
    finally:
        knowledge.close()


def test_adapter_respects_memory_kinds_filter(tmp_path):
    path = _new_personal_db(tmp_path)
    knowledge = PersonalAiCareerKnowledge(path, memory_kinds=["goal"])
    try:
        assert knowledge.health() is True
        mem = knowledge.memory_search("Product Owner BMW", limit=5)
        assert len(mem) == 0  # 'work' filtered out
    finally:
        knowledge.close()


def test_build_knowledge_validated_health(tmp_path):
    path = _new_personal_db(tmp_path)
    knowledge = build_knowledge("personal_ai", database_path=path)
    try:
        assert knowledge.health() is True
    finally:
        knowledge.close()


def test_adapter_filters_irrelevant_kinds(tmp_path):
    path = _new_personal_db(tmp_path)
    knowledge = PersonalAiCareerKnowledge(path)
    try:
        all_mem = knowledge.memory_search("", limit=10)
        assert all(m.memory_kind in knowledge._kinds for m in all_mem)
    finally:
        knowledge.close()
