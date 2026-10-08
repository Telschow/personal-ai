# Testing

## Overview

Testing strategy follows the test pyramid with synthetic data only.

## Unit Tests

Deterministic components: parsing, normalization, chunking, metadata, configuration, filtering, routing, storage, retrieval interfaces, serialization.

## Integration Tests

Ingestion → storage, storage → retrieval, retrieval → context, agent → tool, memory → retrieval with synthetic data.

## System Tests

End-to-end CLI workflows, API endpoints, agent loop with tools.

## Privacy Tests

Public test suite works without real emails, ChatGPT exports, documents, financial info, credentials, personal databases.
* No private paths in generated output
* No sensitive data in logs
* No secrets in fixtures
* No private data in test reports
* Ingestion boundary tests prevent scanning .git, .ssh, .env, credentials
* .env never tracked
* Private directories never tracked

## Security Tests

Input validation, path traversal prevention, tool permission gating, prompt injection resistance.

## Evaluation

* RAG evaluation with synthetic corpus
* Memory behavior tests
* Agent tool selection tests

## Performance Tests

Latency, retrieval time, embedding time, model inference time, memory use, storage size, throughput.

## Commands

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check src tests
```

## Synthetic Fixtures

All fixtures under tests/fixtures/synthetic/ use example.invalid domains and synthetic names.

## Reproducibility

Tests are deterministic where possible. No real personal data required.