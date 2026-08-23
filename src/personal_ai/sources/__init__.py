"""Typed models describing items yielded by source adapters."""

from personal_ai.sources.base import SourceAdapter, SourceError
from personal_ai.sources.chatgpt import (
    ChatGPTSourceAdapter,
)
from personal_ai.sources.chatgpt import (
    build_conversation_record as build_chatgpt_conversation_record,
)
from personal_ai.sources.filesystem import FilesystemSourceAdapter
from personal_ai.sources.gemini import GeminiSourceAdapter, build_conversation_record
from personal_ai.sources.keep import KeepSourceAdapter, build_note_record
from personal_ai.sources.models import SourceRecord
from personal_ai.sources.notebooklm import (
    NotebookLMSourceAdapter,
    build_article_record,
)

__all__ = [
    "ChatGPTSourceAdapter",
    "FilesystemSourceAdapter",
    "GeminiSourceAdapter",
    "KeepSourceAdapter",
    "NotebookLMSourceAdapter",
    "SourceAdapter",
    "SourceError",
    "SourceRecord",
    "build_article_record",
    "build_chatgpt_conversation_record",
    "build_conversation_record",
    "build_note_record",
]
