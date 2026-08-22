"""Minimal typed client for a local Ollama server."""

from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Self

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


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    model: str
    done: bool


class OllamaClient:
    """Small synchronous client for Ollama's ``/api/chat`` endpoint."""

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

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }
        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.TransportError as exc:
            msg = f"Could not reach Ollama at {self.base_url}: {exc}"
            raise OllamaConnectionError(msg) from exc

        if response.is_error:
            raise OllamaHTTPStatusError(response.status_code, response.text[:500])

        return self._parse_chat_response(response)

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
