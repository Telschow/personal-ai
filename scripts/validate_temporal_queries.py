"""Validate temporal aggregation queries against the real event corpus.

Ingests the Chrome + YouTube corpora through the normal ingestion APIs, then
exercises the deterministic temporal aggregation operations (activity summary,
top searches, top channels, top videos, and UTC time buckets) against the real
~73K-event store.

It verifies that:

- query results are deterministic (running each twice yields identical output),
- counts are internally consistent (grouped counts sum to the stored total),
- time filtering narrows results as expected, and
- the permanent production database is never touched (in-memory by default;
  an explicit ``--database PATH`` is required to persist elsewhere).
"""

import argparse
import time
from pathlib import Path

from personal_ai.event_ingestion import (
    ingest_chrome_history,
    ingest_youtube_history,
)
from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_YOUTUBE_SEARCH,
)
from personal_ai.retrieval import (
    ActivityBucketsRequest,
    ActivitySummaryRequest,
    ChannelTrendsRequest,
    SearchTrendsRequest,
    VideoTrendsRequest,
    activity_by_bucket,
    activity_summary,
    top_channels,
    top_searches,
    top_videos,
)
from personal_ai.storage import EventStore, connect_database

_CHROME_DIR_REL = "previous_project_and_raw_data/Chrome"
_YOUTUBE_DIR_REL = "previous_project_and_raw_data/YouTube y YouTube Music/historial"

# Large enough to enumerate every distinct time bucket in your corpus.
_BUCKET_CAP = 2000


def _corpus_dir(rel: str) -> Path:
    return (Path(__file__).resolve().parent.parent / rel).resolve()


def smoke_test() -> None:
    """Cheap structural check without your own corpus."""
    connection = connect_database(":memory:")
    try:
        store = EventStore(connection)
        assert store.activity_summary() == ()
        assert store.top_search_queries() == ()
        assert store.top_channels() == ()
        assert store.top_videos() == ()
        assert store.activity_by_bucket() == ()
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


def _run_aggregations(store: EventStore) -> None:
    """Run each aggregation twice and assert the results are deterministic."""

    def snapshot(query_fn) -> tuple:
        return query_fn()

    pairs = [
        ("activity_summary", lambda: activity_summary(store, ActivitySummaryRequest())),
        (
            "top_chrome_searches",
            lambda: top_searches(
                store, SearchTrendsRequest(event_type=EVENT_TYPE_SEARCH_QUERY)
            ),
        ),
        (
            "top_youtube_searches",
            lambda: top_searches(
                store, SearchTrendsRequest(event_type=EVENT_TYPE_YOUTUBE_SEARCH)
            ),
        ),
        ("top_channels", lambda: top_channels(store, ChannelTrendsRequest(limit=5))),
        ("top_videos", lambda: top_videos(store, VideoTrendsRequest(limit=5))),
        (
            "activity_by_month",
            lambda: activity_by_bucket(
                store, ActivityBucketsRequest(bucket="month", limit=12)
            ),
        ),
    ]

    print("\nDeterminism check (each operation run twice):")
    for name, fn in pairs:
        first = snapshot(fn)
        second = snapshot(fn)
        assert first == second, f"{name} results are not deterministic"
        print(f"  {name}: OK (stable across runs)")


def _consistency_checks(store: EventStore, stored_total: int) -> None:
    """Verify grouped counts are internally consistent with the store total."""
    summary = activity_summary(store, ActivitySummaryRequest())
    grouped_total = sum(r.count for r in summary)
    assert grouped_total == stored_total, (
        f"activity_summary sums to {grouped_total}, store has {stored_total}"
    )

    buckets = activity_by_bucket(
        store, ActivityBucketsRequest(bucket="month", limit=_BUCKET_CAP)
    )
    bucket_total = sum(r.count for r in buckets)
    assert bucket_total == stored_total, (
        f"monthly buckets sum to {bucket_total}, store has {stored_total}"
    )

    channel_total = sum(r.count for r in top_channels(store, ChannelTrendsRequest()))
    assert channel_total <= stored_total

    video_total = sum(r.count for r in top_videos(store, VideoTrendsRequest()))
    assert video_total <= stored_total

    print(
        "  activity_summary sum",
        grouped_total,
        "== stored",
        stored_total,
        "- consistent",
    )
    print(
        "  monthly bucket sum",
        bucket_total,
        "== stored",
        stored_total,
        "- consistent",
    )


