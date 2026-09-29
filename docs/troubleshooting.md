# Troubleshooting

## Common Issues

### Server fails to start
* Check Ollama is running: `ollama ps`
* Verify OLLAMA_BASE_URL in .env
* Check port availability

### Ingestion fails
* Ensure source path exists and is readable
* Verify file type is supported
* Check ingestion logs for specific error

### Search returns no results
* Verify data ingested successfully
* Check retrieval mode: keyword, semantic, hybrid
* Ensure embeddings generated for semantic/hybrid

### Agent loops or hangs
* Check max_tool_rounds setting
* Verify tool handlers are not blocking
* Review logs for repeated tool calls

### Permission denied
* Verify PolicyEngine decision in logs
* Check required permissions for tool
* Ensure approver is available for APPROVAL_REQUIRED

## Logs

* Operational logs via structlog
* Error logs with context
* No private data in logs
* Verbose mode: `uv run personal-ai server --verbose`

## Resources

* FAQ: docs/FAQ.md
* Architecture: docs/architecture/
* Security: docs/security.md
* Privacy: docs/privacy.md