"""Phase 23: Unicode lexical tokenization closure tests.

White-box coverage for the shared Unicode tokenization primitive
(``personal_ai.memory.tokenizer``), used consistently by retrieval
(``MemoryRetriever.tokenize``) and reconciliation (``normalize_text``):

* Latin-script Unicode (DE/ES accented text) tokenizes deterministically
  without ASCII corruption and without accent stripping;
* combining sequences normalize stably (NFKC);
* CJK / non-Latin scripts never collapse to zero tokens merely because they
  are non-ASCII (lexical preservation != word segmentation);
* emoji/symbol/punctuation-only input legitimately produces zero tokens, but
  emoji never destroy neighboring lexical tokens and the formatting-only
  variation selector never leaks as a junk token (the Phase 22 defect);
* reconciliation/retrieval share one normalization so Unicode overlaps match
  and cross-language semantics are never silently merged;
* tokenization is never the security authority (secret -> hard reject stays
  intact regardless of tokenization behavior).

Hermetic: no network, no Ollama, no production data.
"""

from __future__ import annotations

import sqlite3

import pytest

from personal_ai.memory.models import (
    MemoryCandidate,
    MemoryEvidenceRef,
)
from personal_ai.memory.policy import MemoryDecision, MemoryPolicy, Sensitivity
from personal_ai.memory.reconcile import (
    normalize_text,
    token_overlap,
)
from personal_ai.memory.retriever import (
    MemoryRetriever,
    tokenize,
)
from personal_ai.memory.store import MemoryStore
from personal_ai.memory.tokenizer import normalize_for_tokenize

BASE_NOW = "2026-08-30T00:00:00+00:00"


# --------------------------------------------------------------------------
# Test tokenize: Latin-script Unicode (section 7)
# --------------------------------------------------------------------------


def test_german_lexical_tokens() -> None:
    assert tokenize("München") == ("münchen",)
    assert tokenize("MÜNCHEN") == ("münchen",)
    assert tokenize("Österreich") == ("österreich",)
    assert tokenize("Grüße") == ("grüsse",)
    assert tokenize("täglich") == ("täglich",)
    assert tokenize("Straße") == ("strasse",)
    assert tokenize("größer") == ("grösser",)


def test_spanish_lexical_tokens() -> None:
    assert tokenize("España") == ("españa",)
    assert tokenize("ESPAÑA") == ("españa",)
    assert tokenize("mañana") == ("mañana",)
    assert tokenize("corazón") == ("corazón",)
    assert tokenize("dirección") == ("dirección",)
    assert tokenize("diagnóstico") == ("diagnóstico",)
    assert tokenize("canción") == ("canción",)


def test_latin_accented_words_survive() -> None:
    assert tokenize("café") == ("café",)
    assert tokenize("déjà") == ("déjà",)
    assert tokenize("naïve") == ("naïve",)
    assert tokenize("Über") == ("über",)


def test_combining_sequences_normalize_stably() -> None:
    composed = tokenize("étude")
    decomposed = tokenize("e\u0301tude")
    assert decomposed == ("étude",)
    assert decomposed == composed
    assert normalize_for_tokenize("e\u0301tude") == "étude"
    assert normalize_for_tokenize("Mu\u0308nchen") == normalize_for_tokenize("München")


def test_tokenization_is_deterministic() -> None:
    text = "München, España y café!"
    assert tokenize(text) == tokenize(text)
    assert tokenize(text) == tuple(" ".join(tokenize(text)).split())


def test_no_ascii_transliteration_and_no_accent_stripping() -> None:
    assert tokenize("München") != tokenize("Munchen")
    assert tokenize("München") != tokenize("Munich")
    assert tokenize("España") != tokenize("Espana")
    assert tokenize("España") != tokenize("Spain")
    assert tokenize("café") != tokenize("cafe")
    assert tokenize("Straße") == tokenize("STRASSE")  # ß->ss is established casefold
    assert tokenize("Grüße") == ("grüsse",)
    assert tokenize("grüße") == ("grüsse",)


