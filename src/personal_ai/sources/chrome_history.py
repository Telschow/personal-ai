"""Chrome history parsing: turns raw Chrome export records into Events.

The Chrome history export (the ``Historial.json`` / ``History.json`` file
from a Google Takeout or manual export) is a JSON object keyed by
``"Browser History"`` holding a list of visit records. Each record carries
``title``, ``url``, ``time_usec`` (microseconds since the Unix epoch),
``page_transition_qualifier``, ``favicon_url`` and ``client_id``.

The parser normalizes each record into an :class:`~personal_ai.events.models.Event`
with:

- ``event_time`` set to the project's canonical UTC ISO-8601 representation.
- ``event_type`` classified as ``search_query`` for Google search URLs whose
  query can be extracted, otherwise ``url_visit``.
- ``search_query`` holding the decoded query for search events.
- ``metadata`` preserving the original provider fields (transition
  qualifier, favicon URL, client id) for provenance.

Malformed records (missing timestamps, non-numeric timestamps, missing or
empty URLs) are skipped rather than failing the whole import, and reported
through a structured result so callers can count them.
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
    Event,
    compute_event_id,
)

SOURCE_TYPE = "chrome_history"

# Chrome history export may use different keys depending on locale/format.
BROWSER_HISTORY_KEY = "Browser History"

# Google search endpoints. Netloc is compared case-insensitively and any
# port is stripped.
_GOOGLE_SEARCH_HOSTS = frozenset(
    {
        "google.com",
        "www.google.com",
        "google.de",
        "www.google.de",
        "google.co.uk",
        "www.google.co.uk",
        "google.fr",
        "www.google.fr",
        "google.es",
        "www.google.es",
        "google.it",
        "www.google.it",
    }
)


@dataclass(frozen=True, slots=True)
class ChromeHistoryResult:
    """Outcome of parsing one Chrome history file.

    ``events`` lists the successfully produced events in deterministic
    order. ``skipped`` counts records that could not be turned into events
    (missing or invalid timestamps, missing URLs). ``skipped_reasons``
    counts skipped records grouped by reason for reporting.
    """

    events: tuple[Event, ...]
    skipped: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)


class ChromeHistoryParseError(Exception):
    """Raised when a history export payload cannot be decoded at all."""


class ChromeHistoryLoadError(Exception):
    """Raised when a history export file cannot be read as JSON."""


def _cleaned(value: object) -> str:
    """Strip string fields; any other JSON value contributes nothing."""
    if isinstance(value, str):
        return value.strip()
    return ""


def _netloc_without_port(netloc: str) -> str:
    """Return the lowercased host of a parsed URL netloc, sans port."""
    host = netloc.rsplit(":", 1)[0] if ":" in netloc else netloc
    return host.lower()


def _is_google_search_url(url: str) -> bool:
    """Report whether a URL is a Google search page (``/search`` path).

    Google search pages share the ``/search`` path on a Google host. Other
    Google URLs (``/travel/...``, ``/mail/...``, ``/search?hl=`` without a
    query, Maps, etc.) are not treated as text searches.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (
        _netloc_without_port(parsed.netloc) in _GOOGLE_SEARCH_HOSTS
        and parsed.path.rstrip("/") == "/search"
    )


def extract_search_query(url: str) -> str | None:
    """Extract and decode the ``q`` query parameter of a search URL.

    Returns the URL-decoded query string, or ``None`` when the URL is not a
    Google search page or carries no usable ``q`` parameter. Only the first
    ``q`` value is used; empty queries yield ``None``.
    """
    if not _is_google_search_url(url):
        return None
    try:
        parsed = urlparse(url)
        values = parse_qs(parsed.query, keep_blank_values=False).get("q")
    except ValueError:
        return None
    if not values:
        return None
    raw = values[0]
    # parse_qs already decodes percent-escapes; some exports embed double
    # encoding, so unquote once more defensively.
    decoded = unquote(raw).strip()
    return decoded or None


