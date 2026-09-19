# SECURITY.md

Threat model and mitigations for the job agent.

## Principles

1. **The workspace is the only sandbox.** Jobs are fetched only from
   configured sources; `direct_company_domains`, sitemap URLs and RSS roots
   are explicit allow-listed config, never free-form model input.
2. **Model output is never executable.** No shell execution, no `eval`/`exec`,
   no dynamic imports, no toolchain that turns model text into commands.
3. **Secrets never leave their write site.** Tokens are read from config/env at
   construction, never echoed in `sources list`, reports, or structured logs.
4. **Personal data stays private.** CV/profile content, job descriptions, and
   prompts are never written to logs. Logs carry operationally necessary
   identifiers (job_id, source, duration, counts) only.

## Automatic submission / publishing

There is no execution path: `auto_submit` and `auto_publish` are
**hard-blocked** by the config validator (`ValueError`) so a misconfiguration
cannot silently enable them. Application generation (tailoring, cover letters)
is a future, always-approval-gated stage.

## Secrets inventory

| Secret          | Storage site        | Usage                                       |
|-----------------|---------------------|---------------------------------------------|
| ATS tokens      | `config.yaml` / env | Fetching public board APIs                   |
| LLM credentials | env (`JOB_AGENT_LLM_BASE_URL`) | Ollama (local) only; no cloud keys    |

`requirements.txt`/`pyproject.toml` pins no secrets. `.env.example` documents
the optional webhook variables (not implemented) — never commit a real `.env`.

## Network behavior

- All fetches go to configured source URLs only.
- Timeouts and a `MAX_RESPONSE_BYTES` cap bound each request.
- Failures are per-source isolated; a hostile/broken source cannot take down
  the scan, and its error is logged without response content.

## Logging rules (enforced in code review)

- Never log: JD text, CV text, cover letters, prompts, model responses,
  tokens, full cookies, or salary values beyond aggregate digest totals.
- Structured `key=value` fields only: `event`, `source`, `operation`, `job_id`,
  `duration`, `counts`, `status`, `error` (truncated).

## Reporting

The digest and JSON reports contain shortlisted job titles, companies,
locations, salary bands and scores — the same information already visible to
whoever can run `scan`. They never embed tokens or CV content.

## Sensitivity of stored data

`output/*.sqlite3` contains full job descriptions of *public* postings only.
Treat it as you would any local store; it never contains credentials.