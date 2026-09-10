"""Hermetic Phase 37 evaluation: deterministic synthetic-fixture retrieval suite.

Measurement of the three ``ChunkIndex`` backends — keyword (FTS5), semantic
(embedding), and their RRF hybrid — against hand-authored relevance
judgments. Everything is deterministic (no Ollama, no network, no
randomness): the semantic backend is driven by a fixed concept-vector
provider and persisted embeddings; the corpus lives in an in-memory SQLite
database; metrics are pure functions over chunk identifiers.

Ergonomic note: the evaluation harness is intentionally measurement-only —
it drives backends through the public ``search`` surface and computes
recall/precision/hit/reciprocal-rank from the returned chunk ids. The
candidate-window, filter-propagation, prefix-stability, and fail-closed
behaviours are asserted here against the *real* indexes to prove the harness
measures production-relevant behaviour and not the harness itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personal_ai.documents import Document, DocumentChunk, Embedding
from personal_ai.documents.models import compute_content_hash
from personal_ai.hybrid_index import HybridChunkIndex
from personal_ai.retrieval import ChunkIndex
from personal_ai.retrieval_evaluation import (
    EvaluationCase,
    EvaluationResult,
    evaluate_case,
    hit_at_k,
    mean_reciprocal_rank,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    summarize,
)
from personal_ai.semantic_index import SemanticChunkIndex
from personal_ai.storage import (
    ChunkStore,
    DocumentFilter,
    DocumentStore,
    EmbeddingStore,
    connect_database,
)
from personal_ai.storage.chunks import ChunkSearchResult
from personal_ai.tools import create_default_registry
from personal_ai.tools.search import SearchTool

EVAL_MODEL = "eval-model"

# Concept axes shared by chunk vectors and query vectors, fixed order.
_AXES = (
    "backup",
    "retention",
    "encryption",
    "schedule",
    "ticket",
    "auth",
    "archive",
    "review",
    "plan",
    "project",
)

# (chunk_id, document_id, text, vector | None)
# ``None`` = no embedding: the chunk is present but not semantically indexable.
_CORPUS = [
    (
        "eval-backup-retention",
        "doc-eval",
        "PostgreSQL backup retention for the analytics database.",
        (3, 1, 0, 0, 0, 0, 0, 0, 0, 0),
    ),
    (
        "eval-snapshot-policy",
        "doc-eval",
        "Snapshot policy keeps four weekly database snapshots.",
        (0, 4, 0, 0, 0, 0, 1, 0, 0, 0),
    ),
    (
        "eval-backup-jobs",
        "doc-eval",
        "Daily backup jobs run on a fixed schedule.",
        (3, 0, 0, 2, 0, 0, 0, 0, 0, 0),
    ),
    (
        "eval-backup-encryption",
        "doc-eval",
        "Backup encryption protects the nightly archives.",
        (3, 0, 3, 0, 0, 0, 0, 0, 0, 0),
    ),
    (
        "eval-retention-policy",
        "doc-eval",
        "The retention policy spans twelve quarters of project work.",
        (0, 2, 0, 0, 0, 0, 0, 0, 0, 2),
    ),
    (
        "eval-nightly-copies",
        "doc-eval",
        "Nightly copies move data into the archive.",
        (0, 0, 0, 1, 0, 0, 3, 0, 0, 0),
    ),
    (
        "eval-ticket-EXA2017",
        "doc-eval",
        "Ticket EXA-2017 records the auth outage.",
        (0, 0, 0, 0, 4, 0, 0, 0, 0, 0),
    ),
    (
        "eval-ticket-EXA2018",
        "doc-eval",
        "Ticket EXA-2018 records the auth outage.",
        (0, 0, 0, 0, 4, 0, 0, 0, 0, 0),
    ),
    (
        "eval-ticket-auth991",
        "doc-eval",
        "Ticket AUTH991 includes the access review notes.",
        (0, 0, 0, 0, 2, 2, 0, 0, 0, 0),
    ),
    (
        "eval-dup-alpha",
        "doc-eval",
        "Quarterly plan review for the product roadmap.",
        (0, 0, 0, 0, 0, 0, 0, 0, 3, 0),
    ),
    (
        "eval-dup-beta",
        "doc-eval",
        "Quarterly plan review for the product roadmap.",
        (0, 0, 0, 0, 0, 0, 0, 0, 3, 0),
    ),
    (
        "eval-proj-alpha-cert",
        "doc-alpha",
        "Retire the legacy certificate service for the alpha project.",
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 3),
    ),
    (
        "eval-proj-beta-cert",
        "doc-beta",
        "Retire the legacy certificate service for the beta project.",
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 3),
    ),
]
for index in range(8):
    _CORPUS.append(
        (
            f"eval-window-c{index}",
            "doc-eval",
            f"Weekly checklist item for backup review {index}.",
            (0, 0, 0, 0, 0, 0, 0, 3, 0, 0),
        )
    )

# Deterministic provider mapping: query text -> concept vector. Unmapped
# text raises loudly (proves the provider was never needed when it should not
# have been called).
_QUERY_VECTORS: dict[str, tuple[float, ...]] = {
    "PostgreSQL backup retention": (2, 1, 0, 0, 0, 0, 0, 0, 0, 0),
    "How long are database backups kept?": (1, 3, 0, 0, 0, 0, 0, 0, 0, 0),
    "backup retention": (2, 2, 0, 0, 0, 0, 1, 0, 0, 0),
    "EXA-2017": (0, 0, 0, 0, 4, 0, 0, 0, 0, 0),
    "ticket": (0, 0, 0, 0, 4, 0, 0, 0, 0, 0),
    "retire legacy certificate service": (0, 0, 0, 0, 0, 0, 0, 0, 0, 3),
    "checklist backup": (0, 0, 0, 0, 0, 0, 0, 3, 0, 0),
    "quarterly plan review": (0, 0, 0, 0, 0, 0, 0, 0, 3, 0),
}


class DictProvider:
    """Deterministic fake provider: explicit query text -> vector mapping."""

    model = EVAL_MODEL

    def __init__(self, mapping: dict[str, tuple[float, ...]]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def embed(self, text: str) -> Embedding:
        self.calls.append(text)
        vector = self._mapping[text]
        return Embedding(model=self.model, vector=vector)


def _vector(design: dict[str, int]) -> tuple[float, ...]:
    return tuple(float(design.get(axis, 0)) for axis in _AXES)


class Corpus:
    """One in-memory SQLite corpus with keyword + semantic + hybrid backends."""

    def __init__(self) -> None:
        self.connection = connect_database(":memory:")
        self.message = ""
        self.documents = DocumentStore(self.connection)
        self.chunks = ChunkStore(self.connection)
        self.embeddings = EmbeddingStore(self.connection)
        self.provider = DictProvider(_QUERY_VECTORS)
        self._seed()
        self.backends = {
            "keyword": self.chunks,
            "semantic": SemanticChunkIndex(self.connection, self.provider),
            "hybrid": HybridChunkIndex(
                self.chunks, SemanticChunkIndex(self.connection, self.provider)
            ),
        }

    def _add_document(
        self,
        document_id: str,
        *,
        source_type: str = "eval",
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.documents.add(
            Document(
                id=document_id,
                source="synthetic/fixture.yml",
                source_type=source_type,
                content_hash=compute_content_hash(b"synthetic"),
                created_at="2026-08-26T10:00:00+00:00",
                modified_at="2026-08-26T10:00:00+00:00",
                metadata=metadata or {},
            )
        )

    def _seed(self) -> None:
        self._add_document("doc-eval")
        self._add_document(
            "doc-alpha", metadata={"mime_type": "application/x-eval-alpha"}
        )
        self._add_document(
            "doc-beta", metadata={"mime_type": "application/x-eval-beta"}
        )
        for chunk_id, document_id, text, vector in _CORPUS:
            self.chunks.add(
                DocumentChunk(id=chunk_id, document_id=document_id, text=text)
            )
            if vector is not None:
                self.embeddings.add(
                    Embedding(model=EVAL_MODEL, vector=vector), chunk_id
                )

    def close(self) -> None:
        self.connection.close()


@pytest.fixture()
def corpus() -> Corpus:
    fixture = Corpus()
    yield fixture
    fixture.close()


def _hit(
    chunk_id: str,
    rank: float = 0.0,
    document_id: str = "doc-1",
    text: str = "chunk text",
) -> ChunkSearchResult:
    return ChunkSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=0,
        text=text,
        rank=rank,
    )


class RecordingIndex:
    """A fake ``ChunkIndex`` recording every call and returning fixed hits."""

    def __init__(self, hits: tuple[ChunkSearchResult, ...] = ()) -> None:
        self._hits = hits
        self.calls: list[tuple[str, int, object]] = []

    def search(
        self,
        query: str,
        limit: int = 10,
        filters: DocumentFilter | None = None,
    ) -> tuple[ChunkSearchResult, ...]:
        self.calls.append((query, limit, filters))
        return self._hits


class FailingIndex:
    """A fake ``ChunkIndex`` that always raises — models unavailability."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def search(
        self,
        query: str,
        limit: int = 10,
        filters: DocumentFilter | None = None,
    ) -> tuple[ChunkSearchResult, ...]:
        raise self._error


