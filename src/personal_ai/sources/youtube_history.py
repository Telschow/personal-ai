"""YouTube history parsing: turns YouTube Takeout history HTML into Events.

YouTube's Takeout export does not provide history as JSON the way Chrome
does. The behavioral history lives in the ``historial`` directory as two
self-contained HTML pages:

- ``historial-de-reproducciones.html`` — "watch history"
- ``historial-de-búsqueda.html`` — "search history"

Both pages share the same markup: a sequence of MDL ``content-cell`` divs,
one per recorded action. Each record is distinguished by a leading verb
phrase rather than by which file it appears in:

- ``Has visto <a href="https://www.youtube.com/watch?v=...">TITLE</a>``
  (a video watch; ``watch?v=`` target).
- ``Buscaste <a href="https://www.youtube.com/results?search_query=...">``
  (a search).
- ``Has visto <a href="https://www.youtube.com/post/...">`` — a view of a
  community post, not a video; out of the two-type scope and skipped.
- ``Has visitado ...``, survey responses, and "ads seen on page" rows —
  advertising noise, skipped.

Every record carries a wall-clock timestamp with a Europe/Madrid
abbreviation, e.g. ``11 sept 2023, 22:18:26 CEST``. Timestamps are
normalized to the project's canonical UTC ISO-8601 representation using
the stated abbreviation's UTC offset (``CEST`` = +02:00, ``CET`` =
+01:00).

Repeated watches of the same video are legitimate distinct temporal events:
each carries its own timestamp, and the deterministic event ID
(:func:`~personal_ai.events.models.compute_event_id`) incorporates the
event time plus URL, so distinct watches never collide. Records duplicated
across both history pages (the same watch can appear in each file with an
identical timestamp) produce the same ID and are idempotently deduplicated
when persisted.
"""

import datetime as dt
import html as html_lib
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

from personal_ai.events.models import (
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
    Event,
    compute_event_id,
)

SOURCE_TYPE = "youtube"

# Marker that identifies a file as a YouTube history export (same MDL
# content-cell markup used by both watch and search history pages).
HISTORY_CELL_MARKER = "content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1"

# Leading verb phrases that disambiguate record kinds within a cell.
VERB_VIDEO_WATCH = "Has visto "
VERB_YOUTUBE_SEARCH = "Buscaste "

# Spanish month abbreviations observed in the corpus ("sept" = September).
_SPANISH_MONTHS = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sept": 9,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}

# UTC offsets (hours) for the Europe/Madrid timezone abbreviations used by
# the takeaways. Only CEST and CET are needed by the observed corpus.
_UTC_OFFSET_HOURS = {"CEST": 2, "CET": 1}

# <div class="content-cell ...body-1"> ... </div> — one behavior record.
_CELL_RE = re.compile(
    r'<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
    r"(.*?)"
    r"</div>",
    re.DOTALL,
)

_ANCHOR_RE = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)

# "11 sept 2023, 22:18:26 CEST" (Spanish locale wall-clock + abbreviation).
_TIMESTAMP_RE = re.compile(
    r"\b([0-9]{1,2}) ([a-z]{3,}) ([0-9]{4}), "
    r"([0-9]{1,2}):([0-9]{2}):([0-9]{2}) ([A-Z]{2,5})\s*$"
)


@dataclass(frozen=True, slots=True)
class YouTubeHistoryResult:
    """Outcome of parsing one YouTube history file.

    ``events`` lists the successfully produced events in deterministic
    order. ``skipped`` counts records that could not become events.
    ``skipped_reasons`` counts skipped records grouped by reason for
    reporting (ad/tracking visits, survey responses, ads seen on page, and
    views of YouTube community posts rather than videos).
    """

    events: tuple[Event, ...]
    skipped: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)


class YouTubeHistoryParseError(Exception):
    """Raised when a history export payload cannot be decoded at all."""


class YouTubeHistoryLoadError(Exception):
    """Raised when a history export file cannot be read."""


def _has_nbsp(value: str) -> str:
    return value.replace("\xa0", " ")


def _cell_text(cell: str) -> str:
    """Reduce one content-cell's HTML to normalized plain text."""
    return _has_nbsp(re.sub(r"<[^>]+>", " ", re.sub(r"\s+", " ", cell))).strip()


def youtube_timestamp_to_iso(
    day: int,
    month_name: str,
    year: int,
    hour: int,
    minute: int,
    second: int,
    tz: str,
) -> str | None:
    """Normalize a YouTube wall-clock timestamp to canonical UTC ISO-8601.

    Accepts the Spanish month abbreviations and timezone abbreviations used
    by the export. Returns ``None`` when the month or timezone is
    unrecognized, so malformed records can be skipped by callers.
    """
    month = _SPANISH_MONTHS.get(month_name.lower())
    offset_hours = _UTC_OFFSET_HOURS.get(tz.upper())
    if month is None or offset_hours is None:
        return None
    try:
        local = dt.datetime(year, month, day, hour, minute, second, tzinfo=dt.UTC)
    except ValueError:
        return None
    return (local - dt.timedelta(hours=offset_hours)).isoformat()


def is_history_export(payload: str) -> bool:
    """Report whether an HTML payload is a YouTube history export.

    History exports are recognized by their shared MDL content-cell markup.
    This structural check keeps discovery inside the source's own file
    shape while ignoring unrelated YouTube HTML.
    """
    return HISTORY_CELL_MARKER in payload


