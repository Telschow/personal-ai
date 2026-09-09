"""Hermetic tests for the hybrid (RRF) ChunkIndex composition backend.

The hybrid layer is tested exclusively through dependency-seam fakes — no
SQLite, no FTS5, no embeddings, no providers — to prove the composition
algorithm independently of any concrete backend.
"""

import pytest

from personal_ai.hybrid_index import HybridChunkIndex
from personal_ai.retrieval import ChunkIndex, SearchDocumentsRequest, search_documents
from personal_ai.storage import DocumentFilter
from personal_ai.storage.chunks import DEFAULT_SEARCH_LIMIT, ChunkSearchResult
from personal_ai.tools.search import SearchTool


def _hit(
    chunk_id: str,
    document_id: str = "doc-1",
    text: str = "chunk text",
    chunk_index: int | None = 0,
    rank: float = 0.0,
    source_type: str | None = None,
    source: str | None = None,
) -> ChunkSearchResult:
    return ChunkSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        text=text,
        rank=rank,
        source_type=source_type,
        source=source,
    )


class RecordingIndex:
    """A fake ChunkIndex recording every call; never touches a store."""

    def __init__(self, hits: tuple[ChunkSearchResult, ...] = ()) -> None:
        self._hits = hits
        self.calls: list[tuple[str, int, object]] = []

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        filters: DocumentFilter | None = None,
    ) -> tuple[ChunkSearchResult, ...]:
        self.calls.append((query, limit, filters))
        return self._hits