# --- Metric conventions ------------------------------------------------------


def test_recall_at_k_empty_relevant_is_vacuous_perfect() -> None:
    assert recall_at_k(frozenset(), (), k=5) == 1.0
    assert recall_at_k(frozenset(), ("a", "b"), k=5) == 1.0


def test_recall_at_k_counts_hits_over_relevant_size() -> None:
    relevant = frozenset({"a", "b"})
    assert recall_at_k(relevant, ("a", "b", "c"), k=3) == 1.0
    assert recall_at_k(relevant, ("a", "c"), k=3) == pytest.approx(0.5)
    assert recall_at_k(relevant, (), k=3) == 0.0


def test_recall_at_k_honours_k_slice() -> None:
    relevant = frozenset({"b"})
    assert recall_at_k(relevant, ("x", "b"), k=1) == 0.0


def test_recall_at_k_rejects_zero_k() -> None:
    with pytest.raises(ValueError):
        recall_at_k(frozenset(), (), k=0)


def test_precision_at_k_fixed_denominator_penalises_short_lists() -> None:
    relevant = frozenset({"a"})
    assert precision_at_k(relevant, (), k=5) == 0.0
    assert precision_at_k(relevant, ("a",), k=5) == pytest.approx(1 / 5)
    assert precision_at_k(relevant, ("a",), k=1) == 1.0
    assert precision_at_k(frozenset(), ("a",), k=5) == 0.0


