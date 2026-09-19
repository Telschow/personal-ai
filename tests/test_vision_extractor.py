"""Tests for page vision extraction: rendering, prompts, and the provider."""

import json

import httpx
import pymupdf
import pytest

from personal_ai.documents.vision import (
    VISION_PROMPT,
    VISION_PROMPT_VERSION,
    VISION_SYSTEM_PROMPT,
    VisionExtractionError,
    VisionExtractor,
    render_page,
)
from personal_ai.ollama_client import OllamaClient, OllamaHTTPStatusError
from personal_ai.ollama_vision import OllamaVisionExtractor


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Read width/height from a PNG IHDR chunk."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


_ALIVE_DOCUMENTS: list[object] = []


def make_page(width: float, height: float) -> object:
    """Build a renderable PyMuPDF page, keeping its document alive."""
    doc = pymupdf.open()
    page_pix = doc.new_page(width=width, height=height)
    _ALIVE_DOCUMENTS.append(doc)
    return page_pix


class TestRenderPage:
    def test_renders_valid_png_bytes(self) -> None:
        rendered = render_page(make_page(400, 300))

        width, height = png_dimensions(rendered)
        assert width > 0 and height > 0

    def test_oversized_page_is_capped_at_max_dimension(self) -> None:
        rendered = render_page(make_page(2000, 1000))

        width, height = png_dimensions(rendered)
        assert max(width, height) <= 1568
        assert max(width, height) >= 1560

    def test_aspect_ratio_is_preserved_when_capped(self) -> None:
        rendered = render_page(make_page(2000, 1000))

        width, height = png_dimensions(rendered)
        assert abs(width / height - 2.0) < 0.02

    def test_small_page_is_not_enlarged(self) -> None:
        rendered = render_page(make_page(200, 100))

        width, height = png_dimensions(rendered)
        # Natural 150-dpi render: 200pt = 417px, 100pt = 208px; never scaled up.
        assert max(width, height) == round(200 * 150 / 72)
        assert width < 1568

    def test_zero_sized_page_raises_controlled_error(self) -> None:
        class EmptyPage:
            rect = pymupdf.Rect(0, 0, 0, 0)

        with pytest.raises(VisionExtractionError):
            render_page(EmptyPage())

    def test_render_failure_surfaces_as_controlled_error(self) -> None:
        class BrokenPage:
            rect = pymupdf.Rect(0, 0, 100, 100)

            def get_pixmap(self, **kwargs: object) -> object:
                raise RuntimeError("boom")

        with pytest.raises(VisionExtractionError):
            render_page(BrokenPage())


class TestVisionContract:
    def _extractor(self, model: str = "vision-model") -> OllamaVisionExtractor:
        client = OllamaClient(
            model=model,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "message": {"role": "assistant", "content": "x"},
                        "done": True,
                    },
                )
            ),
        )
        return OllamaVisionExtractor(client)

    def test_ollama_extractor_satisfies_protocol(self) -> None:
        extractor = self._extractor()

        assert isinstance(extractor, VisionExtractor)
        assert extractor.model == "vision-model"
        assert extractor.prompt_version == VISION_PROMPT_VERSION

    def test_prompt_version_is_configurable(self) -> None:
        assert self._extractor().prompt_version == VISION_PROMPT_VERSION
        assert self._extractor().prompt_version == "v2"


class TestOllamaVisionExtractor:
    def make_extractor(
        self,
        handler,  # type: ignore[no-untyped-def]
        model: str = "vision-model",
    ) -> OllamaVisionExtractor:
        client = OllamaClient(model=model, transport=httpx.MockTransport(handler))
        return OllamaVisionExtractor(client)

    def test_extract_encodes_image_and_returns_content(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "message": {"role": "assistant", "content": "SKYLINE-42"},
                    "done": True,
                },
            )

        extractor = self.make_extractor(handler)

        result = extractor.extract(b"\x89PNG\x00\x00\x00raw image bytes")

        assert result == "SKYLINE-42"
        body = json.loads(requests[0].content)
        assert body["model"] == "vision-model"
        assert body["think"] is False
        system, user = body["messages"]
        assert system == {"role": "system", "content": VISION_SYSTEM_PROMPT}
        assert user["role"] == "user"
        assert user["content"] == VISION_PROMPT
        assert user["images"] == ["iVBORwAAAHJhdyBpbWFnZSBieXRlcw=="]

    def test_provider_errors_propagate_unchanged(self) -> None:
        extractor = self.make_extractor(
            lambda request: httpx.Response(500, json={"error": "boom"})
        )

        with pytest.raises(OllamaHTTPStatusError):
            extractor.extract(b"image bytes")

    def test_empty_model_output_returns_empty_string(self) -> None:
        extractor = self.make_extractor(
            lambda request: httpx.Response(
                200,
                json={
                    "message": {"role": "assistant", "content": "   \n  "},
                    "done": True,
                },
            )
        )
        assert extractor.extract(b"image bytes").strip() == ""
