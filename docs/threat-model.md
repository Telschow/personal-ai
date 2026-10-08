# Threat Model - Personal AI

## STRIDE Analysis

### Spoofing
- **Risk**: Malicious actor forges ingestion source identity
- **Impact**: Untrusted content attributed to legitimate user
- **Mitigation**: Content hash deduplication on ingest; source metadata immutable; verify signer on ingestion

### Tampering
- **Risk**: Untrusted document/email/chat content modified in transit or at rest
- **Impact**: Data integrity loss; prompt injection via modified content
- **Mitigation**: SHA-256 content hash at ingest; read-only DB after warm; FTS5 content verification; corpus audit utilities exist

### Repudiation
- **Risk**: User denies having ingested certain content
- **Impact**: Audit/compliance gap
- **Mitigation**: Immutable append-only ingestion log; content hash stored in DB metadata; deletion leaves audit trail

### Information Disclosure
- **Risk**: PII leaked via logs, error messages, embedding similarity, cross-source leakage
- **Impact**: GDPR Art 3 the-rs vý.ways harrox the path.uids) = keys in the data frames

The number of sample segments / points in the plotted graph.

    Returns
    --------
    "error": "threat"

- **Mitigation**: No PII in logs by default; structured logging only; sanitize prompts before LLM send; embedding filtering; .gitignore enforces data dirs

### Denial of Service
- **Risk**: Zip bombs, huge PDFs, ReDoS regex, unbounded result sets
- **Impact**: Resource exhaustion; service outage
- **Mitigation**: Size caps on ingestion; regex timeout; result limits; pytest-socket offline test

### Elevation of Privilege
- **Risk**: Tool with excessive privileges (write/delete/network/shell/email)
- **Impact**: Full system compromise; data exfiltration
- **Mitigation**: Per-tool privilege review; default-deny; path confinement; confirmation policy; tool schema validation

## LINDDUN Analysis

### Data Collection
- Ingests: Email, browser history, finance, conversations
- Storage: SQLite/FTS5; knowledge.db; raw_data_extracted
- Categories: identifiers, contact, location, contact, profile, preferences

### Data Usage
- Content flows to LLM prompts with tools attached
- Embeddings generated; vectors may be searchable
- Third-party model providers (Ollama local only)

### Data Leakage
- Git history contains opencode.json with credentials
- README claims no personal data contradicted by profile.yaml
- Chrome history in previous_project_and_raw_data/ has API keys
- Financial_data/ contains bank statements
- Email_Outlook/ contains full mailbox

### Data Deletion
- delete_for_document exists for chunks but cascade to FTS/embeddings unverified
- GDPR Art 17 compliance unproven
- No "right to be forgotten" test suite

### Data Regulation
- GDPR Art 5 (minimization), Art 17 (deletion), Art 32 (security)
- No formal DPA; local-only deployment assumed

## Attack Trees

### Prompt Injection via Ingestion
1. Untrusted PDF/email reaches ingestion pipeline
2. Content not sanitized for hidden Unicode/control chars
3. Reaches LLM prompt with tool signatures attached
4. Model executes unintended tool call
5. **Mitigation**: Ingest-time normalization + prompt delimiters + tool output validation

### Data Exfiltration via Embeddings
1. Vector store allows similarity search
2. Malicious query reconstructs nearest neighbor text
3. Embeddings leak training data properties
4. **Mitigation**: Local Ollama only; no remote embedding service; hash-verifiable data pipes

### Supply Chain Attack
1. Dependencies compromised (pip, mcp, uvicorn)
2. Malicious code runs at install
3. **Mitigation**: `uv sync --locked`; pip-audit; CodeQL; Dependabot

## Defenses Summary

| Control | Status | Evidence |
|---------|--------|----------|
| Content hash at ingest | Partial | SHA-256 stored in DB metadata |
| .gitignore enforcement | Good | Sensitive dirs excluded |
| SQL PRAGMAs | Weak | Only `foreign_keys = ON` |
| FTS5 input sanitization | Gap | Direct user query in MATCH |
| LLM prompt delimiters | Missing | No visible sanitization |
| Tool output validation | Missing | SearchResult flows raw to agent |
| Deletion cascade verified | No | Only chunks deleted |
| Code scanning | In place | CodeQL, Gitleaks with a custom rule, dependency review and pip-audit run in GitHub Actions |
| Threat model | Incomplete | PUBLICATION_THREAT_MODEL.md exists; full STRIDE/LINDDUN needed |