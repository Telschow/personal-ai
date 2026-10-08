"""Domain model for temporal events.

Temporal events are time-anchored facts about the user's activity (a
browser visit, a search, a watched video, a reviewed place). They are
first-class entities distinct from documents: they never get flattened
into document chunks or embeddings, and are retrieved structurally
through time-range and event-type filtering.

The schema is deliberately conservative and reusable across temporal
sources. Chrome is the first concrete source; ``channel_name`` and
``duration_seconds`` already exist for future sources such as YouTube
even though Chrome does not populate them.
"""

import hashlib
from dataclasses import dataclass, field

# Canonical event types emitted by source loaders.
EVENT_TYPE_URL_VISIT = "url_visit"
EVENT_TYPE_SEARCH_QUERY = "search_query"
EVENT_TYPE_VIDEO_WATCH = "video_watch"
EVENT_TYPE_YOUTUBE_SEARCH = "youtube_search"


def compute_event_id(
    source: str,
    event_type: str,
    event_time: str,
    url: str,
) -> str:
    """Derive a stable, deterministic identity for one event.

    Chrome timestamps carry microsecond precision, so the combination of
    ``source``, ``event_type``, ``event_time`` and ``url`` is unique per
    visit. Components are joined with NUL so field boundaries can never
    merge ambiguously. Re-ingesting the same unchanged event always yields
    the same ID, keeping ingestion idempotent.
    """
    if url is None:
        url = ""
    material = f"{source}\x00{event_type}\x00{event_time}\x00{url}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Event:
    """A single temporal event.

    ``id`` must come from :func:`compute_event_id`. ``event_time`` is the
    project's canonical UTC ISO-8601 representation. ``title``, ``url``,
    ``search_query``, ``channel_name`` and ``duration_seconds`` are nullable
    and only populated when the source provides them, so one table can hold
    heterogeneous event types. ``metadata`` preserves arbitrary source
    provenance without throwing information away.
    """

    id: str
    event_type: str
    event_time: str
    source: str
    title: str | None = None
    url: str | None = None
    search_query: str | None = None
    channel_name: str | None = None
    duration_seconds: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)
