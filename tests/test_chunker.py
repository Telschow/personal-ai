"""Unit tests for deterministic document chunking."""

from itertools import pairwise

import pytest

from personal_ai.documents import (
    DocumentChunk,
    TextExtractionResult,
    chunk_document,
    compute_chunk_id,
)

DOC_ID = "doc-1"


def make_extraction(text: str, document_id: str = DOC_ID) -> TextExtractionResult:
    return TextExtractionResult(
        document_id=document_id,
        source_type="file",
        source_key="notes/ideas.txt",
        content_hash="hash-1",
        text=text,
    )


def chunk(text: str, *, chunk_size: int, overlap: int) -> tuple[DocumentChunk, ...]:
    return chunk_document(make_extraction(text), chunk_size=chunk_size, overlap=overlap)


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_document(make_extraction("")) == ()


def test_short_text_yields_single_verbatim_chunk() -> None:
    chunks = chunk("hello world", chunk_size=100, overlap=10)

    assert len(chunks) == 1
    assert chunks[0].text == "hello world"
    assert chunks[0].document_id == DOC_ID


def test_long_text_is_split_into_multiple_ordered_chunks() -> None:
    text = "".join(chr(ord("a") + i % 26) for i in range(50))
    chunks = chunk(text, chunk_size=20, overlap=5)

    assert len(chunks) == 3
    assert [c.metadata["chunk_index"] for c in chunks] == [0, 1, 2]


def test_text_exactly_chunk_size_fits_in_one_chunk() -> None:
    text = "x" * 20

    chunks = chunk(text, chunk_size=20, overlap=5)

    assert len(chunks) == 1
    assert chunks[0].text == text


def test_text_one_past_boundary_needs_second_chunk() -> None:
    text = "abcdefghijk"  # 11 characters

    chunks = chunk(text, chunk_size=10, overlap=3)

    assert len(chunks) == 2
    assert chunks[0].text == "abcdefghij"
    assert chunks[1].text == "hijk"


def test_exact_tiling_without_tail_window() -> None:
    chunks = chunk("y" * 20, chunk_size=10, overlap=0)

    assert [c.text for c in chunks] == ["y" * 10, "y" * 10]


def test_adjacent_chunks_share_exactly_overlap_characters() -> None:
    text = "".join(str(i % 10) for i in range(60))
    chunks = chunk(text, chunk_size=15, overlap=4)

    for previous, current in pairwise(chunks):
        assert current.text[:4] == previous.text[-4:]


def test_repeated_chunking_is_deterministic() -> None:
    extraction = make_extraction("some document body " * 40)

    first = chunk_document(extraction, chunk_size=64, overlap=16)
    second = chunk_document(extraction, chunk_size=64, overlap=16)

    assert second == first


def test_chunk_ids_are_stable_across_calls() -> None:
    extraction = make_extraction("stable identity check " * 30)

    chunks = chunk_document(extraction)
    again = chunk_document(extraction)

    assert [c.id for c in chunks] == [c.id for c in again]


def test_compute_chunk_id_is_deterministic_and_position_sensitive() -> None:
    assert compute_chunk_id(DOC_ID, 0, "same") == compute_chunk_id(DOC_ID, 0, "same")
    assert compute_chunk_id(DOC_ID, 0, "same") != compute_chunk_id(DOC_ID, 1, "same")


def test_chunk_ids_bind_to_document_and_content() -> None:
    base = chunk("alpha", chunk_size=3, overlap=1)[0]
    same_document_other_text = chunk("beta", chunk_size=3, overlap=1)[0]
    other_document = chunk_document(
        make_extraction("alpha", document_id="doc-2"),
        chunk_size=3,
        overlap=1,
    )[0]

    assert same_document_other_text.id != base.id
    assert other_document.id != base.id


def test_document_identity_is_preserved_on_every_chunk() -> None:
    extraction = make_extraction("identity flows through " * 20, document_id="doc-9")

    chunks = chunk_document(extraction, chunk_size=25, overlap=5)

    assert all(c.document_id == "doc-9" for c in chunks)


def test_chunks_carry_full_provenance_metadata() -> None:
    chunks = chunk("provenance", chunk_size=100, overlap=10)

    assert chunks[0].metadata == {
        "chunk_index": 0,
        "source_type": "file",
        "source_key": "notes/ideas.txt",
        "content_hash": "hash-1",
    }


def test_no_text_is_lost_across_overlapping_windows() -> None:
    text = "".join(chr(ord("a") + i % 26) for i in range(103))
    chunks = chunk(text, chunk_size=30, overlap=10)

    rebuilt = chunks[0].text + "".join(c.text[10:] for c in chunks[1:])

    assert rebuilt == text


def test_final_window_contained_in_previous_is_skipped() -> None:
    # size=12, step=10: starts 0, 10, 20 — the [20:21] tail is already covered.
    chunks = chunk("z" * 21, chunk_size=12, overlap=2)

    assert [c.text for c in chunks] == ["z" * 12, "z" * 11]


def test_whitespace_is_preserved_verbatim() -> None:
    text = "line one\n\nline\ttwo  \nthree"

    chunks = chunk(text, chunk_size=100, overlap=10)

    assert chunks[0].text == text


def test_whitespace_only_text_produces_verbatim_chunks() -> None:
    text = "\n\n  \t\n"

    chunks = chunk(text, chunk_size=4, overlap=0)

    assert [c.text for c in chunks] == ["\n\n  ", "\t\n"]


def test_page_number_defaults_to_none() -> None:
    chunks = chunk("no page info yet", chunk_size=100, overlap=10)

    assert all(c.page_number is None for c in chunks)


def test_default_configuration_produces_valid_chunks() -> None:
    extraction = make_extraction("default settings " * 200)

    chunks = chunk_document(extraction)

    assert len(chunks) > 1
    assert all(len(c.text) <= 1200 for c in chunks)


@pytest.mark.parametrize(("chunk_size", "overlap"), [(0, 0), (-5, 0)])
def test_non_positive_chunk_size_is_rejected(chunk_size: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        chunk("body", chunk_size=chunk_size, overlap=overlap)


def test_negative_overlap_is_rejected() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk("body", chunk_size=10, overlap=-1)


@pytest.mark.parametrize(("chunk_size", "overlap"), [(10, 10), (10, 15)])
def test_overlap_at_or_above_chunk_size_is_rejected(
    chunk_size: int, overlap: int
) -> None:
    with pytest.raises(ValueError, match="smaller than chunk_size"):
        chunk("body", chunk_size=chunk_size, overlap=overlap)
