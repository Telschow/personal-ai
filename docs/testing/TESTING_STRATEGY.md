# Testing Strategy

## Test Pyramid

UNIT
- parsing, normalization, chunking, metadata, config, filtering, routing, storage, retrieval interfaces, serialization
- Deterministic components, no LLM, no personal data

INTEGRATION
- ingestion → storage
- storage → retrieval
- retrieval → context
- agent → tool
- memory → retrieval
Synthetic data only

SYSTEM
- End-to-end CLI workflows
- API endpoints
- Agent loop with tools

REGRESSION
- Privacy regression tests
- Configuration regression
- API compatibility

PRIVACY
- Private data never required
- No private paths in output
- No secrets in fixtures
- No sensitive data in logs

SECURITY
- Input validation
- Path traversal prevention
- Tool permission gating
- Prompt injection resistance

EVALUATION
- RAG relevance, recall, precision, ranking
- Memory behavior
- Agent tool selection

PERFORMANCE
- Latency, retrieval time, embedding time, memory use

## Principles
- Synthetic fixtures only
- No real personal data
- Deterministic where possible
- Fail-closed on privacy violations