def test_precision_at_k_rejects_zero_k() -> None:
    with pytest.raises(ValueError):
        precision_at_k(frozenset({"a"}), ("a",), k=0)


def test_hit_at_k_reports_first_relevant_in_window() -> None:
    relevant = frozenset({"b"})
    assert hit_at_k(relevant, ("a", "b", "c"), k=2) == 1
    assert hit_at_k(relevant, ("a", "b", "c"), k=1) == 0
    assert hit_at_k(frozenset(), ("a",), k=5) == 0


def test_reciprocal_rank_uses_zero_based_positions() -> None:
    relevant = frozenset({"b"})
    assert reciprocal_rank(relevant, ("b", "a")) == 1.0
    assert reciprocal_rank(relevant, ("a", "b")) == pytest.approx(0.5)
    assert reciprocal_rank(relevant, ("a", "c")) == 0.0
    assert reciprocal_rank(frozenset(), ("b",)) == 0.0


def test_mean_reciprocal_rank_averages_and_empty_is_zero() -> None:
    assert mean_reciprocal_rank((1.0, 0.5)) == pytest.approx(0.75)
    assert mean_reciprocal_rank(()) == 0.0


def test_metric_helpers_are_pure_over_ids_not_scores() -> None:
    # Metrics consume chunk identifiers only; a result whose rank differs
    # never changes the measurement.
    relevant = frozenset({"a", "b"})
    assert recall_at_k(relevant, ("a", "b"), k=2) == 1.0
    assert precision_at_k(relevant, ("a", "b"), k=2) == 1.0


