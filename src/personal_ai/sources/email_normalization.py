"""Deterministic, dependency-free email body normalization.

Pure helpers powering the email source adapter. Everything here is local
and reproducible: no network, no model, no external libraries. The rules
are intentionally conservative — when uncertainty remains the text is
kept, because deleting genuine content is worse than retaining quoted
material.
"""

from __future__ import annotations

import hashlib
import re

# Maximum number of non-empty lines allowed between a signature delimiter
# and the end of the body before the delimiter is ignored.
_MAX_SIGNATURE_LINES = 6

# A signature delimiter must appear within this distance of the end of the
# body; a standalone ``--`` in the middle of meaningful content is kept.
_MAX_SIGNATURE_TAIL_LINES = 12

# Reply separators introducing a quoted tail. Leading ``>``/space prefixes
# are tolerated so nested quotes are still recognized.
_REPLY_SEPARATORS = re.compile(
    r"^[> ]*(?:"
    r"On .+ wrote:$"
    r"|-+ ?Original Message ?-+$"
    r"|-+ ?Forwarded message ?-+$"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

_QUOTED_LINE = re.compile(r"^>\s?", re.MULTILINE)

_SIGNATURE_DELIMITER = re.compile(r"^-- ?$", re.MULTILINE)

_BLOCK_BOUNDARIES = re.compile(
    r"<(?:p|div|li|tr|h[1-6])\b[^>]*>|</(?:p|div|li|tr|h[1-6])\s*>|<br\s*/?>",
    re.IGNORECASE,
)

_SCRIPT_STYLE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)


def normalize_message_id(raw: str | None) -> str:
    """Normalize a ``Message-ID`` header into a deterministic key fragment.

    Surrounding ``<`` and ``>`` delimiters are removed, whitespace is folded
    to single spaces, and the domain (the part after the first ``@``) is
    lowercased. The local part is preserved verbatim: Message-ID local parts
    are not generally case-insensitive, so lowercasing them would risk
    collapsing genuinely distinct identifiers.
    """
    if not raw:
        return ""
    value = re.sub(r"\s+", " ", raw.strip()).strip("<>").strip()
    if "@" not in value:
        return value
    local, _, domain = value.partition("@")
    return f"{local}@{domain.lower()}"


def fallback_message_key(payload: bytes | str) -> str:
    """Deterministic source key base for messages lacking a Message-ID.

    The key is derived from the normalized composed payload, so identical
    missing-ID messages always share a fallback while different payloads
    normally do not.
    """
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return f"noid/{hashlib.sha1(data).hexdigest()}"


def _trailing_quoted_cut(lines: list[str]) -> int | None:
    """Return the index where a trailing ``>``-quoted block starts, if any.

    Only a suffix whose non-blank lines are all quoted lines counts, so a
    quoted block embedded in the middle of a body is never stripped.
    """
    quoted = []
    for line in lines:
        if not line.strip():
            quoted.append(False)
        else:
            quoted.append(bool(_QUOTED_LINE.match(line)))
    index = len(lines) - 1
    while index >= 0 and (not lines[index].strip() or quoted[index]):
        index -= 1
    if index == len(lines) - 1:
        return None
    return index + 1


def _separator_cut(lines: list[str]) -> int | None:
    """Return the index of the last reply separator line, if applicable.

    Conservative: a separator that opens the body (nothing meaningful before
    it) is preserved rather than used as a cut point.
    """
    matches = [
        index for index, line in enumerate(lines) if _REPLY_SEPARATORS.match(line)
    ]
    if not matches:
        return None
    cut = matches[-1]
    if not any(line.strip() for line in lines[:cut]):
        return None
    return cut


def strip_quoted_tail(text: str) -> str:
    """Remove a trailing replied-to or forwarded block from an email body.

    Recognizes conventional ``>`` quoted lines and the common reply
    separators (``On ... wrote:``, ``-----Original Message-----``,
    ``---------- Forwarded message ----------``). Only a trailing region is
    removed; quoted material embedded in the middle of a body is preserved.
    """
    if not text.strip():
        return text
    lines = text.splitlines()
    cut = _trailing_quoted_cut(lines)
    separator_cut = _separator_cut(lines)
    if separator_cut is not None:
        cut = separator_cut if cut is None else min(cut, separator_cut)
    if cut is None or cut == 0:
        return text
    return "\n".join(lines[:cut]).rstrip()


def strip_trailing_signature(text: str) -> str:
    """Remove a trailing signature introduced by an explicit ``--`` line.

    The delimiter must be the final one, close to the end of the body, and
    have at most :data:`_MAX_SIGNATURE_LINES` non-empty lines after it.
    Anything else — including a ``--`` in the middle of the body — is kept.
    """
    lines = text.splitlines()
    delimiters = [
        index for index, line in enumerate(lines) if _SIGNATURE_DELIMITER.match(line)
    ]
    if not delimiters:
        return text
    cut = delimiters[-1]
    if len(lines) - (cut + 1) > _MAX_SIGNATURE_TAIL_LINES:
        return text
    trailing_non_empty = sum(1 for line in lines[cut + 1 :] if line.strip())
    if trailing_non_empty > _MAX_SIGNATURE_LINES:
        return text
    return "\n".join(lines[:cut]).rstrip()


def html_text_with_breaks(html: str) -> str:
    """Convert semantic HTML block boundaries into newlines.

    Purely a pre-pass for the existing tag stripper: paragraph, list, table,
    and heading blocks plus ``<br>`` become line breaks so the resulting
    plain text keeps meaningful word blocks separated. Script and style
    contents are removed first so they never contribute stray lines.
    """
    text = _SCRIPT_STYLE.sub("", html)
    return _BLOCK_BOUNDARIES.sub("\n", text)


def normalize_email_body(text: str) -> str:
    """Deterministically normalize an extracted email body.

    Pipeline: normalize line endings, strip an explicit quoted tail, strip a
    conservative trailing signature, collapse runs of blank lines, trim.
    Meaningful internal whitespace and paragraph boundaries are preserved.
    """
    if not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = strip_quoted_tail(text)
    text = strip_trailing_signature(text)
    text = re.sub(r"\n[ \t\v\f\u00a0]*\n+", "\n\n", text)
    return text.strip()
