"""Google Keep export adapter: one note JSON becomes one normalized record."""

import datetime as dt
import json
from pathlib import Path, PurePosixPath

from personal_ai.documents.models import compute_content_hash
from personal_ai.sources.base import SourceError
from personal_ai.sources.models import SourceRecord

SOURCE_TYPE = "google_keep"

_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".ruff_cache"}
)


class KeepParseError(SourceError):
    """Raised when a Keep export file is not a well-formed note object."""


class TrashedNoteError(SourceError):
    """Raised when a trashed (user-deleted) note is loaded explicitly."""


class EmptyNoteError(SourceError):
    """Raised when a note carries no title, text, list, or annotation data."""


class PathOutsideExportError(SourceError):
    """Raised when a requested source key escapes the export directory."""


class SourceNotFoundError(SourceError):
    """Raised when a requested note does not exist in the export."""


class UnsupportedNoteError(SourceError):
    """Raised when a requested file is not a Keep JSON note."""


def _cleaned(value: object) -> str:
    """Strip string fields; any other JSON value contributes nothing."""
    if isinstance(value, str):
        return value.strip()
    return ""


def _iso_from_usec(usec: object, field: str, source_key: str) -> str:
    if isinstance(usec, bool) or not isinstance(usec, int):
        msg = f"Keep note {source_key!r} has invalid {field}"
        raise KeepParseError(msg)
    seconds, micros = divmod(usec, 1_000_000)
    moment = dt.datetime.fromtimestamp(seconds, tz=dt.UTC).replace(microsecond=micros)
    return moment.isoformat()


def parse_keep_note(payload: bytes) -> dict[str, object]:
    """Decode one Keep export file into its JSON object."""
    try:
        note = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "Keep note payload is not valid UTF-8 JSON"
        raise KeepParseError(msg) from exc
    if not isinstance(note, dict):
        msg = f"Keep note payload is a {type(note).__name__}, expected an object"
        raise KeepParseError(msg)
    return note


def compose_note_text(note: dict[str, object]) -> str:
    """Compose retrieval-ready plain text from a Keep note.

    Sections appear in a fixed order separated by blank lines: the title;
    the note body (``textContent`` followed by ``listContent`` item texts
    as plain lines); then one block per annotation with its title,
    description, and URL. Checkbox state is deliberately excluded so that
    ticking items off never changes document identity. Export bookkeeping
    (colors, timestamps, HTML mirrors, attachment blobs, task ids) never
    enters the text. An annotation URL that already appears verbatim in
    the composed text is omitted rather than duplicated.
    """
    sections: list[str] = []

    title = _cleaned(note.get("title"))
    if title:
        sections.append(title)

    body: list[str] = []
    text_content = _cleaned(note.get("textContent"))
    if text_content:
        body.append(text_content)
    for item in note.get("listContent") or []:
        if isinstance(item, dict):
            entry = _cleaned(item.get("text"))
            if entry:
                body.append(entry)
    if body:
        sections.append("\n".join(body))

    for annotation in note.get("annotations") or []:
        if not isinstance(annotation, dict):
            continue
        parts: list[str] = []
        for key in ("title", "description"):
            value = _cleaned(annotation.get(key))
            if value:
                parts.append(value)
        url = _cleaned(annotation.get("url"))
        if url and url not in "\n".join([*sections, *parts]):
            parts.append(url)
        if parts:
            sections.append("\n".join(parts))

    return "\n\n".join(sections)


def build_note_record(source_key: str, payload: bytes) -> SourceRecord:
    """Normalize one Keep note file into the shared source representation.

    The record's payload is the composed plain text (exactly the bytes
    that are hashed), so downstream extraction sees curated prose rather
    than raw JSON. The note title travels verbatim in metadata as a
    first-class retrieval signal. Identity is a pure function of the
    composed content and the stable source key.
    """
    note = parse_keep_note(payload)
    if note.get("isTrashed") is True:
        msg = f"Keep note {source_key!r} is trashed"
        raise TrashedNoteError(msg)

    text = compose_note_text(note)
    if not text:
        msg = f"Keep note {source_key!r} has no usable content"
        raise EmptyNoteError(msg)

    annotations = [
        item for item in note.get("annotations") or [] if isinstance(item, dict)
    ]
    metadata: dict[str, object] = {
        "filename": PurePosixPath(source_key).name,
        "mime_type": "application/json",
        "title": _cleaned(note.get("title")),
        "is_pinned": bool(note.get("isPinned")),
        "is_archived": bool(note.get("isArchived")),
        "annotation_count": len(annotations),
    }
    materialized = text.encode("utf-8")
    return SourceRecord(
        source_type=SOURCE_TYPE,
        source_key=source_key,
        content_hash=compute_content_hash(materialized),
        created_at=_iso_from_usec(
            note.get("createdTimestampUsec"), "createdTimestampUsec", source_key
        ),
        modified_at=_iso_from_usec(
            note.get("userEditedTimestampUsec"), "userEditedTimestampUsec", source_key
        ),
        payload=materialized,
        metadata=metadata,
    )


class KeepSourceAdapter:
    """Yields Google Keep notes below an export directory as source records.

    Discovery reads ``*.json`` files deterministically (sorted by relative
    POSIX path) and skips trashed and content-less notes, which carry no
    retrievable knowledge. Corrupt files propagate their parse error so
    broken exports are noticed instead of silently shrinking the corpus.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE

    def discover(self) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for candidate in sorted(self.directory.rglob("*.json")):
            if not candidate.is_file():
                continue
            if not self._is_contained(candidate.resolve()):
                continue
            if any(
                part in _EXCLUDED_DIRECTORY_NAMES
                for part in candidate.relative_to(self.directory).parts[:-1]
            ):
                continue
            source_key = candidate.relative_to(self.directory).as_posix()
            try:
                records.append(build_note_record(source_key, candidate.read_bytes()))
            except TrashedNoteError, EmptyNoteError:
                continue
        records.sort(key=lambda record: record.source_key)
        return records

    def load_record(self, source_key: str) -> SourceRecord:
        """Load a single note by its export-relative POSIX path."""
        candidate = (self.directory / source_key).resolve()
        try:
            candidate.relative_to(self.directory)
        except ValueError as exc:
            msg = f"Path escapes Keep export directory: {source_key!r}"
            raise PathOutsideExportError(msg) from exc

        if not candidate.is_file():
            msg = f"No such Keep note: {source_key!r}"
            raise SourceNotFoundError(msg)
        if candidate.suffix.lower() != ".json":
            msg = f"Not a Keep note file: {source_key!r}"
            raise UnsupportedNoteError(msg)

        return build_note_record(source_key, candidate.read_bytes())

    def _is_contained(self, resolved_path: Path) -> bool:
        try:
            resolved_path.relative_to(self.directory)
        except ValueError:
            return False
        return True
