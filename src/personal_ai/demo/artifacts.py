"""Artifact emission for the architecture demonstration.

Writes ``results.json`` and ``metadata.json`` for the run. Both artifacts are
byte-deterministic: no wall-clock time, no random identifier, and no host path
is recorded, so re-running the demo on any machine reproduces the same bytes.
Timestamps would make the artifact a lie about its own reproducibility.

The optional snapshot and video captures are produced by the archify tooling,
not here; this module only records where they belong and what they depict.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from personal_ai.demo.corpus import SYNTHETIC_MARKER
from personal_ai.demo.scenario import demo_stages, run_demo

#: Default artifact directory, relative to the repository root.
DEFAULT_ARTIFACT_DIR = Path("artifacts/demo")

RESULTS_FILENAME = "results.json"
METADATA_FILENAME = "metadata.json"
SNAPSHOT_FILENAME = "snapshot.png"

#: The real repository components the demonstration exercises. Recorded so a
#: reader can tell an executed trace from an illustration.
DEMONSTRATED_COMPONENTS = (
    "personal_ai.ingestion.DocumentIngestor",
    "personal_ai.documents.chunker.chunk_document",
    "personal_ai.storage.DocumentStore",
    "personal_ai.storage.ChunkStore",
    "personal_ai.storage.chunks.SQLiteChunkIndex",
    "personal_ai.retrieval.search_documents",
    "personal_ai.retrieval.build_retrieval_outcome",
    "personal_ai.agents.policy.PolicyEngine",
    "personal_ai.execution.models.plan_transition_from",
)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"


def build_metadata(results: dict[str, Any]) -> dict[str, Any]:
    """Describe the run truthfully, without inventing measurements."""
    stages = results.get("stages", {})
    return {
        "synthetic": True,
        "synthetic_marker": SYNTHETIC_MARKER,
        "purpose": (
            "Demonstrates the personal-ai architecture: private data, ingestion, "
            "retrieval, evidence and provenance, local reasoning, policy and "
            "permissions, human approval, bounded action."
        ),
        "is_benchmark": False,
        "benchmark_disclosure": (
            "This is an architecture demonstration, not a benchmark. Counts, "
            "identifiers, ranks, and policy decisions are read from an actual "
            "execution of the repository's own components. No accuracy, latency, "
            "or throughput number is claimed or estimated."
        ),
        "deterministic": True,
        "determinism_notes": [
            "Fixed synthetic corpus with fixed timestamps; no wall-clock reads.",
            "Keyword retrieval backend (SQLite FTS5 native BM25); no embeddings.",
            "Ranking ties break on chunk id, so ordering is stable.",
            "No language model and no network call is involved at any stage.",
        ],
        "requires_network": False,
        "requires_llm": False,
        "requires_local_model": False,
        "privacy": {
            "personal_data": False,
            "real_emails": False,
            "real_finances": False,
            "real_job_searches": False,
            "external_services_called": False,
            "secrets_exposed": False,
            "email_domains_used": "example.invalid (RFC 2606 reserved)",
        },
        "llm_invoked": bool(
            stages.get("local_reasoning", {}).get("llm_invoked", False)
        ),
        "demonstrated_components": list(DEMONSTRATED_COMPONENTS),
        "stages": list(demo_stages()),
        "action_performed": bool(
            stages.get("human_approval", {}).get("action_performed", False)
        ),
        "stops_at": "human approval boundary",
        "autonomy_claim": (
            "The system is not autonomous at the action boundary: the policy "
            "engine refuses the outbound action until a human approves it."
        ),
        "reasoning_disclosure": (
            "No chain-of-thought is produced or displayed. The reasoning stage "
            "shows only user-facing structured metadata and evidence citations."
        ),
        "snapshot": {
            "filename": SNAPSHOT_FILENAME,
            "generated_by": "archify deliver (optional, not required for tests)",
        },
        "tracked_in_git": False,
        "artifact_policy_note": (
            "Regenerate locally with `uv run python -m personal_ai.demo`. "
            "Generated binaries are not committed."
        ),
    }


def write_artifacts(
    artifact_dir: Path | str = DEFAULT_ARTIFACT_DIR,
    *,
    root: Path | str | None = None,
) -> dict[str, Path]:
    """Run the demo and write ``results.json`` and ``metadata.json``.

    Returns the mapping of written paths. The snapshot image is left to the
    optional archify capture step.
    """
    base = Path(root) if root is not None else Path.cwd()
    target = Path(artifact_dir)
    if not target.is_absolute():
        target = base / target
    target.mkdir(parents=True, exist_ok=True)

    result = run_demo()
    results = result.to_json()

    results_path = target / RESULTS_FILENAME
    results_path.write_text(_canonical_json(results), encoding="utf-8")

    metadata = build_metadata(results)
    metadata["results_sha256"] = hashlib.sha256(results_path.read_bytes()).hexdigest()
    metadata_path = target / METADATA_FILENAME
    metadata_path.write_text(_canonical_json(metadata), encoding="utf-8")

    return {"results": results_path, "metadata": metadata_path}


__all__ = [
    "DEFAULT_ARTIFACT_DIR",
    "DEMONSTRATED_COMPONENTS",
    "METADATA_FILENAME",
    "RESULTS_FILENAME",
    "SNAPSHOT_FILENAME",
    "build_metadata",
    "write_artifacts",
]
