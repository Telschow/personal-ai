"""Typed models describing items yielded by source adapters."""

from personal_ai.sources.base import SourceAdapter, SourceError
from personal_ai.sources.filesystem import FilesystemSourceAdapter
from personal_ai.sources.models import SourceRecord

__all__ = [
    "FilesystemSourceAdapter",
    "SourceAdapter",
    "SourceError",
    "SourceRecord",
]
