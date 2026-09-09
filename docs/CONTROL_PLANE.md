# Execution Control Plane (legacy Phase 39)

A local-first control plane for the agent-orchestration runtime: durable
executions, cursor-based event streams, task-/permission-scoped approvals, and
a Kanban read projection. Everything is single-user, offline, and SQLite-backed
— there is no distributed stack and no dedicated control-plane UI yet (the
legacy Phase 44 HTTP gateway serves the `/api/*` contract to clients such as a
future UI).

```
CLI (personal_ai.execution.cli)   HTTP gateway (legacy Phase 44: /api/executions/*)
   │  --database orch.db                     │
   └───────────────┬─────────────────────────┘
                   ▼
ControlPlane            (service boundary; the only client entry point)
   │
   ▼
Orchestrator            (plan lifecycle: create / resume / pause / cancel /
   │                     approve / reject / retry)
   ├── Planner          (deterministic researcher → verifier task graph)
   ├── TaskExecutor     (policy-gated single-task execution)
   ├── PolicyEngine     (permissions; durable ApprovalContext gate)
   ├── DeterministicVerifier
   └── OrchestrationStore  → SQLite (plans, tasks, events, evidence,
                              approvals, artifacts)
```

## Concepts

Things that are deliberately distinct (never collapsed into one object):

- **Execution / Plan** — a durable, resumable unit of work with an explicit
  lifecycle: `pending → planned → running → verifying → completed` (or
  `failed`, `blocked`, `needs_approval`, `cancelled`).
- **Task** — a unit of work assigned to an agent (`engineer` by default),
  identified by a stable `task_id`, with an explicit status state machine.
- **Event** — an append-only, timestamped audit record correlated with
  plan/task/agent ids. Payloads carry only safe summaries/counts/ids/hashes —
  never full private content, prompts, or tool arguments.
- **Approval** — a durable, scope-exact decision over `(execution_id,
  task_id, permission)`.
- **Evidence** — a provenance-preserving reference to a corpus passage
  (`document_id`, `source_type`, `source`, excerpt), produced by the
  researcher tool.

## State machines

Plans and tasks both have explicit, typed state machines
(`personal_ai/execution/models.py`). Invalid transitions raise
`PlanTransitionError` / `TaskTransitionError`. Key rules enforced in code:

- `failed → running` is allowed **only** as an explicit `retry_task` (recovery).
- `completed` and `cancelled` are terminal — no outgoing transitions.
- `needs_approval → running` happens only via an explicit `approve` +
  `resume` (or the legacy `run_research_plan` alias, which delegates to the
  same canonical `resume_execution` path).
- Same-status re-transitions (e.g. `completed → completed`) are rejected.

## Event model and cursor reads

Every emitted event gets a monotonically increasing `seq` *across the whole
store* (global counter seeded from durable state, so restarts never re-use
sequence numbers). Readers consume per-execution via a cursor:

```
events_after_seq(execution_id, N)  → events with seq > N
```

`execution events` in the CLI exposes this directly (`--cursor`). A cursor of
`0` (or negative) returns the full stream. Because the counter is global, a
single monotonic cursor works for cross-execution aggregation too — acceptable
for a single-user local system and documented here so a future remote/hybrid
deployment would move to per-execution cursors.

## Durable approval model

A task only enters a gated (write/high-risk) tool by first meeting the policy
engine, which asks the durable `ApprovalContext` for a decision. The requested
scope is exactly `(execution_id, task_id, permission)`:

- Approving `A/T1/filesystem.write` does **not** authorize `A/T2`,
  `A/shell`, or any task in another execution.
- Only a **pending** request can be approved/rejected; re-deciding a settled
  request raises. Duplicate approvals are safe (idempotent at the store).
- Stale or malformed approvals (unknown execution, task not in the plan,
  permission never requested) are rejected.
- Rejection is durable: `retry_task` re-opens a failed task, but the durable
  `rejected` record means the gate reappears — retry never re-grants.
- The approval scope is active only for the duration of one task execution
  (`try/finally`), so no request can leak across executions.

The lifecycle on gate: tool → `ApprovalRequiredError` → task
`needs_approval` → plan `needs_approval` → **pause**. The CLI/API then calls
`approve`/`reject`, and `resume` continues exactly where it stopped.

```
ControlPlane.approve(execution_id, task_id, permission, approver="user")
ControlPlane.reject( execution_id, task_id, permission, approver="user")
```

`approval_status()` on any request is one of `pending | approved | rejected`.

## Resumability

An execution can be paused (gate or explicit `pause_execution`) and later
resumed idempotently. `resume_execution` is the single canonical continuation
hook:

- skips work already `verifying`/`completed`;
- re-runs `ready`/`running`/`failed` work through the executor;
- pauses again if any remaining task needs approval;
- is restart-safe: all state and the event cursor live in SQLite, so a paused
  execution survives a process restart with strictly monotonic sequence
  numbers (covered by `test_resumed_execution_keeps_seq_monotonic_across_restart`).