# --- Harness contract --------------------------------------------------------


def test_semantic_and_hybrid_are_chunk_indexes(corpus: Corpus) -> None:
    assert isinstance(corpus.backends["semantic"], ChunkIndex)
    assert isinstance(corpus.backends["hybrid"], ChunkIndex)
    assert isinstance(corpus.backends["keyword"], ChunkIndex)


def test_evaluate_case_returns_one_result_per_backend(corpus: Corpus) -> None:
    case = EvaluationCase(
        name="exact_lexical",
        query="PostgreSQL backup retention",
        relevant_chunk_ids=frozenset({"eval-backup-retention"}),
    )
    results = evaluate_case(case, corpus.backends, k=1)

    assert [r.backend for r in results] == ["keyword", "semantic", "hybrid"]
    assert all(r.case is case and r.k == 1 for r in results)
    assert all(r.count == 1 for r in results)
    assert all(r.recall == 1.0 for r in results)
    assert all(r.precision == 1.0 for r in results)
    assert all(r.hit == 1 for r in results)
    assert all(r.reciprocal_rank == 1.0 for r in results)


def test_evaluate_case_respects_explicit_limit(corpus: Corpus) -> None:
    case = EvaluationCase(name="window", query="checklist backup")
    results = evaluate_case(case, corpus.backends, k=2, limit=5)
    assert [r.count for r in results] == [5, 5, 5]


def test_evaluate_case_rejects_small_k_and_limit(corpus: Corpus) -> None:
    case = EvaluationCase(name="c", query="backup retention")
    with pytest.raises(ValueError):
        evaluate_case(case, corpus.backends, k=0)
    with pytest.raises(ValueError):
        evaluate_case(case, corpus.backends, k=1, limit=0)


def test_evaluate_case_fails_loud_when_backend_raises() -> None:
    case = EvaluationCase(name="boom", query="x")
    backends = {
        "keyword": FailingIndex(RuntimeError("store unavailable")),
        "semantic": RecordingIndex((_hit("s1"),)),
    }
    with pytest.raises(RuntimeError, match="store unavailable"):
        evaluate_case(case, backends, k=1)


def test_report_surface_is_aggregate_only(corpus: Corpus) -> None:
    cases = [
        EvaluationCase(
            name="exact_lexical",
            query="PostgreSQL backup retention",
            relevant_chunk_ids=frozenset({"eval-backup-retention"}),
        ),
        EvaluationCase(
            name="paraphrase",
            query="How long are database backups kept?",
            relevant_chunk_ids=frozenset({"eval-snapshot-policy"}),
        ),
    ]
    results = tuple(
        result for case in cases for result in evaluate_case(case, corpus.backends, k=1)
    )
    rows = summarize(results)
    assert "keyword" in rows and "semantic" in rows and "hybrid" in rows

    # Format is numeric plus case names only — never query text, chunk ids,
    # or document provenance.
    for sensitive in (
        "PostgreSQL",
        "How long",
        "eval-backup-retention",
        "synthetic/fixture.yml",
    ):
        assert sensitive not in rows


