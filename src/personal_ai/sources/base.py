"""Abstract contract shared by all ingestion source adapters."""

from typing import Protocol, runtime_checkable

from personal_ai.sources.models import SourceRecord


class SourceError(Exception):
    """Base class for errors raised by source adapters."""


@runtime_checkable
class SourceAdapter(Protocol):
    """A provider of personal-data records.

    The protocol is structural on purpose: future adapters such as Google
    Takeout, Outlook mail, or finance exports do not need to inherit from
    this project. They only need to satisfy the interface.
    """

    @property
    def source_type(self) -> str:
        """Stable identifier of the originating source type."""
        ...

    def discover(self) -> list[SourceRecord]:
        """Return all currently available records in deterministic order."""
        ...