def _time_filter_check(store: EventStore) -> None:
    """Verify a representative time-range query narrows results correctly."""
    full = activity_summary(store, ActivitySummaryRequest())
    full_total = sum(r.count for r in full)

    # Pick a narrow month from your corpus and confirm the subset count
    # is strictly smaller and non-negative (and equals the monthly bucket).
    months = activity_by_bucket(store, ActivityBucketsRequest(bucket="month"))
    assert months, "expected at least one month bucket"
    target = months[0].bucket  # earliest month in the corpus
    narrowed = activity_summary(
        store,
        ActivitySummaryRequest(
            start_time=f"{target}-01T00:00:00+00:00",
            end_time=f"{target}-31T23:59:59+00:00",
        ),
    )
    narrowed_total = sum(r.count for r in narrowed)
    assert 0 <= narrowed_total < full_total, (
        f"time filtering did not narrow (full={full_total}, narrowed={narrowed_total})"
    )
    assert narrowed_total == months[0].count, (
        f"time filter count {narrowed_total} != monthly bucket "
        f"{months[0].count} for {target}"
    )
    print(
        f"  time-range filter on '{target}': {narrowed_total} events "
        f"({full_total} unfiltered) - filters correctly"
    )


def _print_results(store: EventStore) -> None:
    print("\n---- Representative aggregation results ----")

    print("\n1. Overall activity by source / event type:")
    for r in activity_summary(store, ActivitySummaryRequest()):
        print(f"   {r.source:>14} / {r.event_type:<14} {r.count}")

    print("\n2. Top Chrome searches:")
    for r in top_searches(
        store, SearchTrendsRequest(event_type=EVENT_TYPE_SEARCH_QUERY)
    ):
        print(f"   {r.count:>6}  {r.query}")

    print("\n3. Top YouTube searches:")
    for r in top_searches(
        store, SearchTrendsRequest(event_type=EVENT_TYPE_YOUTUBE_SEARCH)
    ):
        print(f"   {r.count:>6}  {r.query}")

    print("\n4. Top YouTube channels:")
    for r in top_channels(store, ChannelTrendsRequest(limit=5)):
        dur = (
            f"{r.total_duration_seconds:.0f}s"
            if r.total_duration_seconds is not None
            else "n/a"
        )
        print(f"   {r.count:>6}  {r.channel_name}  ({dur})")

    print("\n5. Top YouTube videos:")
    for r in top_videos(store, VideoTrendsRequest(limit=5)):
        print(f"   {r.count:>6}  {r.video_id}  {r.title or ''}")

    print("\n6. Monthly activity (UTC):")
    for r in activity_by_bucket(
        store, ActivityBucketsRequest(bucket="month", limit=12)
    ):
        print(f"   {r.bucket}: {r.count}")

    print("\n7. Representative time-range query (April 2026, by day):")
    daily = activity_by_bucket(
        store,
        ActivityBucketsRequest(
            bucket="day",
            start_time="2026-04-01T00:00:00+00:00",
            end_time="2026-04-30T23:59:59+00:00",
            limit=10,
        ),
    )
    for r in daily:
        print(f"   {r.bucket}: {r.count}")


def run_validation(database_path: str | None) -> None:
    connection = connect_database(database_path or ":memory:")
    try:
        store = EventStore(connection)
        print("Temporal Query Validation")

        t_start = time.monotonic()
        _ingest(store)
        stored_total = store.count_events()
        print(
            f"\nTotal events ingested: {stored_total} "
            f"({time.monotonic() - t_start:.1f}s)"
        )

        _consistency_checks(store, stored_total)
        _time_filter_check(store)
        _run_aggregations(store)
        _print_results(store)

        print("\nTemporal query validation passed.")
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Run smoke test without your own corpus",
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
