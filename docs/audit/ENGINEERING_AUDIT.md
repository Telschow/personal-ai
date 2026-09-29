# Engineering Audit

## Executive Summary

This audit examines the engineering quality, code health, and architectural integrity of the `personal-ai` repository. The system is a local-first personal AI platform that ingests personal data (documents, emails, chats, browsing history, workouts, financial data) into a structured knowledge base, provides question-answering via a local LLM, and includes a job-search/career agent subsystem.

**Overall Assessment:** The codebase is well-structured and deterministic with clear separation between components. The Personal AI core is production-quality local software with careful attention to idempotency, privacy boundaries, and fail-closed design. The Job Agent is a capable career-search system with extensive test coverage. Key risks involve the extensive personal data in the databases and the read-only integration between systems.

---

## Critical Findings

| # | Category | Finding | Severity |
|---|---|---|---|
| E1 | Memory | Memories table stores highly sensitive personal data (relationships, psychological profiles, financial habits, health metrics) with confidence/importance scores. No explicit redaction or expiration enforcement beyond optional `expires_at`. | HIGH |
| E2 | Ingestion | Source adapters (email, financial, chrome, chatgpt) can ingest data from user-specified paths. Broad glob patterns in CLI could potentially access files outside intended directories if misconfigured. | HIGH |
| E3 | Database | 5+ SQLite databases contain full personal data (22k documents, 82k embeddings, 2303 people, conversations, memories). No filesystem-level encryption or access controls beyond OS permissions. | HIGH |
| E4 | Retrieval | `MAX_SEARCH_QUERY_CHARS = 500` and `MAX_SEARCH_LIMIT = 50` provide hard bounds, but no character budget on total context assembled for LLM. Character budget is managed at a higher level. | MEDIUM |
| E5 | Agent | `max_tool_rounds = 8` in Agent loop; no per-tool timeout. Model could potentially loop if tools keep returning results. Policy engine gates tool execution but depends on correct permission profiles. | MEDIUM |

---

## High Findings

| # | Category | Finding | Evidence |
|---|---|---|---|
| E6 | Code Duplication | `memory/` directory has duplicated patterns for access tracking, event logging, and status management across `service.py`, `store.py`, `reconcile.py`. Three implementations of `_safe_payload`-style identifier-only serialization. | Medium |
| E7 | Stale Abstractions | `people/` canonicalization (`canonicalize.py`) has acknowledged heuristic limitation: "do not widen it without adding cases to tests/test_people_canonicalize.py". The system deliberately fuses only name-anchored variants; opaque usernames and email-alone fusion are intentionally prevented but the boundary is fragile. | Medium |
| E8 | Error Handling | Several source adapters raise `SourceError` subclasses but callers may not distinguish between parse errors, empty messages, and path errors. The CLI `cmd_ingest` catches broadly. | Medium |
| E9 | Hidden Global State | `OllamaClient` creates a single `httpx.Client` instance per process; not thread-safe. Server runs FastAPI with single-event-loop, so this is not a practical concurrency issue but is worth documenting. | Low |
| E10 | Platform Assumptions | `job_agent/config.py` assumes `host.docker.internal:11434` as default LLM base URL for Docker containers. Local development uses `127.0.0.1:11434`. The `personal_ai/config.py` defaults to `localhost:11434`. No automatic DNS switch based on environment. | Low |

---

## Medium Findings

| # | Category | Finding | Evidence |
|---|---|---|---|
| E11 | Unbounded Memory Growth | `chunk_embeddings` table has 82,075 rows with vector data. No automatic cleanup when documents are deleted or expired. Depend on embedding backfiller to manage. | Medium |
| E12 | Logging Problems | `log_event` used extensively but log levels not consistently applied. Some `log_event` calls include sensitive data (tokens, URLs) that could appear in log output. The `personal_ai/logging_setup.py` configures structlog but downstream usage varies. | Medium |
| E13 | Observability Gaps | No metrics exposed for retrieval latency, cache hit rate, or tool execution success/failure rates. The `statistics()` methods on `MemoryService` and `MemoryStore` exist but are not wired to any monitoring system. | Medium |
| E14 | Fragile Paths | `cli.py` `parse_args` uses `args.workspace.resolve()` and `args.database.resolve()` without existence validation. If workspace/database paths are deleted between CLI parse and execution, errors surface at runtime rather than startup. | Medium |
| E15 | Inconsistent Conventions | Two patterns for environment variable loading: `personal_ai/config.py` uses `os.environ.get()` with explicit defaults, while `job_agent/config.py` uses Pydantic `BaseModel` with `Field(default_factory=...)`. Both work but mix paradigms. | Low |
| E16 | Weak Typing | `job_agent/db.py` `upsert_job` constructs SQL with 47+ column positions as raw string literal. Column order must match exactly; any schema change breaks without compile-time check (Python dynamic typing). | Low |
| E17 | Retry Problems | `job_agent/pipeline.py` `run_lifecycle` has per-source retry logic but only 0 retries per query (`max_retries_per_query = 0` in config). Source adapters themselves may retry (httpx timeouts), but the pipeline does not re-try failed source queries. | Low |