def chrome_timestamp_to_iso(time_usec: object) -> str | None:
    """Convert a Chrome ``time_usec`` value to UTC ISO-8601.

    Chrome history records store microseconds since the Unix epoch. An
    absent, boolean, or non-numeric value is ``None``, so malformed records
    can be skipped by callers.
    """
    if isinstance(time_usec, bool) or not isinstance(time_usec, (int, float)):
        return None
    return dt.datetime.fromtimestamp(time_usec / 1_000_000, tz=dt.UTC).isoformat()


def event_from_record(
    record: dict[str, object],
    *,
    source: str = SOURCE_TYPE,
) -> Event | None:
    """Normalize one raw Chrome record into an Event, or None if malformed.

    A record without a usable URL or timestamp cannot become an event and
    yields ``None``. Otherwise the event's ``event_type`` is classified by
    the URL (search query vs. plain visit) and both the original provider
    fields and the raw timestamp are preserved in metadata for provenance.
    """
    if not isinstance(record, dict):
        return None

    url = _cleaned(record.get("url"))
    if not url:
        return None

    event_time = chrome_timestamp_to_iso(record.get("time_usec"))
    if event_time is None:
        return None

    title = _cleaned(record.get("title")) or None

    search_query = extract_search_query(url)
    if search_query is not None:
        event_type = EVENT_TYPE_SEARCH_QUERY
    else:
        event_type = EVENT_TYPE_URL_VISIT

    metadata: dict[str, object] = {}
    for key in ("page_transition_qualifier", "favicon_url", "client_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value

    event_id = compute_event_id(source, event_type, event_time, url)

    return Event(
        id=event_id,
        event_type=event_type,
        event_time=event_time,
        source=source,
        title=title,
        url=url,
        search_query=search_query,
        metadata=metadata,
    )


def parse_history_json(payload: bytes) -> object:
    """Decode a Chrome history export payload into raw Python data."""
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "Chrome history payload is not valid UTF-8 JSON"
        raise ChromeHistoryParseError(msg) from exc


def parse_history(payload: bytes, *, source: str = SOURCE_TYPE) -> ChromeHistoryResult:
    """Parse a full Chrome history export into events.

    Returns a :class:`ChromeHistoryResult` with produced events and a count
    of records skipped due to missing or malformed timestamps/URLs.
    """
    data = parse_history_json(payload)
    return normalize_history_data(data, source=source)


def normalize_history_data(
    data: object, *, source: str = SOURCE_TYPE
) -> ChromeHistoryResult:
    """Normalize already-decoded Chrome history data into events.

    ``data`` must be an object with a ``"Browser History"`` list. This
    separation lets callers decode once and reuse the classification
    logic. Malformed records are skipped and reported by reason.
    """
    if not isinstance(data, dict):
        return ChromeHistoryResult(events=(), skipped=0)

    records = data.get(BROWSER_HISTORY_KEY)
    if not isinstance(records, list):
        return ChromeHistoryResult(events=(), skipped=0)

    events: list[Event] = []
    skipped = 0
    reasons: dict[str, int] = {}

    for record in records:
        if not isinstance(record, dict):
            skipped += 1
            reasons["not_an_object"] = reasons.get("not_an_object", 0) + 1
            continue
        event = event_from_record(record, source=source)
        if event is None:
            skipped += 1
            reason = _skip_reason(record)
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        events.append(event)

    # Deterministic order independent of source file ordering.
    events.sort(key=lambda event: (event.event_time, event.id))
    return ChromeHistoryResult(
        events=tuple(events), skipped=skipped, skipped_reasons=reasons
    )


def _skip_reason(record: dict[str, object]) -> str:
    """Classify why a record produced no event."""
    if not isinstance(record.get("url"), str) or not record.get("url").strip():
        return "missing_url"
    return "missing_or_invalid_timestamp"