## Kanban board contract

`ControlPlane.board(execution_id)` is a read projection over durable state
(plan status + tasks + events + approval requests). Contract:

| Column | Task status(es) |
| --- | --- |
| `backlog` | pending, planned |
| `ready` | ready |
| `running` | running |
| `waiting_approval` | needs_approval, waiting, blocked |
| `verifying` | verifying |
| `done` | completed |
| `failed` | failed, cancelled |

Each card includes `task_id`, `title`, `status`, current approval
state/summary, `started_at` (from the first `task.started` event) and
`completed_at` (durable task field), plus proof counts (artifacts, evidence,
tool calls) so a UI never needs to read task internals.

## Service boundary

`ControlPlane` is the **only** client entry point (the CLI and, as of
Phase 44 (legacy), the HTTP gateway at `/api/executions/*`). It owns construction of
the policy engine with the durable
approver, the router, planner, executor, and board. No client touches SQLite
store internals. Lifecycle: `create_research_execution`, `run/resume`,
`pause`, `cancel`, `approve/reject`, `retry`. Reads: `list_executions`,
`get_execution`, `tasks`, `events`/`events_after`, `board`, `artifacts`,
`evidence`, `result` (a safe summary the CLI/API can render without loading
task internals).

Optional memory surface: `ControlPlane(..., memory=MemoryService)`. When
absent, the execution runtime is fully usable and every `memory_*` call
raises `MemoryNotConfiguredError`. When present, memory operations go only
through the plane: `memory_create*`, `memory_get`, `memory_update`,
`memory_search`, `memory_list`, `memory_archive`, `memory_delete`,
`memory_purge`, `memory_events`, `memory_service`. See
[docs/MEMORY.md](MEMORY.md).

Agent memory retrieval: when a `MemoryService` is wired, `ControlPlane`
automatically registers the read-only `search_memory` agent tool (permission
`memory.read`) into the tool registry it hands the `PolicyEngine` — default
builds and injected registries alike — so the researcher can collect durable
memory as untrusted evidence. Without memory the tool is absent, and the rest
of the runtime is unchanged.

Automatic chat recall (legacy Phase 42) is a *separate* application-layer path that
shares the same `MemoryService → MemoryRetriever → MemoryStore` source of
truth but never runs through the control plane:

- **Explicit** — `search_memory` is agent-invoked, policy-gated, and scoped by
  the work-function context (see [docs/MEMORY.md](MEMORY.md)).
- **Automatic** — the HTTP chat API (`personal_ai.server.complete_chat` +
  `personal_ai.memory.chat.ChatMemory`) performs a small deterministic recall
  before the model call: bounded query (≤ 200 chars, derived from the current
  user message), at most 3 memories, default global scope, strictly read-only,
  no events, no approvals, no policy consultation, no model call to build the
  query. The rendered block is appended *below* the user request and is
  explicitly labeled UNTRUSTED reference data — it can never become
  instructions, permissions, approvals, model routing, or agent selection.

Both are optional: no `MemoryService` means neither exists and every surface
behaves exactly as before.

## CLI

`python -m personal_ai.execution.cli` — flat argparse, `--json` for machine
consumers. `--database` is the orchestration DB (execution + memory live in
the same SQLite file); `research` additionally needs `--corpus` (the
personal-knowledge DB) to wire the corpus tools.

```
research OBJECTIVE --corpus KNOWLEDGE_DB [--json]
execution list [--json]
execution show EXECUTION_ID [--json]
execution events EXECUTION_ID [--cursor N] [--json]
execution board EXECUTION_ID [--json]
execution approve EXECUTION_ID TASK_ID PERMISSION [--approver NAME]
execution reject  EXECUTION_ID TASK_ID PERMISSION [--approver NAME]
execution resume EXECUTION_ID [--json]
execution pause  EXECUTION_ID [--json]
execution cancel  EXECUTION_ID [--json]
execution retry  EXECUTION_ID TASK_ID [--json]
memory add CONTENT [--kind KIND] [--scope SCOPE] [--scope-id ID] [--summary S]
         [--source-id ID] [--confidence F] [--importance F] [--expires-at TS]
         [--json]
memory list [--status active|archived|deleted] [--json]
memory show MEMORY_ID [--json]
memory search QUERY [--scope SCOPE] [--scope-id ID] [--limit N]
           [--include-expired] [--json]
memory archive MEMORY_ID [--json]
memory delete MEMORY_ID [--json]
memory purge MEMORY_ID [--json]
```

Example (research runs, fails verification on an empty/mismatched corpus, and
the failed task is retried and cancelled):

```bash
uv run python -m personal_ai.execution.cli --database orch.db \
    research "What are my goals?" --corpus knowledge.db
# exec-<hex>  status=failed
# [verifying] exec-<hex>-research   ...
# [failed]    exec-<hex>-verify     Verify research evidence
# events: 14
```

## HTTP API contract (implemented)