class FailingIndex:
    """A fake ChunkIndex that always raises — models backend unavailability."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls: list[tuple[str, int, object]] = []

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        filters: DocumentFilter | None = None,
    ) -> tuple[ChunkSearchResult, ...]:
        self.calls.append((query, limit, filters))
        raise self._error


def _hybrid(
    keyword: tuple[ChunkSearchResult, ...] = (),
    semantic: tuple[ChunkSearchResult, ...] = (),
) -> HybridChunkIndex:
    return HybridChunkIndex(RecordingIndex(keyword), RecordingIndex(semantic))


# --- Protocol ----------------------------------------------------------------


def test_hybrid_chunk_index_satisfies_chunk_index_protocol() -> None:
    assert isinstance(_hybrid(), ChunkIndex)


# --- Basic union -------------------------------------------------------------


def test_keyword_only_candidate() -> None:
    assert [h.chunk_id for h in _hybrid(keyword=(_hit("k1"),)).search("query")] == [
        "k1"
    ]


def test_semantic_only_candidate() -> None:
    assert [h.chunk_id for h in _hybrid(semantic=(_hit("s1"),)).search("query")] == [
        "s1"
    ]


def test_candidates_from_both_backends() -> None:
    keyword = (_hit("k1"), _hit("both"))
    semantic = (_hit("s1"), _hit("both"))
    hits = _hybrid(keyword, semantic).search("query")
    assert [h.chunk_id for h in hits] == ["both", "k1", "s1"]


# --- Deduplication -----------------------------------------------------------


def test_same_chunk_id_in_both_backends_yields_exactly_one_result() -> None:
    hits = _hybrid(keyword=(_hit("c1"),), semantic=(_hit("c1"),)).search("query")
    assert len(hits) == 1
    assert [h.chunk_id for h in hits] == ["c1"]


def test_identical_text_with_different_chunk_ids_stays_two_results() -> None:
    keyword = (_hit("c1", text="same text"), _hit("c2", text="same text"))
    semantic = (_hit("c1", text="same text"),)
    hits = _hybrid(keyword, semantic).search("query")
    assert [h.chunk_id for h in hits] == ["c1", "c2"]
    assert all(h.text == "same text" for h in hits)


def test_duplicate_merges_evidence_into_one_fused_score() -> None:
    hits = _hybrid(keyword=(_hit("c1"),), semantic=(_hit("c1"),)).search("query")
    assert hits[0].rank == pytest.approx(2.0 / 60.0)


# --- RRF formula and rank indexing -------------------------------------------


def test_rrf_uses_zero_based_positions() -> None:
    hits = _hybrid(keyword=(_hit("top"),)).search("query")
    assert hits[0].rank == pytest.approx(1.0 / 60.0)


def test_rrf_equal_weight_across_backends() -> None:
    hits = _hybrid(keyword=(_hit("kw"),), semantic=(_hit("sem"),)).search("query")
    assert hits[0].rank == pytest.approx(1.0 / 60.0)
    assert hits[1].rank == pytest.approx(1.0 / 60.0)


def test_rrf_two_position_candidates_order_and_tie_break() -> None:
    keyword = (_hit("a"), _hit("b"))
    semantic = (_hit("b"), _hit("a"))
    hits = _hybrid(keyword, semantic).search("query")
    assert hits[0].rank == pytest.approx(1.0 / 60.0 + 1.0 / 61.0)
    assert hits[1].rank == pytest.approx(1.0 / 61.0 + 1.0 / 60.0)
    assert [h.chunk_id for h in hits] == ["a", "b"]


def test_absent_backend_contributes_zero() -> None:
    hits = _hybrid(keyword=(_hit("a"), _hit("b"))).search("query")
    assert hits[0].rank == pytest.approx(1.0 / 60.0)
    assert hits[1].rank == pytest.approx(1.0 / 61.0)


def test_single_backend_candidate_loses_to_both_backend_candidate() -> None:
    keyword = (_hit("both"), _hit("single"))
    semantic = (_hit("both"),)
    hits = _hybrid(keyword, semantic).search("query")
    assert [h.chunk_id for h in hits] == ["both", "single"]
    assert hits[0].rank == pytest.approx(2.0 / 60.0)
    assert hits[1].rank == pytest.approx(1.0 / 61.0)


# --- Candidate window --------------------------------------------------------


@pytest.mark.parametrize(
    ("final_limit", "expected_candidates"),
    [(1, 4), (5, 20), (25, 100), (50, 200), (100, 200)],
)
def test_candidate_limit_passed_to_both_backends(
    final_limit: int, expected_candidates: int
) -> None:
    keyword = RecordingIndex()
    semantic = RecordingIndex()
    HybridChunkIndex(keyword, semantic).search("query", limit=final_limit)
    assert keyword.calls == [("query", expected_candidates, None)]
    assert semantic.calls == [("query", expected_candidates, None)]


# --- Final result cap --------------------------------------------------------


def test_final_limit_never_exceeded() -> None:
    keyword = tuple(_hit(f"c{i}") for i in range(12))
    hits = _hybrid(keyword=keyword).search("query", limit=3)
    assert len(hits) == 3


def test_fewer_candidates_than_limit_returns_all() -> None:
    hits = _hybrid(keyword=(_hit("solo"),)).search("query", limit=10)
    assert [h.chunk_id for h in hits] == ["solo"]


# --- Filter propagation ------------------------------------------------------


def test_same_filter_reaches_both_backends() -> None:
    filters = DocumentFilter(source_types=("email",))
    keyword = RecordingIndex()
    semantic = RecordingIndex()
    HybridChunkIndex(keyword, semantic).search("query", filters=filters)
    assert keyword.calls[0][2] is filters
    assert semantic.calls[0][2] is filters


def test_all_six_filter_fields_propagate_identically() -> None:
    filters = DocumentFilter(
        source_types=("email", "file"),
        mime_types=("application/pdf",),
        created_after="2026-01-01",
        created_before="2026-12-31",
        modified_after="2026-02-01",
        modified_before="2026-11-30",
    )
    keyword = RecordingIndex()
    semantic = RecordingIndex()
    HybridChunkIndex(keyword, semantic).search("query", filters=filters)
    assert keyword.calls[0][2] is filters
    assert semantic.calls[0][2] is filters


def test_no_filter_passed_as_none() -> None:
    keyword = RecordingIndex()
    semantic = RecordingIndex()
    HybridChunkIndex(keyword, semantic).search("query")
    assert keyword.calls == [("query", 40, None)]
    assert semantic.calls == [("query", 40, None)]


# --- Empty query -------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_empty_and_whitespace_queries_never_call_backends(query: str) -> None:
    keyword = RecordingIndex()
    semantic = RecordingIndex()
    assert HybridChunkIndex(keyword, semantic).search(query) == ()
    assert keyword.calls == []
    assert semantic.calls == []


# --- Invalid limits ----------------------------------------------------------


def test_zero_limit_returns_without_backend_calls() -> None:
    keyword = RecordingIndex()
    semantic = RecordingIndex()
    assert HybridChunkIndex(keyword, semantic).search("query", limit=0) == ()
    assert keyword.calls == []
    assert semantic.calls == []


def test_negative_limit_rejected_before_backend_calls() -> None:
    keyword = RecordingIndex()
    semantic = RecordingIndex()
    with pytest.raises(ValueError, match="non-negative"):
        HybridChunkIndex(keyword, semantic).search("query", limit=-1)
    assert keyword.calls == []
    assert semantic.calls == []


# --- Empty backends ----------------------------------------------------------


def test_keyword_empty_semantic_results_survive() -> None:
    hits = _hybrid(semantic=(_hit("s1"), _hit("s2"))).search("query")
    assert [h.chunk_id for h in hits] == ["s1", "s2"]


def test_semantic_empty_keyword_results_survive() -> None:
    hits = _hybrid(keyword=(_hit("k1"), _hit("k2"))).search("query")
    assert [h.chunk_id for h in hits] == ["k1", "k2"]


def test_both_backends_empty_returns_empty() -> None:
    assert _hybrid().search("query") == ()


# --- Backend failure (fail closed) -------------------------------------------


def test_keyword_failure_fails_closed() -> None:
    keyword = FailingIndex(RuntimeError("keyword unavailable"))
    semantic = RecordingIndex((_hit("s1"),))
    with pytest.raises(RuntimeError, match="keyword unavailable"):
        HybridChunkIndex(keyword, semantic).search("query")
    assert keyword.calls
    assert semantic.calls == []


def test_semantic_failure_fails_closed() -> None:
    keyword = RecordingIndex((_hit("k1"),))
    semantic = FailingIndex(RuntimeError("semantic unavailable"))
    with pytest.raises(RuntimeError, match="semantic unavailable"):
        HybridChunkIndex(keyword, semantic).search("query")
    assert keyword.calls and semantic.calls


def test_search_tool_wraps_hybrid_failure_as_canonical_error() -> None:
    keyword = FailingIndex(RuntimeError("keyword unavailable"))
    semantic = RecordingIndex((_hit("s1"),))
    tool = SearchTool(HybridChunkIndex(keyword, semantic))

    result = tool.search_documents({"query": "query"})

    assert result["status"] == "error"
    assert result["error"] == "retrieval_unavailable"
    assert result["results"] == []


# --- Determinism -------------------------------------------------------------


def test_repeated_searches_are_identical() -> None:
    keyword = (_hit("b", rank=7.0), _hit("a", rank=7.0))
    semantic = (_hit("a", rank=-3.0), _hit("b", rank=-3.0), _hit("c", rank=1.5))
    hybrid = _hybrid(keyword, semantic)
    first = hybrid.search("query")
    assert first == hybrid.search("query")
    assert first == hybrid.search("query")


def test_window_permutation_orders_strictly_by_rrf() -> None:
    first = _hybrid(keyword=(_hit("a"),), semantic=(_hit("a"), _hit("b"))).search(
        "query"
    )
    permuted = _hybrid(keyword=(_hit("a"),), semantic=(_hit("b"), _hit("a"))).search(
        "query"
    )
    assert [h.chunk_id for h in first] == ["a", "b"]
    assert [h.chunk_id for h in permuted] == ["a", "b"]
    assert first[0].rank == pytest.approx(2.0 / 60.0)
    assert permuted[0].rank == pytest.approx(1.0 / 60.0 + 1.0 / 61.0)


# --- Provenance --------------------------------------------------------------


def test_provenance_preserved_on_merged_duplicate() -> None:
    hit = _hit("c1", source_type="file", source="notes.txt")
    hits = _hybrid(keyword=(hit,), semantic=(hit,)).search("query")
    assert len(hits) == 1
    assert hits[0].document_id == "doc-1"
    assert hits[0].chunk_index == 0
    assert hits[0].text == "chunk text"
    assert hits[0].source_type == "file"
    assert hits[0].source == "notes.txt"


def test_distinct_provenance_not_corrupted() -> None:
    keyword = (_hit("a", document_id="d1", source_type="email", source="s@x.test"),)
    semantic = (_hit("b", document_id="d2", source_type="file", source="notes.txt"),)
    hits = _hybrid(keyword, semantic).search("query")
    got = {(h.chunk_id, h.document_id, h.source_type, h.source) for h in hits}
    assert got == {
        ("a", "d1", "email", "s@x.test"),
        ("b", "d2", "file", "notes.txt"),
    }


# --- Public result shape -----------------------------------------------------


def test_public_results_are_chunk_search_results_without_backend_fields() -> None:
    keyword = (_hit("a"),)
    semantic = (_hit("b"),)
    hits = _hybrid(keyword, semantic).search("query")
    assert hits
    assert all(isinstance(hit, ChunkSearchResult) for hit in hits)
    for hit in hits:
        with pytest.raises(AttributeError):
            _ = hit.keyword_rank
        with pytest.raises(AttributeError):
            _ = hit.semantic_rank
        with pytest.raises(AttributeError):
            _ = hit.rrf_score
        with pytest.raises(AttributeError):
            _ = hit.keyword_score


# --- Raw score independence --------------------------------------------------


def test_fusion_uses_tuple_positions_not_backend_rank_values() -> None:
    keyword = (_hit("a", rank=999.0), _hit("b", rank=-500.0))
    hits = _hybrid(keyword=keyword).search("query")
    assert hits[0].chunk_id == "a"
    assert hits[0].rank == pytest.approx(1.0 / 60.0)
    assert hits[1].rank == pytest.approx(1.0 / 61.0)


def test_fusion_never_adds_backend_scores() -> None:
    keyword = (_hit("hi", rank=999.0), _hit("lo", rank=-999.0))
    semantic = (_hit("lo", rank=999.0),)
    hits = _hybrid(keyword, semantic).search("query")
    assert hits[0].chunk_id == "lo"
    assert hits[1].chunk_id == "hi"
    assert hits[0].rank == pytest.approx(1.0 / 61.0 + 1.0 / 60.0)
    assert hits[1].rank == pytest.approx(1.0 / 60.0)


# --- Read-only / consumer integration ----------------------------------------


def test_search_never_exercises_anything_but_backend_search() -> None:
    keyword = RecordingIndex((_hit("a"),))
    semantic = RecordingIndex((_hit("b"),))
    HybridChunkIndex(keyword, semantic).search("query")
    assert len(keyword.calls) == 1
    assert len(semantic.calls) == 1


def test_search_documents_accepts_the_hybrid_backend() -> None:
    keyword = RecordingIndex((_hit("a"), _hit("b"), _hit("c")))
    semantic = RecordingIndex((_hit("d"),))
    hits = search_documents(
        HybridChunkIndex(keyword, semantic),
        SearchDocumentsRequest(query="query", limit=2),
    )
    assert isinstance(HybridChunkIndex(keyword, semantic), ChunkIndex)
    assert len(hits) <= 2
    assert all(isinstance(hit, ChunkSearchResult) for hit in hits)


def test_search_tool_consumes_hybrid_backend_end_to_end() -> None:
    keyword = RecordingIndex((_hit("a"),))
    semantic = RecordingIndex((_hit("b"),))
    result = SearchTool(HybridChunkIndex(keyword, semantic)).search_documents(
        {"query": "query"}
    )
    assert result["status"] == "results"
    assert {item["chunk_id"] for item in result["results"]} == {"a", "b"}
