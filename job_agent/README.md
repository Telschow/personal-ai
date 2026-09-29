# Personal Job Agent

Local-first, aggressive/global career intelligence agent. It discovers public
jobs, normalizes them, scores them against a structured career profile, and
prepares later stages for tailored applications. Never auto-submits; every
application requires explicit human approval.

## Status: WORKING MVP — READY FOR REAL-WORLD USAGE

End-to-end loop (discover → normalize → score → persist → analyze → tailor →
local UI) is implemented and validated. A bounded broad-discovery benchmark
(17 sources, 144 queries) confirmed **material additional value from non-ATS
sources** (remoteok, Berlin startups) over the ATS surface. See
`docs/PROJECT_STATE.md` for the full benchmark results and engineering history.
STOP ENGINEERING after this checkpoint; future work belongs to explicitly
scoped phases.

## Current profile policy

- Salary floor: **€120k** when compensation is explicitly published
  (hard exclusion below the floor).
- Target compensation: **€150k**.
- Global / very aggressive discovery enabled.
- Defence is treated as a **normal industry**, not penalized automatically.
- Unknown salary is reviewable rather than rejected.
- Relocation is allowed.

## Concepts

- **Sources** — a taxonomy of job sources: ATS board APIs (Greenhouse, Lever,
  Ashby, SmartRecruiters, Workable), RSS/JSON feeds, sitemap discovery, direct
  company pages (JSON-LD `JobPosting`), restricted / browser-automation kinds.
- **Normalization** — every raw job is canonicalized to a stable identity,
  salary-canonicalized to EUR, location-normalized, and classified for
  remote/hybrid/on-site before it is stored.
- **Scoring** — deterministic policy scoring (fit, salary, location, purpose,
  leadership, WLB) with a documented baseline, confidence, and a hard-fail
  path. See `docs/ARCHITECTURE.md`.
- **Lifecycle** — jobs are tracked across scans: stale when not seen for N
  days, closed when missing from N consecutive scans; duplicate groups are
  folded into a single entry.
- **Digest** — a dated markdown report of the current shortlist.
- **Career fit (Slice 2)** — for any stored job, `job-agent fit JOB_ID` runs a
  deterministic, evidence-attributed assessment: `current_fit` (weighted match
  against the derived career profile) vs. `career_upside` (growth potential),
  with strengths/gaps/transferable-skills/positioning and an explicit risk
  note. Deterministic scores are always authoritative; an optional LLM
  narrative (`career.llm.enabled`) explains them but never changes them.
  Evidence is provenance-tagged (verified profile facts / documented parent
  retrieval / inferred automation heuristics) and never auto-promoted.
- **Career documents (Slice 3)** — `job-agent career documents ingest PATH`
  ingests a real CV (.txt/.md/.docx/.pdf), extract bounded structured
  evidence, reconcile it against the evidence base (conflicts are recorded,
  never silently resolved), and persist documents/evidence idempotently
  (content-hash identities; re-ingesting an unchanged file is a no-op).
- **Tailored proposal (Slice 3/3.5)** — `job-agent tailor JOB_ID --cv PATH`
  produces a **proposal-only** tailored CV artifact: deterministic
  requirement→capability→evidence mapping, positioning plan, per-claim
  validation (including entity-level anti-fabrication: fabricated
  employers/titles/dates/scope/team-size are flagged, never silently
  accepted), append-only versioning per job, and an optional LLM polish + a
  semantic mapping refinement that operates strictly over existing evidence.
  All LLM layers are fail-closed: anything malformed, slow, or unverified
  falls back to the deterministic floor, and generated material is never
  authoritative career evidence. Artifacts are inspectable via `job-agent
  career artifacts list|show|diff`.

## Job-Agent MVP (M3) — Local Web UI

The Job-Agent now includes a local HTTP API and web UI mounted in the
Personal AI gateway at `/api/job-agent/*`.

### Start the server

```bash
# From the job_agent directory
python -m personal_ai.server \
  --workspace /path/to/workspace \
  --database /path/to/personal-ai.db \
  --job-db /path/to/jobs.sqlite3 \
  --port 8080
```

Or set environment variables:
```bash
export PERSONAL_AI_WORKSPACE=/path/to/workspace
export PERSONAL_AI_DATABASE=/path/to/personal-ai.db
export JOB_AGENT_DB=/path/to/jobs.sqlite3
python -m personal_ai.server --port 8080
```

