"""Typed structured knowledge extraction results and their contract."""

from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

from personal_ai.documents.extractor import TextExtractionResult

_COLLECTION_FIELDS = (
    "goals",
    "organizations",
    "people",
    "projects",
    "topics",
)


class MalformedStructuredOutputError(Exception):
    """Raised when model output cannot be validated as a structured extraction."""


@dataclass(frozen=True, slots=True)
class StructuredExtraction:
    """Conservative structured understanding of a single document.

    Deliberately small ontology: free-form facts belong in ``topics``
    until real data justifies richer fields. Identity ties every result
    to its canonical document regardless of which extractor produced it.
    """

    document_id: str
    summary: str = ""
    people: tuple[str, ...] = ()
    organizations: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    goals: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class StructuredExtractor(Protocol):
    """A provider of structured knowledge for extracted document text.

    Structural on purpose: a future vision-based extractor satisfies a
    separate boundary while producing the same result model, and external
    implementations need not inherit from anything in this project.
    """

    def extract(self, extraction: TextExtractionResult) -> StructuredExtraction:
        """Return the structured understanding of one extracted document."""
        ...


def parse_structured_extraction(
    document_id: str, payload: object
) -> StructuredExtraction:
    """Validate provider output into a typed :class:`StructuredExtraction`.

    Unknown keys are ignored; known fields are type-checked without ever
    placing their values into error messages.
    """
    if not isinstance(payload, dict):
        msg = f"Expected a JSON object, got {type(payload).__name__}"
        raise MalformedStructuredOutputError(msg)

    summary = payload.get("summary", "")
    if not isinstance(summary, str):
        msg = f"'summary' must be a string, got {type(summary).__name__}"
        raise MalformedStructuredOutputError(msg)

    collections = {
        field_name: _validated_collection(field_name, payload.get(field_name, []))
        for field_name in _COLLECTION_FIELDS
    }

    return StructuredExtraction(document_id=document_id, summary=summary, **collections)


def _validated_collection(field_name: str, raw_value: object) -> tuple[str, ...]:
    if not isinstance(raw_value, list) or not all(
        isinstance(item, str) for item in raw_value
    ):
        msg = f"'{field_name}' must be an array of strings"
        raise MalformedStructuredOutputError(msg)
    return tuple(raw_value)


def normalize_structured_extraction(
    extraction: StructuredExtraction,
) -> StructuredExtraction:
    """Whitespace-clean a structured extraction deterministically.

    Strips surrounding whitespace from the summary and from every
    collection item, drops empty items, and collapses exact duplicates
    preserving first-occurrence order. Case and accents are preserved
    (``München`` stays distinct from ``MUNCHEN``), so normalization is
    mechanical: it never transliterates, folds, or invents values.
    Metadata is preserved by value (copied, never aliased or mutated).
    """
    summary = extraction.summary.strip()
    collections = {
        field_name: _normalized_collection(getattr(extraction, field_name))
        for field_name in _COLLECTION_FIELDS
    }
    return replace(
        extraction,
        summary=summary,
        metadata=dict(extraction.metadata),
        **collections,
    )


def _normalized_collection(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        candidate = value.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            cleaned.append(candidate)
    return tuple(cleaned)
