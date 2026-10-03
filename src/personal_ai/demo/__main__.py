"""Command-line entry point for the architecture demonstration.

Run from the repository root::

    uv run python -m personal_ai.demo

This executes the real pipeline against the fixed synthetic corpus and writes
``artifacts/demo/results.json`` and ``artifacts/demo/metadata.json``. It needs
no network, no language model, and no local model. Both files are
byte-deterministic across runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from personal_ai.demo.artifacts import DEFAULT_ARTIFACT_DIR, write_artifacts


def main(argv: list[str] | None = None) -> int:
    """Generate the demonstration artifacts and print a short summary."""
    parser = argparse.ArgumentParser(
        prog="personal-ai-demo",
        description=(
            "Generate the deterministic, synthetic-only architecture demo artifacts."
        ),
    )
    parser.add_argument(
        "--artifact-dir",
        default=str(DEFAULT_ARTIFACT_DIR),
        help=f"Output directory relative to the repository root (default: {DEFAULT_ARTIFACT_DIR}).",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root the artifact directory is resolved against.",
    )
    args = parser.parse_args(argv)

    written = write_artifacts(args.artifact_dir, root=Path(args.root))

    import json

    results = json.loads(written["results"].read_text(encoding="utf-8"))
    stages = results["stages"]

    print("personal-ai architecture demo (synthetic, offline, deterministic)")
    print(f"  sources ingested : {stages['ingest']['documents_ingested']}")
    print(f"  chunks indexed   : {stages['ingest']['total_chunks']}")
    print(f"  retrieval hits   : {stages['retrieve']['hit_count']}")
    print(
        "  provenance       : "
        f"{'complete' if stages['evidence']['all_provenance_complete'] else 'INCOMPLETE'}"
    )
    print(f"  policy decision  : {stages['policy']['decision']}")
    print(
        "  approval boundary: "
        f"{'held' if stages['human_approval']['execution_blocked'] else 'NOT HELD'}"
    )
    print(f"  action performed : {stages['bounded_action']['action_performed']}")
    for label, path in written.items():
        print(f"  wrote {label:<9}: {path}")
    print("  snapshot        : artifacts/demo/snapshot.png (optional archify capture)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
