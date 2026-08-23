"""Minimal typed client for a local Ollama server."""

from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import httpx

DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaError(Exception):
    """Base class for all errors raised by :class:`OllamaClient`."""


class OllamaConnectionError(OllamaError):
    """Raised when the Ollama server cannot be reached."""


class OllamaHTTPStatusError(OllamaError):
    """Raised when Ollama responds with an HTTP error status."""

    def __init__(self, status_code: int, detail: str = "") -> None:
        message = f"Ollama returned HTTP {status_code}"
        if detail:
            message += f": {detail}"
        super().__init__(message)
        self.status_code = status_code


class OllamaResponseError(OllamaError):
    """Raised when Ollama returns a response that cannot be parsed."""


def _parse_tool_calls(message: dict[str, Any]) -> tuple[ToolCall, ...]:
    raw_calls = message.get("tool_calls")
    if raw_calls is None:
        return ()
    if not isinstance(raw_calls, list):
        msg = f"Ollama returned malformed tool_calls: {raw_calls!r}"
        raise OllamaResponseError(msg)

    return tuple(
        _parse_tool_call(raw_call, index) for index, raw_call in enumerate(raw_calls)
    )


def _parse_tool_call(raw_call: Any, position: int) -> ToolCall:
    function = raw_call.get("function") if isinstance(raw_call, dict) else None
    call_id = raw_call.get("id") if isinstance(raw_call, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    arguments = function.get("arguments") if isinstance(function, dict) else None

    if (
        not isinstance(call_id, str)
        or not call_id
        or not isinstance(name, str)
        or not name
        or not isinstance(arguments, dict)
    ):
        msg = (
            "Ollama returned a malformed tool call "
            f"at position {position}: {raw_call!r}"
        )
        raise OllamaResponseError(msg)

    return ToolCall(id=call_id, name=name, arguments=arguments)


def _serialize_message(message: ChatMessage) -> dict[str, Any]:
    serialized: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        serialized["tool_calls"] = [
            {
                "function": {
                    "name": call.name,
                    "arguments": call.arguments,
                }
            }
            for call in message.tool_calls
        ]
    return serialized


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A single tool call requested natively by the model."""

    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A single message in an Ollama conversation."""

    role: str
    content: str
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    model: str
    done: bool
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class EmbedResponse:
    """Raw embedding rows echoed by Ollama, one per input.

    Rows are kept as plain component tuples; semantic vector validation
    belongs to the embedding contract, not to transport parsing.
    """

    model: str
    embeddings: tuple[tuple[object, ...], ...]


class OllamaClient:
    """Small synchronous client for Ollama's ``/api/chat`` and ``/api/embed``."""

    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
        )

    def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, object]] | None = None,
    ) -> ChatResponse:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [_serialize_message(m) for m in messages],
            "stream": False,
        }
        if tools is not None:
            payload["tools"] = list(tools)
        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.TransportError as exc:
            msg = f"Could not reach Ollama at {self.base_url}: {exc}"
            raise OllamaConnectionError(msg) from exc

        if response.is_error:
            raise OllamaHTTPStatusError(response.status_code, response.text[:500])

        return self._parse_chat_response(response)

    def embed(self, text: str) -> EmbedResponse:
        """Embed one piece of text via Ollama's ``/api/embed`` endpoint."""
        try:
            response = self._client.post(
                "/api/embed", json={"model": self.model, "input": text}
            )
        except httpx.TransportError as exc:
            msg = f"Could not reach Ollama at {self.base_url}: {exc}"
            raise OllamaConnectionError(msg) from exc

        if response.is_error:
            raise OllamaHTTPStatusError(response.status_code, response.text[:500])

        return self._parse_embed_response(response)

    def _parse_embed_response(self, response: httpx.Response) -> EmbedResponse:
        try:
            data = response.json()
        except ValueError as exc:
            msg = f"Ollama returned invalid JSON: {response.text[:200]}"
            raise OllamaResponseError(msg) from exc

        malformed = (
            not isinstance(data, dict)
            or not isinstance(raw_rows := data.get("embeddings"), list)
            or not all(isinstance(row, list) for row in raw_rows)
        )
        if malformed:
            msg = (
                "Unexpected embedding response structure from Ollama: "
                f"{response.text[:200]}"
            )
            raise OllamaResponseError(msg)

        return EmbedResponse(
            model=str(data.get("model", self.model)),
            embeddings=tuple(tuple(row) for row in raw_rows),
        )

    def _parse_chat_response(self, response: httpx.Response) -> ChatResponse:
        try:
            data = response.json()
        except ValueError as exc:
            msg = f"Ollama returned invalid JSON: {response.text[:200]}"
            raise OllamaResponseError(msg) from exc

        malformed = (
            not isinstance(data, dict)
            or not isinstance(message := data.get("message"), dict)
            or not isinstance(content := message.get("content"), str)
        )
        if malformed:
            msg = f"Unexpected response structure from Ollama: {response.text[:200]}"
            raise OllamaResponseError(msg)

        return ChatResponse(
            content=content,
            model=str(data.get("model", self.model)),
            done=bool(data.get("done", False)),
            tool_calls=_parse_tool_calls(message),
        )

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