def test_evaluation_result_format_row_is_numeric() -> None:
    case = EvaluationCase(
        name="exact_lexical",
        query="PostgreSQL backup retention",
        relevant_chunk_ids=frozenset({"eval-backup-retention"}),
    )
    result = EvaluationResult(
        case=case, backend="keyword", k=1, returned_chunk_ids=("eval-backup-retention",)
    )
    row = result.format_row()
    assert row.startswith("keyword")
    assert "recall=1.000" in row and "precision=1.000" in row
    assert "count=" in row and "rr=1.000" in row


# --- Evaluation cases (real backends) ----------------------------------------


def test_paraphrase_recovers_semantic_signal_keyword_misses(corpus: Corpus) -> None:
    case = EvaluationCase(
        name="paraphrase",
        query="How long are database backups kept?",
        relevant_chunk_ids=frozenset({"eval-snapshot-policy"}),
    )
    result = {r.backend: r for r in evaluate_case(case, corpus.backends, k=1)}

    assert result["keyword"].count == 0
    assert result["keyword"].recall == 0.0
    assert result["keyword"].precision == 0.0
    assert result["keyword"].reciprocal_rank == 0.0

    assert result["semantic"].returned_chunk_ids == ("eval-snapshot-policy",)
    assert result["semantic"].recall == 1.0
    assert result["semantic"].reciprocal_rank == 1.0

    assert result["hybrid"].recall == 1.0
    assert result["hybrid"].reciprocal_rank == 1.0
    assert result["hybrid"].returned_chunk_ids == ("eval-snapshot-policy",)


def test_mixed_query_fusion_rescues_second_relevant_hit(corpus: Corpus) -> None:
    case = EvaluationCase(
        name="mixed",
        query="backup retention",
        relevant_chunk_ids=frozenset({"eval-backup-retention", "eval-snapshot-policy"}),
    )
    result = {r.backend: r for r in evaluate_case(case, corpus.backends, k=2)}

    assert result["keyword"].returned_chunk_ids == ("eval-backup-retention",)
    assert result["keyword"].recall == pytest.approx(0.5)
    assert result["keyword"].precision == pytest.approx(0.5)

    assert result["semantic"].recall == 1.0
    assert result["semantic"].precision == 1.0

    assert result["hybrid"].recall == 1.0
    assert result["hybrid"].precision == 1.0


def test_exact_identifier_is_found_by_all_backends(corpus: Corpus) -> None:
    case = EvaluationCase(
        name="exact_identifier",
        query="EXA-2017",
        relevant_chunk_ids=frozenset({"eval-ticket-EXA2017"}),
    )
    for result in evaluate_case(case, corpus.backends, k=1):
        assert result.returned_chunk_ids == ("eval-ticket-EXA2017",)
        assert result.recall == 1.0
        assert result.hit == 1
        assert result.reciprocal_rank == 1.0


def test_lexical_distractor_occupies_a_precision_slot(corpus: Corpus) -> None:
    relevant = frozenset({"eval-ticket-EXA2017", "eval-ticket-EXA2018"})
    case = EvaluationCase(
        name="lexical_distractor", query="ticket", relevant_chunk_ids=relevant
    )
    result = {r.backend: r for r in evaluate_case(case, corpus.backends, k=3, limit=3)}

    expected_set = {"eval-ticket-EXA2017", "eval-ticket-EXA2018", "eval-ticket-auth991"}
    for backend in ("keyword", "semantic", "hybrid"):
        assert set(result[backend].returned_chunk_ids) == expected_set
        assert result[backend].recall == 1.0
        assert result[backend].precision == pytest.approx(2 / 3)
        assert result[backend].hit == 1

    # Semantic ordering is deterministic (exact score computation): EXA2017 /
    # EXA2018 both at cosine 1.0, then the auth distractor at 0.707.
    assert result["semantic"].returned_chunk_ids == (
        "eval-ticket-EXA2017",
        "eval-ticket-EXA2018",
        "eval-ticket-auth991",
    )

    # Keyword and hybrid orderings depend on BM25, which is not hand-computed
    # here — assert full determinism instead of a guessed order.
    first = [
        r.returned_chunk_ids for r in evaluate_case(case, corpus.backends, k=3, limit=3)
    ]
    second = [
        r.returned_chunk_ids for r in evaluate_case(case, corpus.backends, k=3, limit=3)
    ]
    assert first == second