# --------------------------------------------------------------------------
# Test tokenize: CJK / non-Latin coverage (section 8-9)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("日本語", ("日本語",)),
        ("東京", ("東京",)),
        ("東京に行きたい", ("東京に行きたい",)),
        ("中文", ("中文",)),
        ("你好", ("你好",)),
        ("한국어", ("한국어",)),
        ("Αθήνα", ("αθήνα",)),
        ("Москва", ("москва",)),
        ("العربية", ("العربية",)),
        ("עברית", ("עברית",)),
    ],
)
def test_non_latin_lexical_text_never_zero_tokens(text: str, expected) -> None:
    tokens = tokenize(text)
    assert tokens, f"{text!r} must not produce zero lexical tokens"
    assert tokens == expected


def test_han_run_is_one_contiguous_token() -> None:
    """Deterministic exact matching, not morphological segmentation."""
    assert tokenize("東京") == ("東京",)
    assert tokenize("𝕂")  # ASCII-safe determinism, no crash


# --------------------------------------------------------------------------
# Test tokenize: emoji and symbol policy (section 10)
# --------------------------------------------------------------------------


def test_emoji_only_input_is_zero_tokens() -> None:
    assert tokenize("👍") == ()
    assert tokenize("❤️") == ()
    assert tokenize("😂😂😂") == ()
    assert tokenize("☕") == ()


def test_variation_selector_never_leaks_as_junk_token() -> None:
    # Phase 22 defect: '❤️' leaked a lone U+FE0F mark token. It must not.
    assert tokenize("❤️") == ()
    assert tokenize("España ❤️") == ("españa",)
    assert "️" not in tokenize("España ❤️")
    assert "�" not in "".join(tokenize("España ❤️❤️"))


def test_emoji_does_not_destroy_neighbor_lexical_tokens() -> None:
    assert tokenize("München 👍") == ("münchen",)
    assert tokenize("España ❤️") == ("españa",)
    assert tokenize("café ☕") == ("café",)
    assert tokenize("Ich liebe München ❤️") == ("ich", "liebe", "münchen")


def test_symbol_only_input_is_zero_tokens() -> None:
    assert tokenize("€€€") == ()
    assert tokenize("→→") == ()
    assert tokenize("♥") == ()


def test_punctuation_heavy_text_keeps_lexical_tokens() -> None:
    assert tokenize("!!! München !!!") == ("münchen",)
    assert tokenize("¿España?") == ("españa",)
    assert tokenize("„München“") == ("münchen",)
    assert tokenize("(café)") == ("café",)
    assert tokenize("München!!!") == ("münchen",)


# --------------------------------------------------------------------------
# Test tokenize: numbers (section 11)
# --------------------------------------------------------------------------


def test_numeric_tokens() -> None:
    assert tokenize("123") == ("123",)
    assert tokenize("2026") == ("2026",)
    assert tokenize("3.14") == ("3", "14")
    assert tokenize("１２３") == ("123",)  # fullwidth digits NFKC-fold to ASCII digits
    assert tokenize("٢٠٢٦") == ("٢٠٢٦",)  # Arabic-Indic digits preserved, non-empty


def test_fullwidth_letters_fold_to_ascii_tokens_never_zero() -> None:
    # NFKC/casefold preserve fullwidth/mathematical-alphanumeric lexical
    # material as ASCII tokens: never a zero-token gap, never an error.
    assert tokenize("ＦＵＬＬＷＩＤＴＨ") == ("fullwidth",)
    assert tokenize("ＴＥＳＴ ＣＡＦÉ") == ("test", "café")
    assert tokenize("𝐌𝐮̈𝐧𝐜𝐡𝐞𝐧") != ()
    assert tokenize("\u212a") != ()  # KELVIN SIGN -> 'k' under casefold, never dropped
    assert tokenize("①②③") == ("123",)  # enclosed numerals NFKC-fold to ASCII digits


# --------------------------------------------------------------------------
# Reconciliation consistency (sections 13-14, 22)
# --------------------------------------------------------------------------


def test_reconciliation_normalization_matches_tokenize() -> None:
    for text in (
        "München",
        "MÜNCHEN",
        "España",
        "ESPAÑA",
        "café",
        "„München“",
        "日本語",
        "Ich liebe München ❤️",
        "e\u0301tude",
    ):
        assert " ".join(tokenize(text)) == normalize_text(text), text


def test_exact_and_case_normalized_unicode_duplicates_match() -> None:
    assert normalize_text("München") == normalize_text("München")
    assert normalize_text("München") == normalize_text("MÜNCHEN")
    assert normalize_text("España") == normalize_text("ESPAÑA")


