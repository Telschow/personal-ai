"""Hermetic tests for the retrieval-backend construction seam (Phase 51)."""

from pathlib import Path

import pytest

from personal_ai.config import RetrievalMode, RetrievalSettings
from personal_ai.documents import Document, DocumentChunk, Embedding
from personal_ai.documents.embedding import EmbeddingProvider
from personal_ai.hybrid_index import HybridChunkIndex
from personal_ai.retrieval_factory import build_chunk_index, runtime_chunk_index
from personal_ai.semantic_index import SemanticChunkIndex
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    connect_database,
)
from personal_ai.storage.chunks import SQLiteChunkIndex


class MappingProvider:
    """Deterministic fake provider: explicit text -> vector mapping."""

    def __init__(
        self,
        vectors: dict[str, tuple[float, ...]],
        model: str = "semantic-test-model",
    ) -> None:
        self.model = model
        self._vectors = vectors
        self.calls: list[str] = []

    def embed(self, text: str) -> Embedding:
        self.calls.append(text)
        return Embedding(model=self.model, vector=self._vectors[text])


class ExplodingProviderFactory:
    """Provider factory that fails loudly if it is ever constructed."""

    def __call__(self, settings: object) -> object:
        raise AssertionError(f"provider factory must not run, got {settings!r}")


class RecordingProviderFactory:
    """Provider factory that records calls and returns a fake provider."""

    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider
        self.calls: list[object] = []

    def __call__(self, settings: object) -> EmbeddingProvider:
        self.calls.append(settings)
        return self._provider


def test_build_chunk_index_keyword_returns_sqlite_index() -> None:
    connection = connect_database(":memory:")
    index = build_chunk_index(connection, RetrievalSettings())
    assert isinstance(index, SQLiteChunkIndex)


def test_build_chunk_index_keyword_never_needs_provider() -> None:
    connection = connect_database(":memory:")
    index = build_chunk_index(connection, RetrievalSettings(), None)
    assert isinstance(index, SQLiteChunkIndex)


def test_build_chunk_index_semantic_returns_semantic_index() -> None:
    connection = connect_database(":memory:")
    provider = MappingProvider({"query": (1.0, 0.0)})
    index = build_chunk_index(
        connection, RetrievalSettings(RetrievalMode.SEMANTIC), provider
    )
    assert isinstance(index, SemanticChunkIndex)


def test_build_chunk_index_hybrid_returns_hybrid_index() -> None:
    connection = connect_database(":memory:")
    provider = MappingProvider({"query": (1.0, 0.0)})
    index = build_chunk_index(
        connection, RetrievalSettings(RetrievalMode.HYBRID), provider
    )
    assert isinstance(index, HybridChunkIndex)


def test_semantic_without_provider_fails_loudly() -> None:
    connection = connect_database(":memory:")
    with pytest.raises(ValueError) as exc_info:
        build_chunk_index(connection, RetrievalSettings(RetrievalMode.SEMANTIC), None)
    message = str(exc_info.value)
    assert "semantic" in message
    assert "PERSONAL_AI_EMBEDDING_MODEL" in message


def test_hybrid_without_provider_fails_loudly() -> None:
    connection = connect_database(":memory:")
    with pytest.raises(ValueError) as exc_info:
        build_chunk_index(connection, RetrievalSettings(RetrievalMode.HYBRID), None)
    message = str(exc_info.value)
    assert "hybrid" in message
    assert "PERSONAL_AI_EMBEDDING_MODEL" in message


