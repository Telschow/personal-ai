"""Bounded CV / career document ingestion (Slice 3).

Supported inputs: plain text (``.txt``), Markdown (``.md``), DOCX
(``python-docx``), and PDF (``pypdf``). Extraction is pure parsing: document
content is never executed, evaluated, or interpolated into instructions. Every
document gets a stable content hash + document id so re-ingesting an unchanged
file is a no-op (idempotent).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

MAX_FILE_SIZE = 1_000_000  # 1 MiB of source material
MAX_SECTIONS = 200
MAX_LINES_PER_SECTION = 200
MAX_SECTION_CHARS = 20_000

TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
DOCX_EXTENSIONS = {".docx"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = frozenset(TEXT_EXTENSIONS | DOCX_EXTENSIONS | PDF_EXTENSIONS) | {".doc"}
_BINARY_UTF8_HEURISTIC_NUL = b"\x00"


class DocumentError(ValueError):
    """Raised for unsupported, oversized, or unreadable documents."""


class DocumentSection(BaseModel):
    """One extracted section of a career document with provenance."""

    section: str
    heading: str | None = None
    text: str
    position: int = 0
    ref: dict[str, Any] = Field(default_factory=dict)


class CareerDocument(BaseModel):
    """A normalised career document with stable identity."""

    document_id: str
    filename: str
    source_path: str
    mime_type: str
    content_hash: str
    size_bytes: int
    sections: list[DocumentSection] = Field(default_factory=list)
    ingested_at: str = ""


def _mime_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return "text/plain" if suffix == ".txt" else "text/markdown"
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix == ".doc":
        return "application/msword"
    if suffix in PDF_EXTENSIONS:
        return "application/pdf"
    return "application/octet-stream"


def _canonical_hash(sections: list[DocumentSection]) -> str:
    """Stable hash over extracted section text (idempotent across binary
    churn that leaves the text unchanged)."""
    joined = "\x1f".join(s.text for s in sorted(sections, key=lambda s: s.position))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def document_id_for(content_hash: str) -> str:
    return f"cv-doc:{content_hash[:16]}"


def _guard_file(path: Path) -> int:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise DocumentError(
            f"unsupported document type: {path.suffix or '(none)'} "
            f"(supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))})"
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DocumentError(f"cannot stat document: {exc}") from exc
    if size > MAX_FILE_SIZE:
        raise DocumentError(f"document too large ({size} bytes > {MAX_FILE_SIZE})")
    return size


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def _runs(lines: list[str]) -> list[tuple[str, int, int]]:
    """Group lines into (content, start, end) runs; returns nothing for a doc
    with no non-empty lines (empty document)."""
    runs: list[tuple[str, int, int]] = []
    start: int | None = None
    buf: list[str] = []
    for idx, line in enumerate(lines):
        if line.strip():
            if start is None:
                start = idx
            buf.append(line.rstrip())
        elif start is not None:
            runs.append(("\n".join(buf), start, idx))
            buf, start = [], None
    if start is not None:
        runs.append(("\n".join(buf), start, len(lines)))
    return runs


def _extract_text(path: Path) -> list[DocumentSection]:
    raw = path.read_bytes()
    if _BINARY_UTF8_HEURISTIC_NUL in raw[:8192]:
        raise DocumentError("document appears to be binary; expected UTF-8 text")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentError("document is not valid UTF-8 text") from exc
    lines = _split_lines(text)
    if not any(line for line in lines):
        raise DocumentError("document contains no text")
    sections: list[DocumentSection] = []
    for idx, (content, start, end) in enumerate(_runs(lines)[:MAX_SECTIONS]):
        sections.append(
            DocumentSection(
                section=f"paragraph-{idx + 1}",
                text=content[:MAX_SECTION_CHARS],
                position=idx,
                ref={"start": start, "end": end},
            )
        )
    return _merge_sections(sections)


def _merge_sections(sections: list[DocumentSection]) -> list[DocumentSection]:
    """Collapse tiny adjacent paragraphs so a plain-text/Markdown best-effort
    document yields a bounded number of meaningful sections."""
    if not sections:
        return sections
    out: list[DocumentSection] = [sections[0]]
    for sec in sections[1:]:
        prev = out[-1]
        if len(prev.text) < 180 and not prev.heading:
            prev.text = f"{prev.text}\n{sec.text}"[:MAX_SECTION_CHARS]
            prev.ref = {"start": prev.ref.get("start", 0), "end": sec.ref.get("end", prev.ref.get("end", 0))}
            continue
        out.append(sec)
    for i, sec in enumerate(out):
        sec.position = i
    return out


def _extract_docx(path: Path) -> list[DocumentSection]:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - guarded by pyproject dep
        raise DocumentError("python-docx is required for .docx documents") from exc
    try:
        doc = Document(str(path))
    except Exception as exc:  # noqa: BLE001 - malformed docx reaches here
        raise DocumentError(f"cannot read docx document: {exc}") from exc

    sections: list[DocumentSection] = []
    position = 0
    seen_text: set[str] = set()
    for paragraph in doc.paragraphs[: MAX_SECTIONS * 4]:
        text = paragraph.text.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen_text:
            continue
        seen_text.add(key)
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        is_heading = "heading" in style or "title" in style or len(text) <= 60 and _looks_like_heading(text)
        sections.append(
            DocumentSection(
                section="heading" if is_heading else f"paragraph-{position + 1}",
                heading=text if is_heading else None,
                text=text[:MAX_SECTION_CHARS] if not is_heading else "",
                position=position,
                ref={"style": style, "start": position, "end": position},
            )
        )
        position += 1
    if not sections:
        raise DocumentError("docx document contains no text")
    return _drop_empty_headings(sections)[:MAX_SECTIONS]


def _extract_pdf(path: Path) -> list[DocumentSection]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - guarded by pyproject dep
        raise DocumentError("pypdf is required for .pdf documents") from exc
    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001 - malformed pdf reaches here
        raise DocumentError(f"cannot read pdf document: {exc}") from exc

    sections: list[DocumentSection] = []
    for idx, page in enumerate(reader.pages[:MAX_SECTIONS]):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 - a single bad page must not kill the doc
            text = ""
        if not text:
            sections.append(
                DocumentSection(
                    section=f"page-{idx + 1}",
                    text="",
                    position=idx,
                    ref={"page": idx + 1, "empty": True},
                )
            )
            continue
        sections.append(
            DocumentSection(
                section=f"page-{idx + 1}",
                text=text[:MAX_SECTION_CHARS],
                position=idx,
                ref={"page": idx + 1},
            )
        )
    if not sections:
        raise DocumentError("pdf document contains no pages")
    return sections


def _looks_like_heading(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:experience|education|skills|summary|profile|certifications|projects|"
            r"achievements|languages)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _drop_empty_headings(sections: list[DocumentSection]) -> list[DocumentSection]:
    return [s for s in sections if not (s.section == "heading" and not s.text)]


def ingest_document(path: str | Path) -> CareerDocument:
    """Parse a supported document into a bounded :class:`CareerDocument`.

    Deterministic, idempotent (same file ⇒ same ``document_id``/hash); never
    executes content. Raises :class:`DocumentError` for unsupported or
    unreadable inputs.
    """
    p = Path(path)
    if not p.is_file():
        raise DocumentError(f"document not found: {p}")
    size = _guard_file(p)
    suffix = p.suffix.lower()
    if suffix in DOCX_EXTENSIONS:
        sections = _extract_docx(p)
    elif suffix in PDF_EXTENSIONS:
        sections = _extract_pdf(p)
    else:
        sections = _extract_text(p)
    content_hash = _canonical_hash(sections)
    return CareerDocument(
        document_id=document_id_for(content_hash),
        filename=p.name,
        source_path=str(p),
        mime_type=_mime_for(p.name),
        content_hash=content_hash,
        size_bytes=size,
        sections=sections,
    )


__all__ = [
    "MAX_SECTION_CHARS",
    "MAX_SECTIONS",
    "MAX_FILE_SIZE",
    "SUPPORTED_EXTENSIONS",
    "CareerDocument",
    "DocumentError",
    "DocumentSection",
    "document_id_for",
    "ingest_document",
]
