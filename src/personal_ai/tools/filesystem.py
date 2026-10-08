"""Safe filesystem tools restricted to an explicit workspace."""

from pathlib import Path


class FilesystemToolError(Exception):
    """Base class for filesystem tool errors."""


class PathOutsideWorkspaceError(FilesystemToolError):
    """Raised when a path escapes the configured workspace."""


class FilesystemTool:
    """Filesystem operations constrained to a workspace directory."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def resolve(self, path: str = ".") -> Path:
        """Resolve a user-supplied path while keeping it inside the workspace."""
        candidate = (self.workspace / path).resolve()

        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise PathOutsideWorkspaceError(
                f"Path escapes workspace: {path!r}"
            ) from exc

        return candidate

    def list_directory(self, arguments: dict[str, object]) -> list[dict[str, object]]:
        """List entries in a workspace directory."""
        path = arguments.get("path", ".")
        if not isinstance(path, str):
            raise TypeError("path must be a string")

        directory = self.resolve(path)

        if not directory.exists():
            raise FileNotFoundError(f"Directory does not exist: {path}")
        if not directory.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")

        entries = []
        for entry in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
            entries.append(
                {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                }
            )

        return entries
