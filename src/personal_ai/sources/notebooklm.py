"""NotebookLM export adapter: article HTML becomes normalized plain text.

Google Takeout stores NotebookLM articles as ``<notebook>/Sources/<name>.html``
fragments (no document shell) next to a sibling ``<name> metadata.json``.
Chat sessions live under ``Chat History/`` and are structurally excluded by
discovery, which only accepts files whose immediate parent directory is named
``Sources``.
"""

import datetime as dt
import json
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from personal_ai.documents.models import compute_content_hash
from personal_ai.sources.base import SourceError
from personal_ai.sources.models import SourceRecord

SOURCE_TYPE = "notebooklm"

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_DROPPED_TAGS = frozenset({"script", "style", "noscript"})


class NotebookLMParseError(SourceError):
    """Raised when an article payload cannot be decoded or parsed."""


class PathOutsideExportError(SourceError):
    """Raised when a requested source key escapes the export directory."""


class SourceNotFoundError(SourceError):
    """Raised when a requested article does not exist in the export."""


class UnsupportedArticleError(SourceError):
    """Raised when a requested file is not an HTML article."""


class EmptyArticleError(SourceError):
    """Raised when an article normalizes to no usable content."""


def _collapse_horizontal(text: str) -> str:
    """Collapse all whitespace runs while keeping preformatted text intact."""
    return " ".join(text.split())


