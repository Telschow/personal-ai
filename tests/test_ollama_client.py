"""Unit tests for the Ollama client, using mocked HTTP responses."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from personal_ai.ollama_client import (
    ChatMessage,
    ChatResponse,
    OllamaClient,
    OllamaConnectionError,
    OllamaHTTPStatusError,
    OllamaResponseError,
)

Handler = Callable[[httpx.Request], httpx.Response]

USER_MESSAGE = [ChatMessage(role="user", content="hi")]


def chat_payload(content: str = "hello") -> dict[str, Any]:
    return {
        "model": "test-model",
        "created_at": "2026-08-22T12:00:00Z",
        "message": {"role": "assistant", "content": content},
        "done": True,
    }


def make_client(
    handler: Handler, **overrides: Any
) -> tuple[OllamaClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    options: dict[str, Any] = {
        "model": "test-model",
        "transport": httpx.MockTransport(recording_handler),
    }
    options.update(overrides)
    return OllamaClient(**options), requests


def test_chat_parses_non_streaming_response() -> None:
    client, _ = make_client(
        lambda request: httpx.Response(200, json=chat_payload("Hello!"))
    )

    with client:
        result = client.chat(USER_MESSAGE)

    assert result == ChatResponse(content="Hello!", model="test-model", done=True)


def test_chat_posts_expected_payload_to_api_chat() -> None:
    messages = [
        ChatMessage(role="system", content="be brief"),
        ChatMessage(role="user", content="hi"),
    ]
    client, requests = make_client(
        lambda request: httpx.Response(200, json=chat_payload())
    )

    with client:
        client.chat(messages)

    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://localhost:11434/api/chat"
    assert json.loads(request.content) == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ],
        "stream": False,
    }


def test_base_url_and_model_are_configurable() -> None:
    payload = {
        "model": "custom-model",
        "message": {"role": "assistant", "content": "hey"},
        "done": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client, requests = make_client(
        handler,
        base_url="http://127.0.0.1:11500/ollama/",
        model="custom-model",
    )

    with client:
        result = client.chat(USER_MESSAGE)

    assert str(requests[0].url) == "http://127.0.0.1:11500/ollama/api/chat"
    assert result == ChatResponse(content="hey", model="custom-model", done=True)


def test_optional_fields_fall_back_to_client_settings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": "hey"}}
        )

    client, _ = make_client(handler)

    with client:
        result = client.chat(USER_MESSAGE)

    assert result.model == "test-model"
    assert result.done is False


def test_http_error_status_raises_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client, _ = make_client(handler)

    with client, pytest.raises(OllamaHTTPStatusError) as exc_info:
        client.chat(USER_MESSAGE)

    assert exc_info.value.status_code == 500
    assert "boom" in str(exc_info.value)


def test_connection_failure_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client, _ = make_client(handler)

    with client, pytest.raises(OllamaConnectionError):
        client.chat(USER_MESSAGE)


def test_invalid_json_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    client, _ = make_client(handler)

    with client, pytest.raises(OllamaResponseError):
        client.chat(USER_MESSAGE)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": "not-a-dict"},
        {"message": {}},
        {"message": {"content": 42}},
    ],
)
def test_malformed_payloads_raise_response_error(payload: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client, _ = make_client(handler)

    with client, pytest.raises(OllamaResponseError):
        client.chat(USER_MESSAGE)
