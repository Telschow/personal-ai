# How OpenCode Works With This Project

## Overview

```
User
  ↓
OpenCode (CLI agent)
  ↓
Agent (role the AI plays)
  ↓
Skill (workflow the AI knows)
  ↓
Tool (action/data access the AI can use)
  ↓
Personal AI Service (FastAPI gateway)
  ↓
Job-Agent Domain (application logic)
  ↓
Database / Web / LLM
```

---

## Agent — "What role is the AI playing?"

OpenCode agents are specialized personas. In this project:

| Agent | Role |
|-------|------|
| `architect` | System design, scalability, technical decisions |
| `planner` | Feature breakdown, task ordering |
| `code-reviewer` | Quality, security, maintainability review |
| `python-reviewer` | PEP 8, type hints, Pythonic idioms |
| `fastapi-reviewer` | Async correctness, DI, Pydantic, OpenAPI |
| `security-reviewer` | OWASP Top 10, secrets, injection, crypto |
| `cavecrew-investigator` | Read-only code locator (where is X defined?) |
| `cavecrew-builder` | Surgical 1-2 file edits |
| `cavecrew-reviewer` | Diff/branch/file review |

**Key principle:** The agent defines *who* is acting, not *what* they do. The same agent can use different skills.

---

## Skill — "What workflow does it know?"

Skills are procedural knowledge — multi-step workflows the agent can execute:

| Skill | Purpose |
|-------|---------|
| `spec-driven-development` | Write spec → plan → implement → test |
| `planning-and-task-breakdown` | Decompose requirements into ordered tasks |
| `incremental-implementation` | Thin verifiable slices, feature flags |
| `test-driven-development` | Red-green-refactor loop |
| `debugging-and-error-recovery` | Systematic root-cause analysis |
| `code-review-and-quality` | Multi-axis review before merge |
| `api-design` | REST patterns: resources, status codes, pagination |
| `database-migrations` | Schema changes, rollbacks, zero-downtime |
| `documentation-and-adrs` | Record decisions, API changes, context |
| `security-and-hardening` | Input validation, auth, OWASP, supply chain |
| `shipping-and-launch` | Pre-launch checklist, monitoring, rollback |
| `git-workflow-and-versioning` | Commits, branches, PRs, releases, changelogs |
| `source-driven-development` | Verify against official docs before implementing |
| `fastapi-patterns` | Project structure, DI, async, auth, testing |
| `python-patterns` | Pythonic idioms, PEP 8, type hints |
| `coding-standards` | Cross-project naming, readability, immutability |
| `performance-optimization` | Profiling, N+1, bottlenecks, Core Web Vitals |
| `caveman` | Ultra-compressed communication |
| `ponytail` | Laziest solution that works (YAGNI, stdlib first) |

**Key principle:** A skill is a *workflow*, not a tool. It tells the agent *how* to approach a task.

---

## Tool — "What action/data access can it use?"

Tools are concrete capabilities. In this project:

### Personal AI Tools (registered in ToolRegistry)
- `search_documents` — FTS5 keyword search over chunks
- `search_knowledge` — Memory + document unified search
- `query_events` — Temporal event queries (Chrome history, workouts)
- `search_workouts` — Workout dataset queries
- `get_document` — Fetch one document by ID (read-only)
- `get_memory` — Fetch one memory by ID (read-only)
- `propose_memory` — Write memory (approval-gated)
- `filesystem_read/write/list` — Sandboxed workspace only

### Job-Agent Tools (via HTTP API)
- `GET /api/job-agent/health` — System status
- `GET /api/job-agent/jobs` — List with filters
- `GET /api/job-agent/jobs/{id}` — Job detail + fit analysis
- `POST /api/job-agent/jobs/{id}/status` — Update user status
- `POST /api/job-agent/discover` — Run bounded discovery

### OpenCode Built-in Tools
- `read`/`write`/`edit`/`glob`/`grep`/`bash` — File operations
- `skill` — Load a skill
- `task` — Launch subagent

**Key principle:** Tools are *explicit allow-listed capabilities*. No eval/exec/shell/dynamic imports. The model chooses from registered tools only.