---

## Low / Cosmetic Findings

| # | Category | Finding |
|---|---|---|
| E18 | Cosmetic | `docs/ROADMAP.md` checkboxes use `- [ ]` and `- [x]` markdown; some phases have all `- [ ]` despite claiming completion (e.g., Phase 3 items). |
| E19 | Cosmetic | `job_agent/config.example.yaml` contains `example-company` placeholder tokens that are never fetched by discovery (built-in guard), but the pattern is not documented in the config file itself. |
| E20 | Cosmetic | Several `*.py` files have `from __future__ import annotations` but mix with legacy string-based type hints in some function signatures. |
| E21 | Cosmetic | `MAX_TOOL_RESULT_CHARS = 12000` in `agent.py` produces `[truncated: showing ...]` markers that could cut off model reasoning mid-thought; no test verifies truncation behavior preserves semantic integrity. |
| E22 | Cosmetic | `personal_ai/memory/models.py` `PersonReference` has `role: str` with no enum constraint; valid roles are documented in `people/extract.py` as `EMAIL_ROLE` and `FINANCIAL_ROLE` but other roles exist in the database (e.g., from Chrome history parsing). |

---

## Recommendations (Non-Modification)

**R1 — Memory Redaction Policy:** Implement a policy that automatically redacts or masks sensitive fields (relationship names, psychological profiles, health metrics) when memories are listed or searched, unless the user explicitly requests full content. Consider adding a `sensitivity` flag to the `Memory` model.

**R2 — Ingestion Path Validation:** Add startup validation in CLI that verify workspace and database paths exist and are writable before executing any ingest commands. Add `--safe-mode` flag that limits ingestion to a whitelisted subdirectory.

**R3 — Database Encryption:** Enable filesystem-level encryption (e.g., `chmod 600` on SQLite files, or use SQLCipher) for all databases containing personal data. The current `data/` directory and `job_agent/output/` directory are not encrypted at rest.

**R4 — Retrieval Context Budget:** Add a hard character budget limit to the total context assembled for LLM consumption. Currently the character budget is allocated across system instructions, query, evidence, and format instructions, but there is no top-level hard cap beyond the model's context window.

**R5 — Agent Tool Timeout:** Add per-tool timeout to the Agent loop to prevent infinite loops if a tool keeps returning results. Currently `max_tool_rounds = 8` provides a round limit but no time limit per round.

**R6 — Log Sanitization:** Implement log sanitization to strip sensitive data (API keys, URLs with tokens, personal identifiers) from log output. Add a `sanitize_log` hook in `logging_setup.py`.

**R7 — Environment-Specific Config:** Unify the two config paradigms (personal_ai vs job_agent) and add environment detection (Docker vs local) to auto-select correct LLM base URL, rather than relying on manual configuration changes.

**R8 — Schema Validation:** Add runtime schema validation for SQLite databases used by the personal AI system. Use `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` consistently. Add migration testing to prevent schema drift.

**R9 — People Identity Boundary:** Document the explicit boundary: the people layer fuses ONLY name-anchored variants (via `canonical_identity`). Email addresses are stored as aliases but identity is NEVER fused by email alone. This boundary must be maintained when adding new source adapters.

**R10 — Testing Coverage Gaps:** Increase test coverage for:
- Ingestion with edge-case source files (empty, malformed, extremely large)
- Memory reconciliation conflict scenarios
- Policy engine denial and approval-required paths
- Retrieval with empty/no-matches status
- Vision extraction failure modes
- People canonicalization with ambiguous names

---

## Confidence Estimate

| Finding | Confidence |
|---|---|
| E1-E5 | High (code inspection, database schema verified) |
| E6-E12 | Medium (patterns observed, some inference required) |
| E13-E22 | Low-Medium (structural observations, some subjective calls) |

---

## Next Steps (Post-Audit)

1. Document the memory sensitivity classification system
2. Add filesystem permissions hardening guide
3. Create ingestion whitelist/blacklist mechanism
4. Implement log sanitization pipeline
5. Add retrieval context budget enforcement
6. Expand test coverage for critical edge cases
7. Unify configuration paradigms between personal_ai and job_agent subsystems