Open http://localhost:8080/api/job-agent/health to verify.

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/job-agent/health` | GET | Health check + job counts by user status |
| `/api/job-agent/jobs` | GET | List jobs with filtering (user_status, track, location, min_fit, source, analyzed) |
| `/api/job-agent/jobs/{id}` | GET | Job detail with factual info, analysis, provenance |
| `/api/job-agent/jobs/{id}/status` | POST | Update user status (`NEW`/`SAVED`/`REJECTED`/`APPLIED`) |
| `/api/job-agent/discover` | POST | Run bounded discovery (params: limit_total, limit_per_track, max_pages, dry_run) |

### Web UI

Open http://localhost:8080/ (served from `src/personal_ai/static/`) for a minimal
dashboard:
- Dashboard with counts by user status
- Job list with filtering (status, track, location, min fit score) and sorting
- Job detail modal with factual info, agent analysis (fit score, strengths,
  gaps, transferables, positioning, narrative), provenance, and actions
  (Save, Reject, Applied, Reset, Open Original)
- Run discovery from the UI

### User Status Persistence

Job status (`NEW` → `SAVED` / `REJECTED` / `APPLIED`) is stored in the Job-Agent
SQLite database and survives application restarts. Status is user-owned and
never modified by discovery or analysis pipelines.

### Discovery

`POST /api/job-agent/discover` runs a bounded discovery cycle using the same
catalog-driven planner as the CLI. Typical bounded run:
```json
{
  "limit_total": 30,
  "limit_per_track": 5,
  "max_pages": 30
}
```
Returns per-source pacing, yield accounting, five-level dedup breakdown,
and lifecycle counts.

## Run (CLI)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,discovery]'
cp config.example.yaml config.yaml       # then edit profile/profile.yaml
pytest -q
job-agent scan
```

Disable global search for a fast seed-only scan:

```bash
job-agent scan --no-global-search
```

Preview without persisting (in-memory DB, offline global search):

```bash
job-agent scan --dry-run
```

## Configuration

Copy `config.example.yaml` to `config.yaml` and edit. The `career:` section
controls fit intelligence: knowledge provider (`none` / `personal_ai`), fit
weights, and whether the optional LLM narrative is enabled. See
`config.example.yaml` for all defaults and the full reference.

## Commands

```
job-agent scan [--dry-run] [--no-global-search] [--source NAME]
job-agent profile
job-agent sources list|check
job-agent jobs [--status STATUS] [--json]
job-agent fit JOB_ID [--json] [--no-llm]
job-agent career documents ingest PATH [--json]
job-agent career documents list [--limit N] [--json]
job-agent career evidence list [--conflicts] [--json]
job-agent career artifacts list JOB_ID [--json]
job-agent career artifacts show JOB_ID [--version N] [--json]
job-agent career artifacts diff JOB_ID [--from-version N --to-version M] [--json]
job-agent tailor JOB_ID --cv PATH [--json] [--no-llm] [--no-semantic]
job-agent digest
job-agent stats
job-agent decision JOB_ID approve|reject [--note NOTE]
```

## Career Intelligence GUI

Launch the local web interface for daily job management:

```bash
cd job_agent
uv run python -m job_agent.cli gui
```

or

```bash
uv run python -m streamlit run job_agent/gui/app.py --server.address 127.0.0.1 --server.port 8501
```

The GUI opens at http://127.0.0.1:8501 and provides:

### Pages

- **📊 Dashboard** — Latest crawl, new jobs, active jobs, high-fit jobs, jobs reviewed, applications, interviews
- **📅 Daily Intelligence** — NEW, CHANGED, HIGH-FIT, HIGH-FIT Munich, HIGH-FIT AI/Autonomous, NEWLY SALARY-DISCLOSED, STALE/CLOSING
- **🔍 Job Explorer** — Searchable table with filters (company, provider, role family, location, salary, remote, fit range, application status, feedback status)
- **📋 Calibration** — Feedback queue with next/previous/skip/label/note workflow
- **📑 Shortlists** — Top overall, Munich, AI/Autonomous, Product Leadership, Career Pivot, International
- **📄 CV Generation** — Generate tailored CV from real jobs with evidence manifest
- **💼 LinkedIn** — Optimize LinkedIn profile with current vs recommended view
- **📁 Projects** — Portfolio project recommendations with effort estimates
- **📝 Applications** — Application tracker with stage updates
- **👤 Career Profile** — Read-only career configuration (targets, constraints, preferences)
- **🕷️ Crawl Control** — Last crawl stats, trigger new crawl via CLI
- **🏥 Provider Health** — Provider status (healthy, zero results, failed, timeout)
- **📊 Reports** — Browse generated markdown reports

### Daily Workflow

1. Open Dashboard to see today's opportunities
2. Review Job Explorer or Daily Intelligence for new/high-fit jobs
3. Use Calibration queue to review and label jobs
4. Generate CV for target jobs
5. Track applications in Applications page
6. Review Provider Health and Reports as needed

### Feedback Workflow

- Click any job to view details
- Submit feedback with label (strong_interest, interested, maybe, not_interested, wrong_role, etc.) + optional note
- Feedback persists immediately and updates calibration metrics
- No automatic ranking weight changes from GUI

### CV Workflow

- Select target job from explorer
- Configure language, master CV path, LLM options
- Generate CV with evidence manifest
- Preview markdown output
- Save artifact for future use

### LinkedIn Safety

- LinkedIn recommendations do **not** mutate profiles automatically
- Current profile is read-only
- User must manually apply changes
- No scraping, login automation, or automated profile modification

## Safety

- The system never submits applications or publishes anything automatically
  (`auto_submit` / `auto_publish` are hard-blocked by the config validator).
- The filesystem workspace is the only sandbox; no shell/network execution of
  model output.
- LLM output is proposal-only and fail-closed: the deterministic pipeline is
  always authoritative, every LLM claim must reference allow-listed evidence
  ids, and generated career material is a proposal — it does not become
  authoritative career evidence automatically.
- Secrets (tokens, API keys) live in environment or `config.yaml` and are
  redacted from every diagnostic surface.
- see `docs/SECURITY.md`.