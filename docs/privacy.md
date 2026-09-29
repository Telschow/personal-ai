# Privacy

Personal data is intended to remain local.

* Private runtime data is not included in Git
* Examples use synthetic data with example.invalid
* Credentials belong in local .env, never committed
* Users must inspect data sources before ingestion
* External model providers receive data only when explicitly configured
* Local models can be used

Data directory: `data/private/` is gitignored.

Never commit real emails, ChatGPT exports, financial records, medical records, credentials, tokens, private databases, real embeddings.

See `PUBLIC_DATA_POLICY.md`.
