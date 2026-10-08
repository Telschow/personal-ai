"""Validate Chrome history ingestion into the personal AI system.

Runs the same ingestion path production uses (:func:`ingest_chrome_history`)
against the real Chrome corpus, then independently validates the resulting
EventStore and verifies idempotency. By default everything runs in memory so
the permanent production database is never modified; pass ``--database PATH``
to persist explicitly.
"""

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

from personal_ai.event_ingestion import (
    ChromeHistoryIngestionSummary,
    ingest_chrome_history,
)
from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
)
from personal_ai.sources.chrome_history_loader import ChromeHistoryLoader
from personal_ai.storage import EventStore, connect_database
from personal_ai.storage.events import EventQuery

_CHROME_DIR_REL = "previous_project_and_raw_data/Chrome"


def _chrome_dir() -> Path:
    """Resolve the Chrome corpus relative to the repository root."""
    return (Path(__file__).resolve().parent.parent / _CHROME_DIR_REL).resolve()


def smoke_test() -> None:
    """Quick structural validation without your own corpus."""
    connection = connect_database(":memory:")
    try:
        store = EventStore(connection)
        assert store.count_events() == 0
    finally:
        connection.close()
    print("Smoke test passed.")


def _fetch_stored(store: EventStore, total_events: int) -> tuple:
    """Return all stored events from the EventStore."""
    return store.search(EventQuery(limit=max(total_events, 1_000_000)))


def _report_and_validate(
    store: EventStore, summary: ChromeHistoryIngestionSummary
) -> int:
    """Report the ingestion summary and independently validate the store.

    Queries the EventStore rather than trusting only the ingestion counters,
    verifying stored count, absence of duplicate IDs, event types, and the
    canonical UTC timestamp representation. Returns the stored event count.
    """
    total_events = summary.events_stored

    print(f"\nIngestion results ({summary.files_discovered} file(s)):")
    print(f"  records discovered:   {summary.records_discovered}")
    print(f"  events stored:        {summary.events_stored}")
    print(f"  url_visit events:     {summary.url_visits}")
    print(f"  search_query events:  {summary.search_queries}")
    print(f"  skipped/malformed:    {summary.skipped}")

    if summary.skipped_reasons:
        print("  skip reasons:")
        for reason, count in sorted(summary.skipped_reasons.items()):
            print(f"    - {reason}: {count}")

    stored = _fetch_stored(store, total_events)

    # 1. Stored event count matches what ingestion reported.
    assert len(stored) == total_events, (
        f"Stored {len(stored)} events but ingestion reported {total_events}"
    )

    # 2. No duplicate event IDs.
    ids = [event.id for event in stored]
    assert len(ids) == len(set(ids)), "Duplicate event IDs found!"

    # 3. Event types are exactly the two supported kinds and counts agree.
    type_counts = {
        EVENT_TYPE_URL_VISIT: sum(
            1 for e in stored if e.event_type == EVENT_TYPE_URL_VISIT
        ),
        EVENT_TYPE_SEARCH_QUERY: sum(
            1 for e in stored if e.event_type == EVENT_TYPE_SEARCH_QUERY
        ),
    }
    assert type_counts[EVENT_TYPE_URL_VISIT] == summary.url_visits
    assert type_counts[EVENT_TYPE_SEARCH_QUERY] == summary.search_queries

    # 4. Timestamps are valid, parseable, timezone-aware, and canonical UTC.
    min_time = None
    max_time = None
    for event in stored:
        parsed = dt.datetime.fromisoformat(event.event_time)
        assert parsed.tzinfo is not None, f"Non-UTC timestamp on {event.id}"
        # Canonical project representation is UTC with zero offset.
        assert parsed.utcoffset() == dt.timedelta(0), (
            f"Non-zero UTC offset on {event.id}: {event.event_time}"
        )
        if min_time is None or event.event_time < min_time:
            min_time = event.event_time
        if max_time is None or event.event_time > max_time:
            max_time = event.event_time

    print(f"  timestamp range:      {min_time} .. {max_time}")
    print("\n  Integrity:")
    print("    no duplicate event IDs")
    print("    event types match reported counts")
    print("    all timestamps valid, aware UTC (+00:00)")

    return total_events


def run_validation(database_path: str | None) -> None:
    """Full validation: ingest real Chrome corpus, verify, check idempotency."""
    chrome_dir = _chrome_dir()
    if not chrome_dir.is_dir():
        print(f"Chrome history directory not found: {chrome_dir}")
        sys.exit(1)

    # In-memory by default so the permanent production database is never
    # touched; an explicit --database opt-in is required to persist.
    connection = connect_database(database_path or ":memory:")
    try:
        store = EventStore(connection)

        # Confirm the loader discovers history files (and report count).
        discovered = ChromeHistoryLoader(chrome_dir).discover_files()
        print("Chrome History Validation")
        print(f"  history files discovered: {len(discovered)}")
        for path in discovered:
            print(f"    {path.relative_to(chrome_dir)}")

        # System under test: the same orchestration production uses.
        print("\nIngesting Chrome history...")
        t_start = time.monotonic()
        summary = ingest_chrome_history(chrome_dir, store)
        elapsed = time.monotonic() - t_start
        print(f"  (ingestion completed in {elapsed:.1f}s)")

        total_events = _report_and_validate(store, summary)

        # Idempotency: run ingestion a second time; nothing new.
        print("\nIdempotency check...")
        summary2 = ingest_chrome_history(chrome_dir, store)
        assert summary2.events_stored == 0, (
            f"Second ingestion inserted {summary2.events_stored} new events"
        )
        assert store.count_events() == total_events
        print(f"  first ingestion: {total_events} events")
        print(f"  second ingestion: {summary2.events_stored} new events")
        print("  Idempotency verified!")
    finally:
        connection.close()

    print("\nValidation passed.")


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
