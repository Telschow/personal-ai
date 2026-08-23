"""Typed models describing items yielded by source adapters."""

from personal_ai.sources.base import SourceAdapter, SourceError
from personal_ai.sources.filesystem import FilesystemSourceAdapter
from personal_ai.sources.keep import KeepSourceAdapter, build_note_record
from personal_ai.sources.models import SourceRecord
from personal_ai.sources.notebooklm import (
    NotebookLMSourceAdapter,
    build_article_record,
)

__all__ = [
    "FilesystemSourceAdapter",
    "KeepSourceAdapter",
    "NotebookLMSourceAdapter",
    "SourceAdapter",
    "SourceError",
    "SourceRecord",
    "build_article_record",
    "build_note_record",
]
