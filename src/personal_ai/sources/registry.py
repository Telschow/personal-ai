"""Explicit registry resolving source-type names to adapters.

The CLI never branches on individual sources: it resolves a name through
this table and hands the adapter to the generic orchestration. Adding a
new source means adding one entry here.
"""

from pathlib import Path

from personal_ai.sources.base import SourceAdapter, SourceError
from personal_ai.sources.chatgpt import SOURCE_TYPE as CHATGPT_SOURCE_TYPE
from personal_ai.sources.chatgpt import ChatGPTSourceAdapter
from personal_ai.sources.gemini import SOURCE_TYPE as GEMINI_SOURCE_TYPE
from personal_ai.sources.gemini import GeminiSourceAdapter
from personal_ai.sources.keep import SOURCE_TYPE as KEEP_SOURCE_TYPE
from personal_ai.sources.keep import KeepSourceAdapter
from personal_ai.sources.notebooklm import SOURCE_TYPE as NOTEBOOKLM_SOURCE_TYPE
from personal_ai.sources.notebooklm import NotebookLMSourceAdapter

_ADAPTER_CLASSES: dict[str, type] = {
    KEEP_SOURCE_TYPE: KeepSourceAdapter,
    NOTEBOOKLM_SOURCE_TYPE: NotebookLMSourceAdapter,
    GEMINI_SOURCE_TYPE: GeminiSourceAdapter,
    CHATGPT_SOURCE_TYPE: ChatGPTSourceAdapter,
}


def known_source_types() -> tuple[str, ...]:
    """Return the registered source-type names in registration order."""
    return tuple(_ADAPTER_CLASSES)


def resolve_source_adapter(source_type: str, directory: Path) -> SourceAdapter:
    """Construct the registered adapter for ``source_type``.

    Raises :class:`SourceError` for unknown names so callers can turn the
    failure into a clean user-facing error instead of guessing formats.
    """
    adapter_class = _ADAPTER_CLASSES.get(source_type)
    if adapter_class is None:
        known = ", ".join(known_source_types())
        msg = f"Unknown source type {source_type!r}. Known sources: {known}"
        raise SourceError(msg)
    return adapter_class(directory)


__all__ = ["known_source_types", "resolve_source_adapter"]
