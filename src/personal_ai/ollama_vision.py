"""Ollama-backed implementation of page-level vision extraction."""

import base64

from personal_ai.documents.vision import (
    VISION_PROMPT,
    VISION_PROMPT_VERSION,
    VISION_SYSTEM_PROMPT,
)
from personal_ai.ollama_client import ChatMessage, OllamaClient


class OllamaVisionExtractor:
    """Vision extractor over :class:`OllamaClient`.

    Lives outside the pure document domain: it only translates between the
    chat primitive and the typed vision contract. The base64 encoding and
    the exact prompt version are fixed at construction, so cache identity
    (``model`` + ``prompt_version``) is stable. Provider errors from the
    client propagate unchanged; model output is returned verbatim so that
    empty or partial output stays distinguishable from malformed transport.
    """

    def __init__(
        self,
        client: OllamaClient,
        prompt_version: str = VISION_PROMPT_VERSION,
    ) -> None:
        self._client = client
        self.model = client.model
        self.prompt_version = prompt_version

    def extract(self, image: bytes) -> str:
        encoded = base64.b64encode(image).decode("ascii")
        response = self._client.chat(
            [
                ChatMessage(role="system", content=VISION_SYSTEM_PROMPT),
                ChatMessage(role="user", content=VISION_PROMPT, images=(encoded,)),
            ],
            think=False,
        )
        return response.content
