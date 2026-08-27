"""Validate the literal event keyword filter against the real event corpus.

Ingests the Chrome + YouTube corpora through the normal ingestion APIs, then
exercises the literal ``keyword`` substring filter (title, url,
search_query, channel_name) across the structural query and every temporal
aggregation, against the real ~73K-event store.

It verifies that:

- keyword-filtered results are deterministic (running each twice yields
  identical output),
- a keyword never inflates counts (each filtered result is a subset of the
  unfiltered result),
- combining keyword with a time range still narrows correctly, and
- the permanent production database is never touched (in-memory by default;
  an explicit ``--database PATH`` is required to persist elsewhere).

The literal keyword is case-insensitive for ASCII and matched as an exact
substring (``%`` / ``_`` are literal). None of the candidate keywords need be
present in the real corpus: a keyword with no matching events reports zero
results instead of failing.
"""

import argparse
import time
from pathlib import Path

from personal_ai.event_ingestion import (
    ingest_chrome_history,
    ingest_youtube_history,
)
from personal_ai.retrieval import (
    ActivityBucketsRequest,
    ActivitySummaryRequest,
    ChannelTrendsRequest,
    EventQueryRequest,
    SearchTrendsRequest,
    VideoTrendsRequest,
    activity_by_bucket,
    activity_summary,
    query_events,
    top_channels,
    top_searches,
    top_videos,
)
from personal_ai.storage import EventStore, connect_database

_CHROME_DIR_REL = "previous_project_and_raw_data/Chrome"
_YOUTUBE_DIR_REL = "previous_project_and_raw_data/YouTube y YouTube Music/historial"

# Candidate keywords to probe. They need not all be present in the corpus; a
# keyword with zero matches reports a zero result rather than an error.
_CANDIDATE_KEYWORDS = ("career", "interview", "finance", "assessio")


def _corpus_dir(rel: str) -> Path:
    return (Path(__file__).resolve().parent.parent / rel).resolve()


def smoke_test() -> None:
    """Cheap structural check without the real corpus."""
    connection = connect_database(":memory:")
    try:
        store = EventStore(connection)
        assert query_events(store, EventQueryRequest(keyword="career")) == ()
        assert store.top_search_queries(keyword="career") == ()
    finally:
        connection.close()
    print("Smoke test passed.")


def _ingest(store: EventStore) -> None:
    """Ingest Chrome + YouTube corpora, printing timing."""
    for name, path, fn in (
        ("Chrome", _corpus_dir(_CHROME_DIR_REL), ingest_chrome_history),
        ("YouTube", _corpus_dir(_YOUTUBE_DIR_REL), ingest_youtube_history),
    ):
        if not path.is_dir():
            print(f"  {name} corpus not found at {path}; skipping")
            continue
        t_start = time.monotonic()
        summary = fn(path, store)
        print(
            f"  {name}: {summary.events_stored} events "
            f"({time.monotonic() - t_start:.1f}s)"
        )


def _run_deterministic(store: EventStore, keyword: str) -> None:
    """Run each keyword query twice and assert the results are deterministic."""
    pairs = [
        ("events", lambda: query_events(store, EventQueryRequest(keyword=keyword))),
        (
            "activity_summary",
            lambda: activity_summary(store, ActivitySummaryRequest(keyword=keyword)),
        ),
        (
            "top_searches",
            lambda: top_searches(store, SearchTrendsRequest(keyword=keyword)),
        ),
        (
            "top_channels",
            lambda: top_channels(store, ChannelTrendsRequest(keyword=keyword)),
        ),
        (
            "top_videos",
            lambda: top_videos(store, VideoTrendsRequest(keyword=keyword)),
        ),
        (
            "activity_by_bucket",
            lambda: activity_by_bucket(
                store, ActivityBucketsRequest(bucket="month", keyword=keyword)
            ),
        ),
    ]
    for name, fn in pairs:
        first = fn()
        second = fn()
        assert first == second, f"keyword='{keyword}' {name} not deterministic"


