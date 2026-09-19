# Architecture

## Layering

```
config.yaml ──► Config (typed pydantic, validated)
                    │
discovery ──────────┤
  build_sources     │
  discovery_search  │  WebSearchDiscovery
                    ▼
              sources / sitemap
                    │            fetch() → list[Job]
                    ▼
              normalizer  canonical  salary
                    │            normalize_job()
                    ▼
                   db.py  (SQLite migrations v1..v2)
                    │            upsert_job / dedup / lifecycle
                    ▼
              scoring  .......... score(job, profile, policy) → Score
                    │
                    ├── pipeline.run_sources   (per-source isolation)
                    ├── pipeline.ingest_global_jobs
                    ├── pipeline.run_lifecycle
                    │
                    ▼
                 report.write_digest  → output/reports/daily-YYYY-MM-DD.md
                    │
                    ▼
                  cli.py (subcommands)
```

Dependency direction is strictly downward: `cli` orchestrates, modules never
import `cli`; sources never touch the database or config; `db` never imports
sources or scoring. No module performs I/O unless it is the module's stated
responsibility.

## Career fit layer (Slice 2)

```
cli.cmd_fit ─────► career.retrieval.build_retrieval_plan / collect_evidence / compact_evidence
                       │                                          ▲
                       ▼                                          │ CareerEvidence
              career.knowledge (CareerKnowledge seam)             │  (id, claim, level, categories,
                       │                                          │   keywords, confidence)
                       │  "personal_ai" (read-only adapter)       │  levels: verified > documented
                       │  "none" (NullCareerKnowledge)            │          > inferred > candidate
                       ▼                                          │  never auto-promoted
     career.evidence.build_profile_evidence ──────────────────────┤
                       │                                          │
                       ▼                                          │
              career.fit.analyze_fit                              │
                       │  current_fit (deterministic weighted)     │
                       │  evidence_coverage                        │
                       │  career_upside = trajectory × coverage    │
                       ▼                                          │
              career.positioning / strengths / gaps / transferable ┘
                       │
                       ├──► career.llm.OllamaJsonClient.analyse_fit   (opt-in narrative,
                       │     schema-validated, evidence-id allow-list,  explains, never decides)
                       ▼
              db.save_career_fit → career_fit table (SQLite)
```

Principles:

- **Deterministic scores are authoritative.** Trade-offs
  (`current_fit` vs `career_upside`) are computed arithmetically; retrieval
  and the LLM can only add *evidence* (documented parent records) and
  *narrative*, never change a score.
- **Evidence provenance is first-class.** Every claim carries a
  `VerificationLevel`; ranking is by trust, confidence, recency, id. No
  automatic promotion between levels.
- **The parent knowledge seam is optional, local, read-only.** The default
  `none` provider yields profile-only evidence. `personal_ai` opens the parent
  database read-only (URI `mode=ro`) and, on any failure, degrades to
  `none` with an explicit risk note — never a silent empty result.
- **Bounded I/O.** Retrieval uses template queries over structured concepts
  (never raw JD text), caps per source, and compacts to a fixed character
  window for the fit calculator and LLM narrative.
- **Fail-closed LLM.** Malformed/oversized/over-long narrative output is
  dropped to `None`; the model can only propose, never decide or write.

## Career document + tailoring layer (Slice 3 / 3.5)

```
cli.career documents ingest PATH ──► career.documents.ingest_document (.txt/.md/.docx/.pdf)
                 │                     content_hash + document_id (idempotent), bounded parse
                 ▼
        career.documents.candidate_evidence_from_document
                 ▼
        career.reconcile.reconcile_document ─ exact kept / new DOCUMENTED / conflicts recorded
                 ▼
        db.save_career_document + save_career_evidence_many + save_career_reconciliation
                 │
cli.tailor JOB_ID --cv PATH ──────► career.mapping.map_requirements (deterministic floor, always stored)
                 │                   │
                 │                   ├──► career.semantic_mapping.refine_mapping (sync/optional)
                 │                   │      evidence allow-list, layer=deterministic|semantic, fail-closed
                 │                   ▼
                 │           career.achievements + career.positioning_plan (deterministic)
                 │                   ▼
                 │           career.validation.validate_claims (numeric + entity anti-fabrication)
                 │                   ▼
                 │           career.cv_llm.generate_proposal (opt-in LLM polish, retries/backoff,
                 │             evidence-id allow-list, fail-closed None)  ── rearranges, never invents
                 │                   ▼
                 ▼           db.save_career_artifact (append-only (job_id, version), migration V5)
        cli.career artifacts list|show|diff  (provenance, human + JSON)
```

