"""Structured Chrome history loader.

Reads Chrome history export JSON files and produces typed
:class:`~personal_ai.events.models.Event` domain objects without flattening
into text blobs or documents.

Discovery follows the repository's loader conventions: ``*.json`` files
below the directory are found deterministically (sorted by relative POSIX
path), staying inside the directory boundary, and only files whose decoded
shape carries a ``"Browser History"`` list are treated as history. Other
Chrome export sidecars (Settings, Bookmarks, extensions, device info) are
structurally different files and excluded. Corrupt JSON propagates its
parse error so broken exports are noticed instead of silently shrinking the
corpus.

The loader is stateless and does not touch any database. It reads files
from disk and returns domain objects for the caller to persist.
"""

from pathlib import Path

from personal_ai.events.models import Event
from personal_ai.sources.chrome_history import (
    BROWSER_HISTORY_KEY,
    SOURCE_TYPE,
    ChromeHistoryLoadError,
    ChromeHistoryParseError,
    ChromeHistoryResult,
    normalize_history_data,
    parse_history_json,
)

_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".ruff_cache"}
)


class ChromeHistoryLoader:
    """Reads Chrome history export files and produces typed Event objects.

    The loader is stateless and does not touch any database. It reads
    files from disk and returns domain objects for the caller to persist.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    def discover_files(self) -> list[Path]:
        """Return sorted list of Chrome history export JSON files.

        Files are found recursively, restricted to the directory, in
        deterministic sorted order. Only JSON files whose decoded shape
        carries a top-level ``"Browser History"`` list are considered;
        other Chrome export sidecars are structurally different files and
        excluded.
        """
        files: list[Path] = []
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
            try:
                data = parse_history_json(candidate.read_bytes())
            except ChromeHistoryParseError:
                continue
            if isinstance(data, dict) and isinstance(
                data.get(BROWSER_HISTORY_KEY), list
            ):
                files.append(candidate)
        return sorted(files)

    def load_file(self, path: Path) -> tuple[Event, ...]:
        """Load one Chrome history file into Event objects.

        Malformed individual records within a valid history file are
        skipped, not fatal. Use :meth:`load_file_result` to inspect the
        structured result including skipped-record counts.
        """
        return self.load_file_result(path).events

    def load_file_result(self, path: Path) -> ChromeHistoryResult:
        """Load one file, returning a structured result for validation."""
        data = self._decode(path)
        return normalize_history_data(data, source=SOURCE_TYPE)

    def load_all(self) -> tuple[Event, ...]:
        """Load all events from all discovered history files.

        Events are returned in deterministic order (by event_time then id),
        concatenating per-file results in discovery order.
        """
        results: list[Event] = []
        for path in self.discover_files():
            results.extend(self.load_file(path))
        return tuple(results)

    def _decode(self, path: Path) -> object:
        try:
            data = parse_history_json(path.read_bytes())
        except ChromeHistoryParseError as exc:
            msg = f"Chrome history file {self._source_key(path)!r} is not valid JSON"
            raise ChromeHistoryLoadError(msg) from exc
        if not isinstance(data, dict) or not isinstance(
            data.get(BROWSER_HISTORY_KEY), list
        ):
            msg = (
                f"Chrome history file {self._source_key(path)!r} is not a "
                "history export"
            )
            raise ChromeHistoryLoadError(msg)
        return data

    def _source_key(self, path: Path) -> str:
        return path.relative_to(self.directory).as_posix()

    def _is_contained(self, resolved_path: Path) -> bool:
        try:
            resolved_path.relative_to(self.directory)
        except ValueError:
            return False
        return True