---

## Domain Service — "What deterministic application logic exists?"

Business logic lives in Python domain services, NOT in prompts:

| Service | Responsibility |
|---------|----------------|
| `job_agent/scoring.py` | Deterministic ScoringPolicy (salary, location, purpose, leadership, WLB) |
| `job_agent/career/fit.py` | current_fit vs career_upside, evidence-attributed |
| `job_agent/career/evidence.py` | Trust ladder: VERIFIED > DOCUMENTED > INFERRED |
| `job_agent/career/profile.py` | CareerProfile with per-field provenance |
| `job_agent/career/tailoring.py` | Requirement → Capability → Evidence mapping |
| `job_agent/career/validation.py` | Entity-level anti-fabrication checks |
| `job_agent/providers.py` | Native provider feeds (RemoteOK, Remotive) |
| `job_agent/normalizer.py` | Job canonicalization, salary EUR normalization |
| `job_agent/pipeline.py` | Ingest + lifecycle (stale/closed/dedup) |
| `job_agent/application.py` | Typed stage machine (NOT_APPLIED → HIRED) |

**Key principle:** Domain services are *testable without network/Ollama/filesystem*. They are the source of truth for business rules.

---

## Model — "What generates/interprets language?"

- **Ollama** (local) runs `qwen3.5:9b`
- Used for:
  - Chat completions (Personal AI gateway)
  - Structured extraction (documents, emails, financial)
  - Career fit narrative (consultative only)
  - CV tailoring proposal (fail-closed, evidence-allowlisted)
  - Semantic mapping refinement (operates only over existing evidence)
- **Never** used for:
  - Scoring decisions (deterministic pipeline authoritative)
  - Database writes (explicit tools only)
  - Application submission (human approval required)

**Key principle:** LLM is a *component*, not the authority. Deterministic pipeline wins.

---

## Database — "What persists facts?"

Single SQLite file (`personal-ai.db` + `job-agent.db` on shared volume):

| Table | Contents |
|-------|----------|
| `documents`, `chunks`, `embeddings` | Ingested corpus |
| `memories` | Durable facts (fact, preference, decision, project_context, entity, summary, instruction) |
| `workouts` | Normalized exercise sessions |
| `executions`, `tasks`, `events`, `approvals` | Control plane |
| `jobs`, `evaluations`, `job_sources` | Job-Agent discovery + scoring |
| `applications` | Career application lifecycle |
| `career_documents`, `career_evidence`, `career_reconciliation` | CV ingestion + evidence |
| `career_artifacts`, `career_artifact_evidence` | Tailored CV proposals (append-only) |
| `user_artifacts` | CV/cover letter uploads with approval |
| `provider_runs`, `discovery_runs` | Content-free telemetry |
| `preferences`, `job_decisions` | User decisions + inferred preferences |

**Key principle:** Database stores *facts*, not model outputs. LLM outputs are proposals stored separately with provenance.

---

## Human — "What decisions remain mine?"

| Decision | Who Makes It |
|----------|--------------|
| Which jobs to save/reject/apply to | Human (UI actions) |
| Whether a tailored CV is accurate | Human (REQUIRES_REVIEW → APPROVED/REJECTED) |
| Whether to submit an application | Human (never auto-submit) |
| Which interview stories to use | Human (story bank is read-only) |
| Salary negotiation | Human |
| Career direction changes | Human (updates profile.yaml) |

**Hard boundary:** `auto_submit: false` and `never_submit: true` are hard-blocked by config validator.

---

## Why This Architecture?

```
LLM ≠ source of truth    →  Deterministic pipeline is authoritative
LLM ≠ database           →  SQLite stores facts; LLM outputs are proposals
LLM ≠ autonomous decider →  Human approves every submission
```

The system is designed so you can:
1. **Trust the scores** — they're deterministic, explainable, provenance-tagged
2. **Verify the CV** — every bullet references evidence you can inspect
3. **Control the flow** — nothing submits without your explicit action
4. **Run locally** — no cloud APIs, no data leaves your machine
5. **Debug when needed** — every decision traces to code, not a prompt