Principles:

- **Documents are parsed, never executed.** Bounded extractors only; document
  content is inert data, never interpolated into instructions; file-size and
  section-count caps enforced.
- **Idempotent by content hash.** Re-ingesting an unchanged file is a no-op;
  changed content yields a new document id. Evidence identity is derived from
  `(claim, source=document_id)` so reruns reconcile, never duplicate.
- **The deterministic floor always stands.** `map_requirements` runs every
  time and its output is always stored; the semantic layer only re-labels
  existing mappings and can be disabled; fail-closed to the floor on any
  model/parse failure.
- **Anti-fabrication is deterministic and entity-aware.** Numbers and named
  entities in any artifact section must trace to allow-listed evidence;
  unmatched → claim not SUPPORTED → artifact REQUIRES_REVIEW.
- **Artifacts are immutable and versioned.** Every tailor run appends a
  `(job_id, version)` row; approve/reject is human-only; generated material
  is a proposal, never authoritative evidence.

## Module responsibilities

| Module          | Responsibility                                          |
|-----------------|---------------------------------------------------------|
| `config.py`     | Typed, validated configuration (pydantic); env overrides for infra settings; hard-blocks `auto_submit`/`auto_publish`; validates career weights sum to 1. |
| `migrations.py` | SQLite schema migrations under `PRAGMA user_version` (v1..v5).   |
| `db.py`         | Persistence: jobs, evaluations, job_decisions, scan_runs, career_documents, career_evidence, career_reconciliation, career_artifacts (+append-only versioning), career_artifact_evidence, career_fit. Concurrency-safe artifact versioning (UNIQUE `(job_id, version)` + collision retry, busy_timeout). |
| `canonical.py`  | Pure canonical identity for dedup (`canonical_key`).    |
| `salary.py`     | Pure parsing/normalization of compensation to EUR with a fixed FX table; distinguishes unknown vs unpublished vs below-floor. |
| `normalizer.py` | Pure reshape of a raw `Job` into the stored canonical form (normalized location, EUR salary, remote mode). |
| `scoring.py`    | Deterministic policy scoring + config-to-policy mapping. No LLM. |
| `sources.py`    | Source taxonomy + fetch adapters (HTTP only, no side effects). |
| `sitemap.py`    | Sitemap discovery sources atop `StructuredPageSource`. |
| `discovery.py`  | Wire-only construction from config → source instances; token-redacted inventory for `sources list`. |
| `discovery_search.py` | Search-engine discovery (ddgs, rate-limited) → candidate URLs → job pages → jobs. |
| `pipeline.py`   | Orchestration: run sources, ingest global jobs, lifecycle, dedup; per-source failure isolation; aggregate reports. |
| `report.py`     | Dated digest markdown (executive summary + top shortlist). |
| `logging_setup.py` | Structured `key=value` logging; no credential/JD/CV content. |
| `career.profile`  | Pure derivation of a structured `CareerProfile` from raw profile YAML (inference + defaults; every derived field lands in `inferred_fields`). |
| `career.evidence` | `CareerEvidence` model + deterministic hierarchy (verified > documented > inferred > candidate), stable content-addressed ids, dedup, ranking. |
| `career.requirements` | Pure structured extraction of job attributes (role family, concepts, languages, seniority, scope) from a stored `Job`. |
| `career.knowledge` | `CareerKnowledge` seam + `NullCareerKnowledge` + lazy read-only `PersonalAiCareerKnowledge` adapter. |
| `career.retrieval` | Template query plans, bounded concept-mapped retrieval, trust-ranked dedup, char-budget compaction. |
| `career.positioning` | Deterministic positioning copy: strengths, transferable skills, communication guidance. |
| `career.fit` | Deterministic `analyze_fit`: current_fit, coverage, career_upside, strengths, gaps, risks. |
| `career.llm` | Opt-in `OllamaJsonClient.analyse_fit` + `classify_mapping`: schema-validated narrative/semantic refinement with evidence allow-list; fail-closed to `None`. |
| `career.documents` | Bounded pure parsers (.txt/.md/.docx/.pdf): content hash + document id, candidate evidence extraction, mime/size/section caps. |
| `career.reconcile` | Deterministic evidence reconciliation: exact kept / new DOCUMENTED / conflicts recorded, never silently resolved. |
| `career.mapping` | Deterministic requirement→capability→evidence coverage floor (STRONG/PARTIAL/TRANSFERABLE/GAP/UNKNOWN) with evidence ids. |
| `career.semantic_mapping` | Optional semantic re-classification over existing evidence only (allow-list, layer/reasoning provenance, fail-closed to floor). |
| `career.validation` | Per-claim validation: numeric anti-fabrication + entity checks (dates, org sizes, scope, tech, credentials, employer/title). |
| `career.artifacts` | `CareerArtifact` model with per-job versioning; statuses DRAFT → VALIDATED / REQUIRES_REVIEW → APPROVED/REJECTED (human-only). |
| `career.cv_llm` | Opt-in LLM tailored-proposal polish: evidence-id allow-list, tenacity retries/backoff, fail-closed `None`. |
| `career.tailoring` | Assemble the artifact deterministically (mapping + positioning + validation), optional LLM polish, append-only persistence. |
| `cli.py`        | Subcommands: `scan`, `profile`, `sources`, `jobs`, `fit`, `digest`, `stats`, `decision`, `career documents|evidence|artifacts`, `tailor`. |

