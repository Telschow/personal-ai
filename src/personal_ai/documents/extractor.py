"""Provider-independent extraction of text from source records."""

from dataclasses import dataclass, field

from personal_ai.documents.models import compute_document_id
from personal_ai.sources.models import SourceRecord


class TextExtractionError(Exception):
    """Raised when a record's payload cannot be decoded as UTF-8."""


@dataclass(frozen=True, slots=True)
class TextExtractionResult:
    """Text extracted from a single source record.

    ``document_id`` is derived with the same identity helpers used by
    canonicalization, so an extracted result can always be associated with
    its canonical document without recomputing identity at call sites.
    """

    document_id: str
    source_type: str
    source_key: str
    content_hash: str
    text: str
    metadata: dict[str, object] = field(default_factory=dict)


def extract_text(record: SourceRecord) -> TextExtractionResult:
    """Decode a source record's materialized payload as strict UTF-8.

    Empty payloads yield empty text. Invalid UTF-8 raises
    :class:`TextExtractionError` instead of being silently replaced.
    """
    if record.payload is None:
        msg = f"Source record {record.source_key!r} has no materialized payload"
        raise ValueError(msg)

    try:
        text = record.payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"Source record {record.source_key!r} payload is not valid UTF-8"
        raise TextExtractionError(msg) from exc

    return TextExtractionResult(
        document_id=compute_document_id(
            record.source_type, record.source_key, record.content_hash
        ),
        source_type=record.source_type,
        source_key=record.source_key,
        content_hash=record.content_hash,
        text=text,
        metadata=dict(record.metadata),
    )
