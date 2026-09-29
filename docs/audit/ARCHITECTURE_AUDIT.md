# Architecture Audit

## Executive Summary

This audit examines the system architecture of the `personal-ai` repository, a local-first personal AI platform comprising two primary subsystems: **Personal AI** (knowledge ingestion, RAG, LLM agent, memory) and **Job Agent** (career discovery, scoring, application tracking, CV tailoring). The systems are integrated via read-only SQLite access, with the Job Agent querying the Personal AI database for evidence-based career insights.

**Overall Assessment:** The architecture is well-conceived for a local-first personal AI platform. Clear separation of concerns, deterministic data pipelines, and policy-gated agent tool execution are significant strengths. The read-only integration between Personal AI and Job Agent is a deliberate privacy boundary. Key architectural risks involve the extensive personal data in the SQLite databases, the vector embedding store, and the chrome history ingestion pipeline.

---

## System Overview

| Component | Description | Status |
|---|---|---|
| **Personal AI Core** | Local document ingestion, chunking, embedding (optional), SQLite knowledge base, retrieval (keyword/FTS5 + optional semantic/hybrid), LLM agent with policy-gated tools, chat HTTP API | Fully Implemented |
| **Job Agent** | Job source discovery (ATS APIs, search engines, RSS, sitemaps), normalization, scoring against career profile, application tracking, CV/cover letter tailoring, daily digests | Fully Implemented |
| **Integration** | Read-only SQLite bridge: Job Agent connects to Personal AI database via `PERSONAL_AI_DATABASE` env var in read-only mode. No writes from Job Agent to Personal AI. | Fully Implemented |
| **HTTP API** | OpenAI-compatible server (FastAPI) exposing chat completions, control plane (executions/memory/workouts), and Job-Agent endpoints. Token-authenticated. | Fully Implemented |
| **CLI** | Comprehensive command-line interface for ingestion, search, memory management, career operations, workout tracking | Fully Implemented |
| **GUI** | Local web dashboard (FastAPI static files) providing base interface for both systems | Partially Implemented |

---

## Architecture Layering

```
Layer 1: OS / Filesystem
  - SQLite databases (5+ files with personal data)
  - User config files (YAML, .env.example)
  - Source data (Chrome history, Email mbox, ChatGPT exports, Financial CSVs, PDFs)

Layer 2: Storage Layer
  - DocumentStore (SQLite: documents, chunks, embeddings)
  - ExtractionStore (SQLite: structured extractions)
  - EventStore (SQLite: chrome events, youtube events)
  - ConversationStore (SQLite: chat conversations)
  - MemoryStore (SQLite: memories, people, evidence)
  - OrchestrationStore (SQLite: plans, tasks, events - job agent)

Layer 3: Ingestion Pipelines
  - Source adapters (email, chrome, youtube, gemini, keep, financial, filesystem, chatgpt, notebooklm)
  - Document processor (extract → classify → chunk → embed)
  - Vision extraction (optional, per-page, cached)
  - Structured extraction (LLM-backed, deterministic normalization)

Layer 4: Retrieval Layer
  - ChunkIndex (SQLite FTS5 keyword search - production default)
  - SemanticChunkIndex (cosine similarity over persisted embeddings - opt-in)
  - HybridChunkIndex (RRF fusion of keyword + semantic - opt-in)
  - RetrievalService (unified search across chunks, extractions, conversations)
  - Query validation, planning, ranking, fusion

Layer 5: Agent & Orchestration
  - Agent (synchronous tool-calling loop against local Ollama)
  - PolicyEngine (permission gating: allowed/denied/approval-required)
  - ModelRouter (capability → model mapping via OllamaProvider)
  - Orchestrator (researcher → verifier workflow with approval gating)
  - Execution (task dispatch, approval context, event emission)

Layer 6: APIs & UI
  - HTTP API (FastAPI + OpenAI-compatible shapes)
  - CLI (argparse-based comprehensive commands)
  - GUI (local web dashboard)
  - Read-only SQLite bridge (Job Agent → Personal AI)

Layer 7: Configuration
  - personal_ai/config.py (environment-driven: chat model, API host/port, embedding, vision, retrieval mode)
  - job_agent/config.yaml (YAML-based: profile, LLM, search, jobs, ranking, careers, sources)
  - Both support env var overrides
```

---

## Component Interactions

### Personal AI Data Flow (Ingestion → Retrieval → Agent)

