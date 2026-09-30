"""Thread isolation regression tests for SQLite connection lifecycle."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from job_agent.gui.services import CareerService


def test_service_no_persistent_connection():
    """Verify service does not retain a persistent SQLite connection."""
    service = CareerService(config_path="config.yaml")
    assert not hasattr(service, "_conn") or service._conn is None, (
        "CareerService must not hold a persistent SQLite connection"
    )


def test_service_db_path_resolvable():
    """Verify service can resolve database path without creating connection."""
    service = CareerService(config_path="config.yaml")
    db_path = service._get_db_path()
    assert db_path is not None
    assert len(db_path) > 0


def test_service_operations_work_in_thread():
    """Verify service operations work in a separate thread."""
    service = CareerService(config_path="config.yaml")

    def worker():
        return service.count_jobs()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker)
        count = future.result(timeout=10)

    assert count > 0


def test_service_operations_work_across_threads():
    """Verify service operations work correctly when called from different threads."""
    service = CareerService(config_path="config.yaml")

    errors = []

    def worker():
        try:
            service.count_jobs()
            service.get_jobs(limit=5)
        except Exception as e:
            errors.append(e)

    threads = []
    for _ in range(5):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=10)

    assert len(errors) == 0, f"Errors in threads: {errors}"


def test_service_db_context_manager():
    """Verify the _db() context manager creates independent connections."""
    service = CareerService(config_path="config.yaml")

    conn1 = None
    conn2 = None

    with service._db() as conn:
        conn1 = conn
        assert isinstance(conn1, sqlite3.Connection)

    with service._db() as conn:
        conn2 = conn
        assert isinstance(conn2, sqlite3.Connection)

    # Connections should be different objects (not cached)
    assert conn1 is not conn2, "Each _db() context should create an independent connection"


def test_service_latest_discovery_run_thread_safe():
    """Verify latest_discovery_run works from multiple threads."""
    service = CareerService(config_path="config.yaml")

    errors = []
    results = []

    def worker():
        try:
            run = service.get_latest_discovery_run()
            results.append(run)
        except Exception as e:
            errors.append(e)

    threads = []
    for _ in range(3):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=10)

    assert len(errors) == 0, f"Errors in threads: {errors}"
    assert len(results) == 3


def test_service_count_jobs_thread_safe():
    """Verify count_jobs works from multiple threads."""
    service = CareerService(config_path="config.yaml")

    errors = []
    results = []

    def worker():
        try:
            count = service.count_jobs()
            results.append(count)
        except Exception as e:
            errors.append(e)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker) for _ in range(4)]
        for f in futures:
            f.result(timeout=10)

    assert len(errors) == 0, f"Errors in threads: {errors}"
    assert len(results) == 4
    # All results should be the same
    assert len(set(results)) == 1, f"Different counts: {results}"


if __name__ == "__main__":
    test_service_no_persistent_connection()
    test_service_db_path_resolvable()
    test_service_operations_work_in_thread()
    test_service_operations_work_across_threads()
    test_service_db_context_manager()
    test_service_latest_discovery_run_thread_safe()
    test_service_count_jobs_thread_safe()
    print("All thread isolation tests passed!")
