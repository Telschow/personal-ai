"""Hermetic tests for the semantic (embedding-backed) ChunkIndex backend."""

import math

import pytest

from personal_ai.documents import Document, DocumentChunk, Embedding
from personal_ai.documents.embedding import EmbeddingProvider
from personal_ai.documents.models import compute_content_hash, compute_document_id
from personal_ai.retrieval import ChunkIndex, SearchDocumentsRequest, search_documents
from personal_ai.semantic_index import SemanticChunkIndex, cosine_similarity
from personal_ai.storage import (
    ChunkStore,
    DocumentFilter,
    DocumentStore,
    EmbeddingStore,
    connect_database,
)
from personal_ai.tools.search import SearchTool


class MappingProvider:
    """Deterministic fake provider: explicit text -> vector mapping.

    Unmapped text falls back to a fixed neutral default so behavior stays
    fully deterministic and never touches a network.
    """

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


class NeverProvider:
    """Provider that must never be invoked; fails loudly if it is."""

    model = "semantic-test-model"

    def embed(self, text: str) -> Embedding:
        raise AssertionError(f"provider must not be called, got {text!r}")


class FailProvider(MappingProvider):
    """Provider that raises — models embedding backend unavailability."""

    def embed(self, text: str) -> Embedding:
        raise RuntimeError("embedding backend unavailable")


class Harness:
    def __init__(self) -> None:
        self.connection = connect_database(":memory:")
        self.chunk_store = ChunkStore(self.connection)
        self.embedding_store = EmbeddingStore(self.connection)
        self.document_store = DocumentStore(self.connection)

    def add_document(
        self,
        document_id: str,
        *,
        source_type: str = "file",
        source: str = "notes.txt",
        created_at: str = "2026-08-26T10:00:00+00:00",
        metadata: dict[str, object] | None = None,
    ) -> str:
        self.document_store.add(
            Document(
                id=document_id,
                source=source,
                source_type=source_type,
                content_hash=compute_content_hash(b"x"),
                created_at=created_at,
                modified_at=created_at,
                metadata=metadata or {},
            )
        )
        return document_id

    def add_chunk(self, chunk_id: str, document_id: str, text: str) -> str:
        self.chunk_store.add(
            DocumentChunk(id=chunk_id, document_id=document_id, text=text)
        )
        return chunk_id

    def add_embedding(
        self,
        chunk_id: str,
        vector: tuple[float, ...],
        model: str = "semantic-test-model",
    ) -> None:
        self.embedding_store.add(Embedding(model=model, vector=vector), chunk_id)

    def index(self, provider: EmbeddingProvider) -> SemanticChunkIndex:
        return SemanticChunkIndex(self.connection, provider)

    def snapshot(self) -> tuple[tuple[object, ...], tuple[object, ...]]:
        chunks = tuple(
            self.connection.execute(
                "SELECT * FROM document_chunks ORDER BY chunk_id"
            ).fetchall()
        )
        embeddings = tuple(
            self.connection.execute(
                "SELECT * FROM chunk_embeddings ORDER BY chunk_id"
            ).fetchall()
        )
        return chunks, embeddings


def _doc(source_type: str = "file") -> str:
    return compute_document_id(source_type, "synthetic", "hash")


# --- Provider contract ----------------------------------------------------


def test_mapping_provider_satisfies_embedding_provider_protocol() -> None:
    assert isinstance(MappingProvider({}), EmbeddingProvider)


def test_semantic_chunk_index_satisfies_chunk_index_protocol() -> None:
    harness = Harness()
    assert isinstance(harness.index(MappingProvider({})), ChunkIndex)