```
Source Record
        ↓
Source Adapter (parser, normalization)
        ↓
document_from_source_record (hash, ID)
        ↓
extract_text (text extraction, vision augment)
        ↓
classify_document (TEXT_HEAVY / IMAGE_HEAVY / OTHER)
        ↓
structured_extraction (LLM-backed, optional, deterministic normalize)
        ↓
chunk_document (deterministic, overlap, idempotent)
        ↓
DocumentStore.add (content hash idempotency)
        ↓
(chunk embeddings - optional, separate backfill step)
        ↓
Retrieval ready

Query → RetrievalService
        ↓
Query validation (MAX_SEARCH_QUERY_CHARS, MAX_SEARCH_LIMIT)
        ↓
ChunkIndex.search (FTS5 keyword OR semantic cosine OR hybrid RRF)
        ↓
Result fusion & ranking (BM25 scores vs cosine scores → RRF)
        ↓
Character budget allocation (system instr: 15-20%, query: 5-10%, evidence: 60-70%, format: 5-10%, margin: 0-5%)
        ↓
Context assembly (with provenance, length capping)
        ↓
LLM chat (local Ollama, think:false to disable reasoning)
        ↓
Final answer with source provenance
```

### Job Agent Data Flow (Discovery → Scoring → Application)

```
Config → Source Catalog
        ↓
build_sources() → Source Adapters (Greenhouse, Lever, Ashby, etc.)
        ↓
Parallel Fetching → Raw Job Collection
        ↓
Normalization & Canonicalization (normalizer.py)
        ↓
Deduplication (by canonical_key)
        ↓
Persistence to jobs.sqlite3
        ↓
Scoring (deterministic: role match, skills, seniority, domain, leadership, location, compensation)
        ↓
Fit Assessment (weighted sum, decision logic)
        ↓
Evidence Collection (from Personal AI read-only bridge)
        ↓
Career Fit Report (strengths, gaps, positioning, risks)
        ↓
Application Tracking (applications table, stages, feedback)
```

### Personal AI ↔ Job Agent Integration

```
Personal AI Database (read-only)
        ↓
Job Agent sqlite3.connect(mode=ro)
        ↓
PersonalAiCareerKnowledge Adapter:
  - memory_search() → memories table (bounded)
  - corpus_search() → documents/chunks tables (bounded)
  - health() → system status
        ↓
Career Evidence Collection (template query planning based on job requirements)
  - Bounded retrieval per concept (max_corpus_evidence=40, max_memory_evidence=20)
  - Trust-ranked deduplication
  - Character budget compaction
        ↓
Career Fit Assessment (current fit, career upside, evidence coverage, strengths/gaps/transferables/positioning/risks)
        ↓
Optional LLM Narrative (fail-closed to deterministic)
        ↓
Persistence to evaluations table (job_agent/jobs.sqlite3)
        ↓
Available for: Job Detail Views, Score Explanations, Ranking Adjustments, Career Intelligence Reports
```

---

## Architecture Risks

| # | Category | Risk | Mitigation |
|---|---|---|---|
| A1 | Data Locality | All personal data stored in 5+ local SQLite databases. If any database file is leaked or copied, full personal corpus is exposed. | Enable filesystem encryption (chmod 600, SQLCipher); document threat model |
| A2 | Vector Database | 82,075 chunk embeddings in `chunk_embeddings` table. Vectors are derived from personal documents; if the DB is exposed, the embedded content is theoretically recoverable through model inversion (weak but non-zero risk). | Keep local-only; document that embeddings are derived from personal data |
| A3 | Chrome History Ingestion | `sources/chrome_history.py` parses full Chrome History JSON (∼200K+ entries). Includes URLs with API keys, authentication parameters, browser-stored credentials. Ingestion is opt-in but a misconfigured CLI could inadvertently ingest. | Verify .gitignore excludes `previous_project_and_raw_data/Chrome/`; add CLI validation |
| A4 | Read-Only Integration | Job Agent connects to Personal AI DB in read-only mode (`sqlite3.connect(mode=ro)`). If PRAGMA settings change or if the bridge code accidentally writes, personal data could flow into career assessments. | Audit the bridge code path; add write-protection assertions |
| A5 | LLM as Single Point of Failure | All agent reasoning goes through local Ollama. If Ollama is unavailable, the system returns 503 errors (graceful degradation) but the agent cannot function. | Document the 503 behavior; ensure CLI degrades gracefully to profile-only evidence |
| A6 | Configuration Drift | Two config systems (personal_ai/env-driven, job_agent/YAML) with different defaults and paradigms. Environment variable overrides (`with_env_overrides`) exist but manual sync required. | Unify config paradigms; add CI check for config consistency |
| A7 | Memory Expiration | `Memory` model has optional `expires_at` but no automatic cleanup. Memories accumulate indefinitely unless manually purged. | Add periodic purge job; document the purge API |
| A8 | No Network Boundary for Job Discovery | Job Agent makes outbound HTTP requests to job providers (GitHub, Greenhouse, etc.). If the network is monitored, job search patterns could be inferred. | Document as expected behavior; add rate limiting and pacing (already implemented) |