class _ArticleRenderer(HTMLParser):
    """Renders one NotebookLM article fragment as structured plain text.

    A small state machine over the tag vocabulary actually observed in
    Takeout exports: block elements become separate blocks joined later by
    blank lines, inline elements are transparent, list items become bullet
    or numbered lines, table rows become pipe-separated lines, and images
    only bump a counter so embedded base64 blobs never leak into the text.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.image_count = 0
        self._inline: list[str] = []
        self._pre_depth = 0
        self._drop_depth = 0
        self._lists: list[dict[str, object]] = []
        self._list_lines: list[str] = []
        self._row: list[str] = []
        self._table_lines: list[str] = []

    def _flush_inline(self) -> str | None:
        text = "".join(self._inline)
        self._inline = []
        collapsed = _collapse_horizontal(text).strip()
        return collapsed or None

    def _emit_block(self, text: str) -> None:
        cleaned = "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")
        if cleaned:
            self.blocks.append(cleaned)

    def _end_block(self) -> None:
        self._flush_list()
        self._flush_table()
        text = self._flush_inline()
        if text is not None:
            self._emit_block(text)

    def _end_list_item(self) -> None:
        if not self._lists:
            return
        text = self._flush_inline()
        if text is None:
            return
        self._list_lines.append(text)

    def _flush_list(self) -> None:
        if self._list_lines:
            self._emit_block("\n".join(self._list_lines))
            self._list_lines = []

    def _flush_table(self) -> None:
        if self._table_lines:
            self._emit_block("\n".join(self._table_lines))
            self._table_lines = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            self.image_count += 1
            return
        if tag == "br":
            self._inline.append("\n" if self._pre_depth else " ")
            return
        if tag in _DROPPED_TAGS:
            self._drop_depth += 1
            return
        if tag == "hr":
            self._end_block()
            return
        if tag in _HEADING_TAGS or tag == "p":
            self._end_block()
            return
        if tag in {"ul", "ol"}:
            self._end_block()
            self._lists.append({"ordered": tag == "ol", "index": 0})
            return
        if tag == "li":
            self._end_list_item()
            context = self._lists[-1]
            depth = max(0, len(self._lists) - 1)
            if context["ordered"]:
                context["index"] = int(context["index"]) + 1
                marker = f"{context['index']}. "
            else:
                marker = "- "
            self._inline.append("  " * depth + marker)
            return
        if tag == "table":
            self._end_block()
            return
        if tag == "tr":
            self._end_list_item()
            self._row = []
            return
        if tag == "pre":
            self._end_block()
            self._pre_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROPPED_TAGS:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if tag == "li":
            self._end_list_item()
            return
        if tag in {"ul", "ol"}:
            self._end_list_item()
            if self._lists:
                self._lists.pop()
                if not self._lists:
                    self._flush_list()
            return
        if tag in {"th", "td"}:
            cell = self._flush_inline() or ""
            self._row.append(cell)
            return
        if tag == "tr":
            if any(self._row):
                self._table_lines.append(" | ".join(self._row))
            self._row = []
            return
        if tag == "table":
            self._flush_table()
            return
        if tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
            self._end_pre()
            return
        if tag == "p":
            self._end_block()

    def handle_data(self, data: str) -> None:
        if self._drop_depth:
            return
        self._inline.append(data)

    def close(self) -> None:
        super().close()
        self._end_list_item()
        self._flush_list()
        self._end_block()

    def _end_pre(self) -> None:
        text = "".join(self._inline)
        self._inline = []
        if text.strip():
            self._emit_block(text)


def compose_article_text(payload: bytes) -> tuple[str, int]:
    """Normalize one article payload into ``(plain text, image count)``."""
    try:
        html = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = "NotebookLM article payload is not valid UTF-8"
        raise NotebookLMParseError(msg) from exc

    renderer = _ArticleRenderer()
    try:
        renderer.feed(html)
        renderer.close()
    except Exception as exc:
        msg = "NotebookLM article payload could not be parsed"
        raise NotebookLMParseError(msg) from exc

    return "\n\n".join(renderer.blocks), renderer.image_count


def _normalize_instant(value: object) -> str | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.astimezone(dt.UTC).isoformat()


def read_article_metadata(path: Path) -> dict[str, str]:
    """Read stable provenance from an article's sibling metadata file."""
    metadata_path = path.with_name(path.stem + " metadata.json")
    if not metadata_path.is_file():
        return {}
    try:
        data = json.loads(metadata_path.read_bytes().decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    provenance: dict[str, str] = {}
    title = data.get("title")
    if isinstance(title, str) and title.strip():
        provenance["title"] = title.strip()
    nested = data.get("metadata")
    if isinstance(nested, dict):
        content_type = nested.get("originalSourceContentType")
        if isinstance(content_type, str) and content_type:
            provenance["source_content_type"] = content_type
        added = _normalize_instant(nested.get("sourceAddedTimestamp"))
        if added:
            provenance["added_at"] = added
    return provenance


def build_article_record(
    source_key: str,
    payload: bytes,
    *,
    title: str | None = None,
    source_content_type: str | None = None,
    added_at: str | None = None,
) -> SourceRecord:
    """Normalize one article file into the shared source representation.

    The record's payload is the normalized plain text (exactly the bytes
    that are hashed), so semantically identical HTML yields identical
    identity regardless of serialization details. ``title`` comes from the
    sibling export metadata when available and falls back to the filename
    stem otherwise.
    """
    text, image_count = compose_article_text(payload)
    if not text:
        msg = f"NotebookLM article {source_key!r} has no usable content"
        raise EmptyArticleError(msg)

    parts = PurePosixPath(source_key).parts
    metadata: dict[str, object] = {
        "filename": parts[-1],
        "mime_type": "text/html",
        "title": (title or "").strip() or PurePosixPath(source_key).stem,
        "notebook": parts[0] if len(parts) > 1 else "",
        "image_count": image_count,
    }
    if source_content_type:
        metadata["source_content_type"] = source_content_type

    materialized = text.encode("utf-8")
    added = _normalize_instant(added_at) if added_at else None
    return SourceRecord(
        source_type=SOURCE_TYPE,
        source_key=source_key,
        content_hash=compute_content_hash(materialized),
        created_at=added or "",
        modified_at=added or "",
        payload=materialized,
        metadata=metadata,
    )


class NotebookLMSourceAdapter:
    """Yields NotebookLM articles below a Takeout export as source records."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE

    def discover(self) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for candidate in sorted(self.directory.rglob("*.html")):
            if candidate.parent.name != "Sources" or not candidate.is_file():
                continue
            if not self._is_contained(candidate.resolve()):
                continue
            records.append(self._build(candidate))
        records.sort(key=lambda record: record.source_key)
        return records

    def load_record(self, source_key: str) -> SourceRecord:
        candidate = (self.directory / source_key).resolve()
        try:
            candidate.relative_to(self.directory)
        except ValueError as exc:
            msg = f"Path escapes NotebookLM export directory: {source_key!r}"
            raise PathOutsideExportError(msg) from exc

        if not candidate.is_file():
            msg = f"No such NotebookLM article: {source_key!r}"
            raise SourceNotFoundError(msg)
        if candidate.suffix.lower() != ".html" or candidate.parent.name != "Sources":
            msg = f"Not a NotebookLM article file: {source_key!r}"
            raise UnsupportedArticleError(msg)

        return self._build(candidate)

    def _build(self, path: Path) -> SourceRecord:
        source_key = path.relative_to(self.directory).as_posix()
        provenance = read_article_metadata(path)
        return build_article_record(
            source_key,
            path.read_bytes(),
            title=provenance.get("title"),
            source_content_type=provenance.get("source_content_type"),
            added_at=provenance.get("added_at"),
        )

    def _is_contained(self, resolved_path: Path) -> bool:
        try:
            resolved_path.relative_to(self.directory)
        except ValueError:
            return False
        return True
