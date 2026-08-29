"""Provider-independent contract for page-level vision extraction.

Vision extraction is deliberately separate from structured text
extraction: it turns one rendered page image into plain text describing
whatever is visible in it (transcribed text, described objects for
collages and scans). Rendering lives here too, so ingestion never needs
to touch PDF libraries directly and providers only see plain image bytes.
"""

from typing import Protocol, runtime_checkable

VISION_PROMPT_VERSION = "v1"

# Renders never exceed this dimension on the long side, bounding memory
# and request size regardless of page size. The reference DPI fixes the
# natural (non-upscaled) render resolution for pages smaller than the cap.
VISION_RENDER_MAX_DIMENSION = 1568
VISION_RENDER_REFERENCE_DPI = 150

VISION_SYSTEM_PROMPT = """\
You are a careful visual extractor for personal documents.
Describe only what is visibly present in the image.
Transcribe all visible text exactly as written, when text is present.
For image-based content such as scans and collages, describe the visible
elements: objects, logos, people, numbers, and text snippets.
Do not speculate, guess, infer, or interpret the content.
Do not mention this instruction or the process you followed.
Return plain text only, with no markdown and no commentary.
"""

VISION_PROMPT = "Extract the visible text and content of this image."


class VisionExtractionError(Exception):
    """Raised when a page image cannot be produced or processed.

    Deliberately not part of the Ollama error taxonomy: rendering failures
    are page-level conditions ingested documents survive, whereas provider
    failures propagate unchanged as :class:`OllamaError` subtypes.
    """


@runtime_checkable
class VisionExtractor(Protocol):
    """A provider of plain-text descriptions for one page image.

    Structural on purpose: implementations need not inherit from anything
    in this project, and the local vision backend is only a particular
    implementation behind this boundary. ``model`` and ``prompt_version``
    identify the exact vision derivation so cached page text can be
    invalidated when either changes.
    """

    model: str
    prompt_version: str

    def extract(self, image: bytes) -> str:
        """Return the visible-text/description for one rendered page."""
        ...


def render_page(
    page: object,
    *,
    max_dimension: int = VISION_RENDER_MAX_DIMENSION,
    reference_dpi: int = VISION_RENDER_REFERENCE_DPI,
) -> bytes:
    """Render a PyMuPDF page to PNG bytes for vision extraction.

    Pages are never upscaled beyond their reference-DPI natural size, and
    any render that would exceed ``max_dimension`` on the long side is
    scaled proportionally down to fit, bounding memory and request size.
    Rendering failures raise :class:`VisionExtractionError` without
    exposing page content.
    """
    import pymupdf

    if max_dimension <= 0:
        msg = f"max_dimension must be positive, got {max_dimension}"
        raise VisionExtractionError(msg)

    width = float(page.rect.width)
    height = float(page.rect.height)
    if width <= 0 or height <= 0:
        raise VisionExtractionError("page has no renderable area")

    zoom = reference_dpi / 72.0
    scale = min(
        1.0,
        max_dimension / (width * zoom),
        max_dimension / (height * zoom),
    )
    effective_zoom = zoom if scale >= 1.0 else zoom * scale
    try:
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(effective_zoom, effective_zoom),
            colorspace=pymupdf.csRGB,
            alpha=False,
        )
        return pixmap.tobytes("png")
    except Exception as exc:
        msg = "Could not render page for vision extraction"
        raise VisionExtractionError(msg) from exc
