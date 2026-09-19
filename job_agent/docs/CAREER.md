# Career intelligence (Slice 3 / 3.5)

Deterministic, evidence-attributed fit analysis and proposal-only tailored CV
artifacts for stored jobs. See `docs/ARCHITECTURE.md` for the layer diagram and
`docs/IMPLEMENTATION_PLAN.md` for scope/non-goals.

## Two axes, not one score

- **current_fit** — weighted match of the job's structured attributes against
  the derived `CareerProfile` (role family, AI relevance, leadership,
  technical depth, product scope, seniority, domain). Deterministic weights
  from `career.weights`, validated to sum to 1.
- **career_upside** — roll-forward growth: trajectory progress ×
  `(0.5 + 0.5 × evidence_coverage)`. A stretch role is attractively scored on
  upside without inflating the current-match score.
- **evidence_coverage** — fraction of required capability concepts backed by at
  least one piece of evidence (0..1).

## Evidence provenance

Every `CareerEvidence` carries a `VerificationLevel`:

| Level      | Meaning                                                     |
|------------|-------------------------------------------------------------|
| verified   | Directly stated in the user's own profile YAML              |
| documented | Retrieved read-only from the parent personal_ai DB          |
| inferred   | Derived from automation heuristics (e.g. source_meta tags)  |
| candidate  | Present only as a possibility                               |

Ranking is by `(level, confidence, recency, id)`; no automatic promotion.
Evidence modulates coverage/credibility/risks — it never changes the weighted
deterministic scores.

## Knowledge seam

`CareerKnowledge` is a narrow protocol (`health`, `memory_search`,
`corpus_search`). Two providers:

- `none` (default) — profile-only evidence, fully offline.
- `personal_ai` — read-only (`mode=ro`, URI) access to the parent database's
  memory + FTS5 chunks. Lazy import; on any failure it degrades to `none`
  with an explicit risk note (never a silent empty result).

## Retrieval

Retrieval builds **template queries over structured concepts** (never raw JD
text), runs at most `MAX_QUERIES` bounded queries per concept, deduplicates on
`evidence_id`, ranks by trust, and compacts to a fixed character budget for the
fit calculator and the LLM narrative.

## LLM narrative (opt-in, fail-closed)

`career.llm.enabled` turns on `OllamaJsonClient.analyse_fit`. The model sees
only pre-compacted evidence items plus an allow-list of `evidence_id`s and must
return schema-validated JSON (`FitNarrative`) whose evidence references all
exist in that list. Any failure (malformed, oversized, unreferenced ids)
produces no narrative — `llm_used=false` is recorded and the deterministic
output stands. The LLM explains, never decides or writes.

Strict-schema calls are made with Ollama's top-level `"think": false` on the
request body (`OllamaJsonClient.analyse_fit`/`classify_mapping`,
`OllamaCvClient.generate_proposal`): reasoning models like `qwen3.5:9b`
otherwise emit the whole completion into `message.thinking` and return an
empty `message.content`, which previously made every strict-schema call fail
closed (measured 165–243 s per call, content_len 0). With thinking disabled,
the same calls return parseable content in roughly 10–40 s. A per-call
timeout is configurable (`llm.timeout_seconds`, default 300) and every call
is recorded in an aggregate-only `LlmCallLog` (call type / model / duration /
outcome, never content).

## Documents + evidence (Slice 3)

`career documents ingest PATH` ingests .txt/.md/.markdown/.docx/.pdf CVs:
bounded pure parsing (no execution, no instruction interpolation, 1 MiB /
200-section / 20k-char caps), stable `content_hash` + `document_id`
(`cv-doc:<hash>`), candidate evidence extraction, and deterministic
reconciliation against the existing evidence base (exact matches kept,
new facts added `DOCUMENTED`, conflicts recorded explicitly — never silently
resolved). Re-ingesting an unchanged file is a no-op (idempotent); changed
content produces a new hash/document. Per-page `ref` metadata keeps PDF page
numbers. PDF requires `pypdf` (a documented dependency).

## Tailored CV proposal (Slice 3/3.5)

`tailor JOB_ID --cv PATH` produces a **proposal-only** `CareerArtifact`
(DRAFT → VALIDATED / REQUIRES_REVIEW → APPROVED/REJECTED, approve/reject is
human-only):

1. Deterministic requirement→capability→evidence mapping (mapping.py floor,
   always runs and is always stored: STRONG/PARTIAL/TRANSFERABLE/GAP/UNKNOWN,
   every mapping carries evidence ids).
2. Optional **semantic mapping refinement** (`--no-semantic` disables): a
   re-classification over existing evidence only, gated by an evidence-id
   allow-list, never creating or deleting evidence, with provenance
   (`layer=deterministic|semantic`) + reasoning. Explicit negative-evidence
   GAPs are never upgraded; unknown ids rejected; disabled/malformed →
   deterministic floor. Proposals never *downgrade* the deterministic
   coverage (same-level/upward only; measured 2026-09-16: small models
   proposed STRONG→UNKNOWN on a DIRECT case, qwen3.5:9b did not — the guard
   blocks it). `CAREFUL`: the LLM proposes labels; the deterministic
   numeric floor and anti-fabrication validation still decide.
3. Optional **LLM polish** (cv_llm.py): bounded retries with exponential
   backoff (tenacity, transient 408/429/5xx/transport only, ≤ max_retries),
   else fail-closed `None`.
4. **Entity-level claim validation** (validation.py): per-claim numeric and
   entity checks (dates/timeframes, team/org sizes, scope qualifiers,
   technologies/tools/products, credentials, employer/title). Any numeric or
   entity problem ⇒ claim not SUPPORTED ⇒ artifact REQUIRES_REVIEW.
5. **Append-only artifact versioning** (migration V5): every `tailor()` run
   adds a new `(job_id, version)` row; latest = `MAX(version)`; artifacts
   are immutable after write; `career artifacts list|show|diff` inspect
   versions; re-running never auto-approves.

Output is always marked *PROPOSAL — NOT APPROVED*. Generated career material
is a proposal and does not become authoritative career evidence automatically.

## CLI

```bash
job-agent fit JOB_ID [--json] [--no-llm]
job-agent career documents ingest PATH [--json]
job-agent career documents list [--limit N] [--json]
job-agent career evidence list [--conflicts] [--json]
job-agent career artifacts list JOB_ID [--version-from N --version-to M] [--json]
job-agent career artifacts show JOB_ID [--version N] [--json]
job-agent career artifacts diff JOB_ID [--from-version N --to-version M] [--json]
job-agent tailor JOB_ID --cv PATH [--json] [--no-llm] [--no-semantic]
```

- `--json` emits the machine-readable form; human mode prints provenance
  markers (`(semantic)` on semantically-refined coverage) and the proposal
  banner.
- `--no-llm` disables all opt-in LLM layers for this run.
- `--no-semantic` (tailor only) disables the semantic mapping refinement;
  the deterministic floor is unaffected.
- Missing knowledge provider → one risk note appended; assessment still runs.

Results persist idempotently to the `career_*` tables (migrations V3/V4/V5).