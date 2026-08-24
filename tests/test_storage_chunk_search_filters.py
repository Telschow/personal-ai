"""Integration tests for metadata-aware filtering of chunk search."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_ai.documents import Document, DocumentChunk
from personal_ai.storage import (
    ChunkSearchResult,
    ChunkStore,
    DocumentFilter,
    DocumentStore,
    connect_database,
)


def make_document(**overrides: object) -> Document:
    values: dict[str, object] = {
        "id": "doc-1",
        "source": "notes/doc-1.json",
        "source_type": "keep",
        "content_hash": "hash-doc-1",
        "created_at": "2025-01-10T09:00:00+00:00",
        "modified_at": "2025-01-10T09:00:00+00:00",
        "metadata": {},
    }
    values.update(overrides)
    return Document(**values)  # type: ignore[arg-type]


def make_chunk(**overrides: object) -> DocumentChunk:
    values: dict[str, object] = {
        "id": "chunk-doc-1",
        "document_id": "doc-1",
        "text": "guitar practice routine",
        "metadata": {"chunk_index": 0},
    }
    values.update(overrides)
    return DocumentChunk(**values)  # type: ignore[arg-type]


class SearchHarness:
    """Document and chunk stores sharing one database, like real ingestion."""

    def __init__(self) -> None:
        self._connection = connect_database(":memory:")
        self.documents = DocumentStore(self._connection)
        self.chunks = ChunkStore(self._connection)

    def seed(self, document: Document, *chunks: DocumentChunk) -> None:
        self.documents.add(document)
        self.chunks.add_many(chunks)

    def close(self) -> None:
        self.chunks.close()


@pytest.fixture()
def harness() -> SearchHarness:
    fixture = SearchHarness()
    fixture.seed(
        make_document(id="doc-keep-old"),
        make_chunk(
            id="chunk-keep-old",
            document_id="doc-keep-old",
            text="guitar practice schedule",
        ),
    )
    fixture.seed(
        make_document(
            id="doc-notebooklm",
            source_type="notebooklm",
            source="articles/theory.html",
            created_at="2026-03-05T12:00:00+00:00",
            modified_at="2026-06-01T08:00:00+00:00",
            content_hash="hash-nlm",
        ),
        make_chunk(
            id="chunk-notebooklm",
            document_id="doc-notebooklm",
            text="guitar chord theory",
            metadata={"chunk_index": 0},
        ),
    )
    fixture.seed(
        make_document(
            id="doc-chatgpt-unknown-dates",
            source_type="chatgpt",
            source="conversations/c-1.json",
            created_at="",
            modified_at="",
            content_hash="hash-cgpt",
        ),
        make_chunk(
            id="chunk-chatgpt-unknown-dates",
            document_id="doc-chatgpt-unknown-dates",
            text="guitar maintenance log",
        ),
    )
    yield fixture
    fixture.close()


def search(harness: SearchHarness, **kwargs: object):
    return harness.chunks.search("guitar", **kwargs)


def hit_ids(results) -> list[str]:
    return [hit.chunk_id for hit in results]


# --- construction-time validation -----------------------------------------


def test_source_types_accepts_sequences_and_normalizes_to_tuples() -> None:
    assert DocumentFilter(source_types=["keep", "gemini"]) == DocumentFilter(
        source_types=("keep", "gemini")
    )


def test_empty_source_types_sequence_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        DocumentFilter(source_types=())


def test_blank_source_types_member_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        DocumentFilter(source_types=("keep", ""))


def test_unparsable_date_boundary_is_rejected() -> None:
    with pytest.raises(ValueError, match="ISO-8601"):
        DocumentFilter(created_after="not-a-date")


def test_inverted_created_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="created_after"):
        DocumentFilter(
            created_after="2026-06-01T00:00:00+00:00",
            created_before="2026-01-01T00:00:00+00:00",
        )


def test_inverted_modified_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="modified_after"):
        DocumentFilter(
            modified_after=datetime(2026, 6, 1, tzinfo=UTC).isoformat(),
            modified_before=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        )


def test_mixed_naive_and_aware_boundaries_are_rejected() -> None:
    with pytest.raises(ValueError, match="offset-naive and offset-aware"):
        DocumentFilter(
            created_after="2026-01-01T00:00:00",
            created_before="2026-12-31T00:00:00+00:00",
        )


# --- filter semantics ------------------------------------------------------


def test_no_filter_argument_matches_unfiltered_baseline(harness: SearchHarness) -> None:
    baseline = search(harness)

    assert len(baseline) == 3
    assert search(harness, filters=None) == baseline


def test_empty_filter_is_equivalent_to_no_filter(harness: SearchHarness) -> None:
    baseline = search(harness)

    assert DocumentFilter().is_empty
    assert search(harness, filters=DocumentFilter()) == baseline


def test_single_source_type_restricts_hits(harness: SearchHarness) -> None:
    hits = search(harness, filters=DocumentFilter(source_types=("keep",)))

    assert hit_ids(hits) == ["chunk-keep-old"]
    assert hits[0].document_id == "doc-keep-old"


def test_multiple_source_types_use_inclusion_semantics(harness: SearchHarness) -> None:
    hits = search(
        harness, filters=DocumentFilter(source_types=("notebooklm", "chatgpt"))
    )

    assert sorted(hit_ids(hits)) == ["chunk-chatgpt-unknown-dates", "chunk-notebooklm"]


def test_unknown_source_type_returns_no_results(harness: SearchHarness) -> None:
    assert search(harness, filters=DocumentFilter(source_types=("gemini",))) == ()


def test_created_window_keeps_only_documents_inside_bounds(
    harness: SearchHarness,
) -> None:
    hits = search(
        harness,
        filters=DocumentFilter(
            created_after="2026-01-01T00:00:00+00:00",
            created_before="2026-12-31T23:59:59+00:00",
        ),
    )

    # The chat export's empty (unknown) timestamp never satisfies a window.
    assert hit_ids(hits) == ["chunk-notebooklm"]


def test_date_boundaries_are_inclusive(harness: SearchHarness) -> None:
    instant = "2026-03-05T12:00:00+00:00"

    hits = search(
        harness,
        filters=DocumentFilter(created_after=instant, created_before=instant),
    )

    assert hit_ids(hits) == ["chunk-notebooklm"]


def test_date_only_boundaries_act_as_inclusive_day_bounds(tmp_path: Path) -> None:
    fixture = SearchHarness()
    try:
        fixture.seed(
            make_document(
                id="doc-day",
                created_at="2026-02-20T18:30:00+00:00",
                modified_at="2026-02-20T18:30:00+00:00",
                content_hash="hash-day",
            ),
            make_chunk(
                id="chunk-day",
                document_id="doc-day",
                text="kite design sketch",
            ),
        )
        hits = fixture.chunks.search(
            "kite",
            filters=DocumentFilter(created_after="2026-02-20"),
        )

        assert hit_ids(hits) == ["chunk-day"]
    finally:
        fixture.close()


def test_modified_window_filters_on_the_modified_column(harness: SearchHarness) -> None:
    hits = search(
        harness,
        filters=DocumentFilter(modified_after="2026-06-01T00:00:00+00:00"),
    )

    # Only the notebook article was modified in 2026; its creation year and
    # the other documents' creation dates are irrelevant to this bound.
    assert hit_ids(hits) == ["chunk-notebooklm"]


def test_combined_filters_narrow_jointly(harness: SearchHarness) -> None:
    hits = search(
        harness,
        filters=DocumentFilter(
            source_types=("notebooklm", "keep"),
            created_after="2026-01-01T00:00:00+00:00",
        ),
    )

    # keep matches the source list but predates the window; notebooklm fits.
    assert hit_ids(hits) == ["chunk-notebooklm"]


def test_orphaned_chunks_leave_filtered_results_but_not_unfiltered(
    harness: SearchHarness,
) -> None:
    orphan = make_chunk(
        id="chunk-orphan",
        document_id="doc-deleted",
        text="guitar strap review",
    )
    harness.chunks.add(orphan)

    unfiltered = search(harness)
    filtered = search(
        harness, filters=DocumentFilter(source_types=("keep", "notebooklm", "chatgpt"))
    )

    assert "chunk-orphan" in hit_ids(unfiltered)
    assert "chunk-orphan" not in hit_ids(filtered)


# --- preserved search behavior under filtering -----------------------------


def test_bm25_ranking_survives_filtering(harness: SearchHarness) -> None:
    frequent = make_document(
        id="doc-frequent",
        source_type="notebooklm",
        created_at="2026-04-01T00:00:00+00:00",
        modified_at="2026-04-01T00:00:00+00:00",
        content_hash="hash-frequent",
    )
    harness.seed(
        frequent,
        make_chunk(
            id="chunk-frequent",
            document_id="doc-frequent",
            text="guitar guitar guitar drills",
            metadata={"chunk_index": 0},
        ),
    )

    hits = search(harness, filters=DocumentFilter(source_types=("notebooklm",)))

    assert hit_ids(hits)[0] == "chunk-frequent"
    assert isinstance(hits[0], ChunkSearchResult)
    assert hits[0].rank < hits[-1].rank


def test_deterministic_tie_break_survives_filtering(harness: SearchHarness) -> None:
    for suffix in ("b", "a"):
        harness.seed(
            make_document(
                id=f"doc-twin-{suffix}",
                source_type="notebooklm",
                created_at="2026-05-01T00:00:00+00:00",
                modified_at="2026-05-01T00:00:00+00:00",
                content_hash=f"hash-twin-{suffix}",
            ),
            make_chunk(
                id=f"chunk-twin-{suffix}",
                document_id=f"doc-twin-{suffix}",
                text="guitar identical body",
                metadata={"chunk_index": 0},
            ),
        )

    hits = search(harness, filters=DocumentFilter(source_types=("notebooklm",)))

    twins = [hit.chunk_id for hit in hits if hit.chunk_id.startswith("chunk-twin")]
    assert twins == ["chunk-twin-a", "chunk-twin-b"]


def test_limit_applies_after_filtering(harness: SearchHarness) -> None:
    # The globally best-ranked hit belongs to an excluded source type; the
    # limit must still surface the best surviving match, not drop results.
    dominant = make_document(
        id="doc-dominant",
        source="notes/dominant.txt",
        content_hash="hash-dominant",
        metadata={},
    )
    harness.seed(
        dominant,
        make_chunk(
            id="chunk-dominant",
            document_id="doc-dominant",
            text="guitar guitar guitar guitar",
            metadata={"chunk_index": 0},
        ),
    )

    hits = search(
        harness,
        limit=1,
        filters=DocumentFilter(source_types=("notebooklm",)),
    )

    assert hit_ids(hits) == ["chunk-notebooklm"]
