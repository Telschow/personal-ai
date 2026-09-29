# Privacy Testing

## Requirements
Public test suite must work without real emails, ChatGPT exports, documents, financial info, credentials, personal databases.

## Tests
- No private paths in generated output
- No sensitive data in logs
- No secrets in fixtures
- No private data in test reports
- Ingestion boundary tests prevent scanning .git, .ssh, .env, credentials
- .env never tracked
- Private directories never tracked

## Ingestion Boundary Tests
Prove ingestion cannot accidentally traverse .git, .ssh, .env, credential directories, system directories, private runtime directories unless explicitly configured.

## Secrets Regression
Automated checks for secrets patterns in tracked files.
Use gitleaks/detect-secrets in CI.

## Privacy Isolation
Memory writes explicit and auditable.
Logging never includes prompts with private data, retrieved documents, full email bodies, tokens, API keys, credentials, private memory contents.

## Synthetic Fixtures Policy
All fixtures in tests/fixtures/synthetic/ must use example.invalid domains and synthetic names.