def test_zero_similarity_chunks_are_observed_not_measured(corpus: Corpus) -> None:
    # Documented finding: SemanticChunkIndex includes candidates whose cosine
    # is >= 0.0, so with non-negative concept vectors a fully-embedded corpus
    # never produces "no semantic match". This is a fixture/interpretation
    # limitation of the synthetic harness, not a production defect.
    hits = corpus.backends["semantic"].search("ticket", limit=200)
    assert len(hits) == len(_CORPUS)
    relevant = {"eval-ticket-EXA2017", "eval-ticket-EXA2018"}
    assert all(h.chunk_id in relevant for h in hits[:2])
    assert any(h.chunk_id not in relevant for h in hits[2:])  # zero-cos slices leak


def test_duplicate_text_stays_two_results_in_every_backend(corpus: Corpus) -> None:
    case = EvaluationCase(
        name="duplicate_text",
        query="quarterly plan review",
        relevant_chunk_ids=frozenset({"eval-dup-alpha", "eval-dup-beta"}),
    )
    result = {r.backend: r for r in evaluate_case(case, corpus.backends, k=2)}

    for backend in ("keyword", "semantic", "hybrid"):
        assert set(result[backend].returned_chunk_ids) == {
            "eval-dup-alpha",
            "eval-dup-beta",
        }
        assert result[backend].count == 2
        assert result[backend].recall == 1.0
        assert result[backend].precision == 1.0


def test_provenance_survives_through_the_hybrid_index(corpus: Corpus) -> None:
    results = corpus.backends["hybrid"].search("PostgreSQL backup retention", limit=1)
    assert len(results) == 1
    hit = results[0]
    assert hit.chunk_id == "eval-backup-retention"
    assert hit.document_id == "doc-eval"
    assert hit.source_type == "eval"
    assert hit.source == "synthetic/fixture.yml"


# --- Candidate-window behaviour (real backends) -------------------------------


def test_window_top_k_stability_and_candidate_window(corpus: Corpus) -> None:
    for backend in corpus.backends.values():
        s1 = backend.search("checklist backup", limit=1)
        assert [h.chunk_id for h in s1] == ["eval-window-c0"]

        s5 = backend.search("checklist backup", limit=5)
        assert [h.chunk_id for h in s5] == [f"eval-window-c{i}" for i in range(5)]

        s8 = backend.search("checklist backup", limit=8)
        assert [h.chunk_id for h in s8] == [f"eval-window-c{i}" for i in range(8)]

    keyword = corpus.backends["keyword"]
    assert keyword.search("checklist backup", limit=25).__len__() == 8
    semantic = corpus.backends["semantic"]
    assert semantic.search("checklist backup", limit=25).__len__() == 21


def test_hybrid_passes_candidate_window_to_backends(corpus: Corpus) -> None:
    keyword = RecordingIndex((_hit("k1"),))
    semantic = RecordingIndex((_hit("s1"),))
    hybrid = HybridChunkIndex(keyword, semantic)
    assert [h.chunk_id for h in hybrid.search("anything", limit=1)] == ["k1"]
    assert keyword.calls == [("anything", 4, None)]
    assert semantic.calls == [("anything", 4, None)]
    assert [h.chunk_id for h in hybrid.search("anything", limit=5)] == ["k1", "s1"]
    assert keyword.calls[-1] == ("anything", 20, None)
    assert semantic.calls[-1] == ("anything", 20, None)
    assert [h.chunk_id for h in hybrid.search("anything", limit=100)] == ["k1", "s1"]
    assert keyword.calls[-1] == ("anything", 200, None)
    assert semantic.calls[-1] == ("anything", 200, None)


