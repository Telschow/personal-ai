"""Filesystem source adapter restricted to an explicit workspace."""

import datetime as dt
import os
from pathlib import Path, PurePosixPath

from personal_ai.documents.models import compute_content_hash
from personal_ai.sources.base import SourceError
from personal_ai.sources.models import SourceRecord

SOURCE_TYPE = "file"

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
    }
)


class PathOutsideWorkspaceError(SourceError):
    """Raised when a requested source key escapes the workspace."""


class UnsupportedFileError(SourceError):
    """Raised when a file has no supported extension."""


class SourceNotFoundError(SourceError):
    """Raised when a requested source does not exist in the workspace."""


def _iso_timestamp(epoch_seconds: float) -> str:
    return dt.datetime.fromtimestamp(epoch_seconds, tz=dt.UTC).isoformat()


class FilesystemSourceAdapter:
    """Yields supported files below a workspace root as source records.

    Discovery is deterministic (sorted by relative POSIX path), stays inside
    the workspace, and never follows symlinked directories. Symlinked files
    are only included when their resolved target remains inside the
    workspace. Records carry the exact bytes that were hashed so later
    analysis cannot observe a changed file between discovery and extraction.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE

    def discover(self) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for root, directories, filenames in os.walk(self.workspace, followlinks=False):
            root_path = Path(root)
            directories[:] = sorted(
                name for name in directories if name not in EXCLUDED_DIRECTORY_NAMES
            )
            for name in sorted(filenames):
                candidate = root_path / name
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                resolved = candidate.resolve()
                if not self._is_contained(resolved):
                    continue
                records.append(
                    self._build_record(
                        candidate.relative_to(self.workspace).as_posix(), resolved
                    )
                )
        records.sort(key=lambda record: record.source_key)
        return records

    def load_record(self, source_key: str) -> SourceRecord:
        """Load a single record by its workspace-relative path."""
        candidate = (self.workspace / source_key).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            msg = f"Path escapes workspace: {source_key!r}"
            raise PathOutsideWorkspaceError(msg) from exc

        if not candidate.is_file():
            msg = f"No such workspace file: {source_key!r}"
            raise SourceNotFoundError(msg)

        suffix = candidate.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            msg = f"Unsupported file extension: {suffix!r}"
            raise UnsupportedFileError(msg)

        return self._build_record(
            candidate.relative_to(self.workspace).as_posix(), candidate
        )

    def _is_contained(self, resolved_path: Path) -> bool:
        try:
            resolved_path.relative_to(self.workspace)
        except ValueError:
            return False
        return True

    def _build_record(self, source_key: str, path: Path) -> SourceRecord:
        stat_result = path.stat()
        payload = path.read_bytes()
        suffix = PurePosixPath(source_key).suffix.lower()
        return SourceRecord(
            source_type=self.source_type,
            source_key=source_key,
            content_hash=compute_content_hash(payload),
            created_at=_iso_timestamp(stat_result.st_mtime),
            modified_at=_iso_timestamp(stat_result.st_mtime),
            payload=payload,
            metadata={
                "filename": PurePosixPath(source_key).name,
                "extension": suffix,
                "mime_type": SUPPORTED_EXTENSIONS[suffix],
                "size_bytes": stat_result.st_size,
            },
        )