def _filtered_is_subset(store: EventStore) -> None:
    """A keyword-filtered aggregation must never exceed the unfiltered one."""
    full_searches = sum(r.count for r in top_searches(store, SearchTrendsRequest()))
    full_channels = sum(r.count for r in top_channels(store, ChannelTrendsRequest()))
    full_videos = sum(r.count for r in top_videos(store, VideoTrendsRequest()))

    for keyword in _CANDIDATE_KEYWORDS:
        kw_searches = sum(
            r.count for r in top_searches(store, SearchTrendsRequest(keyword=keyword))
        )
        kw_channels = sum(
            r.count
            for r in top_channels(store, ChannelTrendsRequest(keyword=keyword))
        )
        kw_videos = sum(
            r.count for r in top_videos(store, VideoTrendsRequest(keyword=keyword))
        )
        assert kw_searches <= full_searches, f"keyword '{keyword}' inflated searches"
        assert kw_channels <= full_channels, f"keyword '{keyword}' inflated channels"
        assert kw_videos <= full_videos, f"keyword '{keyword}' inflated videos"


def _time_range_check(store: EventStore) -> None:
    """Combining keyword with a time range still narrows correctly."""
    months = activity_by_bucket(store, ActivityBucketsRequest(bucket="month"))
    assert months, "expected at least one month bucket"
    target = months[0].bucket
    start, end = f"{target}-01T00:00:00+00:00", f"{target}-31T23:59:59+00:00"

    unfiltered = query_events(
        store, EventQueryRequest(start_time=start, end_time=end)
    )
    best = _CANDIDATE_KEYWORDS[0]
    filtered = query_events(
        store,
        EventQueryRequest(keyword=best, start_time=start, end_time=end),
    )
    assert len(filtered) <= len(unfiltered), (
        f"keyword + time-range inflated events "
        f"({len(filtered)} > {len(unfiltered)})"
    )
    print(
        f"  keyword+time-range '{best}' in {target}: "
        f"{len(filtered)} events (<= {len(unfiltered)} unfiltered)"
    )


def _print_keyword_results(store: EventStore) -> None:
    print("\n---- Keyword-filtered results ----")
    for keyword in _CANDIDATE_KEYWORDS:
        events = query_events(store, EventQueryRequest(keyword=keyword))
        searches = top_searches(store, SearchTrendsRequest(keyword=keyword))
        channels = top_channels(store, ChannelTrendsRequest(keyword=keyword))
        videos = top_videos(store, VideoTrendsRequest(keyword=keyword))
        print(f"\n  keyword '{keyword}': {len(events)} matching events")
        if searches:
            print("    top searches:")
            for r in searches[:5]:
                print(f"      {r.count:>5}  {r.query}")
        if channels:
            print("    top channels:")
            for r in channels[:5]:
                print(f"      {r.count:>5}  {r.channel_name}")
        if videos:
            print("    top videos:")
            for r in videos[:5]:
                print(f"      {r.count:>5}  {r.video_id}  {r.title or ''}")
        if not events and not searches and not channels and not videos:
            print("    (no results — keyword absent from this corpus)")


def run_validation(database_path: str | None) -> None:
    connection = connect_database(database_path or ":memory:")
    try:
        store = EventStore(connection)
        print("Temporal Keyword Validation")

        t_start = time.monotonic()
        _ingest(store)
        stored_total = store.count_events()
        print(
            f"\nTotal events ingested: {stored_total} "
            f"({time.monotonic() - t_start:.1f}s)"
        )

        for keyword in _CANDIDATE_KEYWORDS:
            _run_deterministic(store, keyword)
        _filtered_is_subset(store)
        _time_range_check(store)
        _print_keyword_results(store)

        print("\nTemporal keyword validation passed.")
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Run smoke test without the real corpus",
    )
    parser.add_argument(
        "--database",
        type=str,
        default=None,
        help=(
            "Optional path to persist events into (defaults to in-memory so "
            "the production database is never modified)."
        ),
    )
    args = parser.parse_args()
    if args.smoke_only:
        smoke_test()
    else:
        run_validation(args.database)


if __name__ == "__main__":
    main()