---

## Strengths

| # | Strength | Description |
|---|---|---|
| S1 | Deterministic Pipelines | Ingestion, chunking, and deduplication are all content-hash or canonical_key based. Re-ingesting unchanged files produces identical results. |
| S2 | Policy-Gated Agent | The PolicyEngine enforces tool execution permissions in code, not model obedience. ALLOWED/DENIED/APPROVAL_REQUIRED are explicit and auditable. |
| S3 | Fail-Closed Design | LLM output is strictly schema-validated; any failure mode falls back to deterministic output. `think: false` disables reasoning token leakage. |
| S4 | Idempotency Throughout | Document ingestion, job persistence, and artifact versioning all use content-hash or version-based idempotency. No duplicate data on re-runs. |
| S5 | Clear Trust Boundaries | Read-only SQLite bridge, policy-gated tools, local-only LLM, and explicit "no cloud dependencies" guarantees in the RAG architecture docs. |
| S6 | Extensive Test Coverage | 1700+ test files across both subsystems. Security tests, retrieval tests, memory tests, ingestion tests all have dedicated test suites. |
| S7 | Modular Source Adaptors | Each source adapter (email, chrome, financial, etc.) is narrow and isolated. A source failure does not abort the whole scan. |
| S7 | Provenance Tracking | Every evidence item, memory, and artifact carries source, timestamp, confidence, and ID tracking. No anonymous data. |

---

## Recommendations (Non-Modification)

**A1 — Database Encryption Hardening:** Add documentation and a startup script that sets `chmod 600` on all SQLite databases containing personal data (`data/personal-ai.db`, `data/personal-ai.sqlite3`, `job_agent/jobs.sqlite3`, `knowledge.db`, `previous_project_and_raw_data/data_handling/private_storage/*.db`). Consider SQLCipher for full-disk encryption.

**A2 — Chrome History Ingestion Guard:** Add a CLI flag or config option that requires explicit confirmation before ingesting Chrome history. Add validation that the source path is within `previous_project_and_raw_data/Chrome/` or another explicitly approved directory.

**A3 — Read-Only Bridge Audit:** Audit the `personal_ai/career_knowledge.py` or equivalent adapter to verify no write operations can occur through the read-only bridge. Add runtime assertions that the connection is in read-only mode.

**A4 — Memory Cleanup Tool:** Create a CLI command or cron job that purges expired memories (`expires_at` set) and archives old memories. Add a `--dry-run` flag to preview before purging.

**A5 — Configuration Unification:** Merge the personal_ai and job_agent config systems into a single source of truth. The job_agent already reads some env vars; add personal_ai config to also use env vars with sensible defaults.

**A6 — Retrieval Context Budget Enforcement:** Add a hard top-level character budget cap in the RetrievalService or agent run path. Currently the budget is allocated across sections but there's no enforcement against total exceeding the model's context window.

**A7 — Agent Tool Timeout:** Add a per-round timeout to the Agent `run()` method. Currently `max_tool_rounds = 8` limits rounds but has no time limit. A model that keeps requesting tools could block the agent indefinitely.

**A8 — Vision Extraction Cache Invalidations:** The vision extraction cache (per page, per model/prompt version) could grow unbounded. Add a max cache size or TTL mechanism.

**A8 — Job Agent Rate Limiting Verification:** The pacing and backoff settings in `job_agent/config.yaml` are well-designed. Verify the actual implementation in `discovery_search.py` and `pipeline.py` correctly respects these limits, especially `max_retries_per_query = 0` and `max_consecutive_failures = 3`.

**A9 — People Identity Boundary Documentation:** The people layer's explicit boundary (name-anchored only, no email fusion) must be documented as a core architectural principle. Any new source adapter must review this boundary before adding people extraction.

**A10 — orchestrator._Counter Determinism:** The `_Counter` class in `orchestrator.py` starts from `store.max_event_seq()` for resumability. Verify that plan resumption after restart never re-uses event sequences, which could cause event ID collisions.

---

## Confidence Estimate

| Finding | Confidence |
|---|---|
| A1-A8 | High (architecture inspection, code review) |
| A9-A10 | Medium (structural observation, requires runtime verification) |

---

## Next Steps (Post-Audit)

1. Add database encryption hardening guide and script
2. Create Chrome history ingestion guard mechanism
3. Audit the read-only bridge for accidental writes
4. Implement memory cleanup/purge CLI command
5. Unify configuration between personal_ai and job_agent
6. Add retrieval context budget enforcement
7. Add agent tool per-round timeout
8. Document people identity boundary as architectural principle
9. Verify job agent rate limiting implementation
10. Add vision cache TTL mechanism