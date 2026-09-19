"""User-uploaded career artifacts (CV, cover letter) — Phase 4.1.

This module defines the artifact model for files the user explicitly uploads
and attaches to a job. These are distinct from the LLM-generated *proposal*
artifacts in :mod:`career.artifacts` (which are versioned, append-only, and
carry evidence trail for validation).

User artifacts follow a simple lifecycle:
    UPLOADED -> APPROVED (terminal) or ARCHIVED
    DRAFT is reserved for generated artifacts awaiting review.

Files are stored on disk at a configured artifact root (JOB_AGENT_ARTIFACTS).
The database stores metadata only: identity, provenance, content hash, and
approval state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class UserArtifactType(StrEnum):
    CV = "cv"
    COVER_LETTER = "cover_letter"


class UserArtifactStatus(StrEnum):
    UPLOADED = "uploaded"
    DRAFT = "draft"
    APPROVED = "approved"
    ARCHIVED = "archived"


class UserArtifactSource(StrEnum):
    UPLOADED = "uploaded"
    GENERATED = "generated"


MAX_ARTIFACT_SIZE = 10 * 1024 * 1024  # 10 MiB

ALLOWED_MIME_TYPES = {
    UserArtifactType.CV: {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    },
    UserArtifactType.COVER_LETTER: {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "text/plain",
    },
}

EXTENSION_TO_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".txt": "text/plain",
}


class ArtifactValidationError(ValueError):
    """Raised when an artifact file fails validation."""


def validate_artifact_file(
    path: Path,
    artifact_type: UserArtifactType,
    *,
    max_size: int = MAX_ARTIFACT_SIZE,
) -> tuple[str, int]:
    """Validate an artifact file and return (mime_type, size_bytes).

    Raises :class:`ArtifactValidationError` on failure.
    """
    if not path.is_file():
        raise ArtifactValidationError(f"file not found: {path}")

    size = path.stat().st_size
    if size == 0:
        raise ArtifactValidationError("file is empty")
    if size > max_size:
        raise ArtifactValidationError(f"file too large: {size} bytes > {max_size} ({max_size // (1024 * 1024)} MiB)")

    suffix = path.suffix.lower()
    if suffix not in EXTENSION_TO_MIME:
        raise ArtifactValidationError(
            f"unsupported file extension: {suffix} (allowed: {', '.join(sorted(EXTENSION_TO_MIME))})"
        )

    mime = EXTENSION_TO_MIME[suffix]
    if mime not in ALLOWED_MIME_TYPES[artifact_type]:
        raise ArtifactValidationError(
            f"file type {mime} not allowed for {artifact_type.value} "
            f"(allowed: {', '.join(sorted(ALLOWED_MIME_TYPES[artifact_type]))})"
        )

    # Content sniffing for PDF (magic bytes)
    if mime == "application/pdf":
        with path.open("rb") as f:
            header = f.read(5)
            if not header.startswith(b"%PDF-"):
                raise ArtifactValidationError("file does not appear to be a valid PDF")

    return mime, size


def content_hash(path: Path) -> str:
    """SHA-256 hash of file content."""
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact_id_for(content_hash: str, job_id: str, artifact_type: UserArtifactType) -> str:
    """Stable artifact ID from content hash + job + type (short prefix)."""
    return f"ua:{artifact_type.value[:2]}:{job_id}:{content_hash[:16]}"


def safe_filename(filename: str) -> str:
    """Normalize a filename for safe storage."""
    import re

    name = Path(filename).name
    name = re.sub(r"[^\w.\-]", "_", name)
    name = re.sub(r"_+", "_", name)
    return name[:200]


def resolve_artifact_root() -> Path:
    """Resolve the artifact storage root from environment or default."""
    import os

    root = os.environ.get("JOB_AGENT_ARTIFACTS")
    if root:
        return Path(root).expanduser().resolve()
    # Default: next to the job database in the data volume
    return Path("/data/artifacts")


def artifact_storage_path(
    root: Path,
    job_id: str,
    artifact_type: UserArtifactType,
    filename: str,
) -> Path:
    """Compute the on-disk storage path for an artifact.

    Structure: {root}/{job_id}/{artifact_type}/{safe_filename}
    """
    safe = safe_filename(filename)
    return root / job_id / artifact_type.value / safe


class UserArtifact(BaseModel):
    """A user-uploaded or generated career artifact attached to a job."""

    id: str
    job_id: str
    artifact_type: UserArtifactType
    status: UserArtifactStatus = UserArtifactStatus.UPLOADED
    filename: str
    mime_type: str
    storage_path: str
    content_hash: str
    size_bytes: int
    created_at: str
    updated_at: str
    approved_at: str | None = None
    source: UserArtifactSource = UserArtifactSource.UPLOADED
    source_artifact_id: str | None = None
    metadata: dict = Field(default_factory=dict)

    def approve(self, *, now: datetime | None = None) -> UserArtifact:
        """Return a copy with status APPROVED and approval timestamp."""
        now = now or datetime.now(UTC)
        return UserArtifact(
            **{
                **self.model_dump(),
                "status": UserArtifactStatus.APPROVED,
                "approved_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        )

    def archive(self, *, now: datetime | None = None) -> UserArtifact:
        """Return a copy with status ARCHIVED."""
        now = now or datetime.now(UTC)
        return UserArtifact(
            **{
                **self.model_dump(),
                "status": UserArtifactStatus.ARCHIVED,
                "updated_at": now.isoformat(),
            }
        )

    def with_metadata(self, **updates: object) -> UserArtifact:
        """Return a copy with updated metadata (shallow merge)."""
        now = datetime.now(UTC)
        merged = {**self.metadata, **updates}
        return UserArtifact(
            **{
                **self.model_dump(),
                "metadata": merged,
                "updated_at": now.isoformat(),
            }
        )

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


__all__ = [
    "UserArtifact",
    "UserArtifactType",
    "UserArtifactStatus",
    "UserArtifactSource",
    "ArtifactValidationError",
    "validate_artifact_file",
    "content_hash",
    "artifact_id_for",
    "safe_filename",
    "resolve_artifact_root",
    "artifact_storage_path",
    "MAX_ARTIFACT_SIZE",
    "ALLOWED_MIME_TYPES",
    "EXTENSION_TO_MIME",
]
