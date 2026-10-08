"""Orchestration for ingesting temporal events into EventStore.

This module bridges event loaders (which read source exports and produce
typed :class:`~personal_ai.events.models.Event` domain objects) with the
:class:`~personal_ai.storage.events.EventStore` (which persists them). It
mirrors the conversation-ingestion orchestration: source loading and
persistence are separate, and re-running ingestion on the same unchanged
source produces the same event count via the store's idempotent upsert.

Idempotency is derived from deterministic event identity
(:func:`~personal_ai.events.models.compute_event_id`): the same source
event always maps to the same event ID, so a second ingestion inserts no
new rows.
"""

from dataclasses import dataclass, field
from pathlib import Path

from personal_ai.events.models import (
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
)
from personal_ai.sources.chrome_history import SOURCE_TYPE
from personal_ai.sources.chrome_history_loader import ChromeHistoryLoader
from personal_ai.sources.youtube_history import SOURCE_TYPE as YOUTUBE_SOURCE_TYPE
from personal_ai.sources.youtube_history_loader import YouTubeHistoryLoader
from personal_ai.storage.events import EventStore


@dataclass(frozen=True, slots=True)
class ChromeHistoryIngestionSummary:
    """Outcome of ingesting a Chrome history export directory.

    ``records_discovered`` counts every record encountered (both those that
    became events and those skipped as malformed). ``skipped_reasons``
    groups skipped records by reason (e.g. ``missing_url``,
    ``missing_or_invalid_timestamp``) so diagnostics do not require the
    caller to re-parse the source.
    """

    files_discovered: int
    records_discovered: int
    events_stored: int
    search_queries: int
    url_visits: int
    skipped: int
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    source_type: str = SOURCE_TYPE


def ingest_chrome_history(
    directory: Path, store: EventStore
) -> ChromeHistoryIngestionSummary:
    """Load and persist all Chrome history events from a directory.

    Returns a summary counting discovered files, produced events, search
    queries extracted, plain URL visits, and records skipped as malformed
    (grouped by reason). Running it twice on the same unchanged directory
    inserts no new events.
    """
    loader = ChromeHistoryLoader(directory)
    files = loader.discover_files()

    events_stored = 0
    records_discovered = 0
    search_queries = 0
    url_visits = 0
    skipped = 0
    reasons: dict[str, int] = {}

    for path in files:
        result = loader.load_file_result(path)
        records_discovered += len(result.events) + result.skipped
        skipped += result.skipped
        for reason, count in result.skipped_reasons.items():
            reasons[reason] = reasons.get(reason, 0) + count
        if result.events:
            events_stored += store.save_events(tuple(result.events))
            for event in result.events:
                if event.event_type == "search_query":
                    search_queries += 1
                elif event.event_type == "url_visit":
                    url_visits += 1

    return ChromeHistoryIngestionSummary(
        files_discovered=len(files),
        records_discovered=records_discovered,
        events_stored=events_stored,
        search_queries=search_queries,
        url_visits=url_visits,
        skipped=skipped,
        skipped_reasons=reasons,
    )


@dataclass(frozen=True, slots=True)
class YouTubeHistoryIngestionSummary:
    """Outcome of ingesting a YouTube history export directory.

    ``records_discovered`` counts every behavioral record encountered
    (both those that became events and those skipped as out-of-scope or
    malformed). ``skipped_reasons`` groups skipped records by reason
    (e.g. ``ad_or_tracking_visit``, ``survey_response``,
    ``watched_non_video``) so diagnostics do not require the caller to
    re-parse the source.
    """

    files_discovered: int
    records_discovered: int
    events_stored: int
    video_watches: int
    youtube_searches: int
    skipped: int
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    source_type: str = YOUTUBE_SOURCE_TYPE


def ingest_youtube_history(
    directory: Path, store: EventStore
) -> YouTubeHistoryIngestionSummary:
    """Load and persist all YouTube history events from a directory.

    Returns a summary counting discovered files, produced events, video
    watches, searches, and records skipped as out-of-scope or malformed
    (grouped by reason). Running it twice on the same unchanged directory
    inserts no new events.
    """
    loader = YouTubeHistoryLoader(directory)
    files = loader.discover_files()

    events_stored = 0
    records_discovered = 0
    video_watches = 0
    youtube_searches = 0
    skipped = 0
    reasons: dict[str, int] = {}

    for path in files:
        result = loader.load_file_result(path)
        records_discovered += len(result.events) + result.skipped
        skipped += result.skipped
        for reason, count in result.skipped_reasons.items():
            reasons[reason] = reasons.get(reason, 0) + count
        if result.events:
            events_stored += store.save_events(tuple(result.events))
            for event in result.events:
                if event.event_type == EVENT_TYPE_VIDEO_WATCH:
                    video_watches += 1
                elif event.event_type == EVENT_TYPE_YOUTUBE_SEARCH:
                    youtube_searches += 1

    return YouTubeHistoryIngestionSummary(
        files_discovered=len(files),
        records_discovered=records_discovered,
        events_stored=events_stored,
        video_watches=video_watches,
        youtube_searches=youtube_searches,
        skipped=skipped,
        skipped_reasons=reasons,
    )