def test_hybrid_results_prefix_is_stable_across_limits(corpus: Corpus) -> None:
    hybrid = corpus.backends["hybrid"]
    wide = [h.chunk_id for h in hybrid.search("checklist backup", limit=25)]
    narrow = [h.chunk_id for h in hybrid.search("checklist backup", limit=5)]
    assert (
        wide[:5]
        == narrow
        == [
            "eval-window-c0",
            "eval-window-c1",
            "eval-window-c2",
            "eval-window-c3",
            "eval-window-c4",
        ]
    )


# --- Document-metadata filtering ----------------------------------------------


def _cert_cases() -> tuple[EvaluationCase, ...]:
    both = frozenset({"eval-proj-alpha-cert", "eval-proj-beta-cert"})
    alpha = DocumentFilter(mime_types=("application/x-eval-alpha",))
    beta = DocumentFilter(mime_types=("application/x-eval-beta",))
    gamma = DocumentFilter(mime_types=("application/x-eval-gamma",))
    return (
        EvaluationCase(
            name="cert_unfiltered",
            query="retire legacy certificate service",
            relevant_chunk_ids=both,
        ),
        EvaluationCase(
            name="cert_alpha",
            query="retire legacy certificate service",
            relevant_chunk_ids=frozenset({"eval-proj-alpha-cert"}),
            filters=alpha,
        ),
        EvaluationCase(
            name="cert_beta",
            query="retire legacy certificate service",
            relevant_chunk_ids=frozenset({"eval-proj-beta-cert"}),
            filters=beta,
        ),
        EvaluationCase(
            name="cert_both",
            query="retire legacy certificate service",
            relevant_chunk_ids=both,
            filters=DocumentFilter(
                mime_types=("application/x-eval-alpha", "application/x-eval-beta")
            ),
        ),
        EvaluationCase(
            name="cert_gamma",
            query="retire legacy certificate service",
            relevant_chunk_ids=frozenset(),
            filters=gamma,
        ),
    )


def test_filtering_restricts_every_backend_to_owning_document(corpus: Corpus) -> None:
    for case in _cert_cases():
        for result in evaluate_case(case, corpus.backends, k=2):
            assert set(result.returned_chunk_ids) == set(case.relevant_chunk_ids), (
                f"{result.backend} / {case.name}: got {result.returned_chunk_ids}"
            )


def test_filtering_never_surfaces_an_excluded_chunk(corpus: Corpus) -> None:
    alpha = DocumentFilter(mime_types=("application/x-eval-alpha",))
    for backend in corpus.backends.values():
        hits = backend.search(
            "retire legacy certificate service", limit=10, filters=alpha
        )
        assert {h.chunk_id for h in hits} == {"eval-proj-alpha-cert"}


def test_no_matching_filter_is_a_legitimate_no_result(corpus: Corpus) -> None:
    gamma = DocumentFilter(mime_types=("application/x-eval-gamma",))
    calls_before = list(corpus.provider.calls)
    for backend in corpus.backends.values():
        hits = backend.search(
            "retire legacy certificate service", limit=10, filters=gamma
        )
        assert hits == ()
    assert corpus.provider.calls == calls_before


# --- No-result handling (dedicated corpus without embeddings) ------------------


