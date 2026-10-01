# Publication Threat Model

> **Historical audit record — not a current-state security assessment.**
>
> This document is the publication audit performed *before* the repository was
> made public. The findings in "Current Findings" below describe the repository
> **as it stood at that time** and are retained as evidence of what the audit
> caught. They are *not* a description of the current tree, and several have
> since been remediated.
>
> For the current privacy posture, see `PUBLIC_DATA_POLICY.md` and the
> regression tests in `tests/test_privacy_regression.py`, which are enforced in
> CI. Do not read "Current Findings" below as an up-to-date security claim.

## Adversary Capability
Clone repo, inspect all files, git history, branches, tags, commit messages, metadata, run software, execute examples, inspect artifacts.

## Information Leakage Vectors

1. Tracked source code containing personal data
2. Git history containing deleted sensitive files
3. Commit messages/author metadata exposing identity
4. Configuration files with secrets
5. Test fixtures with real personal data
6. Documentation with personal examples
7. Generated artifacts committed (db, embeddings, logs)
8. Browser history / email exports in repo
9. File paths revealing username/machine
10. Embeddings/vector stores allowing reconstruction

## Current Findings

Findings as recorded at audit time (historical). Personal identifiers are
described by category rather than restated verbatim.

- job_agent/profile/profile.yaml tracked with the repository owner's real name, home city, education, employment history, and salary constraints -> SENSITIVE_PERSONAL
- Git author email address visible in commit metadata -> PERSONAL
- jobs.sqlite3 empty file tracked at root -> UNKNOWN
- README claimed repo contains no personal data, contradicted by the above
- previous_project_and_raw_data/ contained browser history with API keys, gitignored but present on FS
- Financial_data/ contained bank statements, gitignored
- data/ contained SQLite DBs with personal data, gitignored
- No secrets found in tracked source code
- No embeddings/vector stores tracked
- No .env files tracked except example

## Remediation Status Since Audit

| Finding | Status |
|---------|--------|
| `job_agent/profile/profile.yaml` tracked | Remediated — only `profile.yaml.example` is tracked; the real file is gitignored |
| `jobs.sqlite3` tracked at root | Remediated — untracked and covered by `*.db` / `*.sqlite3` ignore rules |
| Personal job-search config (`config.yaml`, `company_radar.yaml`) | Remediated — untracked; synthetic `.example.yaml` files are published instead |
| Author email in this document | Remediated — described by category, not restated |
| Personal data in docs and test fixtures | Remediated — synthetic identities only, enforced by `tests/test_privacy_regression.py` |
| Local-only personal directories | Unchanged by design — remain gitignored and untracked (`Financial_data/`, `Email_Outlook/`, `Workouts/`, `data/`) |

## Impact
Public release would expose identity, career history, salary expectations, location, employer history. Enables social engineering, targeted phishing, doxxing.

## Classification

**NOT-SAFE-TO-PUBLISH in the form audited.** This classification applied to the
repository as it stood at audit time and is preserved as historical evidence.
It has since been remediated; see "Remediation Status Since Audit" above and
`PUBLIC_DATA_POLICY.md` for the current, enforced posture.
