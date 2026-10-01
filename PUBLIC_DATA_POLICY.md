# Public Data Policy

## What May Be Committed

* Source code
* Documentation (Markdown, reST)
* Architecture diagrams (Mermaid, PNG, SVG)
* Synthetic test fixtures
* Example configuration files (.env.example, config.example.yaml, company_radar.example.yaml)
* License files
* README and contributing guidelines
* CI/CD configuration
* Dockerfiles
* Build scripts

## What May NOT Be Committed

* Real personal documents (PDF, DOCX, TXT, etc.)
* Real email exports (MBOX, EML, etc.)
* Real chat exports (ChatGPT, Gemini, etc.)
* Real financial records (bank statements, CSVs, PDFs)
* Real medical records
* Real credentials, tokens, API keys
* Private databases (*.db, *.sqlite3, *.sqlite)
* Real vector stores or embeddings derived from personal data
* Private logs containing personal data
* Private paths or machine-specific configuration
* Environment files with real values (.env)
* SSH keys, private keys
* Browser history, cookies
* Any data that could reveal personal identity, health, finances, relationships, career, or location

## Synthetic Data Requirements

All examples and test fixtures must use:
* Domains: example.com, example.org, example.invalid
* Names: Alice Example, Bob Example, etc.
* Addresses, phone numbers, etc. must be clearly fictional
* No real values merely anonymized

## Enforcement

What is actually enforced today:

* **CI secret scanning** — the `secret-scan` job runs Gitleaks and `pip-audit`
  on every push and pull request to `main` (`.github/workflows/ci.yml`).
* **Privacy regression tests** — `tests/test_privacy_regression.py` runs as
  part of the normal test suite in CI. It asserts that synthetic fixtures
  contain no real PII, and that tracked documentation and configuration files
  contain no real contact identifiers (email addresses outside the synthetic
  allow-list, phone numbers, or credential blocks). This is a narrow, explicit
  invariant — not a general PII detector — and does not replace human review.
* **Static analysis** — Ruff, Ruff format, and MyPy gates run in CI alongside
  a CodeQL analysis job.
* **`.gitignore`** — excludes private data directories, runtime databases
  (`*.db`, `*.sqlite3`, and WAL/SHM sidecars), personal job-search
  configuration, backup files, and local AI tooling state.
* **Manual review** — required before merging.

Not currently enforced:

* No pre-commit hook framework is configured. Local commits are not scanned
  before they reach the remote; rely on the CI jobs above.

Contributors adding new data sources or fixtures are responsible for keeping
this policy satisfied; CI will reject prohibited identifiers in tracked
documentation and configuration.

## Policy Updates

This policy may be updated as needed. Contributors must comply with the latest version.