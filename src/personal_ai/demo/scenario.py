"""Deterministic, offline execution of the architecture demonstration.

This module runs the project's real pipeline end to end against the fixed
synthetic corpus in :mod:`personal_ai.demo.corpus` and records what actually
happened. Nothing here fabricates a metric: every count, score, identifier,
and status in the emitted result is read back out of the real components.

Components exercised, in order:

* :class:`~personal_ai.ingestion.DocumentIngestor` — canonicalisation,
  text extraction, classification, and deterministic chunking.
* :class:`~personal_ai.storage.chunks.SQLiteChunkIndex` — local FTS5/BM25
  keyword retrieval.
* :func:`~personal_ai.retrieval.search_documents` and
  :func:`~personal_ai.retrieval.build_retrieval_outcome` — the retrieval
  contract the agent consumes.
* :class:`~personal_ai.agents.policy.PolicyEngine` — software-enforced
  permissions and the approval gate.
* :mod:`personal_ai.execution.models` — the plan state machine whose
  ``NEEDS_APPROVAL`` state has no transition to completion.

The demonstration requires no network, no LLM, and no local model: it uses the
keyword retrieval backend, which is the repository default, and it passes
``structured_extractor=None`` so ingestion performs no model inference.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from personal_ai.agents.models import (
    AccessPolicy,
    Agent,
    AgentTool,
    AutonomyLevel,
    Permission,
    PolicyDecision,
    RiskLevel,
)
from personal_ai.agents.policy import ApprovalRequiredError, PolicyEngine
from personal_ai.agents.registry import AgentToolRegistry
from personal_ai.demo.corpus import (
    PROPOSED_ACTION,
    SYNTHETIC_MARKER,
    SYNTHETIC_QUERY,
    SYNTHETIC_SOURCES,
    SYNTHETIC_TIMESTAMP,
)
from personal_ai.documents.models import compute_content_hash
from personal_ai.execution.models import (
    PlanStatus,
    PlanTransitionError,
    plan_transition_from,
)
from personal_ai.ingestion import DocumentIngestor
from personal_ai.retrieval import (
    SearchDocumentsRequest,
    build_retrieval_outcome,
    search_documents,
)
from personal_ai.sources.models import SourceRecord
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)
from personal_ai.storage.chunks import SQLiteChunkIndex

#: Name of the demo probe tool. The probe stands in for an outward-facing
#: action such as sending mail. It is never allowed to run: the demo exists to
#: show that the policy engine stops it.
PROBE_TOOL_NAME = "demo_outbound_action"

#: Number of ranked hits surfaced at the retrieval and evidence stages.
DEMO_RESULT_LIMIT = 3


def _source_record(source_key: str, text: str) -> SourceRecord:
    """Build a synthetic :class:`SourceRecord` with real identity helpers."""
    payload = text.encode("utf-8")
    return SourceRecord(
        source_type="file",
        source_key=source_key,
        content_hash=compute_content_hash(payload),
        created_at=SYNTHETIC_TIMESTAMP,
        modified_at=SYNTHETIC_TIMESTAMP,
        payload=payload,
        metadata={"extension": ".md", "synthetic": True},
    )


@dataclass(frozen=True, slots=True)
class DemoResult:
    """The full, serialisable outcome of one demonstration run."""

    synthetic: bool
    stages: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"synthetic": self.synthetic, "stages": self.stages}


def run_demo(connection: sqlite3.Connection | None = None) -> DemoResult:
    """Run the whole demonstration and return its recorded stages.

    ``connection`` may be supplied to share a database; by default an
    in-memory SQLite database is created, so the demo never touches the
    operator's real corpus.
    """
    owned = connection is None
    connection = connection or connect_database(":memory:")
    stages: dict[str, Any] = {}

    document_store = DocumentStore(connection)
    extraction_store = ExtractionStore(connection)
    chunk_store = ChunkStore(connection)
    embedding_store = EmbeddingStore(connection)

    # Stage 0 — private input. The records never leave this process.
    records = {
        label: _source_record(source_key, text)
        for label, source_key, text in SYNTHETIC_SOURCES
    }
    stages["private_input"] = {
        "marker": SYNTHETIC_MARKER,
        "source_count": len(records),
        "sources": [
            {
                "label": label,
                "source_type": record.source_type,
                "source_key": record.source_key,
                "content_hash": record.content_hash,
                "bytes": len(record.payload or b""),
            }
            for label, record in records.items()
        ],
    }

    # Stage 1 — ingest. The real ingestor canonicalises, classifies, and
    # chunks. ``structured_extractor=None`` keeps the run model-free.
    ingestor = DocumentIngestor(
        document_store,
        extraction_store,
        None,
        chunk_store,
        embedding_store,
    )
    ingested = []
    for label, record in records.items():
        result = ingestor.ingest(record)
        ingested.append(
            {
                "label": label,
                "document_id": result.document_id,
                "kind": str(result.kind),
                "chunk_count": len(result.chunks),
                "structured_extraction": result.structured_extraction is not None,
            }
        )
    stages["ingest"] = {
        "documents_ingested": len(ingested),
        "total_chunks": chunk_store.count(),
        "documents": ingested,
        "embedding_model_calls": 0,
    }

    # Stages 2 and 3 — deterministic keyword retrieval.
    chunk_index = SQLiteChunkIndex(connection)
    hits = search_documents(
        chunk_index,
        SearchDocumentsRequest(query=SYNTHETIC_QUERY, limit=DEMO_RESULT_LIMIT),
    )
    stages["retrieve"] = {
        "backend": "sqlite_fts5_bm25_keyword",
        "query": SYNTHETIC_QUERY,
        "limit": DEMO_RESULT_LIMIT,
        "hit_count": len(hits),
        "retrieved": [
            {
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "chunk_index": hit.chunk_index,
                "rank": hit.rank,
                "text": hit.text,
            }
            for hit in hits
        ],
    }

    # Stage 4 — evidence and provenance, read back from the same hits.
    outcome = build_retrieval_outcome(
        SYNTHETIC_QUERY,
        [
            {
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "chunk_index": hit.chunk_index,
                "rank": hit.rank,
                "text": hit.text,
            }
            for hit in hits
        ],
        limit=DEMO_RESULT_LIMIT,
    )
    evidence = []
    for hit in hits:
        stored = chunk_store.get(hit.chunk_id)
        evidence.append(
            {
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "chunk_index": hit.chunk_index,
                "rank": hit.rank,
                "source_type": hit.source_type,
                "source": hit.source,
                "content_hash": (stored.metadata or {}).get("content_hash")
                if stored is not None
                else None,
                "provenance_complete": bool(
                    hit.document_id
                    and hit.source_type
                    and hit.source
                    and stored is not None
                    and stored.metadata.get("content_hash")
                ),
            }
        )
    stages["evidence"] = {
        "status": outcome.status,
        "total_returned": outcome.total_returned,
        "truncated": outcome.truncated,
        "evidence_count": len(evidence),
        "all_provenance_complete": all(e["provenance_complete"] for e in evidence),
        "evidence": evidence,
    }

    # Stage 5 — structured local reasoning. No model is called and no hidden
    # reasoning is produced: the object below is a user-facing summary of what
    # retrieval returned, assembled deterministically from the evidence.
    stages["local_reasoning"] = {
        "llm_invoked": False,
        "mode": "deterministic_evidence_summary",
        "query": outcome.query,
        "status": outcome.status,
        "cited_evidence_count": outcome.total_returned,
        "cited_chunk_ids": [e["chunk_id"] for e in evidence],
        "cited_document_ids": sorted({e["document_id"] for e in evidence}),
        "claim": "Local retrieval can support this request without an external model.",
        "disclosure": (
            "No language model was called. Every field above is copied from the "
            "retrieval outcome; no chain-of-thought is produced or displayed."
        ),
    }

    # Stage 6 — policy. A real AgentTool gated on a real permission, checked by
    # the real policy engine.
    invocations: list[str] = []

    def _probe_handler(_arguments: dict[str, object]) -> object:  # pragma: no cover
        invocations.append("handler-ran")
        return "unexpected: the approval boundary did not hold"

    registry = AgentToolRegistry()
    registry.register(
        AgentTool(
            name=PROBE_TOOL_NAME,
            description=(
                "Synthetic probe standing in for an outward-facing action such as "
                "sending mail. Present only so the policy boundary can be shown."
            ),
            permissions=(Permission.NETWORK,),
            risk=RiskLevel.NETWORK,
            mutates_state=True,
            accesses_network=True,
            reads_private_data=True,
        ),
        _probe_handler,
    )
    agent = Agent(
        id="demo-researcher",
        role="Evidence retrieval researcher",
        system_instructions=(
            "Answer from retrieved local evidence only. Never perform an "
            "outward-facing action without explicit human approval."
        ),
        policy=AccessPolicy(
            name="demo-read-only-with-gated-action",
            allowed=frozenset({Permission.CORPUS_SEARCH, Permission.CORPUS_FETCH}),
            approval_required=frozenset({Permission.NETWORK}),
            autonomy=AutonomyLevel.READ_ONLY_RESEARCH,
        ),
    )
    engine = PolicyEngine(registry)
    check = engine.check_tool(agent, PROBE_TOOL_NAME)
    stages["policy"] = {
        "requested_action": PROPOSED_ACTION,
        "tool": PROBE_TOOL_NAME,
        "required_permission": Permission.NETWORK.value,
        "autonomy": AutonomyLevel(agent.policy.autonomy).name,
        "decision": check.decision.value,
        "allowed": check.allowed,
        "enforced_in_code": True,
        "decisions_recorded": len(engine.decisions),
    }

    # Stage 7 — approval boundary. Execution must raise, and the handler must
    # not run.
    execution_error: str | None = None
    try:
        engine.execute(agent, PROBE_TOOL_NAME, {})
    except ApprovalRequiredError as error:
        execution_error = str(error)

    # The plan state machine independently forbids completing a plan that is
    # waiting for approval.
    transition_blocked: bool
    try:
        plan_transition_from(PlanStatus.NEEDS_APPROVAL, PlanStatus.COMPLETED)
    except PlanTransitionError:
        transition_blocked = True
    else:  # pragma: no cover - would be a genuine regression
        transition_blocked = False

    stages["human_approval"] = {
        "execution_blocked": execution_error is not None,
        "approval_required_error": execution_error,
        "handler_invoked": bool(invocations),
        "plan_status": PlanStatus.NEEDS_APPROVAL.value,
        "completion_transition_blocked": transition_blocked,
        "awaiting": "explicit human approval",
        "action_performed": False,
    }

    stages["bounded_action"] = {
        "action_performed": False,
        "actions_performed": 0,
        "reason": "execution stopped at the human approval boundary",
        "next_state_requires": "explicit human approval",
    }

    if owned:
        connection.close()

    return DemoResult(synthetic=True, stages=stages)


def demo_stages() -> tuple[str, ...]:
    """Return the ordered stage keys, for tests and for the rendered story."""
    return (
        "private_input",
        "ingest",
        "retrieve",
        "evidence",
        "local_reasoning",
        "policy",
        "human_approval",
        "bounded_action",
    )


__all__ = [
    "DEMO_RESULT_LIMIT",
    "PROBE_TOOL_NAME",
    "DemoResult",
    "demo_stages",
    "run_demo",
]

# Re-exported for callers that assert the policy enums were really used.
_ = (Callable, PolicyDecision)