## Data model (v5 schema)

- `jobs` — canonical jobs; unique id per source; `canonical_key`, EUR salary
  fields, normalized location, remote mode, lifecycle status + counters.
- `evaluations` — one score per job: total, decision, weights snapshot,
  breakdown reasons.
- `job_decisions` — human decisions (approve/reject) appended over time.
- `scan_runs` — per-source scan telemetry (counts, durations, errors,
  aggregate only).
- `career_fit` — one deterministic fit assessment per job: current_fit /
  career_upside / evidence_coverage, strengths/gaps/transferable/positioning/
  risks, evidence refs + knowledge sources, and an optional validated
  narrative (llm_used flag).
- `career_documents` — ingested CVs: stable content hash + document id,
  filename/source/mime/size, bounded sections (with per-page refs for PDFs),
  ingested_at.
- `career_evidence` — extracted evidence claims (evidence_id PK), levels
  verified > documented, source + document id, normalized facts, confidence.
- `career_reconciliation` — per-document reconcile logs: exact/new/conflict
  counts + statuses only (never content).
- `career_artifacts` — append-only tailored-proposal versions per `(job_id,
  version)` (UNIQUE index), immutable rows: sections/mapping/positioning/
  validation JSON, `llm_used`/`status`/`require_approval`. Approve/reject is
  human-only.
- `career_artifact_evidence` — one-to-many evidence links per artifact row.

## Key decisions

- **Idempotent ingestion**: `j.id` is derived by the source adapter; re-running
  a scan refreshes rather than duplicates. Dedup additionally folds distinct
  ids that canonicalize to the same `(company, title, location)` identity.
- **Salary canonicalization before scoring**: every raw range is parsed and
  converted to EUR once, in `normalizer`; `scoring` never sees raw currency.
- **Deterministic scoring**: the exact same policy produces the same total,
  so scans are reproducible and auditable. An LLM may later *interpret*, but
  never silently change, the policy baseline.
- **Fit is two axes, not one score**: `current_fit` (near-term match) and
  `career_upside` (roll-forward growth) are reported separately so a
  stretch role is visibly attractive without inflating the current-match
  score.
- **Evidence modulates credibility, never arithmetic**: retrieval richness
  changes `evidence_coverage` and confidence/risk notes; the weighted
  deterministic scores stay pure.
- **Lifecycle is time-based, not equivalence-based**: a job that disappears
  from a source is marked stale, then closed after `close_after_missing_scans`
  consecutive misses — a flaky source cannot close jobs on a single miss.

## Non-goals (Slice 1 + Slice 2)

No browser automation, no application submission, no LLM scoring, no
vector/RAG embeddings inside job_agent (parent retrieval is read-only and
optional), no preference-learning writes, no autonomous loops, no
CV/cover-letter generation.