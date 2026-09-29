"""Conservative name-anchored identity canonicalization (Phase 50A).

Deterministic, rule-based folding of display-name variants into a canonical
identity. This collapses mechanical artifact families and unambiguous
nickname spellings into a single stable key:

* artifact fixes — quote wrappers, leading title-case ``ñ``, comma/order
  swapping, salutation prefixes (``Herr``, ``Guten Tag``), parenthetical
  annotations (``(über TUM)``, ``(via Google Drive)``), room-code suffixes
  (``EF-703``), ``+``-joined surnames, and dot-joined ``alias.surname`` /
  ``surname.alias`` username forms;
* unambiguous nickname aliases — ``Daniel`` / ``Dani`` / ``Dan`` share the
  canonical given name ``daniel`` via ``GIVEN_NAME_ALIASES``.

It is deliberately NOT an email-anchored or structural merger: ``canonical_
identity`` never folds a display form that carries no name-anchored structure
(opaque usernames like ``dirk.telschow``, digits, address-shaped tokens) and
``PersonStore`` never folds by email alone — a shared mailbox alias on
unrelated rows is not a personhood signal.

The identity key is the canonical given name plus the lexicographically last
surname token. Picking the last surname (not the whole set) is what lets
compound-surname variants (``Alice Example Arjona``) and single-surname
forms (``Alice Example``) unify deterministically while distinct people
(``Dirk`` / ``Nicolas`` / ``WG``) stay separate by given name. This is an
acknowledged heuristic: do not widen it without adding cases to
``tests/test_people_canonicalize.py``.
"""

from __future__ import annotations

import re
import unicodedata

from personal_ai.people.models import normalize_identity

GIVEN_NAME_ALIASES: dict[str, str] = {
    "alice": "alice",
    "ali": "alice",
    "al": "alice",
}

_SALUTATION_TOKENS = frozenset(
    {
        "dear",
        "dr",
        "frau",
        "guten",
        "herr",
        "hi",
        "miss",
        "mr",
        "mrs",
        "ms",
        "prof",
        "tag",
    }
)

_PAREN_RE = re.compile(r"\([^)]*\)")
_DIGIT_RE = re.compile(r"\d")
_WS = re.compile(r"\s+")


def canonical_parts(name: str) -> tuple[str, frozenset[str]] | None:
    """Split a display form into ``(canonical given, frozenset surnames)``.

    Returns ``None`` for forms without name-anchored structure (empty/junk,
    opaque usernames, digit suffixes, address-shaped tokens): those keep
    their ``normalize_identity`` form unchanged via ``canonical_identity``.
    """
    folded = _fold_tokens(_tokens_for(_prepare(name)))
    if not folded:
        return None
    return _parts_for(folded)


def canonical_identity(name: str) -> str:
    """Return the canonical identity key for a display form.

    Foldable names map to ``given + primary surname``; everything else falls
    back to :func:`normalize_identity` unchanged, so identities that predate
    this layer never silently change.
    """
    parts = canonical_parts(name)
    if parts is None:
        return normalize_identity(name)
    given, surnames = parts
    if not surnames:
        return given
    return f"{given} {max(surnames)}"


def _prepare(raw: str) -> str:
    """Normalize artifact noise while never touching accents or letters."""
    text = raw.strip()
    text = text.strip("'\"")
    text = text.removeprefix("\u00f1")
    text = text.replace("+", " ")
    text = _PAREN_RE.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _tokens_for(text: str) -> list[str]:
    """Split on whitespace, dropping empty and digit-bearing comma segments."""
    segments: list[str] = []
    for segment in text.split(","):
        segment = segment.strip()
        if not segment or _DIGIT_RE.search(segment):
            continue
        segments.append(segment)
    if not segments:
        return []
    tokens: list[str] = []
    for segment in segments:
        tokens.extend(segment.split())
    return tokens


def _fold_tokens(tokens: list[str]) -> list[str]:
    return [unicodedata.normalize("NFKC", token).casefold() for token in tokens]


def _parts_for(folded: list[str]) -> tuple[str, frozenset[str]] | None:
    if len(folded) == 1 and "." in folded[0]:
        return _dot_username_parts(folded[0])
    while folded and folded[0] in _SALUTATION_TOKENS:
        folded.pop(0)
    if not folded:
        return None
    if len(folded) == 1:
        token = folded[0]
        if _DIGIT_RE.search(token):
            return None
        return token, frozenset()
    alias_at = [
        index for index, token in enumerate(folded) if token in GIVEN_NAME_ALIASES
    ]
    given_at = alias_at[0] if alias_at else 0
    given = (
        GIVEN_NAME_ALIASES[folded[given_at]]
        if folded[given_at] in GIVEN_NAME_ALIASES
        else folded[given_at]
    )
    surnames = frozenset(
        token
        for index, token in enumerate(folded)
        if index != given_at and not _DIGIT_RE.search(token) and "." not in token
    )
    return given, surnames


def _dot_username_parts(token: str) -> tuple[str, frozenset[str]] | None:
    """Fold ``alias.surname`` and ``surname.alias`` username forms only."""
    parts = token.split(".")
    if len(parts) != 2 or not all(parts):
        return None
    first, second = parts
    if first in GIVEN_NAME_ALIASES:
        return GIVEN_NAME_ALIASES[first], frozenset({second})
    if second in GIVEN_NAME_ALIASES:
        return GIVEN_NAME_ALIASES[second], frozenset({first})
    return None
