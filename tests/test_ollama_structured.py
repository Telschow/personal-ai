"""Tests for the Ollama-backed structured extractor, with mocked HTTP."""

import json
from typing import Any

import httpx
import pytest

from personal_ai.documents import (
    MalformedStructuredOutputError,
    StructuredExtraction,
    StructuredExtractor,
    TextExtractionResult,
)
from personal_ai.ollama_client import OllamaClient, OllamaConnectionError
from personal_ai.ollama_structured import (
    STRUCTURED_EXTRACTION_SYSTEM_PROMPT,
    OllamaStructuredExtractor,
)

SECRET_MARKER = "s3cret-document-body"


def make_extraction() -> TextExtractionResult:
    return TextExtractionResult(
        document_id="doc-1",
        source_type="file",
        source_key="notes/ideas.txt",
        content_hash="hash-1",
        text=SECRET_MARKER,
    )


def chat_payload(content: str) -> dict[str, Any]:
    return {
        "model": "test-model",
        "created_at": "2026-08-22T12:00:00Z",
        "message": {"role": "assistant", "content": content},
        "done": True,
    }


def make_extractor(
    handler: Any,
) -> tuple[OllamaStructuredExtractor, list[httpx.Request], OllamaClient]:
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = OllamaClient(
        model="test-model",
        transport=httpx.MockTransport(recording_handler),
    )
    return OllamaStructuredExtractor(client), requests, client


def json_response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json=chat_payload(json.dumps(payload)))


def test_extract_posts_document_text_and_parses_json_result() -> None:
    extractor, requests, client = make_extractor(
        lambda request: json_response({"summary": "Career notes"})
    )

    with client:
        result = extractor.extract(make_extraction())

    assert result == StructuredExtraction(document_id="doc-1", summary="Career notes")

    body = json.loads(requests[0].content)
    assert body["model"] == "test-model"
    assert body["stream"] is False
    roles = [message["role"] for message in body["messages"]]
    assert roles == ["system", "user"]
    assert body["messages"][0]["content"] == STRUCTURED_EXTRACTION_SYSTEM_PROMPT
    assert body["messages"][1]["content"] == SECRET_MARKER


def test_extract_tolerates_markdown_fenced_json() -> None:
    fenced = '```json\n{"summary": "fenced"}\n```'
    extractor, _requests, client = make_extractor(
        lambda request: httpx.Response(200, json=chat_payload(fenced))
    )

    with client:
        result = extractor.extract(make_extraction())

    assert result.summary == "fenced"


def test_non_json_output_raises_malformed_error_without_leaking_text() -> None:
    extractor, _requests, client = make_extractor(
        lambda request: httpx.Response(200, json=chat_payload("I cannot do that."))
    )

    with client, pytest.raises(MalformedStructuredOutputError) as exc_info:
        extractor.extract(make_extraction())

    message = str(exc_info.value)
    assert SECRET_MARKER not in message
    assert "non-JSON" in message


def test_empty_model_output_raises_malformed_error() -> None:
    extractor, _requests, client = make_extractor(
        lambda request: httpx.Response(200, json=chat_payload(""))
    )

    with client, pytest.raises(MalformedStructuredOutputError):
        extractor.extract(make_extraction())


def test_provider_connection_errors_propagate_unwrapped() -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    extractor, _requests, client = make_extractor(failing_handler)

    with (
        client,
        pytest.raises(OllamaConnectionError) as exc_info,
    ):
        extractor.extract(make_extraction())

    assert not isinstance(exc_info.value, MalformedStructuredOutputError)


def test_http_status_errors_propagate_unwrapped() -> None:
    from personal_ai.ollama_client import OllamaHTTPStatusError

    extractor, _requests, client = make_extractor(
        lambda request: httpx.Response(500, text="boom")
    )

    with client, pytest.raises(OllamaHTTPStatusError):
        extractor.extract(make_extraction())


def test_ollama_extractor_satisfies_structured_extractor_protocol() -> None:
    extractor, _requests, _client = make_extractor(lambda request: json_response({}))

    assert isinstance(extractor, StructuredExtractor)


def test_extract_never_touches_the_source_origin() -> None:
    """source_key points nowhere; extraction must rely on text only."""
    extraction = TextExtractionResult(
        document_id="doc-9",
        source_type="file",
        source_key="does/not/exist.txt",
        content_hash="hash-9",
        text="irrelevant to filesystem",
    )
    extractor, _requests, client = make_extractor(
        lambda request: json_response({"summary": "ok"})
    )

    with client:
        result = extractor.extract(extraction)

    assert result.document_id == "doc-9"