def test_punctuation_does_not_break_lexical_equality() -> None:
    assert normalize_text("„München“") == normalize_text("München")
    assert normalize_text("¡España!") == normalize_text("España")


def test_accent_distinction_is_preserved() -> None:
    assert normalize_text("café") != normalize_text("cafe")
    assert normalize_text("España") != normalize_text("Espana")


def test_no_semantic_cross_language_merge() -> None:
    # Lexical normalization must never equate different languages.
    assert normalize_text("München") != normalize_text("Munich")
    assert normalize_text("München") != normalize_text("Spanien")
    assert normalize_text("café") != normalize_text("coffee")
    unicode_tokens = tokenize("España München café")
    assert "españa" in unicode_tokens and "münchen" in unicode_tokens


def test_overlap_agrees_on_unicode_normalization() -> None:
    assert token_overlap(normalize_text("München ❤️"), normalize_text("MÜNCHEN")) == 1.0
    assert token_overlap(normalize_text("España"), normalize_text("Espana")) == 0.0


def test_retrieval_uses_same_normalization_as_matching() -> None:
    from personal_ai.memory.models import MemoryDraft as _Draft

    store = MemoryStore(sqlite3.connect(":memory:"))
    draft = _Draft(
        kind="preference",
        content="Der Nutzer liebt München und Café.",
        summary="",
        source_type="user",
        source_id="t",
        scope="global",
        scope_id=None,
        confidence=0.5,
        importance=0.5,
        expires_at=None,
    )
    store.save(draft.to_memory(BASE_NOW))
    retriever = MemoryRetriever(store, now=lambda: BASE_NOW)
    hits = retriever.search("MÜNCHEN ❤️")
    assert hits
    assert hits[0].relevance == 1.0
    assert hits[0].memory.content == "Der Nutzer liebt München und Café."


# --------------------------------------------------------------------------
# Security independence (section 12)
# --------------------------------------------------------------------------


def _evidence(**overrides) -> MemoryEvidenceRef:
    base = {
        "source_type": "user",
        "source_id": "doc-1",
        "source_document_id": None,
        "source_timestamp": "2026-01-05T10:00:00+00:00",
    }
    base.update(overrides)
    return MemoryEvidenceRef(**base)


def _candidate(**overrides) -> MemoryCandidate:
    base = {
        "statement": "The user prefers local-first tools.",
        "kind": "preference",
        "confidence": 0.9,
        "durability": 0.8,
        "relevance": 0.8,
        "specificity": 0.7,
        "recurrence": 1,
        "utility": 0.7,
        "temporal_scope": "current",
        "assertion_status": "asserted",
        "summary": "tooling",
        "evidence": (_evidence(),),
    }
    base.update(overrides)
    return MemoryCandidate(**base)


@pytest.mark.parametrize(
    "statement",
    [
        "Mein Passwort ist TESTVALUE123",
        "Mi contraseña es TESTVALUE123",
        "El usuario tiene una contraseña: TESTVALUE456",
        "Mein API-Schlüssel ist TESTKEY123",
        "Zugangstoken TESTKEY",
        "The user stores token sk_live_abcdefghijklmnopqrst",
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjg",
    ],
)
def test_secret_rejection_is_independent_of_tokenization(statement: str) -> None:
    """Secret -> hard reject even when the surrounding text tokenizes to non-ASCII."""
    decision = MemoryPolicy().evaluate(_candidate(statement=statement))
    assert decision.decision is MemoryDecision.REJECT, statement
    assert decision.sensitivity is Sensitivity.SECRET, statement


def test_emoji_surrounding_secret_does_not_change_policy() -> None:
    plain = _candidate(statement="The user's Passwort TESTVALUE123 is set.")
    emoji = _candidate(statement="The user's Passwort TESTVALUE123 ❤️👍 is set.")
    assert tokenize("❤️👍") == ()  # emoji tokenize to nothing: not the authority
    assert MemoryPolicy().evaluate(emoji) == MemoryPolicy().evaluate(plain)


def test_highly_sensitive_keeps_existing_escalation() -> None:
    """IBAN and medical text keep REQUIRE_APPROVAL escalation (not REJECT)."""
    for statement in (
        "The user account IBAN is DE89370400440532013000.",
        "Meine IBAN lautet DE89370400440532013000.",
        "El diagnóstico médico del usuario es privado.",
    ):
        decision = MemoryPolicy().evaluate(_candidate(statement=statement))
        assert decision.decision is MemoryDecision.REQUIRE_APPROVAL, statement
        assert decision.sensitivity is Sensitivity.HIGHLY_SENSITIVE, statement


