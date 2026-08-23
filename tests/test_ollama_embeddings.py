"""Unit tests for the Ollama-backed embedding provider, using mocked HTTP."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from personal_ai.documents import MalformedEmbeddingError, parse_embedding
from personal_ai.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaHTTPStatusError,
    OllamaResponseError,
)
from personal_ai.ollama_embeddings import OllamaEmbedder

Handler = Callable[[httpx.Request], httpx.Response]

LONG_INPUT = "distinctive chunk body that must never appear in error messages"


def embed_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "nomic-embed-text",
        "embeddings": [[0.1, -0.2, 0.3]],
    }
    payload.update(overrides)
    return payload


def make_embedder(
    handler: Handler, **client_overrides: Any
) -> tuple[OllamaEmbedder, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = OllamaClient(
        model="nomic-embed-text",
        transport=httpx.MockTransport(recording_handler),
        **client_overrides,
    )
    return OllamaEmbedder(client), requests


def test_embed_posts_expected_payload_to_api_embed() -> None:
    embedder, requests = make_embedder(
        lambda request: httpx.Response(200, json=embed_payload())
    )

    result = embedder.embed("hello world")

    assert len(requests) == 1
    assert requests[0].url.path == "/api/embed"
    sent = json.loads(requests[0].content)
    assert sent == {"model": "nomic-embed-text", "input": "hello world"}

    expected = parse_embedding("nomic-embed-text", [[0.1, -0.2, 0.3]])
    assert result == expected


def test_embed_returns_typed_result_with_serving_model() -> None:
    embedder, _requests = make_embedder(
        lambda request: httpx.Response(200, json=embed_payload())
    )

    result = embedder.embed("body text")

    assert result.model == "nomic-embed-text"
    assert result.vector == (0.1, -0.2, 0.3)
    assert result.dimensions == 3


def test_embedder_exposes_the_configured_model_identity() -> None:
    embedder, _requests = make_embedder(
        lambda request: httpx.Response(200, json=embed_payload())
    )

    assert embedder.model == "nomic-embed-text"


def test_empty_text_is_rejected_before_any_network_call() -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be contacted for empty input")

    embedder, _requests = make_embedder(failing_handler)

    with pytest.raises(ValueError):
        embedder.embed("")


def test_provider_http_errors_propagate_unchanged() -> None:
    embedder, _requests = make_embedder(
        lambda request: httpx.Response(500, text="model not found")
    )

    with pytest.raises(OllamaHTTPStatusError):
        embedder.embed(LONG_INPUT)


def test_connection_errors_propagate_unchanged() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    embedder, _requests = make_embedder(unreachable)

    with pytest.raises(OllamaConnectionError):
        embedder.embed(LONG_INPUT)


def test_malformed_envelope_raises_response_error() -> None:
    embedder, _requests = make_embedder(
        lambda request: httpx.Response(200, json={"unexpected": True})
    )

    with pytest.raises(OllamaResponseError):
        embedder.embed(LONG_INPUT)


def test_non_numeric_components_raise_domain_error() -> None:
    embedder, _requests = make_embedder(
        lambda request: httpx.Response(
            200, json=embed_payload(embeddings=[["bad", 0.5]])
        )
    )

    with pytest.raises(MalformedEmbeddingError):
        embedder.embed(LONG_INPUT)


def test_wrong_vector_count_raises_domain_error() -> None:
    embedder, _requests = make_embedder(
        lambda request: httpx.Response(
            200,
            json=embed_payload(embeddings=[[0.1], [0.2]]),
        )
    )

    with pytest.raises(MalformedEmbeddingError) as exc_info:
        embedder.embed(LONG_INPUT)

    message = str(exc_info.value)
    assert LONG_INPUT not in message
    assert "0.1" not in message and "0.2" not in message


def test_error_messages_never_contain_input_text() -> None:
    embedder, _requests = make_embedder(
        lambda request: httpx.Response(200, json=embed_payload(embeddings=[[]]))
    )

    with pytest.raises(MalformedEmbeddingError) as exc_info:
        embedder.embed(LONG_INPUT)

    assert LONG_INPUT not in str(exc_info.value)
