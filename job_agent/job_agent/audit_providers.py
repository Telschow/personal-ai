"""Provider diagnostics - test live sources."""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "/mnt/immich/projects/personal-ai/job_agent")
from job_agent.config import load_config
from job_agent.discovery import build_sources


def test_providers():
    """Test each provider for connectivity."""
    cfg = load_config("config.yaml")
    sources = build_sources(cfg)

    print("=== PROVIDER DIAGNOSTICS ===\n")

    for src in sources:
        print(f"Source: {src.name}")
        print(f"  Type: {src.kind}")
        print(
            f"  Token/Board: {getattr(src, 'token', getattr(src, 'board', getattr(src, 'site', getattr(src, 'company', 'N/A'))))}"
        )

        # Try to fetch with timeout
        try:
            import signal

            def timeout_handler(signum, frame):
                raise TimeoutError("Fetch timed out")

            # Set 10 second timeout
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(10)

            start = time.time()
            jobs = src.fetch()
            elapsed = time.time() - start
            signal.alarm(0)

            print("  Status: SUCCESS")
            print(f"  Jobs fetched: {len(jobs)}")
            print(f"  Time: {elapsed:.2f}s")

        except TimeoutError:
            print("  Status: TIMEOUT")
            print("  Time: >10s")
        except Exception as e:
            print("  Status: ERROR")
            print(f"  Error: {str(e)[:100]}")

        print()


if __name__ == "__main__":
    test_providers()
