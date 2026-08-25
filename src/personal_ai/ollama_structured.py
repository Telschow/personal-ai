"""Ollama-backed implementation of structured knowledge extraction."""

import json

from personal_ai.documents.extractor import TextExtractionResult
from personal_ai.documents.structured import (
    MalformedStructuredOutputError,
    StructuredExtraction,
    parse_structured_extraction,
)
from personal_ai.ollama_client import ChatMessage, OllamaClient

STRUCTURED_EXTRACTION_SYSTEM_PROMPT = """\
You extract structured information from personal documents.
Respond with ONLY a JSON object, no prose and no markdown.
Use exactly these keys:
  "summary": string,
  "people": array of strings,
  "organizations": array of strings,
  "projects": array of strings,
  "goals": array of strings,
  "topics": array of strings
Use an empty string or empty arrays when nothing applies.
Never invent facts that are not present in the document.
"""

EXTRACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "people": {"type": "array", "items": {"type": "string"}},
        "organizations": {"type": "array", "items": {"type": "string"}},
        "projects": {"type": "array", "items": {"type": "string"}},
        "goals": {"type": "array", "items": {"type": "string"}},
        "topics": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "people",
        "organizations",
        "projects",
        "goals",
        "topics",
    ],
}


class OllamaStructuredExtractor:
    """Structured extractor over :class:`OllamaClient`.

    Lives outside the pure document domain: it only translates between
    the chat primitive and the typed extraction contract. Provider errors
    from the client propagate unchanged; malformed model output raises
    :class:`MalformedStructuredOutputError` without leaking contents.
    """

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def extract(self, extraction: TextExtractionResult) -> StructuredExtraction:
        response = self._client.chat(
            [
                ChatMessage(role="system", content=STRUCTURED_EXTRACTION_SYSTEM_PROMPT),
                ChatMessage(role="user", content=extraction.text),
            ],
            think=False,
            format=EXTRACTION_SCHEMA,
        )
        return parse_structured_extraction(
            extraction.document_id,
            _decode_json_payload(response.content),
        )


def _decode_json_payload(content: str) -> object:
    try:
        return json.loads(_strip_json_fences(content))
    except ValueError as exc:
        msg = f"Model returned non-JSON output ({len(content)} characters)"
        raise MalformedStructuredOutputError(msg) from exc


def _strip_json_fences(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip().startswith("```"):
        lines.pop()
    return "\n".join(lines)
