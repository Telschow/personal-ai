"""Fixed synthetic corpus for the architecture demonstration.

Every value in this module is invented for the demo. There is no personal
data, no maintainer content, no real address, no real organisation, and no
real job posting. Names use the reserved ``example.*`` domains from RFC 2606
so that nothing here can resolve to a real party.

The corpus is a module-level constant rather than a fixture file so that the
demo is hermetic: it needs no data directory, no network, and no model. The
synthetic marker below is asserted by the demo tests.
"""

from __future__ import annotations

from typing import Final

#: Marks every corpus string as synthetic. Asserted by the demo test suite and
#: echoed into the demo metadata so a reader can never mistake the scenario
#: for a real trace.
SYNTHETIC_MARKER: Final = "SYNTHETIC-DEMO-DATA"

#: Fixed timestamps. A demo must not read the wall clock, or the artifact
#: would differ on every run and stop being reproducible.
SYNTHETIC_TIMESTAMP: Final = "2024-01-01T00:00:00Z"

#: The synthetic project document the demo ingests. It is deliberately longer
#: than ``TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS`` so the real classifier
#: routes it down the chunking path.
SYNTHETIC_PROJECT_DOCUMENT: Final = f"""# Meridian Index — Architecture Note

{SYNTHETIC_MARKER}

## Summary

The Meridian Index is a synthetic retrieval service used by this demonstration.
It ranks passages with BM25 keyword scoring over a local SQLite FTS5 index.
Ranking is deterministic: the same corpus and the same query always produce the
same ordered result set, with no randomness and no model inference involved.

## Provenance

Every stored chunk keeps the identifier of the document it came from, the source
type, the stable source key, and the content hash of the original bytes. A result
can therefore always be traced back to the exact input that produced it.

## Rollout

Staging rollout is planned for week two. Production rollout follows in week five
after the retrieval quality review. The rollout checklist is owned by the
platform team and requires sign-off before any production change.

## Constraints

The index is read-only at query time and never opens its own database handle.
Embedding generation is deliberately out of scope for ingestion, so a fresh
install can search without any local model being available.
"""

#: The synthetic job posting the demo ingests alongside the project document.
SYNTHETIC_JOB_POSTING: Final = f"""# Synthetic Job Posting — Evidence Retrieval Engineer

{SYNTHETIC_MARKER}

Company: Example Systems (fictional employer)
Location: Remote
Contact: talent@example.invalid

## Role

We are looking for an engineer who can work on evidence provenance in a
retrieval system. The role involves local keyword retrieval, chunk-level
provenance tracking, and strict approval gates before any external action.

## Requirements

- Deterministic keyword retrieval with SQLite FTS5 and BM25 ranking.
- Provenance attached to every retrieved chunk, including document identity.
- A policy boundary that stops the agent before any outward-facing action.
- Explicit human approval before an action is executed.
"""

#: The query the demo issues at the retrieval stage. Its terms appear in both
#: synthetic documents, so the demo shows a real ranked result set rather than a
#: single hit. Ranking is FTS5 native BM25: a smaller rank is a better match.
SYNTHETIC_QUERY: Final = "deterministic keyword provenance"

#: The proposed action the policy layer must gate. This is a *request*, not a
#: capability: the demo stops at the approval boundary and never performs it.
PROPOSED_ACTION: Final = "send application email to the employer"

#: The source records the demo ingests, keyed by a stable label used in the
#: rendered stages and in the emitted results.
SYNTHETIC_SOURCES: Final = (
    ("project_document", "synthetic/meridian-index.md", SYNTHETIC_PROJECT_DOCUMENT),
    ("job_posting", "synthetic/job-posting.md", SYNTHETIC_JOB_POSTING),
)
