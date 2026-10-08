"""CI gate for the project's existing mypy debt.

``mypy`` is run over the full ``src/personal_ai`` package. This project has
pre-existing type errors (see ``docs/typing.md``), so the gate does not simply
require a zero error count. Instead it compares the current set of errors
against a recorded baseline and fails when an error appears that is not in that
baseline.

Properties that matter:

* mypy really runs over the whole package; nothing is skipped or excluded.
* No error code is disabled, so this cannot mask a new problem.
* Fixing an error only ever shrinks the baseline.
* Introducing a new error fails CI.
* A change that moves an error to a different line does not fail CI, because
  signatures are line-number independent.

Usage::

    python scripts/mypy_ratchet.py            # gate: fail on new errors
    python scripts/mypy_ratchet.py --report   # print counts, never fail
    python scripts/mypy_ratchet.py --update   # accept current errors
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

BASELINE_PATH = Path("config/mypy-baseline.json")
ERROR_RE = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+): error: (?P<message>.*?)(?:  \[(?P<code>[a-z-]+)\])?$"
)


def count_signatures(raw: list[str]) -> dict[str, int]:
    """Collapse raw mypy lines into ``signature -> occurrence count``.

    Line numbers are deliberately dropped so that unrelated edits do not churn
    the baseline, but the occurrence count is kept so that adding a *new* copy
    of an already-recorded error is still detected.
    """
    counts: dict[str, int] = {}
    for signature in raw:
        counts[signature] = counts.get(signature, 0) + 1
    return counts


class MypyUnavailable(RuntimeError):
    """Raised when mypy could not be executed in the current interpreter."""


def run_mypy() -> tuple[list[str], int]:
    """Run mypy and return its error signatures plus the raw process exit code."""
    probe = subprocess.run(
        [sys.executable, "-c", "import mypy"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise MypyUnavailable(
            "mypy is not importable by this interpreter. Run this script inside the "
            "project environment, e.g. `uv run python scripts/mypy_ratchet.py`."
        )

    completed = subprocess.run(
        [sys.executable, "-m", "mypy"],
        capture_output=True,
        text=True,
        check=False,
    )
    raw_signatures: list[str] = []
    for line in completed.stdout.splitlines():
        match = ERROR_RE.match(line.strip())
        if not match:
            continue
        code = match.group("code") or "uncategorised"
        message = " ".join(match.group("message").split())
        raw_signatures.append(f"{match.group('path')}:{code}: {message}")

    # Guard against a silent pass: if mypy ran but we understood none of its
    # output, treat that as a failure rather than an empty error set.
    combined = completed.stdout + completed.stderr
    if completed.returncode not in (0, 1) or (
        "error:" not in combined and "Success" not in combined
    ):
        raise MypyUnavailable(
            f"could not interpret mypy output (exit {completed.returncode}):\n{combined.strip()[:2000]}"
        )

    return count_signatures(raw_signatures), completed.returncode


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return dict(data["errors"])


def save_baseline(counts: dict[str, int]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": (
            "Recorded pre-existing mypy errors for src/personal_ai. See docs/typing.md. "
            "Regenerate with `uv run python scripts/mypy_ratchet.py --update` after "
            "intentionally accepting new debt. Fixing errors should shrink this file."
        ),
        "total_errors": sum(counts.values()),
        "unique_signatures": len(counts),
        "errors": dict(sorted(counts.items())),
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite the baseline from the current run",
    )
    parser.add_argument(
        "--report", action="store_true", help="print counts only, never fail"
    )
    args = parser.parse_args()

    current: dict[str, int]
    returncode: int
    try:
        current, returncode = run_mypy()
    except MypyUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.update:
        save_baseline(current)
        print(
            f"Baseline updated: {sum(current.values())} error(s) across {len(current)} signature(s)."
        )
        return 0

    baseline = load_baseline()
    regressions: list[tuple[str, int, int]] = []
    for signature, count in current.items():
        allowed = baseline.get(signature, 0)
        if count > allowed:
            regressions.append((signature, allowed, count))

    resolved = sum(
        max(0, allowed - count)
        for signature, allowed in baseline.items()
        for count in [current.get(signature, 0)]
    )

    print(f"mypy exit code:     {returncode}")
    print(f"mypy errors:        {sum(current.values())}")
    print(f"baseline allows:    {sum(baseline.values())}")
    if resolved:
        print(
            f"resolved:           {resolved} (baseline is stale; re-run with --update)"
        )

    if args.report:
        return 0

    if regressions:
        print(f"\nFAILED: {len(regressions)} mypy regression(s) beyond the baseline:\n")
        for signature, allowed, count in sorted(regressions):
            print(f"  [{allowed} -> {count}] {signature}")
        print("\nFix these, or run with --update only if the new debt is intentional.")
        return 1

    print("\nOK: no new mypy errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