def test_policy_does_not_consult_tokenizer() -> None:
    # The tokenizer output for secret keywords is irrelevant to classification:
    # policy searches the raw statement, not tokenize(statement).
    statement = "The user stores sk_live_abcdefghijklmnopqrst in the vault."
    decision = MemoryPolicy().evaluate(_candidate(statement=statement))
    assert decision.decision is MemoryDecision.REJECT
    assert decision.sensitivity is Sensitivity.SECRET
    # Sanity: the statement still contains useful lexical tokens (not all zero).
    assert tokenize(statement)


# --------------------------------------------------------------------------
# Audit categorization (section 15-16, aggregate contract)
# --------------------------------------------------------------------------


def test_zero_token_classifier_buckets() -> None:
    from personal_ai.memory.corpus_audit import _classify_zero_token

    assert _classify_zero_token("👍") == "emoji_only"
    assert _classify_zero_token("❤️") == "emoji_only"
    assert _classify_zero_token("😂😂😂") == "emoji_only"
    assert _classify_zero_token("👨\u200d👩\u200d👧") == "emoji_only"
    assert _classify_zero_token("€€€") == "symbol_only"
    assert _classify_zero_token("→→") == "symbol_only"
    assert _classify_zero_token("!!!") == "punctuation_only"
    assert _classify_zero_token("€$!") == "symbol_punctuation"
    assert _classify_zero_token("\u00a0") == "whitespace_only"


def test_audit_lists_non_lexical_zero_tokens_without_error() -> None:
    import json
    import tempfile
    from pathlib import Path

    from personal_ai.memory.corpus_audit import run_corpus_audit

    with tempfile.TemporaryDirectory(prefix="phase23-") as tmp:
        export = Path(tmp) / "gemini"
        export.mkdir()
        data = {
            "id": "c1",
            "title": "t",
            "messages": [
                {"role": "user", "content": "👍"},
                {"role": "user", "content": "München"},
            ],
            "messageCount": 2,
            "createdAt": "2026-01-01T00:00:00.000Z",
            "lastMessageAt": "2026-01-01T00:00:00.000Z",
        }
        (export / "20260101_Chat_000.json").write_text(json.dumps(data))
        report = run_corpus_audit("gemini", export, limit=1)
        assert report.unicode.zero_token_messages >= 1
        assert report.unicode.zero_token_by_category.get("emoji_only", 0) >= 1
        assert report.unicode.zero_token_by_category.get("lexical_unicode", 0) == 0
        # Emoji-only zero tokens are expected: not an error.
        assert report.unicode_errors == []
        assert "zero_token_by_category" in report.summary()
        # München keeps a non-ASCII lexical token.
        assert report.unicode.unicode_tokens >= 1
        # Fullwidth letters NFKC-fold to ASCII tokens: not zero-token, not an error.
        assert report.unicode.ascii_folded_messages == 0


def test_audit_counts_fullwidth_ascii_folded_messages() -> None:
    import json
    import tempfile
    from pathlib import Path

    from personal_ai.memory.corpus_audit import run_corpus_audit

    with tempfile.TemporaryDirectory(prefix="phase23-") as tmp:
        export = Path(tmp) / "gemini"
        export.mkdir()
        data = {
            "id": "c1",
            "title": "t",
            "messages": [
                {
                    "role": "user",
                    "content": "ＦＵＬＬＷＩＤＴＨ ＴＥＳＴ",
                },  # folds to ASCII
            ],
            "messageCount": 1,
            "createdAt": "2026-01-01T00:00:00.000Z",
            "lastMessageAt": "2026-01-01T00:00:00.000Z",
        }
        (export / "20260101_Chat_000.json").write_text(json.dumps(data))
        report = run_corpus_audit("gemini", export, limit=1)
        # Lexical material preserved as ASCII tokens: never a zero-token bucket.
        assert report.unicode.zero_token_messages == 0
        assert report.unicode.zero_token_by_category == {}
        assert report.unicode.ascii_folded_messages == 1
        assert report.unicode.tokens_total >= 1
        assert report.unicode_errors == []
