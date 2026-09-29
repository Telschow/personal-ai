# Public Data Policy

## Principles
Public repository must contain only source code, documentation, and synthetic test fixtures.

## Allowed
Public
- Source code
- Architecture docs
- Generic examples
- Synthetic fixtures
- README with no personal data

Safe_to_publish
- CI config
- Docker files
- License

## Forbidden
Personal
- Name, email, address, phone
Sensitive_personal
- Career history, salary, employers
- Financial data
- Health data
- Relationships
Confidential
- API keys, tokens
- Database dumps
- Embeddings

## Rules
- No real personal data in tracked files
- No real profile data in examples
- All test fixtures must be synthetic
- Git history must be clean
- Author metadata should use pseudonym if public
- .gitignore must cover: data/, Financial_data/, previous_project_and_raw_data/, *.sqlite3, *.db, *.csv, *.mbox, *.json exports
- PRIVATE_DATA_INVENTORY must stay outside public repo

## Enforcement
Pre-commit hooks for secret scanning
CI check for PII patterns
Manual review before release