class TestRuntimeChunkIndex:
    def test_keyword_default_never_builds_provider(self) -> None:
        connection = connect_database(":memory:")
        index = runtime_chunk_index(
            connection,
            embedding_provider_factory=ExplodingProviderFactory(),
            environ={"PERSONAL_AI_RETRIEVAL_MODE": "keyword"},
        )
        assert isinstance(index, SQLiteChunkIndex)

    def test_absent_mode_defaults_to_keyword_without_provider(self) -> None:
        connection = connect_database(":memory:")
        index = runtime_chunk_index(
            connection,
            embedding_provider_factory=ExplodingProviderFactory(),
            environ={},
        )
        assert isinstance(index, SQLiteChunkIndex)

    def test_embedding_model_alone_does_not_turn_on_provider(self) -> None:
        connection = connect_database(":memory:")
        index = runtime_chunk_index(
            connection,
            embedding_provider_factory=ExplodingProviderFactory(),
            environ={"PERSONAL_AI_EMBEDDING_MODEL": "nomic-embed-text"},
        )
        assert isinstance(index, SQLiteChunkIndex)

    def test_semantic_builds_provider_for_configured_model(self) -> None:
        connection = connect_database(":memory:")
        provider = MappingProvider({"query": (1.0, 0.0)})
        factory = RecordingProviderFactory(provider)
        index = runtime_chunk_index(
            connection,
            embedding_provider_factory=factory,
            environ={
                "PERSONAL_AI_RETRIEVAL_MODE": "semantic",
                "PERSONAL_AI_EMBEDDING_MODEL": "nomic-embed-text",
            },
        )
        assert isinstance(index, SemanticChunkIndex)
        assert [getattr(call, "model", None) for call in factory.calls] == [
            "nomic-embed-text"
        ]

    def test_hybrid_builds_provider_and_fuses_both_backends(self) -> None:
        connection = connect_database(":memory:")
        provider = MappingProvider({"query": (1.0, 0.0)})
        factory = RecordingProviderFactory(provider)
        index = runtime_chunk_index(
            connection,
            embedding_provider_factory=factory,
            environ={
                "PERSONAL_AI_RETRIEVAL_MODE": "hybrid",
                "PERSONAL_AI_EMBEDDING_MODEL": "nomic-embed-text",
            },
        )
        assert isinstance(index, HybridChunkIndex)

    def test_semantic_missing_embedding_model_fails_loudly(self) -> None:
        connection = connect_database(":memory:")
        with pytest.raises(ValueError) as exc_info:
            runtime_chunk_index(
                connection,
                embedding_provider_factory=RecordingProviderFactory(
                    MappingProvider({})
                ),
                environ={"PERSONAL_AI_RETRIEVAL_MODE": "semantic"},
            )
        assert "PERSONAL_AI_EMBEDDING_MODEL" in str(exc_info.value)

    def test_invalid_mode_fails_loudly(self) -> None:
        connection = connect_database(":memory:")
        with pytest.raises(ValueError):
            runtime_chunk_index(
                connection,
                embedding_provider_factory=ExplodingProviderFactory(),
                environ={"PERSONAL_AI_RETRIEVAL_MODE": "vector"},
            )


class TestFactoryDrivenHybridSearch:
    def test_factory_hybrid_backend_classifies_both_sources(self) -> None:
        connection = connect_database(":memory:")
        document_store = DocumentStore(connection)
        chunk_store = ChunkStore(connection)
        embedding_store = EmbeddingStore(connection)
        for document_id in ("d-kw", "d-sem", "d-both"):
            document_store.add(
                Document(
                    id=document_id,
                    source="notes.txt",
                    source_type="file",
                    content_hash="hash",
                    created_at="2026-08-26T10:00:00+00:00",
                    modified_at="2026-08-26T10:00:00+00:00",
                    metadata={},
                )
            )
        chunk_store.add(
            DocumentChunk(id="kw", document_id="d-kw", text="quarterly budget")
        )
        chunk_store.add(
            DocumentChunk(id="sem", document_id="d-sem", text="guitar music theory")
        )
        chunk_store.add(
            DocumentChunk(id="both", document_id="d-both", text="quarterly plan")
        )
        embedding_store.add(
            Embedding(model="semantic-test-model", vector=(0.0, 1.0)), "kw"
        )
        embedding_store.add(
            Embedding(model="semantic-test-model", vector=(1.0, 0.0)), "sem"
        )
        embedding_store.add(
            Embedding(model="semantic-test-model", vector=(0.9, 0.1)), "both"
        )

        provider = MappingProvider({"quarterly": (1.0, 0.0)})
        index = build_chunk_index(
            connection, RetrievalSettings(RetrievalMode.HYBRID), provider
        )
        assert isinstance(index, HybridChunkIndex)

        hits = index.search("quarterly", limit=10)

        # The keyword backend contributes ``kw`` (its text matches "quarterly")
        # even though its vector is orthogonal; the semantic backend contributes
        # ``sem`` (closest vector) even though its text does not match the query.
        found = {hit.chunk_id for hit in hits}
        assert {"kw", "sem", "both"} <= found
        assert provider.calls == ["quarterly"]

    def test_seed_mapping_provider_satisfies_protocol(self) -> None:
        assert isinstance(MappingProvider({}), EmbeddingProvider)


def test_retrieval_factory_remains_infrastructure_free() -> None:
    source = Path(
        __import__("personal_ai.retrieval_factory", fromlist=["x"]).__file__
    ).read_text(encoding="utf-8")
    for forbidden in ("httpx", "ollama", "OllamaClient", "OllamaEmbedder"):
        assert forbidden not in source, forbidden