def test_cosine_similarity_contract() -> None:
    assert cosine_similarity((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)
    assert cosine_similarity((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)
    assert cosine_similarity((1.0, 0.0), (-1.0, 0.0)) == pytest.approx(-1.0)
    assert cosine_similarity((2.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)


def test_cosine_zero_norm_is_defined_as_zero() -> None:
    assert cosine_similarity((0.0, 0.0), (1.0, 0.0)) == 0.0
    assert cosine_similarity((0.0, 0.0), (0.0, 0.0)) == 0.0


def test_cosine_invalid_vectors_raise() -> None:
    with pytest.raises(ValueError):
        cosine_similarity((1.0, 0.0), (1.0,))
    with pytest.raises(ValueError):
        cosine_similarity((), ())
    with pytest.raises(ValueError):
        cosine_similarity((float("nan"), 0.0), (1.0, 0.0))
    with pytest.raises(ValueError):
        cosine_similarity((1.0, 0.0), (float("inf"), 0.0))


# --- Semantic retrieval ----------------------------------------------------


def test_exact_match_ranks_above_similar_and_unrelated() -> None:
    harness = Harness()
    harness.add_chunk("exact", "d1", "quarterly budget")
    harness.add_chunk("similar", "d2", "quarterly financial plan")
    harness.add_chunk("unrelated", "d3", "guitar music theory")
    harness.add_embedding("exact", (1.0, 0.0))
    harness.add_embedding("similar", (0.5, math.sqrt(0.75)))
    harness.add_embedding("unrelated", (0.0, 1.0))
    provider = MappingProvider({"quarterly budget": (1.0, 0.0)})

    hits = harness.index(provider).search("quarterly budget")

    assert [hit.chunk_id for hit in hits] == ["exact", "similar", "unrelated"]
    assert hits[0].rank == pytest.approx(1.0)
    assert hits[1].rank == pytest.approx(0.5)
    assert hits[2].rank == pytest.approx(0.0)
    assert provider.calls == ["quarterly budget"]


def test_negative_similarity_candidates_are_not_matches() -> None:
    harness = Harness()
    harness.add_chunk("left", "d1", "left text")
    harness.add_chunk("right", "d2", "right text")
    harness.add_embedding("left", (1.0, 0.0))
    harness.add_embedding("right", (-1.0, 0.0))
    provider = MappingProvider({"query": (1.0, 0.0)})

    hits = harness.index(provider).search("query")

    assert [hit.chunk_id for hit in hits] == ["left"]


def test_no_stored_embeddings_returns_empty_without_provider_call() -> None:
    harness = Harness()
    harness.add_chunk("a", "d1", "text")

    hits = harness.index(NeverProvider()).search("anything")

    assert hits == ()


def test_no_positive_similarity_still_embeds_query() -> None:
    harness = Harness()
    harness.add_chunk("far", "d1", "opposite")
    harness.add_embedding("far", (-1.0, 0.0))
    provider = MappingProvider({"query": (1.0, 0.0)})

    hits = harness.index(provider).search("query")

    assert hits == ()
    assert provider.calls == ["query"]


def test_result_limit_and_zero_limit() -> None:
    harness = Harness()
    harness.add_chunk("a", "d1", "one")
    harness.add_chunk("b", "d2", "two")
    harness.add_chunk("c", "d3", "three")
    for chunk_id in ("a", "b", "c"):
        harness.add_embedding(chunk_id, (1.0, 0.0))
    provider = MappingProvider({"query": (1.0, 0.0)})

    assert [
        hit.chunk_id for hit in harness.index(provider).search("query", limit=2)
    ] == [
        "a",
        "b",
    ]
    assert harness.index(provider).search("query", limit=0) == ()


def test_negative_limit_rejected() -> None:
    harness = Harness()
    harness.add_chunk("a", "d1", "text")
    harness.add_embedding("a", (1.0, 0.0))
    with pytest.raises(ValueError):
        harness.index(MappingProvider({"query": (1.0, 0.0)})).search("query", limit=-1)


def test_equal_similarities_tie_break_by_chunk_id() -> None:
    harness = Harness()
    for chunk_id in ("b", "a", "c"):
        harness.add_chunk(chunk_id, "d1", chunk_id)
        harness.add_embedding(chunk_id, (1.0, 0.0))
    provider = MappingProvider({"query": (1.0, 0.0)})

    hits = harness.index(provider).search("query")

    assert [hit.chunk_id for hit in hits] == ["a", "b", "c"]


def test_unicode_text_is_preserved() -> None:
    harness = Harness()
    harness.add_chunk("m", "d1", "München Reiseplanung für 2026")
    harness.add_embedding("m", (1.0, 0.0))
    provider = MappingProvider({"München": (1.0, 0.0)})

    hits = harness.index(provider).search("München")

    assert len(hits) == 1
    assert hits[0].text == "München Reiseplanung für 2026"
    assert hits[0].rank == pytest.approx(1.0)


def test_empty_and_whitespace_queries_never_touch_provider() -> None:
    harness = Harness()
    harness.add_chunk("a", "d1", "text")
    harness.add_embedding("a", (1.0, 0.0))
    index = harness.index(NeverProvider())

    assert index.search("") == ()
    assert index.search("   ") == ()


def test_semantic_hits_carry_document_provenance() -> None:
    harness = Harness()
    doc_id = harness.add_document(_doc(), source_type="file", source="notes.txt")
    harness.add_chunk("a", doc_id, "quarterly budget")
    harness.add_embedding("a", (1.0, 0.0))
    provider = MappingProvider({"query": (1.0, 0.0)})

    hits = harness.index(provider).search("query")

    assert hits[0].source_type == "file"
    assert hits[0].source == "notes.txt"


# --- Vector validation / model identity -------------------------------------


def test_same_model_dimension_mismatch_fails_deterministically() -> None:
    harness = Harness()
    harness.add_chunk("a", "d1", "text")
    harness.add_embedding("a", (1.0, 0.0))
    provider = MappingProvider({"query": (0.1,)}, model="semantic-test-model")

    with pytest.raises(ValueError):
        harness.index(provider).search("query")


def test_other_model_vectors_are_never_compared() -> None:
    harness = Harness()
    harness.add_chunk("old", "d1", "old text")
    harness.add_embedding("old", (1.0, 0.0), model="other-model")
    provider = MappingProvider({})

    hits = harness.index(provider).search("anything")

    assert hits == ()
    assert provider.calls == []


def test_mixed_model_corpus_returns_only_current_model_chunks() -> None:
    harness = Harness()
    harness.add_chunk("old", "d1", "old text")
    harness.add_chunk("new", "d2", "new text")
    harness.add_embedding("old", (1.0, 0.0), model="old-model")
    harness.add_embedding("new", (1.0, 0.0), model="semantic-test-model")
    provider = MappingProvider({"query": (1.0, 0.0)})

    hits = harness.index(provider).search("query")

    assert [hit.chunk_id for hit in hits] == ["new"]


# --- Filters ----------------------------------------------------------------


def test_document_filter_restricts_candidates_before_similarity() -> None:
    harness = Harness()
    email_doc = harness.add_document(
        _doc("email"), source_type="email", source="me@example.test"
    )
    note_doc = harness.add_document(
        _doc("file"), source_type="file", source="notes.txt"
    )
    harness.add_chunk("mail", email_doc, "quarterly budget")
    harness.add_chunk("note", note_doc, "quarterly budget")
    harness.add_embedding("mail", (1.0, 0.0))
    harness.add_embedding("note", (1.0, 0.0))
    provider = MappingProvider({"query": (1.0, 0.0)})

    hits = harness.index(provider).search(
        "query", filters=DocumentFilter(source_types=("email",))
    )

    assert [hit.chunk_id for hit in hits] == ["mail"]


def test_document_filter_by_mime_type_and_created_window() -> None:
    harness = Harness()
    old = harness.add_document(
        _doc("file"),
        source_type="file",
        created_at="2025-01-01T00:00:00+00:00",
        metadata={"mime_type": "text/markdown"},
    )
    recent = harness.add_document(
        _doc("file2"),
        source_type="file",
        created_at="2026-06-01T00:00:00+00:00",
        metadata={"mime_type": "application/pdf"},
    )
    harness.add_chunk("old", old, "quarterly budget")
    harness.add_chunk("recent", recent, "quarterly budget")
    harness.add_embedding("old", (1.0, 0.0))
    harness.add_embedding("recent", (1.0, 0.0))
    provider = MappingProvider({"query": (1.0, 0.0)})

    hits = harness.index(provider).search(
        "query",
        filters=DocumentFilter(
            mime_types=("application/pdf",),
            created_after="2026-01-01",
            created_before="2026-12-31",
        ),
    )

    assert [hit.chunk_id for hit in hits] == ["recent"]


# --- Read-only guarantee -----------------------------------------------------


def test_search_is_observationally_read_only() -> None:
    harness = Harness()
    doc_id = harness.add_document(_doc())
    harness.add_chunk("a", doc_id, "quarterly budget")
    harness.add_chunk("b", doc_id, "guitar chords")
    harness.add_embedding("a", (1.0, 0.0))
    harness.add_embedding("b", (0.0, 1.0))
    provider = MappingProvider({"query": (1.0, 0.0)})
    index = harness.index(provider)

    before = harness.snapshot()
    hits = index.search("query")
    filtered = index.search("query", filters=DocumentFilter(source_types=("file",)))
    after = harness.snapshot()

    assert len(hits) == 2
    assert len(filtered) == 2
    assert before == after


def test_query_embedding_generation_persists_nothing() -> None:
    harness = Harness()
    harness.add_chunk("a", _doc("file"), "quarterly budget")
    harness.add_embedding("a", (1.0, 0.0))
    provider = MappingProvider({"query": (1.0, 0.0)})
    before = harness.snapshot()

    harness.index(provider).search("query")
    harness.index(provider).search("query")

    assert harness.snapshot() == before
    assert provider.calls == ["query", "query"]


# --- Provider failures -------------------------------------------------------


def test_provider_failure_propagates_and_is_never_no_matches() -> None:
    harness = Harness()
    harness.add_chunk("a", "d1", "text")
    harness.add_embedding("a", (1.0, 0.0))

    with pytest.raises(RuntimeError, match="embedding backend unavailable"):
        harness.index(FailProvider({})).search("query")


def test_search_tool_wraps_semantic_provider_failure_as_error() -> None:
    harness = Harness()
    harness.add_chunk("a", "d1", "text")
    harness.add_embedding("a", (1.0, 0.0))
    tool = SearchTool(harness.index(FailProvider({})))

    result = tool.search_documents({"query": "query"})

    assert result["status"] == "error"
    assert result["error"] == "retrieval_unavailable"


# --- Integration: consumer unaware of the backend ----------------------------


def test_search_documents_accepts_the_semantic_backend() -> None:
    harness = Harness()
    harness.add_chunk("a", "d1", "quarterly budget")
    harness.add_chunk("b", "d2", "guitar chords")
    harness.add_embedding("a", (1.0, 0.0))
    harness.add_embedding("b", (0.0, 1.0))
    provider = MappingProvider({"quarterly budget": (1.0, 0.0)})

    hits = search_documents(
        harness.index(provider),
        SearchDocumentsRequest(query="quarterly budget", limit=2),
    )

    assert [hit.chunk_id for hit in hits] == ["a", "b"]
    assert hits[0].rank > hits[1].rank


def test_search_tool_consumes_semantic_backend_end_to_end() -> None:
    harness = Harness()
    harness.add_chunk("a", "d1", "quarterly budget")
    harness.add_chunk("b", "d2", "guitar chords")
    harness.add_embedding("a", (1.0, 0.0))
    harness.add_embedding("b", (0.0, 1.0))
    provider = MappingProvider({"quarterly budget": (1.0, 0.0)})

    result = SearchTool(harness.index(provider)).search_documents(
        {"query": "quarterly budget", "limit": 2}
    )

    assert result["status"] == "results"
    assert [item["chunk_id"] for item in result["results"]] == ["a", "b"]
