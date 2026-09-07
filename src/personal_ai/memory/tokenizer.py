"""Shared Unicode lexical tokenization primitive (Phase 23).

This is the single normalization/tokenization path used by both memory
retrieval (``MemoryRetriever``) and reconciliation (``MemoryReconciler``) so
that the two layers can never drift into subtly different lexical results.

Normalization
-------------
``normalize_for_tokenize`` applies:

* Unicode NFKC (recomposes decomposed accents, folds compatibility forms)
* casefold (lowercases and brings ``ß`` to ``ss``, ``ẞ`` to ``ss``, …)
* removal of *variation selectors* (U+FE00-U+FE0F and U+E0100-U+E01EF)

Variation selectors are formatting-only marks appended to emoji/pictographs
(e.g. ``❤️``); they are not lexical marks, and a lone selector must not leak
as a token (as a plain ``\\p{M}`` class would produce ``('\\ufe0f',)`` for a
bare ``❤️``). Removing them means a bare emoji tokenizes to zero tokens,
while ``"Ich liebe München ❤️"`` still yields ``("ich", "liebe", "münchen")``.

Tokenization
------------
``tokenize`` matches contiguous runs of Unicode letters, marks, and numbers
(``[\\p{L}\\p{M}\\p{N}]+``) on the normalized form. This covers every
letter-bearing script (Latin with accents, Greek, Cyrillic, Arabic, Han,
Hiragana, Katakana, Hangul, …) as deterministic, exact-matchable tokens.

Deliberate limits (documented, not defects):

* **Lexical preservation != word segmentation.** A Han/Hiragana run without
  whitespace is kept as one contiguous token (``日本語`` -> ``("日本語",)``).
  No morphological segmentation (Jieba/MeCab/…) is performed; exact lexical
  matching is the contract.
* **Emoji/symbol/punctuation-only input is not lexical.** ``👍``, ``€€€``,
  ``!!!`` legitimately produce zero tokens. The phase goal is *zero
  unexpected zero-token lexical messages* — not zero zero-token messages.
* **No transliteration, and no accent stripping.** ``München`` != ``munchen``,
  ``España`` != ``espana``, ``café`` != ``cafe``; casefold/NFKC already
  defined mappings (``Straße`` -> ``strasse``) are preserved exactly as they
  were before this module existed.

Security is intentionally a *separate* path: secret/PII classification uses
raw-text structural detectors and multilingual lexical keywords via
``MemoryPolicy``; tokenization is never the security authority.
"""

from __future__ import annotations

import re
import unicodedata

import regex

# Variation selectors: formatting-only marks (VS1-16 + supplementary VS240-256).
_VARIATION_SELECTOR_RE = re.compile("[\ufe00-\ufe0f\U000e0100-\U000e01ef]")

# Unicode-aware lexical tokenization: Unicode letters, marks, and numbers.
LETTER_MARK_NUMBER_RE = regex.compile(r"[\p{L}\p{M}\p{N}]+", regex.UNICODE)


def strip_variation_selectors(text: str) -> str:
    """Remove formatting-only variation selectors from ``text``."""
    return _VARIATION_SELECTOR_RE.sub("", text)


def normalize_for_tokenize(text: str) -> str:
    """Normalize text to its lexical form: NFKC + casefold + VS removal.

    Never transliterates, never strips accents, never translates.
    """
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return strip_variation_selectors(normalized)


def tokenize(text: str) -> tuple[str, ...]:
    """Split ``text`` into deterministic Unicode lexical tokens."""
    return tuple(LETTER_MARK_NUMBER_RE.findall(normalize_for_tokenize(text)))


def normalize_text(text: str) -> str:
    """Normalize for equality/overlap matching: a token-join of the tokens."""
    return " ".join(LETTER_MARK_NUMBER_RE.findall(normalize_for_tokenize(text)))
