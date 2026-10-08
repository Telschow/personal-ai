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
    ToolCall,
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


def tool_call_payload(**overrides: Any) -> dict[str, Any]:
    call = {
        "id": "call_gvumtiaj",
        "function": {
            "index": 0,
            "name": "list_directory",
            "arguments": {"path": "/tmp"},
        },
    }
    call.update(overrides)
    payload = chat_payload("")
    payload["message"]["tool_calls"] = [call]
    return payload


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


def test_chat_parses_single_tool_call() -> None:
    client, _ = make_client(
        lambda request: httpx.Response(200, json=tool_call_payload())
    )

    with client:
        result = client.chat(USER_MESSAGE)

    assert result.content == ""
    assert result.tool_calls == (
        ToolCall(
            id="call_gvumtiaj",
            name="list_directory",
            arguments={"path": "/tmp"},
        ),
    )


def test_chat_parses_multiple_tool_calls() -> None:
    payload = tool_call_payload()
    payload["message"]["tool_calls"] = [
        {
            "id": "call_one",
            "function": {"index": 0, "name": "list_directory", "arguments": {}},
        },
        {
            "id": "call_two",
            "function": {"index": 1, "name": "read_file", "arguments": {}},
        },
    ]
    client, _ = make_client(lambda request: httpx.Response(200, json=payload))

    with client:
        result = client.chat(USER_MESSAGE)

    assert result.tool_calls == (
        ToolCall(id="call_one", name="list_directory", arguments={}),
        ToolCall(id="call_two", name="read_file", arguments={}),
    )


def test_chat_without_tool_calls_returns_empty_tuple() -> None:
    client, _ = make_client(
        lambda request: httpx.Response(200, json=chat_payload("plain answer"))
    )

    with client:
        result = client.chat(USER_MESSAGE)

    assert result == ChatResponse(content="plain answer", model="test-model", done=True)
    assert result.tool_calls == ()


@pytest.mark.parametrize(
    "tool_calls",
    [
        pytest.param("not-a-list", id="tool_calls-not-a-list"),
        pytest.param(["not-a-dict"], id="entry-not-an-object"),
        pytest.param([{"function": {"name": "f", "arguments": {}}}], id="missing-id"),
        pytest.param(
            [{"id": 7, "function": {"name": "f", "arguments": {}}}], id="non-string-id"
        ),
        pytest.param(
            [{"id": "", "function": {"name": "f", "arguments": {}}}], id="empty-id"
        ),
        pytest.param([{"id": "c"}], id="missing-function"),
        pytest.param([{"id": "c", "function": {"arguments": {}}}], id="missing-name"),
        pytest.param(
            [{"id": "c", "function": {"name": 42, "arguments": {}}}],
            id="non-string-name",
        ),
        pytest.param([{"id": "c", "function": {"name": "f"}}], id="missing-arguments"),
        pytest.param(
            [{"id": "c", "function": {"name": "f", "arguments": "{}"}}],
            id="arguments-not-an-object",
        ),
    ],
)
def test_malformed_tool_call_raises_response_error(tool_calls: Any) -> None:
    payload = chat_payload("")
    payload["message"]["tool_calls"] = tool_calls

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client, _ = make_client(handler)

    with client, pytest.raises(OllamaResponseError):
        client.chat(USER_MESSAGE)


def test_default_timeout_is_180_seconds() -> None:
    client, _ = make_client(lambda request: httpx.Response(200, json=chat_payload()))
    assert client._client.timeout.connect == 180.0


def test_custom_timeout_is_passed_through() -> None:
    client, _ = make_client(
        lambda request: httpx.Response(200, json=chat_payload()),
        timeout=300.0,
    )
    assert client._client.timeout.connect == 300.0


def test_think_false_is_forwarded_in_payload() -> None:
    client, requests = make_client(
        lambda request: httpx.Response(200, json=chat_payload())
    )

    with client:
        client.chat(USER_MESSAGE, think=False)

    body = json.loads(requests[0].content)
    assert body["think"] is False


def test_think_true_is_forwarded_in_payload() -> None:
    client, requests = make_client(
        lambda request: httpx.Response(200, json=chat_payload())
    )

    with client:
        client.chat(USER_MESSAGE, think=True)

    body = json.loads(requests[0].content)
    assert body["think"] is True


def test_format_is_forwarded_in_payload() -> None:
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    client, requests = make_client(
        lambda request: httpx.Response(200, json=chat_payload())
    )

    with client:
        client.chat(USER_MESSAGE, format=schema)

    body = json.loads(requests[0].content)
    assert body["format"] == schema


def test_think_and_format_absent_when_not_provided() -> None:
    client, requests = make_client(
        lambda request: httpx.Response(200, json=chat_payload())
    )

    with client:
        client.chat(USER_MESSAGE)

    body = json.loads(requests[0].content)
    assert "think" not in body
    assert "format" not in body


def test_images_serialized_into_request_payload() -> None:
    message = ChatMessage(
        role="user",
        content="read this page",
        images=("<base64-alpha>", "<base64-beta>"),
    )
    client, requests = make_client(
        lambda request: httpx.Response(200, json=chat_payload())
    )

    with client:
        client.chat([message])

    body = json.loads(requests[0].content)
    assert body["messages"] == [
        {
            "role": "user",
            "content": "read this page",
            "images": ["<base64-alpha>", "<base64-beta>"],
        }
    ]


def test_image_payload_is_forwarded_verbatim() -> None:
    encoded = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    message = ChatMessage(role="user", content="", images=(encoded,))
    client, requests = make_client(
        lambda request: httpx.Response(200, json=chat_payload("description"))
    )

    with client:
        result = client.chat([message])

    body = json.loads(requests[0].content)
    assert body["messages"][0]["images"] == [encoded]
    assert result.content == "description"


def test_messages_without_images_never_include_images_key() -> None:
    client, requests = make_client(
        lambda request: httpx.Response(200, json=chat_payload())
    )

    with client:
        client.chat(USER_MESSAGE)

    body = json.loads(requests[0].content)
    assert "images" not in body["messages"][0]
    assert body["messages"][0] == {"role": "user", "content": "hi"}


def test_images_and_tool_calls_serialize_independently() -> None:
    message = ChatMessage(
        role="assistant",
        content="",
        tool_calls=(
            ToolCall(id="c1", name="list_directory", arguments={"path": "/tmp"}),
        ),
        images=("<base64>",),
    )
    client, requests = make_client(
        lambda request: httpx.Response(200, json=tool_call_payload())
    )

    with client:
        client.chat([message])

    body = json.loads(requests[0].content)
    assert body["messages"][0]["images"] == ["<base64>"]
    assert body["messages"][0]["tool_calls"][0]["function"]["name"] == "list_directory"