def test_no_result_corpus_is_empty_for_every_backend() -> None:
    connection = connect_database(":memory:")
    provider = DictProvider({})
    try:
        chunks = ChunkStore(connection)
        EmbeddingStore(connection)
        chunks.add_many(
            (
                DocumentChunk(
                    id="recipe-1", document_id="doc-1", text="blueberry muffin recipe"
                ),
                DocumentChunk(
                    id="recipe-2", document_id="doc-1", text="overnight oatmeal jar"
                ),
            )
        )
        backends = {
            "keyword": chunks,
            "semantic": SemanticChunkIndex(connection, provider),
            "hybrid": HybridChunkIndex(
                chunks, SemanticChunkIndex(connection, provider)
            ),
        }
        for backend in backends.values():
            assert (
                backend.search("quantum espresso machine brewing schedule", limit=10)
                == ()
            )
        assert provider.calls == []

        case = EvaluationCase(
            name="no_result",
            query="quantum espresso machine brewing schedule",
            relevant_chunk_ids=frozenset(),
        )
        for result in evaluate_case(case, backends, k=5):
            assert result.count == 0
            assert result.returned_chunk_ids == ()
            assert result.precision == 0.0
            assert result.hit == 0
    finally:
        connection.close()


# --- Independent fusion expectation (seam fakes through the harness) -----------


def test_hybrid_fusion_matches_independent_rrf_expectation() -> None:
    keyword = RecordingIndex((_hit("A"), _hit("B"), _hit("C")))
    semantic = RecordingIndex((_hit("B"), _hit("A"), _hit("D")))
    hybrid = HybridChunkIndex(keyword, semantic)
    hits = hybrid.search("query", limit=4)
    assert [h.chunk_id for h in hits] == ["A", "B", "C", "D"]
    assert all(h.rank == pytest.approx(1.0 / 60.0 + 1.0 / 61.0) for h in hits[:2])
    assert all(h.rank == pytest.approx(1.0 / 62.0) for h in hits[2:])

    case = EvaluationCase(
        name="fusion",
        query="query",
        relevant_chunk_ids=frozenset({"A", "B"}),
    )
    (measured,) = evaluate_case(case, {"hybrid": hybrid}, k=2)
    assert measured.returned_chunk_ids == ("A", "B")
    assert measured.recall == 1.0
    assert measured.precision == 1.0
    assert measured.reciprocal_rank == 1.0


# --- Fail-closed envelope ------------------------------------------------------


def test_hybrid_backend_failure_surfaces_as_retrieval_error_envelope() -> None:
    keyword = RecordingIndex((_hit("k1"),))
    semantic = FailingIndex(RuntimeError("embedding backend unavailable"))
    hybrid = HybridChunkIndex(keyword, semantic)
    envelope = SearchTool(hybrid).search_documents({"query": "anything", "limit": 5})
    assert envelope["status"] == "error"
    assert envelope["error"] == "retrieval_unavailable"
    assert envelope["total_returned"] == 0


# --- Default keyword wiring (chat registry) ------------------------------------


def _registry(corpus: Corpus, tmp_path: Path):
    return create_default_registry(tmp_path, chunk_store=corpus.chunks)


def test_chat_search_documents_remains_keyword_only(
    corpus: Corpus, tmp_path: Path
) -> None:
    # search_documents is the narrow chunks keyword tool; even though the
    # corpus carries semantic embeddings, the interactive chat wiring is
    # keyword-only by default (no hybrid/semantic selection knob exists).
    registry = create_default_registry(tmp_path, chunk_store=corpus.chunks)

    envelope = registry.execute(
        "search_documents", {"query": "PostgreSQL backup retention"}
    )
    assert envelope["status"] == "results"  # type: ignore[index]
    assert envelope["total_returned"] == 1  # type: ignore[index]
    assert envelope["results"][0]["chunk_id"] == "eval-backup-retention"  # type: ignore[index]

    # The paraphrase that only the semantic backend could answer yields the
    # canonical keyword no_matches outcome.
    envelope = registry.execute(
        "search_documents", {"query": "How long are database backups kept?"}
    )
    assert envelope["status"] == "no_matches"  # type: ignore[index]
    assert envelope["total_returned"] == 0  # type: ignore[index]

    names = [
        schema["function"]["name"]
        for schema in registry.schemas()  # type: ignore[index]
    ]
    assert "search_documents" in names
