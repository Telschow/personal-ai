"""Deterministic provider-independent chunking of extracted document text."""

import hashlib

from personal_ai.documents.extractor import TextExtractionResult
from personal_ai.documents.models import DocumentChunk

DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 150


def compute_chunk_id(document_id: str, position: int, text: str) -> str:
    """Derive a stable chunk identity from document, ordinal, and content.

    Follows the NUL-separator convention of :func:`compute_document_id`.
    Including the exact chunk text means any change in chunk boundaries
    yields fresh identifiers instead of silently reusing stale ones; the
    ordinal keeps repeated identical passages within one document distinct.
    """
    identity_material = f"{document_id}\x00{position}\x00{text}"
    return hashlib.sha256(identity_material.encode("utf-8")).hexdigest()


def _validated_configuration(chunk_size: int, overlap: int) -> int:
    if chunk_size < 1:
        msg = f"chunk_size must be at least 1, got {chunk_size}"
        raise ValueError(msg)
    if overlap < 0:
        msg = f"overlap must be at least 0, got {overlap}"
        raise ValueError(msg)
    if overlap >= chunk_size:
        msg = (
            f"overlap must be smaller than chunk_size, got "
            f"overlap={overlap} and chunk_size={chunk_size}"
        )
        raise ValueError(msg)
    return chunk_size - overlap


def _chunk_text(
    text: str,
    document_id: str,
    start_position: int,
    page_number: int | None,
    source_type: str,
    source_key: str,
    content_hash: str,
    *,
    chunk_size: int,
    step: int,
) -> tuple[DocumentChunk, ...]:
    """Chunk a single text block, returning chunks with shared provenance."""
    provenance: dict[str, object] = {
        "source_type": source_type,
        "source_key": source_key,
        "content_hash": content_hash,
    }

    chunks: list[DocumentChunk] = []
    previous_end = 0
    for offset, start in enumerate(range(0, len(text), step)):
        end = min(start + chunk_size, len(text))
        if end <= previous_end:
            break
        chunk_text = text[start:end]
        position = start_position + offset
        metadata = {"chunk_index": position, **provenance}
        if page_number is not None:
            metadata["page_number"] = page_number
        chunks.append(
            DocumentChunk(
                id=compute_chunk_id(document_id, position, chunk_text),
                document_id=document_id,
                text=chunk_text,
                page_number=page_number,
                metadata=metadata,
            )
        )
        previous_end = end

    return tuple(chunks)


def chunk_document(
    extraction: TextExtractionResult,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[DocumentChunk, ...]:
    """Split extracted document text into deterministic searchable chunks.

    Chunks are verbatim contiguous slices of the extracted text: whitespace
    is never collapsed, stripped, or normalized, so no character is lost.
    Empty input yields no chunks; whitespace-only input yields chunks
    containing that whitespace unchanged — filtering belongs to callers,
    which already route unusable text away before chunking.

    When ``extraction.pages`` is provided (PDF extraction), each page is
    chunked independently and every resulting chunk carries the page's
    1-based ``page_number``.  This preserves page provenance so that no
    chunk mixes text from different pages.  ``chunk_index`` remains
    globally sequential across all pages for stable identity derivation.

    For non-paginated extractions, ``page_number`` stays ``None`` on every
    chunk, preserving existing behavior unchanged.

    Windows advance by ``chunk_size - overlap``, so adjacent chunks share
    exactly ``overlap`` characters and every character appears in at least
    one chunk.  A trailing window fully contained in its predecessor is
    skipped as redundant.  Chunk order follows text order, exposed both by
    sequence position and the ``chunk_index`` metadata entry.

    Each chunk carries ``document_id`` provenance plus ``source_type``,
    ``source_key``, and ``content_hash`` in metadata.
    """
    step = _validated_configuration(chunk_size, overlap)

    if extraction.pages is not None:
        all_chunks: list[DocumentChunk] = []
        position = 0
        for page in extraction.pages:
            page_chunks = _chunk_text(
                page.text,
                extraction.document_id,
                start_position=position,
                page_number=page.page_number,
                source_type=extraction.source_type,
                source_key=extraction.source_key,
                content_hash=extraction.content_hash,
                chunk_size=chunk_size,
                step=step,
            )
            all_chunks.extend(page_chunks)
            position += len(page_chunks)
        return tuple(all_chunks)

    return _chunk_text(
        extraction.text,
        extraction.document_id,
        start_position=0,
        page_number=None,
        source_type=extraction.source_type,
        source_key=extraction.source_key,
        content_hash=extraction.content_hash,
        chunk_size=chunk_size,
        step=step,
    )
