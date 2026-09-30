"""Structured YouTube history loader.

Reads YouTube Takeout history HTML files and produces typed
:class:`~personal_ai.events.models.Event` domain objects without flattening
into text blobs or documents.

Discovery follows the repository's loader conventions: ``*.html`` files
below the directory are found deterministically (sorted by relative POSIX
path), staying inside the directory boundary, and only files whose decoded
shape carries the YouTube history ``content-cell`` markup are treated as
history. Other YouTube Takeout sidecars (CSV metadata, playlists,
subscriptions, and especially the raw video/music media under ``vídeos``
and ``music``) are structurally different files and excluded.

The loader is stateless and does not touch any database. It reads files
from disk and returns domain objects for the caller to persist.
"""

from pathlib import Path

from personal_ai.events.models import Event
from personal_ai.sources.youtube_history import (
    SOURCE_TYPE,
    YouTubeHistoryLoadError,
    YouTubeHistoryResult,
    is_history_export,
    normalize_history_html,
)

_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".ruff_cache"}
)


class YouTubeHistoryLoader:
    """Reads YouTube history exports and produces typed Event objects.

    The loader is stateless and does not touch any database. It reads files
    from disk and returns domain objects for the caller to persist.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    def discover_files(self) -> list[Path]:
        """Return sorted list of YouTube history export HTML files.

        Files are found recursively, restricted to the directory, in
        deterministic sorted order. Only HTML files whose decoded shape
        carries the history ``content-cell`` markup are considered; other
        YouTube Takeout sidecars and media are structurally different and
        excluded.
        """
        files: list[Path] = []
        for candidate in sorted(self.directory.rglob("*.html")):
            if not candidate.is_file():
                continue
            if not self._is_contained(candidate.resolve()):
                continue
            if any(
                part in _EXCLUDED_DIRECTORY_NAMES
                for part in candidate.relative_to(self.directory).parts[:-1]
            ):
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError, OSError:
                continue
            if is_history_export(text):
                files.append(candidate)
        return sorted(files)

    def load_file(self, path: Path) -> tuple[Event, ...]:
        """Load one YouTube history file into Event objects.

        Unrecognized records within a valid history file are skipped, not
        fatal. Use :meth:`load_file_result` to inspect the structured result
        including skipped-record counts.
        """
        return self.load_file_result(path).events

    def load_file_result(self, path: Path) -> YouTubeHistoryResult:
        """Load one file, returning a structured result for validation."""
        return normalize_history_html(self._decode(path), source=SOURCE_TYPE)

    def load_all(self) -> tuple[Event, ...]:
        """Load all events from all discovered history files.

        Events are returned in deterministic order (by event_time then id),
        concatenating per-file results in discovery order.
        """
        results: list[Event] = []
        for path in self.discover_files():
            results.extend(self.load_file(path))
        return tuple(results)

    def _decode(self, path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            msg = f"Unable to read YouTube history file {self._source_key(path)!r}"
            raise YouTubeHistoryLoadError(msg) from exc

    def _source_key(self, path: Path) -> str:
        return path.relative_to(self.directory).as_posix()

    def _is_contained(self, resolved_path: Path) -> bool:
        try:
            resolved_path.relative_to(self.directory)
        except ValueError:
            return False
        return True
