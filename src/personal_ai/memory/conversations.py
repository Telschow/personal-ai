"""Deterministic, bounded conversation memory extraction.

Conversation exports are ingested into the :class:`ConversationStore` as
first-class entities. This module derives durable *identity-tier* memory
candidates from the user-authored messages of those stored conversations and
routes every candidate through the same policy-gated write path as corpus
extraction (``AutomaticMemoryCurator`` -> ``propose_memory`` gate ->
``MemoryService.apply_candidate``). It performs no raw SQL and no direct
memory writes.

Extraction is deterministic and conservative — no LLM, no assistant claims,
no invented facts:

* only ``user``-role messages on the active branch are considered
  (assistant/system/tool claims are never user facts);
* only explicit first-person self-assertions can yield a candidate;
* negation, questions, requests directed at the model, quoted content, and
  sensitive material (email addresses, phone numbers, currency amounts,
  credential-shaped strings, credential keywords) are skipped;
* temporal scope is derived from the trigger: present-tense declarations are
  ``current``, past-tense ones ``historical``, and recurring-timeframe
  declarations ``recurring``. "I want to do X" never becomes "the user does
  X" — a plan is represented as a ``goal``, never an achieved fact.

Evidence is provenance-only: stable identifiers and ISO timestamps, never
conversation content. Repeated facts across conversations accumulate evidence
on a single memory through the existing reconciler, and re-running on
unchanged conversations is idempotent.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

import regex

from personal_ai.documents.conversations import (
    Conversation,
    ConversationMessage,
)
from personal_ai.memory.corpus import OutcomeTally
from personal_ai.memory.models import (
    AssertionStatus,
    MemoryCandidate,
    MemoryEvidenceRef,
    MemoryKind,
    TemporalScope,
)
from personal_ai.memory.policy import (
    _HIGHLY_SENSITIVE_KEYWORDS as _POLICY_HIGHLY_SENSITIVE_KEYWORDS,
)
from personal_ai.memory.policy import (
    _SENSITIVE_KEYWORDS as _POLICY_SENSITIVE_KEYWORDS,
)
from personal_ai.memory.policy import MemoryPolicy
from personal_ai.memory.reconcile import normalize_text
from personal_ai.storage.conversations import ConversationStore

# Bounds (deterministic, conservative).
MAX_STATEMENT_CHARS = 512
DEFAULT_MAX_CONVERSATIONS = 10_000
DEFAULT_MAX_MESSAGES_PER_CONVERSATION = 1_000
DEFAULT_MAX_CANDIDATES_PER_CONVERSATION = 10

# The evidence ``source_type`` strings this layer produces (recognized as
# ``conversation_statement`` provenance by ``MemoryPolicy``).
CONVERSATION_SOURCE_TYPES: frozenset[str] = frozenset({"chatgpt", "gemini"})


class ConversationLanguage(str, Enum):
    """Detected language of a conversation or assertion."""

    EN = "en"
    DE = "de"
    ES = "es"
    UNKNOWN = "unknown"


# Language-specific trigger words with high specificity.
# These are chosen to be unambiguous markers of each language,
# avoiding extremely common function words that appear in multiple languages.
_LANG_TRIGGERS = {
    ConversationLanguage.DE: frozenset(
        {
            "ich",
            "mein",
            "meine",
            "meinem",
            "meinen",
            "meines",
            "bin",
            "habe",
            "arbeite",
            "wohne",
            "lebe",
            "spreche",
            "kann",
            "möchte",
            "will",
            "ist",
            "war",
            "nicht",
            "kein",
            "keine",
            "keinen",
            "nie",
            "niemals",
            "weil",
            "dass",
            "wie",
            "was",
            "wo",
            "wann",
            "warum",
            "wer",
            "welche",
            "welcher",
            "mich",
            "mir",
            "dir",
            "wir",
            "uns",
            "euch",
            "täglich",
            "wöchentlich",
            "monatlich",
            "regelmäßig",
            "gestern",
            "heute",
            "morgen",
            "jetzt",
            "aktuell",
            "früher",
            "nächstes",
            "nächste",
            "nächsten",
            "letztes",
            "letzte",
            "jeden",
            "manchmal",
            "oft",
            "selten",
            "plane",
            "beabsichtige",
            "habe vor",
            "lerne",
            "studiere",
            "sprich",
            "mag",
            "liebe",
            "genieße",
            "bevorzuge",
            "interessiere",
            "dein",
            "sein",
            "ihr",
            "unser",
            "euer",
            "hallo",
            "welt",
            "hallo welt",
            "guten tag",
            "guten morgen",
            "guten abend",
            "guten",
            "tag",
            "abend",
            "danke",
            "bitte",
            "entschuldigung",
            "ja",
            "nein",
            "arbeitete",
            "wohnte",
            "lebte",
            "studierte",
            "sprach",
            "hatte",
            "joggte",
            "lief",
            "schwamm",
            "trainierte",
            "jogge",
            "laufe",
            "schwimme",
            "meditiere",
            "übe",
            "sei",
            "würde",
            "gehe",
            "komme",
            "schaue",
            "spiele",
            "höre",
            "wünsche",
        }
    ),
    ConversationLanguage.ES: frozenset(
        {
            "yo",
            "mi",
            "mis",
            "soy",
            "estoy",
            "tengo",
            "vivo",
            "trabajo",
            "estudio",
            "hablo",
            "puedo",
            "quiero",
            "me gustaría",
            "nunca",
            "jamás",
            "ningún",
            "ninguna",
            "nada",
            "porque",
            "cuándo",
            "por qué",
            "quién",
            "cuál",
            "cuáles",
            "diariamente",
            "semanalmente",
            "mensualmente",
            "regularmente",
            "ayer",
            "hoy",
            "mañana",
            "ahora",
            "actualmente",
            "antes",
            "próximo",
            "próxima",
            "próximos",
            "próximas",
            "pasado",
            "cada",
            "todos",
            "todas",
            "todo",
            "toda",
            "siempre",
            "planeo",
            "tengo la intención de",
            "aprendo",
            "me gusta",
            "me encanta",
            "prefiero",
            "me interesa",
            "hola",
            "mundo",
            "hola mundo",
            "buenos días",
            "buenas tardes",
            "buenas noches",
            "buenos",
            "días",
            "tardes",
            "noches",
            "gracias",
            "por favor",
            "disculpe",
            "sí",
            "no",
            "me llamo",
            "llamo",
            "me",
            "trabajé",
            "trabajaba",
            "vivía",
            "estudiaba",
            "hablaba",
            "era",
            "estaba",
            "tenía",
            "empecé",
            "comencé",
            "terminé",
            "acabé",
            "estudié",
            "llevo",
            "vengo",
            "vengo de",
            "nací",
            "suelo",
            "solía",
            "a diario",
            "todos los días",
            "cada día",
            "cada semana",
            "cada mes",
            "una vez",
            "dos veces",
            "tres veces",
        }
    ),
    ConversationLanguage.EN: frozenset(
        {
            "i",
            "my",
            "me",
            "am",
            "have",
            "work",
            "live",
            "speak",
            "can",
            "want",
            "will",
            "is",
            "was",
            "not",
            "never",
            "didn't",
            "don't",
            "won't",
            "can't",
            "because",
            "that",
            "how",
            "what",
            "where",
            "when",
            "why",
            "who",
            "which",
            "daily",
            "weekly",
            "monthly",
            "regularly",
            "yesterday",
            "today",
            "tomorrow",
            "now",
            "currently",
            "before",
            "next",
            "last",
            "every",
            "all",
            "sometimes",
            "often",
            "rarely",
            "hope",
            "plan",
            "intend",
            "aspire",
            "learn",
            "study",
            "like",
            "love",
            "enjoy",
            "prefer",
            "interested",
            "hello",
            "world",
            "hello world",
            "good morning",
            "good afternoon",
            "good evening",
            "thank you",
            "thanks",
            "please",
            "excuse me",
            "yes",
            "no",
        }
    ),
}


def _detect_language(text: str) -> ConversationLanguage:
    """Simple deterministic language detection based on trigger words.

    This is not a statistical language detector — it counts unambiguous
    language-specific trigger words (single words and common word pairs).
    If no language has a clear majority, returns UNKNOWN. The detector is
    intentionally conservative; callers fall back to English rules on
    UNKNOWN.
    """
    words = regex.findall(r"[\p{L}\p{M}]+", text.casefold(), flags=regex.UNICODE)
    if not words:
        return ConversationLanguage.UNKNOWN

    scored = {lang: 0 for lang in _LANG_TRIGGERS}
    matched_langs: set[ConversationLanguage] = set()
    ngrams: list[str] = list(words)
    ngrams += [f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)]
    for token in ngrams:
        for lang, triggers in _LANG_TRIGGERS.items():
            if token in triggers:
                scored[lang] += 1
                matched_langs.add(lang)

    best_score = 0
    for lang, score in scored.items():
        best_score = max(best_score, score)
    if best_score == 0:
        return ConversationLanguage.UNKNOWN

    # Prefer languages that actually matched words over the N-gram-free
    # competitor when scores tie on the leading language alone.
    leaders = [lang for lang, score in scored.items() if score == best_score]
    if len(leaders) == 1:
        return leaders[0]
    real_leaders = [lang for lang in leaders if lang in matched_langs]
    if len(real_leaders) == 1:
        return real_leaders[0]
    return ConversationLanguage.UNKNOWN


# --- Per-language pattern factories -----------------------------------------


# Spanish pro-drop first-person verb forms (curated, conservative).
# Spanish often omits the explicit subject pronoun ("yo"); the conjugated
# verb ending itself encodes person. These are first-person singular present
# and past (preterite/imperfect) forms relevant to the rule categories.
#
# Some forms are also common nouns ("trabajo" = job, "estudio" = studio,
# "juego" = game). The first-person gate accepts a pro-drop verb only when it
# is NOT immediately preceded by a determiner/possessive (which would mark a
# noun phrase, e.g. "el trabajo en Google"). Anything still ambiguous passes
# through the full policy pipeline.
_SPANISH_FIRST_PERSON_VERBS = frozenset(
    {
        # copula/auxiliary
        "soy",
        "estoy",
        "tengo",
        "puedo",
        "sé",
        "estuve",
        "fui",
        "era",
        # -ar present
        "trabajo",
        "hablo",
        "estudio",
        "aprendo",
        "entreno",
        "juego",
        "toco",
        "programo",
        "codifico",
        "camino",
        "corro",
        "nado",
        "medito",
        "practico",
        "cocino",
        "bailo",
        "canto",
        "pinto",
        "escribo",
        "viajo",
        "ayudo",
        "enseño",
        "uso",
        "necesito",
        "deseo",
        "espero",
        "planeo",
        "miro",
        "escucho",
        "busco",
        "encuentro",
        "llevo",
        "tomo",
        "compro",
        "visito",
        # -er/-ir present
        "vivo",
        "como",
        "bebo",
        "leo",
        "creo",
        "pienso",
        "siento",
        "quiero",
        "debo",
        "prefiero",
        "conozco",
        "recuerdo",
        "entiendo",
        "oigo",
        "veo",
        "traigo",
        "hago",
        "digo",
        "sigo",
        "salgo",
        "vengo",
        "pongo",
        "doy",
        "suelo",
        # past: preterite
        "trabajé",
        "hablé",
        "estudié",
        "aprendí",
        "entrené",
        "jugué",
        "viví",
        "crecí",
        "pensé",
        "sentí",
        "quise",
        "debí",
        "preferí",
        "pude",
        "conocí",
        "recordé",
        "entendí",
        "oí",
        "traje",
        "hice",
        "dije",
        "seguí",
        "salí",
        "vine",
        "puse",
        "di",
        "soñé",
        "asistí",
        "nací",
        # past: imperfect
        "trabajaba",
        "hablaba",
        "estudiaba",
        "aprendía",
        "entrenaba",
        "jugaba",
        "tocaba",
        "vivía",
        "comía",
        "bebía",
        "leía",
        "creía",
        "pensaba",
        "sentía",
        "quería",
        "debía",
        "prefería",
        "podía",
        "conocía",
        "recordaba",
        "entendía",
        "veía",
        "oía",
        "traía",
        "hacía",
        "decía",
        "seguía",
        "salía",
        "venía",
        "ponía",
        "daba",
        "soñaba",
        "solía",
        "asistía",
    }
)

# Spanish determiners/possessives that mark a following noun.
_SPANISH_DETERMINERS = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "mi",
        "mis",
        "tu",
        "tus",
        "su",
        "sus",
        "nuestro",
        "nuestra",
        "nuestros",
        "nuestras",
        "este",
        "esta",
        "estos",
        "estas",
        "ese",
        "esa",
        "esos",
        "esas",
        "aquel",
        "aquella",
        "aquellos",
        "aquellas",
    }
)


def _spanish_is_first_person(raw: str) -> bool:
    """First-person gate for Spanish, handling pro-drop verbs.

    Accepts an explicit first-person pronoun/possessive anywhere, or a
    pro-drop first-person verb form that is not used as a noun (i.e. not
    immediately preceded by a determiner/possessive).
    """
    if re.search(r"\b(?:yo|mi|mis|me)\b", raw, re.IGNORECASE):
        return True
    words = _tokenize_words(raw)
    for index, word in enumerate(words):
        if word in _SPANISH_FIRST_PERSON_VERBS and (
            index == 0 or words[index - 1] not in _SPANISH_DETERMINERS
        ):
            return True
    return False


def _tokenize_words(raw: str) -> tuple[str, ...]:
    return tuple(
        regex.findall(r"[\p{L}\p{M}\p{N}']+", raw.casefold(), flags=regex.UNICODE)
    )


def _is_first_person(raw: str, lang: ConversationLanguage) -> bool:
    """First-person gate for a detected language.

    English/German require an explicit first-person pronoun/possessive.
    Spanish additionally accepts pro-drop conjugated verbs (subject encoded
    in the verb ending) when not used as a noun phrase.
    """
    if lang == ConversationLanguage.ES:
        return _spanish_is_first_person(raw)
    pattern = _FIRST_PERSON_PATTERNS.get(
        lang, _FIRST_PERSON_PATTERNS[ConversationLanguage.EN]
    )
    return pattern.search(raw) is not None


def _build_first_person_pattern(lang: ConversationLanguage) -> re.Pattern[str]:
    """Build first-person detection pattern for a language."""
    if lang == ConversationLanguage.DE:
        return re.compile(
            r"(?:\bich\b|\bmein\b|\bmeine\b|\bmeinem\b|\bmeinen\b|\bmeines\b|\bmich\b|\bmir\b|"
            r"ich[''][mdv])",
            re.IGNORECASE,
        )
    if lang == ConversationLanguage.ES:
        return re.compile(
            r"(?:\byo\b|\bmi\b|\bmis\b|\bme\b|"
            r"yo[''][mdv])",
            re.IGNORECASE,
        )
    # English (default)
    return re.compile(r"(?:\bi\b|\bmy\b|\bme\b|i[''][mdv])", re.IGNORECASE)


def _build_negation_pattern(lang: ConversationLanguage) -> re.Pattern[str]:
    """Build negation detection pattern for a language."""
    if lang == ConversationLanguage.DE:
        return re.compile(
            r"\b(?:nicht|kein|keine|keinen|keinem|keines|nie|niemals|nichts|"
            r"nein|keineswegs|keinesfalls)\b",
            re.IGNORECASE,
        )
    if lang == ConversationLanguage.ES:
        return re.compile(
            r"\b(?:no|nunca|jamás|ningún|ninguna|ningunos|ningunas|nada|"
            r"tampoco|ni)\b",
            re.IGNORECASE,
        )
    # English (default)
    return re.compile(
        r"\b(?:no|not|never|hardly|scarcely|"
        r"don't|doesn't|didn't|isn't|aren't|wasn't|weren't|"
        r"can't|cannot|won't|wouldn't|shouldn't|couldn't|mustn't|"
        r"haven't|hasn't|hadn't|isn't)\b",
        re.IGNORECASE,
    )


def _build_request_pattern(lang: ConversationLanguage) -> re.Pattern[str]:
    """Build request/model-directed pattern for a language."""
    if lang == ConversationLanguage.DE:
        return re.compile(
            r"^(?:"
            r"kannst du|könntest du|wirst du|würdest du|bitte|hilf mir|gib mir|"
            r"erkläre mir|zeig mir|schreib|erfasse|beschreibe|zusammenfasse|"
            r"erstelle|generiere|entwerfe|überprüfe|fixe|konvertiere|übersetze|"
            r"finde|such|mach|baue|liste|definiere|empfehle|schlage vor|"
            r"vergleiche|analysiere"
            r")\b",
            re.IGNORECASE,
        )
    if lang == ConversationLanguage.ES:
        return re.compile(
            r"^(?:"
            r"puedes|podrías|harás|harías|por favor|ayúdame|dame|dime|"
            r"explícame|muéstrame|escribe|describe|resume|crea|genera|"
            r"redacta|revisa|arregla|convierte|traduce|encuentra|busca|"
            r"haz|construye|lista|define|recomienda|sugiere|compara|analiza"
            r")\b",
            re.IGNORECASE,
        )
    # English (default)
    return re.compile(
        r"^(?:"
        r"can you|could you|will you|would you|please|help me|give me|tell me|"
        r"show me|write|explain|describe|summarize|create|generate|draft|review|"
        r"fix|convert|translate|find|search|make|build|list|define|recommend|"
        r"suggest|compare|analyze"
        r")\b",
        re.IGNORECASE,
    )


def _build_model_direct_pattern(lang: ConversationLanguage) -> re.Pattern[str]:
    """Build model-directed wish pattern for a language."""
    if lang == ConversationLanguage.DE:
        return re.compile(
            r"\bich (?:will|möchte|hoffe) (?:du|der (?:assistent|modell|chatbot|ki))\b",
            re.IGNORECASE,
        )
    if lang == ConversationLanguage.ES:
        return re.compile(
            r"\byo (?:quiero|espero|espero que) (?:tú|el (?:asistente|modelo|chatbot|ia))\b",
            re.IGNORECASE,
        )
    # English (default)
    return re.compile(
        r"\bi (?:want|'?d like|hope) (?:you|the (?:ai|assistant|model|chatbot))\b",
        re.IGNORECASE,
    )


def _build_you_toward_pattern(lang: ConversationLanguage) -> re.Pattern[str]:
    """Build 'you toward model' pattern for a language."""
    if lang == ConversationLanguage.DE:
        return re.compile(r"\bdu\b", re.IGNORECASE)
    if lang == ConversationLanguage.ES:
        return re.compile(r"\bt[uú]\b", re.IGNORECASE)
    # English (default)
    return re.compile(r"\byou\b", re.IGNORECASE)


def _build_goal_pattern(lang: ConversationLanguage) -> re.Pattern[str]:
    """Build goal detection pattern for a language."""
    if lang == ConversationLanguage.DE:
        return re.compile(
            r"\bich (?:möchte|will|plane|beabsichtige|habe vor|würde gerne|würde gern)\b"
            r"|\bmein (?:ziel|traum|plan|ambition|wunsch) (?:ist|'s)\b",
            re.IGNORECASE,
        )
    if lang == ConversationLanguage.ES:
        return re.compile(
            r"\b(?:yo )?(?:quiero|me gustaría|planeo|tengo la intención de|"
            r"estoy planeando|tengo el objetivo de|"
            r"mi (?:meta|sueño|plan|ambición|objetivo) (?:es|era))\b",
            re.IGNORECASE,
        )
    # English (default)
    return re.compile(
        r"\bi (?:want|hope|plan|intend|aspire) to\b|\bi'?d like to\b"
        r"|\bmy (?:goal|dream|plan|ambition|target) (?:is|'s)\b",
        re.IGNORECASE,
    )


def _build_recurring_pattern(lang: ConversationLanguage) -> re.Pattern[str]:
    """Build recurring timeframe pattern for a language."""
    if lang == ConversationLanguage.DE:
        de_weekdays = (
            r"montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|sonnabend"
        )
        de_weekday_adv = (
            r"montags|dienstags|mittwochs|donnerstags|freitags|samstags|sonntags"
        )
        return re.compile(
            r"\b(?:jeden|jede|jeden) (?:morgen|abend|nachmittag|tag|woche|"
            r"wochenende|nacht|monat|jahr)\b"
            r"|\b(?:täglich|wöchentlich|monatlich|jährlich|regelmäßig|"
            r"jedes mal|immer|meistens|öfters)\b"
            rf"|\bjeden (?:{de_weekdays})\b"
            rf"|\b(?:{de_weekday_adv})\b",
            re.IGNORECASE,
        )
    if lang == ConversationLanguage.ES:
        es_weekdays = r"lunes|martes|miércoles|jueves|viernes|sábados?|domingos?"
        return re.compile(
            r"\b(?:cada|todos los|todas las) (?:día|días|mañana|mañanas|tarde|"
            r"tardes|noche|noches|semana|semanas|"
            r"fin de semana|mes|meses|año|años)\b"
            r"|\b(?:diario|diariamente|semanal|semanalmente|mensual|mensualmente|"
            r"anual|anualmente|regularmente|siempre|casi siempre|normalmente)\b"
            rf"|\b(?:cada|todos los|los) (?:{es_weekdays})\b",
            re.IGNORECASE,
        )
    # English (default)
    en_weekdays = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    en_weekday_plural = (
        r"mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays"
    )
    return re.compile(
        r"\bevery (?:morning|evening|afternoon|day|week|weekend|night|month)\b"
        r"|\b(?:daily|weekly|monthly|regularly)\b"
        rf"|\bevery (?:{en_weekdays})\b"
        rf"|\b(?:on )?{en_weekday_plural}\b",
        re.IGNORECASE,
    )


def _build_temporal_patterns(lang: ConversationLanguage) -> dict[str, re.Pattern[str]]:
    """Build temporal scope patterns for a language."""
    if lang == ConversationLanguage.DE:
        return {
            "current": re.compile(
                r"\b(?:ich (?:wohne|lebe|arbeite|bin|sprich|lerne|studiere|"
                r"habe|bin|mag|liebe|genieße|bevorzuge|interessiere|"
                r"mein|meine|meinem|meinen|meines))\b",
                re.IGNORECASE,
            ),
            "historical": re.compile(
                r"\b(?:ich (?:wohnte|lebte|arbeitete|war|sprach|lernt|studierte|"
                r"hatte|war|mochte|liebte|genoss|bevorzugte|interessierte|"
                r"mein|meine|meinem|meinen|meines))\b",
                re.IGNORECASE,
            ),
            "past_markers": re.compile(
                r"\b(?:früher|damals|früher|in der vergangenheit|vor jahren|"
                r"als ich|als kind|jugend|damals|früher)\b",
                re.IGNORECASE,
            ),
        }
    if lang == ConversationLanguage.ES:
        return {
            "current": re.compile(
                r"\b(?:yo (?:vivo|trabajo|estudio|soy|hablo|aprendo|"
                r"tengo|me gusta|me encanta|prefiero|intereso|"
                r"mi|mis|mi))\b",
                re.IGNORECASE,
            ),
            "historical": re.compile(
                r"\b(?:yo (?:vivía|trabajaba|estudiaba|era|hablaba|aprendía|"
                r"tenía|me gustaba|me encantaba|prefería|interesaba|"
                r"mi|mis|mi))\b",
                re.IGNORECASE,
            ),
            "past_markers": re.compile(
                r"\b(?:antes|en el pasado|hace años|cuando yo|de niño|"
                r"juventud|antes|anteriormente)\b",
                re.IGNORECASE,
            ),
        }
    # English (default)
    return {
        "current": re.compile(
            r"\bi (?:live|work|study|am|speak|learn|"
            r"have|like|love|enjoy|prefer|"
            r"my)\b",
            re.IGNORECASE,
        ),
        "historical": re.compile(
            r"\bi (?:lived|worked|studied|was|spoke|learned|"
            r"had|liked|loved|enjoyed|preferred|"
            r"my)\b",
            re.IGNORECASE,
        ),
        "past_markers": re.compile(
            r"\b(?:previously|formerly|used to|in the past|years ago|"
            r"when i|as a child|growing up)\b",
            re.IGNORECASE,
        ),
    }


# Build patterns for all supported languages
_FIRST_PERSON_PATTERNS = {
    lang: _build_first_person_pattern(lang)
    for lang in (
        ConversationLanguage.EN,
        ConversationLanguage.DE,
        ConversationLanguage.ES,
    )
}
_NEGATION_PATTERNS = {
    lang: _build_negation_pattern(lang)
    for lang in (
        ConversationLanguage.EN,
        ConversationLanguage.DE,
        ConversationLanguage.ES,
    )
}
_REQUEST_STARTER_PATTERNS = {
    lang: _build_request_pattern(lang)
    for lang in (
        ConversationLanguage.EN,
        ConversationLanguage.DE,
        ConversationLanguage.ES,
    )
}
_MODEL_DIRECT_PATTERNS = {
    lang: _build_model_direct_pattern(lang)
    for lang in (
        ConversationLanguage.EN,
        ConversationLanguage.DE,
        ConversationLanguage.ES,
    )
}
_YOU_TOWARD_PATTERNS = {
    lang: _build_you_toward_pattern(lang)
    for lang in (
        ConversationLanguage.EN,
        ConversationLanguage.DE,
        ConversationLanguage.ES,
    )
}
_GOAL_PATTERNS = {
    lang: _build_goal_pattern(lang)
    for lang in (
        ConversationLanguage.EN,
        ConversationLanguage.DE,
        ConversationLanguage.ES,
    )
}
_RECURRING_PATTERNS = {
    lang: _build_recurring_pattern(lang)
    for lang in (
        ConversationLanguage.EN,
        ConversationLanguage.DE,
        ConversationLanguage.ES,
    )
}
_TEMPORAL_PATTERNS = {
    lang: _build_temporal_patterns(lang)
    for lang in (
        ConversationLanguage.EN,
        ConversationLanguage.DE,
        ConversationLanguage.ES,
    )
}

# Fallback patterns for unknown languages (English defaults)
_FIRST_PERSON = _FIRST_PERSON_PATTERNS[ConversationLanguage.EN]
_NEGATION = _NEGATION_PATTERNS[ConversationLanguage.EN]
_REQUEST_STARTER = _REQUEST_STARTER_PATTERNS[ConversationLanguage.EN]
_MODEL_DIRECT = _MODEL_DIRECT_PATTERNS[ConversationLanguage.EN]
_YOU_TOWARD = _YOU_TOWARD_PATTERNS[ConversationLanguage.EN]
_GOAL_TRIGGER = _GOAL_PATTERNS[ConversationLanguage.EN]
_RECURRING_TIME = _RECURRING_PATTERNS[ConversationLanguage.EN]

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"\b(?:https?://|www\.)", re.IGNORECASE)
_CURRENCY = re.compile(
    r"(?:[€£$]\s?\d|\d[\d.,]*\s?(?:€|£|\$|USD|EUR|dollars?|euros?))",
    re.IGNORECASE,
)
_CREDENTIAL_SHAPES = re.compile(
    r"\b\d{3}-\d{2}-\d{4}\b"
    r"|\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"
    r"|\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"
    r"|\b(?:sk_live|sk_test)_[A-Za-z0-9]{16,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
)
_SENSITIVE_FORM_SOURCE = (
    f"{_EMAIL.pattern}|{_URL.pattern}|{_CURRENCY.pattern}|{_CREDENTIAL_SHAPES.pattern}"
)
_SENSITIVE_FORM = re.compile(_SENSITIVE_FORM_SOURCE, re.IGNORECASE)

# Content never worth proposing regardless of the policy's own classification:
# privacy-sensitive keyword material is skipped at extraction so it can never
# even surface as a "require approval" candidate.
_SENSITIVE_KEYWORDS: frozenset[str] = frozenset(
    _POLICY_SENSITIVE_KEYWORDS
    | _POLICY_HIGHLY_SENSITIVE_KEYWORDS
    | {
        "salary",
        "credentials",
        "password",
        "passphrase",
        "api key",
        "apikey",
        "access token",
        "secret key",
        "private key",
        "session token",
        "seed phrase",
        "security answer",
    }
)


# All quotation marks treated as quoted-content markers, regardless of
# language. This covers ASCII quotes/backticks as well as typographic double
# and single quotes and guillemets, so quoted third-party speech (e.g. German
# „…“, Spanish «…», English “…”/‘…’) is never turned into a user memory.
# Apostrophe-shaped marks are handled separately because they double as
# internal apostrophes in contractions/possessives ("the user's", "I'm").
_QUOTATION_MARKS = frozenset(
    '"' + "`" + "\u201c\u201d\u201e\u201f" + "\u2018\u201a\u201b" + "\u00ab\u00bb"
)
_APOSTROPHE_MARKS = frozenset("'" + "\u2019")


def _contains_quotation(text: str) -> bool:
    """Conservatively skip any text that looks quoted.

    Presence-based for unambiguous quotation marks, like the original ASCII
    double-quote gate. Apostrophe-shaped marks (ASCII apostrophe and the
    typographic right single quote) only count as quote delimiters when they
    are not strictly between two letters — so English contractions and
    possessives still pass while "Peter said: 'I work at Nimbus Motors'" is skipped.
    Missing a candidate is safe; never producing a quoted fact is the point.
    """
    if any(char in _QUOTATION_MARKS for char in text):
        return True
    for index, char in enumerate(text):
        if char not in _APOSTROPHE_MARKS:
            continue
        prev = text[index - 1] if index > 0 else ""
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if not (prev.isalpha() and nxt.isalpha()):
            return True
    return False


def _sentence_split(text: str) -> tuple[str, ...]:
    """Split message text into candidate sentences.

    Splits on sentence-ending punctuation followed by whitespace (conservative:
    abbreviations and decimals stay intact). Long messages are bounded by the
    per-conversation message cap the caller applies.
    """
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return tuple(p.strip() for p in parts if p.strip())


def _count(skip_reasons: dict[str, int] | None, reason: str) -> None:
    """Increment a content-free skip-reason counter (no-op when disabled)."""
    if skip_reasons is not None:
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1


@dataclass(frozen=True, slots=True)
class _Rule:
    """One deterministic extraction rule: kind, temporal scope, and triggers."""

    kind: MemoryKind
    temporal: TemporalScope
    patterns: tuple[re.Pattern[str], ...]
    confidence: float = 0.85
    durability: float = 0.8
    relevance: float = 0.8
    specificity: float = 0.6
    utility: float = 0.7
    recurrence: int = 1


# First matching rule wins. Rules are ordered so the most specific signal
# (routines, identity, biography) is checked before broader categories.
_EN_WEEKDAYS = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_EN_WEEKDAY_PLURAL = r"mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays"
_RECURRING_TIME = re.compile(
    rf"\bevery (?:morning|evening|afternoon|day|week|weekend|night|month)\b"
    rf"|\b(?:daily|weekly|monthly|regularly)\b"
    rf"|\bevery (?:{_EN_WEEKDAYS})\b"
    rf"|\b(?:on )?{_EN_WEEKDAY_PLURAL}\b",
    re.IGNORECASE,
)
_JOB_TITLES = (
    r"software engineer|data scientist|product manager|project manager|"
    r"engineer|developer|designer|analyst|manager|consultant|researcher|"
    r"scientist|teacher|professor|nurse|doctor|lawyer|architect|accountant|"
    r"marketer|student|technician|administrator|director|founder|writer|"
    r"editor|photographer|musician|athlete|trainer|chef|operator"
)
_INSTRUMENTS = (
    r"guitar|piano|violin|viola|cello|drums|bass|ukulele|flute|saxophone|"
    r"trumpet|clarinet|accordion|keyboard"
)
_SPORTS = (
    r"football|soccer|basketball|tennis|badminton|hockey|cricket|rugby|golf|"
    r"volleyball|swimming|running|cycling|hiking|climbing|skiing|surfing|diving"
)

_RULES: tuple[_Rule, ...] = (
    _Rule(
        MemoryKind.HABIT,
        TemporalScope.RECURRING,
        (_RECURRING_TIME,),
        recurrence=3,
        confidence=0.85,
        durability=0.8,
        relevance=0.8,
    ),
    _Rule(
        MemoryKind.IDENTITY,
        TemporalScope.CURRENT,
        (re.compile(r"\bmy name (?:is|'s|is called)\b", re.IGNORECASE),),
        confidence=0.9,
        durability=0.9,
        relevance=0.85,
        utility=0.8,
    ),
    _Rule(
        MemoryKind.BIOGRAPHY,
        TemporalScope.HISTORICAL,
        (re.compile(r"\bi (?:was born|grew ?up)\b", re.IGNORECASE),),
    ),
    _Rule(
        MemoryKind.WORK,
        TemporalScope.HISTORICAL,
        (
            re.compile(
                r"\bi (?:used to work|worked|used to be|was) (?:as|at|for|in)\b",
                re.IGNORECASE,
            ),
        ),
        durability=0.7,
        relevance=0.7,
    ),
    _Rule(
        MemoryKind.WORK,
        TemporalScope.CURRENT,
        (
            re.compile(r"\bi work (?:as|at|for|in)\b", re.IGNORECASE),
            re.compile(rf"\bi'?m a (?:{_JOB_TITLES})\b", re.IGNORECASE),
            re.compile(r"\bmy (?:job|work)\b", re.IGNORECASE),
        ),
    ),
    _Rule(
        MemoryKind.EDUCATION,
        TemporalScope.HISTORICAL,
        (
            re.compile(
                r"\bi (?:graduated|majored|attended|studied) (?:from|in|at)\b",
                re.IGNORECASE,
            ),
        ),
        durability=0.7,
        relevance=0.7,
    ),
    _Rule(
        MemoryKind.EDUCATION,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi (?:study|am studying|'m studying|attend)\b",
                re.IGNORECASE,
            ),
            re.compile(r"\bi go to (?:school|college|university)\b", re.IGNORECASE),
            re.compile(
                r"\bi'?m a (?:student|freshman|sophomore|junior|senior|"
                r"grad (?:student|school student))\b",
                re.IGNORECASE,
            ),
        ),
    ),
    _Rule(
        MemoryKind.LOCATION_CONTEXT,
        TemporalScope.HISTORICAL,
        (
            re.compile(
                r"\bi (?:moved|relocated) (?:to|in|near)\b|\bi lived in\b"
                r"|\bi used to live\b|\bi was based in\b",
                re.IGNORECASE,
            ),
        ),
        durability=0.7,
        relevance=0.7,
    ),
    _Rule(
        MemoryKind.LOCATION_CONTEXT,
        TemporalScope.CURRENT,
        (
            re.compile(r"\bi (?:live|work from) in\b", re.IGNORECASE),
            re.compile(r"\bi'?m based in\b|\bi'?m from\b", re.IGNORECASE),
            re.compile(
                r"\bmy (?:hometown|city|neighborhood|house|home) is\b", re.IGNORECASE
            ),
        ),
    ),
    _Rule(
        MemoryKind.GOAL,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi (?:want|hope|plan|intend|aspire) to\b|\bi'?d like to\b"
                r"|\bmy (?:goal|dream|plan|ambition|target) (?:is|'s)\b",
                re.IGNORECASE,
            ),
        ),
        confidence=0.8,
        durability=0.6,
        relevance=0.7,
    ),
    _Rule(
        MemoryKind.SKILL,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi (?:speak|am fluent in|'m fluent in|can speak)\b", re.IGNORECASE
            ),
            re.compile(
                rf"\bi (?:play|can play) (?:(?:the|an?)\s+)?(?:{_INSTRUMENTS})\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\bi (?:code|program) in\b|\bi (?:can|know how to) (?:code|program)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\bi'?m (?:learning|teaching myself|practicing|taking a course in)\b",
                re.IGNORECASE,
            ),
        ),
        confidence=0.8,
        durability=0.75,
        relevance=0.8,
    ),
    _Rule(
        MemoryKind.INTEREST,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi (?:like|love|enjoy) (?!it\b|that\b|this\b|them\b)\b",
                re.IGNORECASE,
            ),
            re.compile(r"\bi'?m (?:into|a fan of)\b", re.IGNORECASE),
            re.compile(
                r"\bmy (?:favorite|favourite) (?:hobby|sport|team|band|artist|movie|film|show|"
                r"book|author|genre|food|cuisine|activity|game|podcast|place)\b",
                re.IGNORECASE,
            ),
            re.compile(rf"\bi (?:play|watch) (?:{_SPORTS})\b", re.IGNORECASE),
        ),
        confidence=0.8,
        durability=0.75,
        relevance=0.8,
    ),
    _Rule(
        MemoryKind.PREFERENCE,
        TemporalScope.CURRENT,
        (
            re.compile(r"\bi (?:prefer|'?d rather|would rather)\b", re.IGNORECASE),
            re.compile(r"\bmy (?:preference|preferred)\b", re.IGNORECASE),
        ),
        confidence=0.8,
        durability=0.7,
        relevance=0.8,
    ),
    _Rule(
        MemoryKind.COMMUNICATION_PREFERENCE,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi (?:prefer|like) to (?:communicate|be (?:reached|contacted))\b"
                r"|\bi (?:prefer|like) (?:email|text|phone|video|calls?)\b",
                re.IGNORECASE,
            ),
        ),
        confidence=0.8,
        durability=0.7,
        relevance=0.8,
    ),
    _Rule(
        MemoryKind.RELATIONSHIP,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bmy (?:wife|husband|partner|spouse|fiancee|fiance|girlfriend|boyfriend|"
                r"mom|mum|mother|dad|father|brother|sister|son|daughter|parent|parents|"
                r"children|kids|family|grandmother|grandfather|aunt|uncle|cousin|boss|"
                r"manager|teammate|colleague)\b",
                re.IGNORECASE,
            ),
        ),
    ),
    _Rule(
        MemoryKind.PERSONAL_FACT,
        TemporalScope.CURRENT,
        (
            re.compile(
                r"\bi(?:'?ve| have) (?:a|an|the|my) (?:dog|cat|pet|house|apartment|flat|"
                r"car|bike|motorcycle|boat|garden)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\bi (?:own|drive|ride|use) (?:a|an|my) [a-z]{2,}\b", re.IGNORECASE
            ),
            re.compile(
                r"\b(?:i'?m married|i(?:'?ve)? have been married)\b", re.IGNORECASE
            ),
        ),
        confidence=0.8,
        durability=0.8,
        relevance=0.7,
    ),
)

# Language-specific vocabulary for multilingual rules
_JOB_TITLES_DE = (
    r"softwareentwickler|datenspezialist|produktmanager|projektmanager|"
    r"ingenieur|entwickler|designer|analyst|manager|berater|forscher|"
    r"wissenschaftler|lehrer|professor|krankenschwester|arzt|anwalt|architekt|buchhalter|"
    r"marketer|student|techniker|administrator|direktor|gründer|autor|"
    r"editor|fotograf|musiker|athlet|trainer|koch|operator"
)
_INSTRUMENTS_DE = (
    r"gitarre|klavier|geige|bratsche|cello|schlagzeug|bass|ukulele|flöte|saxophon|"
    r"trompete|klarinette|akkordeon|keyboard"
)
_SPORTS_DE = (
    r"fußball|fussball|basketball|tennis|badminton|hockey|cricket|rugby|golf|"
    r"volleyball|schwimmen|laufen|radfahren|wandern|klettern|ski|surfen|tauchen"
)

_JOB_TITLES_ES = (
    r"ingeniero|científico de datos|gerente de producto|gerente de proyecto|"
    r"ingeniero|desarrollador|diseñador|analista|gerente|consultor|investigador|"
    r"científico|profesor|profesora|enfermero|enfermera|médico|abogado|arquitecto|contador|"
    r"marketer|estudiante|técnico|administrador|director|fundador|escritor|"
    r"editor|fotógrafo|músico|atleta|entrenador|chef|operador"
)
_INSTRUMENTS_ES = (
    r"guitarra|piano|violín|viola|violonchelo|batería|bajo|ukulele|flauta|saxofón|"
    r"trompeta|clarinete|acordeón|teclado"
)
_SPORTS_ES = (
    r"fútbol|baloncesto|tenis|bádminton|hockey|cricket|rugby|golf|"
    r"voleibol|natación|correr|ciclismo|senderismo|escalada|esquí|surf|buceo"
)


def _build_rules(lang: ConversationLanguage) -> tuple[_Rule, ...]:
    """Build extraction rules for a specific language."""
    if lang == ConversationLanguage.DE:
        return (
            _Rule(
                MemoryKind.HABIT,
                TemporalScope.RECURRING,
                (_RECURRING_PATTERNS[ConversationLanguage.DE],),
                recurrence=3,
                confidence=0.85,
                durability=0.8,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.IDENTITY,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\b(?:mein name (?:ist|heißt|heißt)\b|ich heiße)\b",
                        re.IGNORECASE,
                    ),
                ),
                confidence=0.9,
                durability=0.9,
                relevance=0.85,
                utility=0.8,
            ),
            _Rule(
                MemoryKind.BIOGRAPHY,
                TemporalScope.HISTORICAL,
                (
                    re.compile(
                        r"\bich (?:wurde geboren|bin geboren|wuchs auf)\b",
                        re.IGNORECASE,
                    ),
                ),
            ),
            _Rule(
                MemoryKind.WORK,
                TemporalScope.HISTORICAL,
                (
                    re.compile(
                        r"\bich (?:hatte gearbeitet|arbeitete|war) (?:als|bei|für|in)\b",
                        re.IGNORECASE,
                    ),
                ),
                durability=0.7,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.WORK,
                TemporalScope.CURRENT,
                (
                    re.compile(r"\bich arbeite (?:als|bei|für|in)\b", re.IGNORECASE),
                    re.compile(
                        rf"\bich bin (?:ein|eine) (?:{_JOB_TITLES_DE})\b", re.IGNORECASE
                    ),
                    re.compile(r"\bmein (?:job|arbeit)\b", re.IGNORECASE),
                ),
            ),
            _Rule(
                MemoryKind.EDUCATION,
                TemporalScope.HISTORICAL,
                (
                    re.compile(
                        r"\bich (?:habe abgeschlossen|studierte|besuchte) (?:an|in|von)\b",
                        re.IGNORECASE,
                    ),
                ),
                durability=0.7,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.EDUCATION,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bich (?:studiere|lerne|besuche)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bich gehe (?:zur schule|an die (?:hochschule|universität))\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bich bin (?:ein|eine) (?:student|studentin|schüler|schülerin|"
                        r"stipendiat|stipendiatin|doktorand|doktorandin)\b",
                        re.IGNORECASE,
                    ),
                ),
            ),
            _Rule(
                MemoryKind.LOCATION_CONTEXT,
                TemporalScope.HISTORICAL,
                (
                    re.compile(
                        r"\bich (?:zog um|zog nach|zog in)\b|\bich wohnte in\b"
                        r"|\bich lebte in\b|\bich war ansässig in\b",
                        re.IGNORECASE,
                    ),
                ),
                durability=0.7,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.LOCATION_CONTEXT,
                TemporalScope.CURRENT,
                (
                    re.compile(r"\bich (?:wohne|lebe|arbeite von) in\b", re.IGNORECASE),
                    re.compile(
                        r"\bich bin (?:ansässig|basiert) in\b|\bich komme aus\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bmein (?:heimatort|stadt|viertel|haus|heimat) ist\b",
                        re.IGNORECASE,
                    ),
                ),
            ),
            _Rule(
                MemoryKind.GOAL,
                TemporalScope.CURRENT,
                (_GOAL_PATTERNS[ConversationLanguage.DE],),
                confidence=0.8,
                durability=0.6,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.SKILL,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bich (?:spreche|bin flüssig in|kann sprechen)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        rf"\bich (?:spiele|kann spielen) (?:(?:die|das|ein|eine)\s+)?(?:{_INSTRUMENTS_DE})\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bich (?:code|programmiere) in\b|\bich (?:kann|weiß wie man) (?:codieren|programmieren)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bich (?:lerne|bringe mir bei|übe|mache einen kurs in)\b",
                        re.IGNORECASE,
                    ),
                ),
                confidence=0.8,
                durability=0.75,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.INTEREST,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bich (?:mag|liebe|genieße) (?!es\b|das\b|dies\b|sie\b)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(r"\bich (?:stehe auf|bin ein fan von)\b", re.IGNORECASE),
                    re.compile(
                        r"\bmein (?:lieblings|liebling) (?:hobby|sport|team|band|künstler|film|serie|"
                        r"buch|autor|genre|essen|küche|aktivität|spiel|podcast|ort)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        rf"\bich (?:spiele|schaue) (?:{_SPORTS_DE})\b", re.IGNORECASE
                    ),
                ),
                confidence=0.8,
                durability=0.75,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.PREFERENCE,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bich (?:bevorzuge|lieber|würde lieber)\b", re.IGNORECASE
                    ),
                    re.compile(r"\bmein (?:vorliebe|bevorzugt)\b", re.IGNORECASE),
                ),
                confidence=0.8,
                durability=0.7,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.COMMUNICATION_PREFERENCE,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bich (?:bevorzuge|mag) (?:zu kommunizieren|erreichbar zu sein)\b"
                        r"|\bich (?:bevorzuge|mag) (?:e-?mail|nachricht|telefon|video|anrufe?)\b",
                        re.IGNORECASE,
                    ),
                ),
                confidence=0.8,
                durability=0.7,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.RELATIONSHIP,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bmein(?:e|r|s|em|en)? (?:frau|ehemann|partner|ehepartner|verlobte|verlobter|"
                        r"freundin|freund|mutter|mama|vater|papa|bruder|schwester|sohn|tochter|"
                        r"eltern|kinder|kids|familie|großmutter|opa|oma|tante|onkel|cousin|"
                        r"chef|vorgesetzter|kollege|kollegin|teamkollege|teamkollegin)\b",
                        re.IGNORECASE,
                    ),
                ),
            ),
            _Rule(
                MemoryKind.PERSONAL_FACT,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bich (?:habe|besitze) (?:ein|eine|mein) (?:hund|katze|haustier|haus|wohnung|"
                        r"auto|fahrrad|motorrad|boot|garten)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bich (?:besitze|fahre|reite|nutze) (?:ein|eine|mein) [a-zäöüß]{2,}\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\b(?:ich bin verheiratet|ich habe geheiratet)\b",
                        re.IGNORECASE,
                    ),
                ),
                confidence=0.8,
                durability=0.8,
                relevance=0.7,
            ),
        )
    elif lang == ConversationLanguage.ES:
        return (
            _Rule(
                MemoryKind.HABIT,
                TemporalScope.RECURRING,
                (_RECURRING_PATTERNS[ConversationLanguage.ES],),
                recurrence=3,
                confidence=0.85,
                durability=0.8,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.IDENTITY,
                TemporalScope.CURRENT,
                (re.compile(r"\b(?:mi nombre es|me llamo|soy)\b", re.IGNORECASE),),
                confidence=0.9,
                durability=0.9,
                relevance=0.85,
                utility=0.8,
            ),
            _Rule(
                MemoryKind.BIOGRAPHY,
                TemporalScope.HISTORICAL,
                (re.compile(r"\b(?:nací en|crecí en)\b", re.IGNORECASE),),
            ),
            _Rule(
                MemoryKind.WORK,
                TemporalScope.HISTORICAL,
                (
                    re.compile(
                        r"\b(?:trabajaba|trabajé|era|fui) (?:como|en|para|en)\b",
                        re.IGNORECASE,
                    ),
                ),
                durability=0.7,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.WORK,
                TemporalScope.CURRENT,
                (
                    re.compile(r"\btrabajo (?:como|en|para|en)\b", re.IGNORECASE),
                    re.compile(
                        rf"\bsoy (?:un|una) (?:{_JOB_TITLES_ES})\b", re.IGNORECASE
                    ),
                    re.compile(r"\bmi (?:trabajo|empleo)\b", re.IGNORECASE),
                ),
            ),
            _Rule(
                MemoryKind.EDUCATION,
                TemporalScope.HISTORICAL,
                (
                    re.compile(
                        r"\b(?:me gradué|estudié|asistí) (?:en|de|en)\b",
                        re.IGNORECASE,
                    ),
                ),
                durability=0.7,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.EDUCATION,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\b(?:estudio|estoy estudiando|asisto)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bvoy a (?:la escuela|la universidad|el colegio)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bsoy (?:un|una) (?:estudiante|estudiante de primer año|sophomore|junior|senior|"
                        r"estudiante de posgrado|estudiante de escuela)\b",
                        re.IGNORECASE,
                    ),
                ),
            ),
            _Rule(
                MemoryKind.LOCATION_CONTEXT,
                TemporalScope.HISTORICAL,
                (
                    re.compile(
                        r"\b(?:me mudé|me trasladé) (?:a|en|cerca de)\b|\bvivía en\b"
                        r"|\b(?:solía vivir|viví en)\b|\b(?:estaba basado en)\b",
                        re.IGNORECASE,
                    ),
                ),
                durability=0.7,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.LOCATION_CONTEXT,
                TemporalScope.CURRENT,
                (
                    re.compile(r"\b(?:vivo|trabajo desde) en\b", re.IGNORECASE),
                    re.compile(r"\bestoy basado en\b|\bsoy de\b", re.IGNORECASE),
                    re.compile(
                        r"\bmi (?:ciudad natal|ciudad|vecindario|casa|hogar) es\b",
                        re.IGNORECASE,
                    ),
                ),
            ),
            _Rule(
                MemoryKind.GOAL,
                TemporalScope.CURRENT,
                (_GOAL_PATTERNS[ConversationLanguage.ES],),
                confidence=0.8,
                durability=0.6,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.SKILL,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\b(?:hablo|soy fluido en|puedo hablar)\b", re.IGNORECASE
                    ),
                    re.compile(
                        rf"\b(?:toco|puedo tocar) (?:(?:el|la|un|una)\s+)?(?:{_INSTRUMENTS_ES})\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\b(?:programo|codifico) en\b|\b(?:puedo|sé cómo) (?:codificar|programar)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bestoy (?:aprendiendo|enseñándome a mí mismo|practicando|tomando un curso de)\b",
                        re.IGNORECASE,
                    ),
                ),
                confidence=0.8,
                durability=0.75,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.INTEREST,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\b(?:me gusta|me encanta|disfruto) (?!eso\b|eso\b|esto\b|ellos\b)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(r"\b(?:me gusta|soy fan de)\b", re.IGNORECASE),
                    re.compile(
                        r"\bmi (?:hobby|deporte|equipo|banda|artista|película|film|serie|"
                        r"libro|autor|género|comida|cocina|actividad|juego|podcast|lugar) (?:favorito|favorita)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(rf"\b(?:juego|veo) (?:{_SPORTS_ES})\b", re.IGNORECASE),
                ),
                confidence=0.8,
                durability=0.75,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.PREFERENCE,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\b(?:prefiero|preferiría|preferiría)\b", re.IGNORECASE
                    ),
                    re.compile(r"\bmi (?:preferencia|preferido)\b", re.IGNORECASE),
                ),
                confidence=0.8,
                durability=0.7,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.COMMUNICATION_PREFERENCE,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\b(?:prefiero|me gusta) (?:comunicarme|que me contacten)\b"
                        r"|\b(?:prefiero|me gusta) (?:correo|mensaje|teléfono|video|llamadas?)\b",
                        re.IGNORECASE,
                    ),
                ),
                confidence=0.8,
                durability=0.7,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.RELATIONSHIP,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bmi (?:esposa|esposo|pareja|cónyuge|prometida|prometido|novia|novio|"
                        r"mamá|mamá|madre|papá|padre|hermano|hermana|hijo|hija|padre|padres|"
                        r"hijos|hijos|familia|abuela|abuelo|tía|tío|primo|jefe|"
                        r"jefe|compañero|compañera|colega)\b",
                        re.IGNORECASE,
                    ),
                ),
            ),
            _Rule(
                MemoryKind.PERSONAL_FACT,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\b(?:tengo|tengo) (?:un|una|mi) (?:perro|gato|mascota|casa|apartamento|"
                        r"coche|bicicleta|motocicleta|barco|jardín)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\b(?:tengo|conduzco|monto|uso) (?:un|una|mi) [a-záéíóúñ]{2,}\b",
                        re.IGNORECASE,
                    ),
                    re.compile(r"\b(?:estoy casado|me he casado)\b", re.IGNORECASE),
                ),
                confidence=0.8,
                durability=0.8,
                relevance=0.7,
            ),
        )
    else:
        # English rules (original)
        return (
            _Rule(
                MemoryKind.HABIT,
                TemporalScope.RECURRING,
                (_RECURRING_TIME,),
                recurrence=3,
                confidence=0.85,
                durability=0.8,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.IDENTITY,
                TemporalScope.CURRENT,
                (re.compile(r"\bmy name (?:is|'s|is called)\b", re.IGNORECASE),),
                confidence=0.9,
                durability=0.9,
                relevance=0.85,
                utility=0.8,
            ),
            _Rule(
                MemoryKind.BIOGRAPHY,
                TemporalScope.HISTORICAL,
                (re.compile(r"\bi (?:was born|grew ?up)\b", re.IGNORECASE),),
            ),
            _Rule(
                MemoryKind.WORK,
                TemporalScope.HISTORICAL,
                (
                    re.compile(
                        r"\bi (?:used to work|worked|used to be|was) (?:as|at|for|in)\b",
                        re.IGNORECASE,
                    ),
                ),
                durability=0.7,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.WORK,
                TemporalScope.CURRENT,
                (
                    re.compile(r"\bi work (?:as|at|for|in)\b", re.IGNORECASE),
                    re.compile(rf"\bi'?m a (?:{_JOB_TITLES})\b", re.IGNORECASE),
                    re.compile(r"\bmy (?:job|work)\b", re.IGNORECASE),
                ),
            ),
            _Rule(
                MemoryKind.EDUCATION,
                TemporalScope.HISTORICAL,
                (
                    re.compile(
                        r"\bi (?:graduated|majored|attended|studied) (?:from|in|at)\b",
                        re.IGNORECASE,
                    ),
                ),
                durability=0.7,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.EDUCATION,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bi (?:study|am studying|'m studying|attend)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bi go to (?:school|college|university)\b", re.IGNORECASE
                    ),
                    re.compile(
                        r"\bi'?m a (?:student|freshman|sophomore|junior|senior|"
                        r"grad (?:student|school student))\b",
                        re.IGNORECASE,
                    ),
                ),
            ),
            _Rule(
                MemoryKind.LOCATION_CONTEXT,
                TemporalScope.HISTORICAL,
                (
                    re.compile(
                        r"\bi (?:moved|relocated) (?:to|in|near)\b|\bi lived in\b"
                        r"|\bi used to live\b|\bi was based in\b",
                        re.IGNORECASE,
                    ),
                ),
                durability=0.7,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.LOCATION_CONTEXT,
                TemporalScope.CURRENT,
                (
                    re.compile(r"\bi (?:live|work from) in\b", re.IGNORECASE),
                    re.compile(r"\bi'?m based in\b|\bi'?m from\b", re.IGNORECASE),
                    re.compile(
                        r"\bmy (?:hometown|city|neighborhood|house|home) is\b",
                        re.IGNORECASE,
                    ),
                ),
            ),
            _Rule(
                MemoryKind.GOAL,
                TemporalScope.CURRENT,
                (_GOAL_TRIGGER,),
                confidence=0.8,
                durability=0.6,
                relevance=0.7,
            ),
            _Rule(
                MemoryKind.SKILL,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bi (?:speak|am fluent in|'m fluent in|can speak)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        rf"\bi (?:play|can play) (?:(?:the|an?)\s+)?(?:{_INSTRUMENTS})\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bi (?:code|program) in\b|\bi (?:can|know how to) (?:code|program)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bi'?m (?:learning|teaching myself|practicing|taking a course in)\b",
                        re.IGNORECASE,
                    ),
                ),
                confidence=0.8,
                durability=0.75,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.INTEREST,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bi (?:like|love|enjoy) (?!it\b|that\b|this\b|them\b)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(r"\bi'?m (?:into|a fan of)\b", re.IGNORECASE),
                    re.compile(
                        r"\bmy (?:favorite|favourite) (?:hobby|sport|team|band|artist|movie|film|show|"
                        r"book|author|genre|food|cuisine|activity|game|podcast|place)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(rf"\bi (?:play|watch) (?:{_SPORTS})\b", re.IGNORECASE),
                ),
                confidence=0.8,
                durability=0.75,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.PREFERENCE,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bi (?:prefer|'?d rather|would rather)\b", re.IGNORECASE
                    ),
                    re.compile(r"\bmy (?:preference|preferred)\b", re.IGNORECASE),
                ),
                confidence=0.8,
                durability=0.7,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.COMMUNICATION_PREFERENCE,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bi (?:prefer|like) to (?:communicate|be (?:reached|contacted))\b"
                        r"|\bi (?:prefer|like) (?:email|text|phone|video|calls?)\b",
                        re.IGNORECASE,
                    ),
                ),
                confidence=0.8,
                durability=0.7,
                relevance=0.8,
            ),
            _Rule(
                MemoryKind.RELATIONSHIP,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bmy (?:wife|husband|partner|spouse|fiancee|fiance|girlfriend|boyfriend|"
                        r"mom|mum|mother|dad|father|brother|sister|son|daughter|parent|parents|"
                        r"children|kids|family|grandmother|grandfather|aunt|uncle|cousin|boss|"
                        r"manager|teammate|colleague)\b",
                        re.IGNORECASE,
                    ),
                ),
            ),
            _Rule(
                MemoryKind.PERSONAL_FACT,
                TemporalScope.CURRENT,
                (
                    re.compile(
                        r"\bi(?:'?ve| have) (?:a|an|the|my) (?:dog|cat|pet|house|apartment|flat|"
                        r"car|bike|motorcycle|boat|garden)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\bi (?:own|drive|ride|use) (?:a|an|my) [a-z]{2,}\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"\b(?:i'?m married|i(?:'?ve)? have been married)\b",
                        re.IGNORECASE,
                    ),
                ),
                confidence=0.8,
                durability=0.8,
                relevance=0.7,
            ),
        )


# Build cached rules for each language
_RULES_CACHE: dict[ConversationLanguage, tuple[_Rule, ...]] = {
    lang: _build_rules(lang)
    for lang in (
        ConversationLanguage.EN,
        ConversationLanguage.DE,
        ConversationLanguage.ES,
    )
}

# Language-specific verb forms for canonicalization
_VERB_FORMS_DE: dict[str, str] = {
    "bin": "ist",
    "habe": "hat",
    "arbeite": "arbeitet",
    "wohne": "wohnt",
    "lebe": "lebt",
    "spreche": "spricht",
    "sprich": "spricht",
    "kann": "kann",
    "möchte": "möchte",
    "will": "will",
    "mache": "macht",
    "gehe": "geht",
    "komme": "kommt",
    "sehe": "sieht",
    "höre": "hört",
    "lese": "liest",
    "schreibe": "schreibt",
    "lerne": "lernt",
    "studiere": "studiert",
    "mag": "mag",
    "liebe": "liebt",
    "genieße": "genießt",
    "bevorzuge": "bevorzugt",
    "interessiere": "interessiert",
    "fahre": "fährt",
    "laufe": "läuft",
    "schwimme": "schwimmt",
    "meditiere": "meditiert",
    "übe": "übt",
    "trainiere": "trainiert",
    "kochiere": "kocht",
    "wache": "wacht",
    "zog": "zog",
    "lebte": "lebte",
    "arbeitete": "arbeitete",
    "studierte": "studierte",
    "graduierte": "graduierte",
    "besuchte": "besuchte",
    "besucht": "besucht",
    "war": "war",
    "nutzte": "nutzte",
    "basierte": "war basiert",
}

_VERB_FORMS_ES: dict[str, str] = {
    "soy": "es",
    "estoy": "está",
    "tengo": "tiene",
    "vivo": "vive",
    "trabajo": "trabaja",
    "estudio": "estudia",
    "hablo": "habla",
    "puedo": "puede",
    "quiero": "quiere",
    "me gustaría": "le gustaría",
    "hago": "hace",
    "voy": "va",
    "vengo": "viene",
    "veo": "ve",
    "oigo": "oye",
    "leo": "lee",
    "escribo": "escribe",
    "aprendo": "aprende",
    "me gusta": "le gusta",
    "me encanta": "le encanta",
    "prefiero": "prefiere",
    "intereso": "interesa",
    "conduzco": "conduce",
    "corro": "corre",
    "nado": "nada",
    "medito": "medita",
    "practico": "practica",
    "entreno": "entrena",
    "cocino": "cocina",
    "despierto": "despierta",
    "mudé": "se mudó",
    "viví": "vivió",
    "trabajé": "trabajó",
    "estudié": "estudió",
    "me gradué": "se graduó",
    "asistí": "asistió",
    "asisto": "asiste",
    "era": "era",
    "usaba": "usaba",
    "estaba basado": "estaba basado",
}

_VERB_FORMS_EN: dict[str, str] = {
    "am": "is",
    "live": "lives",
    "work": "works",
    "study": "studies",
    "like": "likes",
    "love": "loves",
    "enjoy": "enjoys",
    "want": "wants",
    "hope": "hopes",
    "plan": "plans",
    "intend": "intends",
    "aspire": "aspires",
    "prefer": "prefers",
    "speak": "speaks",
    "play": "plays",
    "code": "codes",
    "program": "programs",
    "have": "has",
    "own": "owns",
    "use": "uses",
    "drive": "drives",
    "ride": "rides",
    "go": "goes",
    "watch": "watches",
    "read": "reads",
    "exercise": "exercises",
    "run": "runs",
    "jog": "jogs",
    "swim": "swims",
    "meditate": "meditates",
    "practice": "practices",
    "train": "trains",
    "cook": "cooks",
    "wake": "wakes",
    "moved": "moved",
    "lived": "lived",
    "worked": "worked",
    "studied": "studied",
    "graduated": "graduated",
    "attended": "attended",
    "attend": "attends",
    "was": "was",
    "used": "used",
    "based": "is based",
}

_PREFIX_FORMS_DE: tuple[tuple[str, str], ...] = (
    ("ich bin", "der nutzer ist"),
    ("ich heiße", "der nutzer heißt"),
    ("mein name ist", "der nutzer heißt"),
    ("mein name heißt", "der nutzer heißt"),
    ("ich würde", "der nutzer würde"),
    ("ich habe", "der nutzer hat"),
)

_PREFIX_FORMS_ES: tuple[tuple[str, str], ...] = (
    ("yo soy", "el usuario es"),
    ("soy", "el usuario es"),
    ("yo me llamo", "el usuario se llama"),
    ("yo estoy", "el usuario está"),
    ("yo tengo", "el usuario tiene"),
    ("yo tendría", "el usuario tendría"),
    ("yo he", "el usuario ha"),
)

_PREFIX_FORMS_EN: tuple[tuple[str, str], ...] = (
    ("i'm", "the user is"),
    ("i’m", "the user is"),
    ("i am", "the user is"),
    ("i'd", "the user would"),
    ("i’d", "the user would"),
    ("i've", "the user has"),
    ("i’ve", "the user has"),
)


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _canonical_statement(
    sentence: str, lang: ConversationLanguage = ConversationLanguage.EN
) -> str:
    """Rewrite a first-person user sentence as a third-person fact.

    Deterministic, bounded transformation: leading first-person forms
    become "the user ...", possessives become "the user's", and the
    sentence-initial verb is conjugated for the singular subject. Unmapped
    verbs pass through unchanged (conservative: never invented).

    The output language follows the input language to preserve
    original-language evidence as per architecture.
    """
    if lang == ConversationLanguage.UNKNOWN:
        lang = ConversationLanguage.EN

    if lang == ConversationLanguage.DE:
        verb_forms = _VERB_FORMS_DE
        third_person = "der nutzer"
    elif lang == ConversationLanguage.ES:
        verb_forms = _VERB_FORMS_ES
        third_person = "el usuario"
    else:
        verb_forms = _VERB_FORMS_EN
        third_person = "the user"

    text = re.sub(r"\s+", " ", sentence.strip())

    # Handle "call me X" / "me llamo X" / "ich heiße X"
    call_me_de = re.match(r"(?i)^ich heiße\s+(\w+)(.*)$", text)
    call_me_es = re.match(r"(?i)^me llamo\s+(\w+)(.*)$", text)
    call_me_en = re.match(r"(?i)^call me\s+(\w+)(.*)$", text)

    if lang == ConversationLanguage.DE and call_me_de:
        return f"Der Nutzer heißt {call_me_de.group(1)}{call_me_de.group(2)}".strip()
    if lang == ConversationLanguage.ES and call_me_es:
        return f"El usuario se llama {call_me_es.group(1)}{call_me_es.group(2)}".strip()
    if lang == ConversationLanguage.EN and call_me_en:
        return f"The user goes by {call_me_en.group(1)}{call_me_en.group(2)}".strip()

    lowered = text.lower()
    for prefix, replacement in (
        _PREFIX_FORMS_DE
        if lang == ConversationLanguage.DE
        else (_PREFIX_FORMS_ES if lang == ConversationLanguage.ES else _PREFIX_FORMS_EN)
    ):
        if lowered.startswith(prefix):
            return _capitalize(replacement + text[len(prefix) :]).strip()

    # Handle first-person start
    first_person_start = {
        ConversationLanguage.DE: ("ich ", "ich"),
        ConversationLanguage.ES: ("yo ", "yo"),
        ConversationLanguage.EN: ("i ", "i"),
    }[lang]

    if lowered.startswith(first_person_start[0]) or lowered == first_person_start[1]:
        rest = (
            text[len(first_person_start[0]) :].lstrip()
            if lowered.startswith(first_person_start[0])
            else text[1:].lstrip()
        )
        if rest:
            verb, sep, tail = rest.partition(" ")
            conjugated = (
                _VERB_FORMS_DE.get(verb.lower(), verb)
                if lang == ConversationLanguage.DE
                else (
                    _VERB_FORMS_ES.get(verb.lower(), verb)
                    if lang == ConversationLanguage.ES
                    else _VERB_FORMS_EN.get(verb.lower(), verb)
                )
            )
            return _capitalize(f"{third_person} {conjugated}{sep}{tail}").strip()

    # Spanish pro-drop: sentence begins directly with a first-person verb
    # (subject encoded in the ending, no "yo"/possessive present).
    if lang == ConversationLanguage.ES:
        verb, sep, tail = text.partition(" ")
        if verb and verb.lower() in verb_forms and _spanish_is_first_person(text):
            conjugated = verb_forms[verb.lower()]
            return _capitalize(f"{third_person} {conjugated}{sep}{tail}").strip()

    # Replace possessives
    possessive_map = {
        ConversationLanguage.DE: (r"\bmein\b", "der nutzer"),
        ConversationLanguage.ES: (r"\bmi\b", "el usuario"),
        ConversationLanguage.EN: (r"\bmy\b", "the user's"),
    }
    pattern, replacement = possessive_map[lang]
    return _capitalize(re.sub(pattern, replacement, text, flags=re.IGNORECASE))


def _evidence_ref(
    conversation: Conversation, message: ConversationMessage
) -> MemoryEvidenceRef:
    """Provenance-only evidence: identifiers and an ISO timestamp."""
    stamp = (
        message.timestamp or conversation.modified_at or conversation.created_at or None
    )
    return MemoryEvidenceRef(
        source_type=conversation.source_type,
        source_id=conversation.id,
        source_document_id=message.id,
        source_timestamp=stamp,
    )


class ConversationMemoryExtractor:
    """Deterministic extraction of identity-tier memory candidates.

    Purely in-memory: takes a ``Conversation`` and its messages and returns
    validated ``MemoryCandidate`` instances. Never writes anything.
    """

    def __init__(
        self,
        *,
        max_messages: int = DEFAULT_MAX_MESSAGES_PER_CONVERSATION,
        max_candidates: int = DEFAULT_MAX_CANDIDATES_PER_CONVERSATION,
    ) -> None:
        self._max_messages = max_messages
        self._max_candidates = max_candidates

    def extract(
        self,
        conversation: Conversation,
        messages: Sequence[ConversationMessage],
        *,
        skip_reasons: dict[str, int] | None = None,
    ) -> tuple[MemoryCandidate, ...]:
        """Return bounded candidates from active-branch user messages.

        ``skip_reasons`` is an optional read-only audit counter: when supplied,
        each rejected sentence/message increments a category key instead of
        being silently dropped. It never changes extraction behavior and is
        only ever counted (content-free, identifier-free).
        """
        if conversation.source_type not in CONVERSATION_SOURCE_TYPES:
            _count(skip_reasons, "unsupported_source_type")
            return ()
        candidates: list[MemoryCandidate] = []
        seen: set[tuple[str, str]] = set()
        for message in messages[: self._max_messages]:
            if not message.is_active_branch:
                _count(skip_reasons, "inactive_branch")
                continue
            if (message.role or "").strip().lower() != "user":
                _count(skip_reasons, "non_user_role")
                continue
            for sentence in _sentence_split(message.content_text):
                candidate = self._candidate_for(
                    conversation, message, sentence, skip_reasons=skip_reasons
                )
                if candidate is None:
                    continue
                key = (candidate.kind.value, normalize_text(candidate.statement))
                if key in seen:
                    _count(skip_reasons, "duplicate_candidate")
                    continue
                seen.add(key)
                candidates.append(candidate)
        return tuple(candidates[: self._max_candidates])

    def _candidate_for(
        self,
        conversation: Conversation,
        message: ConversationMessage,
        sentence: str,
        *,
        skip_reasons: dict[str, int] | None = None,
    ) -> MemoryCandidate | None:
        raw = sentence
        if len(raw) > MAX_STATEMENT_CHARS:
            _count(skip_reasons, "too_long")
            return None
        if raw.endswith("?"):
            _count(skip_reasons, "question")
            return None

        # Detect language of this sentence
        lang = _detect_language(raw)

        # Get language-specific patterns
        negation = _NEGATION_PATTERNS.get(
            lang, _NEGATION_PATTERNS[ConversationLanguage.EN]
        )
        you_toward = _YOU_TOWARD_PATTERNS.get(
            lang, _YOU_TOWARD_PATTERNS[ConversationLanguage.EN]
        )
        request_starter = _REQUEST_STARTER_PATTERNS.get(
            lang, _REQUEST_STARTER_PATTERNS[ConversationLanguage.EN]
        )
        model_direct = _MODEL_DIRECT_PATTERNS.get(
            lang, _MODEL_DIRECT_PATTERNS[ConversationLanguage.EN]
        )
        rules = _RULES_CACHE.get(lang, _RULES_CACHE[ConversationLanguage.EN])

        if not _is_first_person(raw, lang):
            _count(skip_reasons, "not_first_person")
            return None
        if negation.search(raw):
            _count(skip_reasons, "negation")
            return None
        if you_toward.search(raw):
            _count(skip_reasons, "you_toward")
            return None
        if _contains_quotation(raw):
            _count(skip_reasons, "quotation")
            return None
        if _SENSITIVE_FORM.search(raw):
            _count(skip_reasons, "sensitive_form")
            return None
        lowered = raw.lower()
        if any(keyword in lowered for keyword in _SENSITIVE_KEYWORDS):
            _count(skip_reasons, "sensitive_keyword")
            return None
        if request_starter.search(raw):
            _count(skip_reasons, "request")
            return None
        if model_direct.search(raw):
            _count(skip_reasons, "model_direct")
            return None

        for rule in rules:
            if any(pattern.search(raw) for pattern in rule.patterns):
                statement = _canonical_statement(raw, lang)
                if not statement or len(statement) > MAX_STATEMENT_CHARS:
                    _count(skip_reasons, "statement_too_long")
                    return None
                return MemoryCandidate(
                    statement=statement,
                    kind=rule.kind,
                    confidence=rule.confidence,
                    durability=rule.durability,
                    relevance=rule.relevance,
                    specificity=rule.specificity,
                    recurrence=rule.recurrence,
                    utility=rule.utility,
                    temporal_scope=rule.temporal,
                    assertion_status=AssertionStatus.ASSERTED,
                    evidence=(_evidence_ref(conversation, message),),
                )
        _count(skip_reasons, "no_rule_match")
        return None


class ConversationSourceError(ValueError):
    """Raised for unsupported conversation source types."""


@dataclass(frozen=True, slots=True)
class ConversationMemoryReport:
    """Count-only outcome of a conversation memory extraction run.

    Deliberately content-free: totals, candidate/decision counts, and write
    statuses — never statements, evidence identifiers, or conversation text.
    """

    source_type: str
    conversations_scanned: int
    user_messages_scanned: int
    tally: OutcomeTally
    evidence_rows_added: int

    def summary(self) -> dict[str, object]:
        """Count-only view of the run (no content or identifiers)."""
        return {
            "source_type": self.source_type,
            "conversations_scanned": self.conversations_scanned,
            "user_messages_scanned": self.user_messages_scanned,
            "evidence_rows_added": self.evidence_rows_added,
            **self.tally.to_dict(),
        }


class ConversationMemoryIngestor:
    """Runs bounded conversation memory extraction through the write gate.

    Reads a bounded run of stored conversations for one source type, derives
    candidates with the deterministic extractor, and routes every candidate
    through the policy-gated ``AutomaticMemoryCurator`` — never touching SQL
    or the store directly.
    """

    def __init__(
        self,
        conversation_store: ConversationStore,
        memory_service: object,
        *,
        policy: MemoryPolicy | None = None,
        max_conversations: int = DEFAULT_MAX_CONVERSATIONS,
        max_messages_per_conversation: int = DEFAULT_MAX_MESSAGES_PER_CONVERSATION,
        max_candidates_per_conversation: int = DEFAULT_MAX_CANDIDATES_PER_CONVERSATION,
        interactive_approver: Callable[[str, str, str], bool] | None = None,
        auto_approver: Callable[[str, str, str], bool] | None = None,
        extractor: ConversationMemoryExtractor | None = None,
    ) -> None:
        # Imported lazily: pulling in the tools layer at module import time
        # would create a circular import through the chat tool registry.
        from personal_ai.tools.memory import AutomaticMemoryCurator

        self._store = conversation_store
        self._max_conversations = max_conversations
        self._curator = AutomaticMemoryCurator(
            memory_service,
            policy or MemoryPolicy(),
            interactive_approver=interactive_approver,
            auto_approver=auto_approver,
        )
        self._extractor = extractor or ConversationMemoryExtractor(
            max_messages=max_messages_per_conversation,
            max_candidates=max_candidates_per_conversation,
        )

    def ingest(self, source_type: str) -> ConversationMemoryReport:
        """Extract and gate candidates for one conversation source type."""
        if source_type not in CONVERSATION_SOURCE_TYPES:
            raise ConversationSourceError(
                f"unsupported conversation source: {source_type!r}"
            )
        conversations = self._store.list_conversations(
            source_type=source_type,
            limit=self._max_conversations,
        )
        tally = OutcomeTally()
        user_messages = 0
        evidence_rows = 0
        for conversation in conversations:
            messages = self._store.list_messages(conversation.id)
            candidates = self._extractor.extract(conversation, messages)
            user_messages += sum(
                1
                for message in messages
                if (message.role or "").strip().lower() == "user"
            )
            for candidate in candidates:
                outcome = self._curator.curate(candidate)
                tally.add(outcome)
                if outcome.get("applied"):
                    evidence_rows += int(outcome.get("evidence_added", 0) or 0)
        return ConversationMemoryReport(
            source_type=source_type,
            conversations_scanned=len(conversations),
            user_messages_scanned=user_messages,
            tally=tally,
            evidence_rows_added=evidence_rows,
        )
