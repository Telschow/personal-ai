"""Rasterize the delivered demo SVG into a 1920x1080 PNG snapshot.

The archify viewer is a standalone HTML artifact, but the demonstration also
ships a still image so the architecture is readable in places that do not run
JavaScript (a README, a slide, a chat client that renders images).

This script extracts the single inline ``<svg>`` from the delivered HTML and
rasterizes it through librsvg and cairo over ``ctypes``. That keeps the capture
browser-free and reproducible: no headless Chrome, no network, and no extra
Python dependency.

Requirements are system libraries only:

* ``librsvg-2.so.2``
* ``libcairo.so.2``

If either library is missing the script exits with a clear message and writes
nothing. The PNG is a convenience artifact: the tests do not depend on it, and
``demo.html`` remains the authoritative rendering.

Usage::

    uv run python scripts/render_demo_snapshot.py
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import re
import sys
from pathlib import Path

#: Output composition. The task's target canvas.
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080

#: Cairo pixel format used for the output surface.
CAIRO_FORMAT_ARGB32 = 0

#: Matches the single inline diagram the archify renderer emits.
_SVG_PATTERN = re.compile(r"<svg\b.*?</svg>", re.DOTALL)
_VIEW_BOX_PATTERN = re.compile(r'viewBox\s*=\s*"([^"]+)"')


class RenderUnavailable(RuntimeError):
    """Raised when a required system rasterization library is absent."""


def extract_svg(html: str) -> str:
    """Return the single inline ``<svg>`` element from delivered archify HTML,
    with embedded CSS and theme attributes for rasterization."""
    match = _SVG_PATTERN.search(html)
    if match is None:
        raise RenderUnavailable("no inline <svg> found in the delivered HTML")
    svg = match.group(0)

    # Extract theme from <html> tag
    theme_match = re.search(r'<html[^>]*data-theme="([^"]+)"', html)
    theme = theme_match.group(1) if theme_match else "dark"

    # Add data-theme to SVG if missing
    def _add_data_theme(svg_text: str) -> str:
        tag_match = re.search(r"<svg\b[^>]*>", svg_text)
        if not tag_match:
            return svg_text
        tag = tag_match.group(0)
        if "data-theme=" not in tag:
            # Insert data-theme before closing >
            new_tag = tag[:-1] + f' data-theme="{theme}">'
            svg_text = svg_text.replace(tag, new_tag, 1)
        return svg_text

    svg = _add_data_theme(svg)

    # Extract and embed CSS styles from the HTML
    style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.DOTALL)
    if style_blocks:
        css = "\n".join(style_blocks)

        # Inline CSS custom properties (CSS variables) for librsvg compatibility
        # librsvg 2.x does not support CSS custom properties, so we need to replace
        # var(--variable-name) with actual values from the theme rules
        theme_selector = f'[data-theme="{theme}"]'
        theme_vars_pattern = re.compile(
            rf"{re.escape(theme_selector)}\s*\{{([^}}]+)\}}", re.DOTALL
        )
        theme_match = theme_vars_pattern.search(css)
        if theme_match:
            vars_block = theme_match.group(1)
            # Extract all CSS variable definitions
            var_defs = re.findall(r"--([\w-]+)\s*:\s*([^;]+);", vars_block)
            var_map = {f"--{name.strip()}": value.strip() for name, value in var_defs}

            # Also look for nested theme rules
            root_vars_pattern = re.compile(r":root\s*\{([^}]+)\}", re.DOTALL)
            root_match = root_vars_pattern.search(css)
            if root_match:
                root_vars_block = root_match.group(1)
                root_vars = re.findall(r"--([\w-]+)\s*:\s*([^;]+);", root_vars_block)
                for name, value in root_vars:
                    var_map[f"--{name.strip()}"] = value.strip()

            # Replace var() references with actual values
            def replace_var_fn(match):
                var_name = match.group(1).strip()
                return var_map.get(var_name, match.group(0))

            # Handle simple var() references
            css = re.sub(r"var\(\s*(--[\w-]+)\s*\)", replace_var_fn, css)

        # Insert a <style> element after the opening <svg> tag
        def _insert_style(svg_text: str) -> str:
            tag_match = re.search(r"<svg\b[^>]*>", svg_text)
            if not tag_match:
                return svg_text
            opening = tag_match.group(0)
            # Wrap CSS in CDATA to avoid XML escaping issues
            style_tag = f'\n<style type="text/css"><![CDATA[\n{css}\n]]></style>\n'
            # Insert after opening tag
            new_svg = svg_text.replace(opening, opening + style_tag, 1)
            return new_svg

        svg = _insert_style(svg)

    return svg


def parse_view_box(svg: str) -> tuple[float, float]:
    """Return the ``(width, height)`` of the SVG viewBox."""
    match = _VIEW_BOX_PATTERN.search(svg[:2000])
    if match is None:
        raise RenderUnavailable("the inline <svg> has no viewBox")
    parts = match.group(1).split()
    if len(parts) != 4:
        raise RenderUnavailable(f"unsupported viewBox: {match.group(1)!r}")
    return float(parts[2]), float(parts[3])


class _RsvgRect(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_double),
        ("y", ctypes.c_double),
        ("width", ctypes.c_double),
        ("height", ctypes.c_double),
    ]


def _load_libraries() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    """Load librsvg and cairo, or explain precisely what is missing."""
    system = sys.platform
    if system == "darwin":
        # Try to find via ctypes.util, fallback to Homebrew path
        rsvg_path = ctypes.util.find_library("rsvg-2")
        if rsvg_path is None:
            rsvg_path = "/opt/homebrew/lib/librsvg-2.dylib"
        cairo_path = ctypes.util.find_library("cairo")
        if cairo_path is None:
            cairo_path = "/opt/homebrew/lib/libcairo.dylib"
    else:
        # Linux and other Unix-like
        rsvg_path = ctypes.util.find_library("rsvg-2") or "librsvg-2.so.2"
        cairo_path = ctypes.util.find_library("cairo") or "libcairo.so.2"
    try:
        rsvg = ctypes.CDLL(rsvg_path)
        cairo = ctypes.CDLL(cairo_path)
    except OSError as error:
        raise RenderUnavailable(
            f"missing system rasterization library ({error}). "
            "Install librsvg and cairo, or skip the optional snapshot."
        ) from error
    return rsvg, cairo


def render_png(
    svg: str,
    output: Path,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Path:
    """Rasterize ``svg`` into ``output``, letterboxed into ``width`` x ``height``.

    The diagram keeps its authored aspect ratio and is centred, so no geometry
    is ever stretched or cropped.
    """
    rsvg, cairo = _load_libraries()
    svg_width, svg_height = parse_view_box(svg)

    rsvg.rsvg_handle_new_from_data.restype = ctypes.c_void_p
    rsvg.rsvg_handle_new_from_data.argtypes = [
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
    ]
    rsvg.rsvg_handle_render_document.restype = ctypes.c_int
    rsvg.rsvg_handle_render_document.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_RsvgRect),
        ctypes.c_void_p,
    ]
    cairo.cairo_image_surface_create.restype = ctypes.c_void_p
    cairo.cairo_image_surface_create.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    cairo.cairo_create.restype = ctypes.c_void_p
    cairo.cairo_create.argtypes = [ctypes.c_void_p]
    cairo.cairo_surface_write_to_png.restype = ctypes.c_int
    cairo.cairo_surface_write_to_png.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    cairo.cairo_destroy.argtypes = [ctypes.c_void_p]
    cairo.cairo_surface_destroy.argtypes = [ctypes.c_void_p]

    payload = svg.encode("utf-8")
    handle = rsvg.rsvg_handle_new_from_data(payload, len(payload), None)
    if not handle:
        raise RenderUnavailable("librsvg could not parse the inline SVG")

    # Contain the diagram inside the canvas, preserving its aspect ratio.
    scale = min(width / svg_width, height / svg_height)
    draw_width = svg_width * scale
    draw_height = svg_height * scale
    offset_x = (width - draw_width) / 2.0
    offset_y = (height - draw_height) / 2.0

    surface = cairo.cairo_image_surface_create(CAIRO_FORMAT_ARGB32, width, height)
    context = cairo.cairo_create(surface)

    # Opaque white page background so the PNG is readable in any viewer.
    cairo.cairo_set_source_rgb.argtypes = [ctypes.c_void_p] + [
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
    ]
    cairo.cairo_set_source_rgb(context, 1.0, 1.0, 1.0)
    cairo.cairo_paint.argtypes = [ctypes.c_void_p]
    cairo.cairo_paint(context)

    viewport = _RsvgRect(offset_x, offset_y, draw_width, draw_height)
    ok = rsvg.rsvg_handle_render_document(handle, context, ctypes.byref(viewport), None)
    if not ok:
        raise RenderUnavailable("librsvg failed to render the inline SVG")

    output.parent.mkdir(parents=True, exist_ok=True)
    status = cairo.cairo_surface_write_to_png(surface, str(output).encode())
    cairo.cairo_destroy(context)
    cairo.cairo_surface_destroy(surface)
    if status != 0:
        raise RenderUnavailable(f"cairo could not write {output}")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="render-demo-snapshot",
        description="Rasterize the demo diagram into a 1920x1080 PNG snapshot.",
    )
    parser.add_argument("--html", default="artifacts/demo/demo.html")
    parser.add_argument("--out", default="artifacts/demo/snapshot.png")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    args = parser.parse_args(argv)

    html_path = Path(args.html)
    if not html_path.exists():
        print(f"error: {html_path} not found. Deliver the HTML first.", file=sys.stderr)
        return 1

    try:
        written = render_png(
            extract_svg(html_path.read_text(encoding="utf-8")),
            Path(args.out),
            width=args.width,
            height=args.height,
        )
    except RenderUnavailable as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"wrote {written} ({args.width}x{args.height})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