def extract_search_query(url: str) -> str | None:
    """Extract and decode the ``search_query`` of a YouTube results URL."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.netloc.lower() != "www.youtube.com":
        return None
    values = parse_qs(parsed.query, keep_blank_values=False).get("search_query")
    if not values:
        return None
    decoded = unquote(values[0]).strip()
    return decoded or None


def _video_id(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    values = parse_qs(parsed.query, keep_blank_values=False).get("v")
    if not values:
        return None
    return values[0].strip() or None


def _anchor_text(inner: str) -> str:
    text = html_lib.unescape(_has_nbsp(re.sub(r"<[^>]+>", "", inner)))
    collapsed = " ".join(text.split())
    return collapsed.strip()


def _channel_id(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "channel":
        return parts[1] or None
    return None


def _anchors(cell: str) -> list[tuple[str, str]]:
    """Return ``(href, text)`` pairs for every anchor in a cell, in order."""
    return [(href, _anchor_text(inner)) for href, inner in _ANCHOR_RE.findall(cell)]


def event_from_cell(
    cell: str, *, source: str = SOURCE_TYPE
) -> tuple[Event | None, str | None]:
    """Normalize one YouTube history content-cell into an Event.

    Returns ``(event, None)`` on success and ``(None, reason)`` when the
    record cannot become a ``video_watch`` or ``youtube_search`` event.
    """
    text = _cell_text(cell)
    if not text:
        return None, "empty_record"

    ts = _TIMESTAMP_RE.search(text)
    if ts is None:
        return None, "missing_or_invalid_timestamp"
    event_time = youtube_timestamp_to_iso(
        int(ts.group(1)),
        ts.group(2),
        int(ts.group(3)),
        int(ts.group(4)),
        int(ts.group(5)),
        int(ts.group(6)),
        ts.group(7),
    )
    if event_time is None:
        return None, "missing_or_invalid_timestamp"

    if not text.startswith(VERB_YOUTUBE_SEARCH) and not text.startswith(
        VERB_VIDEO_WATCH
    ):
        return None, _skip_reason(text)

    anchors = _anchors(cell)
    if not anchors:
        return None, "missing_url"

    if text.startswith(VERB_YOUTUBE_SEARCH):
        return _search_event(anchors[0], event_time, source)
    return _watch_event(anchors, event_time, source)


def _search_event(
    anchor: tuple[str, str], event_time: str, source: str
) -> tuple[Event | None, str | None]:
    url, _text = anchor
    query = extract_search_query(url)
    if query is None:
        return None, "missing_search_query"

    event_id = compute_event_id(source, EVENT_TYPE_YOUTUBE_SEARCH, event_time, url)
    return (
        Event(
            id=event_id,
            event_type=EVENT_TYPE_YOUTUBE_SEARCH,
            event_time=event_time,
            source=source,
            url=url,
            search_query=query,
        ),
        None,
    )


def _watch_event(
    anchors: list[tuple[str, str]], event_time: str, source: str
) -> tuple[Event | None, str | None]:
    url, _text = anchors[0]
    video_id = _video_id(url)
    if video_id is None:
        # "Has visto" pointing at a community post or non-video target.
        return None, "watched_non_video"

    title = _text or None
    if title and title == url:
        # Export falls back to the URL when no title was captured.
        title = None

    channel_url: str | None = None
    channel_name: str | None = None
    if len(anchors) > 1:
        channel_url, channel_name = anchors[1]
    channel_id = _channel_id(channel_url) if channel_url else None

    metadata: dict[str, object] = {"video_id": video_id}
    if channel_id:
        metadata["channel_id"] = channel_id

    event_id = compute_event_id(source, EVENT_TYPE_VIDEO_WATCH, event_time, url)
    return (
        Event(
            id=event_id,
            event_type=EVENT_TYPE_VIDEO_WATCH,
            event_time=event_time,
            source=source,
            title=title,
            url=url,
            channel_name=channel_name or None,
            metadata=metadata,
        ),
        None,
    )


def _skip_reason(text: str) -> str:
    if text.startswith("Has visitado"):
        return "ad_or_tracking_visit"
    if "Pregunta de encuesta" in text:
        return "survey_response"
    if "Anuncios vistos" in text:
        return "ads_seen_on_page"
    return "unsupported_entry"


def _parse_export(payload: str, *, source: str = SOURCE_TYPE) -> YouTubeHistoryResult:
    events: list[Event] = []
    skipped = 0
    reasons: dict[str, int] = {}

    for cell in _CELL_RE.findall(payload):
        event, reason = event_from_cell(cell, source=source)
        if event is None:
            skipped += 1
            reasons[reason or "unknown"] = reasons.get(reason or "unknown", 0) + 1
            continue
        events.append(event)

    # Deterministic order independent of source file ordering.
    events.sort(key=lambda event: (event.event_time, event.id))
    return YouTubeHistoryResult(
        events=tuple(events), skipped=skipped, skipped_reasons=reasons
    )


def parse_history_html(payload: bytes) -> object:
    """Decode a YouTube history export payload and detect its structure.

    Returns the decoded text for :func:`normalize_history_html`. Raises
    :class:`YouTubeHistoryParseError` if the payload is not decodable text.
    """
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = "YouTube history payload is not valid UTF-8 text"
        raise YouTubeHistoryParseError(msg) from exc


def normalize_history_html(
    payload: bytes, *, source: str = SOURCE_TYPE
) -> YouTubeHistoryResult:
    """Parse a full YouTube history export into events."""
    text = parse_history_html(payload)
    return _parse_export(text, source=source)
