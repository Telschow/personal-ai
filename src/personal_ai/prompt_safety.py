"""Rendering of untrusted text inside labeled prompt blocks.

Memory statements, people names and e-mail addresses are untrusted data. They
are placed between ``<..._context untrusted="true">`` tags, so a value must
never be able to close that block or start a new line that reads as prompt
text. :func:`inline_untrusted` keeps each value on one line and escapes the
angle brackets, which is enough because the blocks are line based.
"""

from __future__ import annotations

_ESCAPES = str.maketrans({"<": "&lt;", ">": "&gt;", "\r": " ", "\n": " "})


def inline_untrusted(text: str) -> str:
    """Return ``text`` on one line with ``<`` and ``>`` escaped."""
    return " ".join(text.translate(_ESCAPES).split())