`src/personal_ai/server.py` serves an OpenAI-compatible chat API **and** a
read/write gateway over the control plane. It is still not a UI server and
never touches SQLite directly — every handler goes through application
services (`ControlPlane`, `WorkoutQueryService`, in-app memory). Base URL
defaults to `http://127.0.0.1:8080/v1` for chat and `/api/...` for the
control-plane/read surfaces.

Model surface:

- `GET /v1/models` → lists exactly `personal-ai` (the served id for the whole
  surface; the underlying Ollama model is internal and never exposed).
- `POST /v1/chat/completions` → non-streaming (default) or streaming
  (`"stream": true`; SSE frames, `data: [DONE]`). `model` in the request is
  accepted and ignored. Memory provenance (ids/kinds/scores/ranks only) is
  surfaced in the response metadata. Streaming errors surface as real HTTP
  statuses before the stream begins, because the agent run completes before
  the first SSE frame is sent.

Control-plane endpoints (1:1 onto `ControlPlane`; JSON shapes match the
`--json` CLI):

```
POST   /api/executions                      create execution (objective)
GET    /api/executions                      list executions
GET    /api/executions/{id}                 get execution
GET    /api/executions/{id}/board           kanban projection
GET    /api/executions/{id}/events?after_seq=N   append-only event stream
GET    /api/executions/{id}/approvals       pending/decided approval requests
POST   /api/executions/{id}/resume          resume / run
POST   /api/executions/{id}/pause           pause
POST   /api/executions/{id}/cancel          cancel
POST   /api/executions/{id}/retry           JSON body {task_id}
POST   /api/executions/{id}/approve         JSON body {task_id, permission}
POST   /api/executions/{id}/reject          JSON body {task_id, permission}
```

Approval bodies are **exact**: a request with `{"approved": true}` or any
extra field is rejected (400) before it reaches the plane — a chat client can
never widen approval scope. The approve/reject decision echoes only
`{execution_id, task_id, permission}`; the outcome is read from
`/approvals`.

Memory surface (read-only over HTTP; mutations stay in-app/CLI):

```
GET /api/memory                 ?status=active|archived|deleted
GET /api/memory/search          ?q=...&limit=10&scope=...&scope_id=...
GET /api/memory/{id}
```

Workout surface (read-only):

```
GET /api/workouts               ?date_from=&date_to=&program_id=&source_file=&limit=
GET /api/workouts/stats
GET /api/workouts/history       ?limit=30
GET /api/workouts/exercises     ?name=
GET /api/workouts/{workout_id}
```

Error mapping (FastAPI exception handlers):

| Condition | Status | `error.type` |
| --- | --- | --- |
| Unknown execution / memory id | 404 | `not_found` |
| Plan/task illegal transition | 409 | `transition_error` |
| No pending approval for scope | 409 | `not_complete` |
| Non-boolean `stream` / malformed body | 400 | `bad_request` |
| Execution/Memory/Workout service not configured | 503 | `service_unavailable` |

Shapes: `{"error": {"type": ..., "message": ...}}`, consistent with the
existing chat error contract.

## Open WebUI: hybrid decision

Open WebUI remains a **chat** front end (see `OPEN_WEBUI.md`); it is optional
and never a dependency of the runtime. It talks to Personal AI as a pure
client — there is no reverse dependency:

- Open WebUI → `/v1/chat/completions` → Agent (grounded personal chat).
- Control-plane operations (Kanban board, approvals, execution history) are
  served under `/api/...` for a **dedicated** control-plane UI to consume —
  they are async and require explicit approvals, so they are deliberately kept
  out of the chat completion loop.
- Approvals can never be granted from chat: the chat layer has no approval
  surface, and the approval endpoints require exact `{task_id, permission}`
  bodies whose scope is checked against a *pending* durable request.

## Security

- Tool execution remains strictly policy-gated; approvals are scoped and
  durable; nothing here executes model-generated code.
- Event payloads and approval records expose only safe identifiers/summaries.
- Corpus text is evidence only: it can never create approvals, change
  permissions, or alter the static `PolicyEngine` (regression test
  `test_prompt_injection_cannot_change_policy_or_request_approvals`).
- No network, no Ollama, no shell, no eval.
- Chat memory (legacy Phase 42) is wired at the application layer only — the server
  never touches SQLite; it consumes a pre-built `ChatMemory` and stays behind
  `application → MemoryService → MemoryRetriever → MemoryStore`.

## Roadmap (after legacy Phase 39)

Legacy Phase 39 is implemented and shipped, and the `/api/executions/*` HTTP
gateway (legacy Phase 44) now serves the durable contract described above. Remaining
planned work:

- A dedicated control-plane UI (Kanban + approvals) consuming the `/api/...`
  contract — this does **not** exist yet.
- Multi-agent task graphs beyond the researcher → verifier workflow.
- Agent memory tools are live (`search_memory`, legacy Phase 41); further
  document-retrieval and memory producer surfaces beyond the current
  allowlist are future work (see `docs/ROADMAP.md`).