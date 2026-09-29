# Ingestion Audit

## Executive Summary

This audit examines the ingestion pipelines of the `personal-ai` repository. The system ingests personal data from diverse sources including documents (PDF, DOCX, TXT, MD), email exports (Outlook, Google Takeout, mbox), browser history (Chrome, Firefox), chat exports (ChatGPT, Gemini), financial data (CSVs, OFX), workout data (Garmin, Strava), filesystem files, and more. Each source has a dedicated adapter that normalizes data into `SourceRecord` objects, which then flow through a unified ingestion pipeline: extraction → classification → chunking → (optional) embedding → storage.

**Overall Assessment:** The ingestion architecture is modular, deterministic, and privacy-conscious. Each source adapter is narrow and isolated, preventing failure propagation. The pipeline emphasizes idempotency (content-hash based), safety (no code execution), and traceability (every chunk traces back to source). Key risks involve the potential for accidental ingestion of files outside intended directories through broad glob patterns or misconfigured paths, the sensitivity of ingested data (emails contain full conversations, financial data contains transaction details, Chrome history contains URLs with authentication tokens), and the lack of content-based access controls beyond path-based restrictions.

---

## Ingestion Architecture Overview

```
Source Data
        ↓
Source Adapter (parser, normalization, validation)
        ↓
SourceRecord (content_hash, source_type, metadata)
        ↓
document_from_source_record (compute content_hash, ID)
        ↓
extract_text (text extraction, optional vision augmentation)
        ↓
classify_document (TEXT_HEAVY / IMAGE_HEAVY / OTHER)
        ↓
structured_extraction (LLM-backed, optional, deterministic normalize)
        ↓
chunk_document (deterministic, fixed/variable size, overlap)
        ↓
DocumentStore.add (content_hash idempotency)
        ↓
(chunk embeddings - optional, separate backfill step)
        ↓
Ingestion Complete
```

### Key Characteristics
- **Idempotent:** Re-ingesting unchanged document creates zero new rows (content-hash based)
- **Bounded:** File size, section count, and section length limits prevent resource exhaustion
- **Safe:** No code execution, no instruction interpolation, no external calls during ingestion
- **Traceable:** Every chunk traces back to source document and exact position (page/offset)
- **Privatized:** No personal data leaves the local machine during ingestion
- **Provider Isolation:** Each source adapter fails independently; one bad source doesn't abort the whole scan

---

## Source Adapters Examined

| Source | Type | Key Characteristics | Privacy Considerations |
|---|---|---|---|
| **filesystem** (`sources/filesystem.py`) | Filesystem | Parses PDF, DOCX, TXT, MD files; uses appropriate libraries (pymupdf, python-docx) | Can ingest any file matching patterns; path traversal risk if not validated |
| **email** (`sources/email.py`) | Email Export | Parses mbox files (Google Takeout, Thunderbird); extracts headers, normalizes body | Contains full conversation content; headers may contain sensitive metadata |
| **financial** (`sources/financial.py`) | Financial CSV | Parses bank, card, investment, portfolio CSUs; redacts IBANs, creditor references | Contains transaction amounts, dates, counterparties; redacts stable identifiers |
| **chatgpt** (`sources/chatgpt_loader.py`) | Chat Export | Parses ChatGPT conversation shards; preserves conversation tree and branching | Full conversation history with timestamps, attachments, metadata |
| **gemini** (`sources/gemini_loader.py`) | Chat Export | Similar to ChatGPT loader; handles Gemini conversation structure | Full conversation history |
| **chrome_history** (`sources/chrome_history.py`) | Browser History | Parses Chrome History JSON; extracts visits, searches, downloads | Contains URLs with API keys, auth tokens, sensitive search terms |
| **youtube_history** (`sources/youtube_history.py`) | Browser History | Parses YouTube history JSON; extracts watch history, searches | Viewing patterns, search terms, video metadata |
| **keep** (`sources/keep.py`) | Note Export | Parses Google Keep notes JSON; extracts text, labels, timestamps | Personal notes, lists, reminders with timestamps |
| **notebooklm** (`sources/notebooklm.py`) | Note Export | Parses NotebookLM activity JSON; extracts sources, notes, timestamps | AI-generated notes and source materials |
| **structured_page** (`sources/structured_page.py`) | Web Scraping | Fetches and parses structured web pages (JSON-LD, Open Graph, etc.) | Public web data; could inadvertently ingest sensitive pages if misconfigured |
| **mcp** (`sources/mcp.py`) | MCP Integration | Model Context Protocol integration (not fully implemented) | Depends on MCP server capabilities |

---

## Source Adapter Security Properties

| Adapter | Path Validation | Recursive Scan Risk | Home Directory Access Risk | Network Dependencies | Content Validation |
|---|---|---|---|---|---|
| **filesystem** | ⚠️ Basic path checking | ✅ Yes (if directory input) | ✅ Yes (if home dir in path) | ❌ None | ⚠️ File type checking only |
| **email** | ✅ Strict mbox validation | ❌ No (single mbox file) | ❌ None | ❌ None | ✅ RFC 822 parsing |
| **financial** | ✅ Path validation | ❌ No (single CSV file) | ❌ None | ❌ None | ✅ CSV structure + redaction patterns |
| **chatgpt** | ✅ Strict JSON validation | ❌ No (single shard file) | ❌ None | ❌ None | ✅ JSON schema validation |
| **gemini** | ✅ Strict JSON validation | ❌ No (single file) | ❌ None | ❌ None | ✅ JSON schema validation |
| **chrome_history** | ⚠️ Basic path checking | ❌ No (single JSON file) | ❌ None | ❌ None | ✅ JSON structure validation |
| **youtube_history** | ⚠️ Basic path checking | ❌ No (single file) | ❌ None | ❌ None | ✅ JSON structure validation |
| **keep** | ⚠️ Basic path checking | ❌ No (single file) | ❌ None | ❌ None | ✅ JSON structure validation |
| **notebooklm** | ⚠️ Basic path checking | ❌ No (single file) | ❌ None | ❌ None | ✅ JSON structure validation |
| **structured_page** | ✅ URL validation | ✅ Yes (if crawling) | ✅ Yes (if home dir in URL) | ✅ Yes (HTTP requests) | ✅ HTML parsing + structure extraction |
| **mcp** | N/A (not implemented) | N/A | N/A | ✅ Yes (MCP server) | N/A |

---

## Ingestion Pipeline Security Properties

| Pipeline Stage | Security Properties | Risk Mitigation |
|---|---|---|
| **Source Adapter** | - Path-based access control<br>- Format validation<br>- Content normalization<br>- Error isolation per source | - Adapters validate input paths<br>- Errors isolated per source (don't abort scan)<br>- Sensitive data redacted where possible (financial redactors) |
| **Extraction** | - Text extraction only (no code execution)<br>- Bounded file/section sizes<br>- Vision augmentation optional and cached | - No instruction interpolation<br>- Size limits prevent resource exhaustion<br>- Vision extraction is optional and opt-in |
| **Classification** | - Deterministic text classification<br>- No external dependencies | - Pure text analysis; no network calls<br>- Safe heuristics for document type |
| **Structured Extraction** | - LLM-backed but deterministic normalization<br>- Provenance stamping (vision model/prompt version)<br>- Re-extraction on config/prompt change | - Output validated and normalized<br>- Idempotent re-extraction on config change<br>- Fail-closed on LLM failure |
| **Chunking** | - Deterministic chunking (fixed/variable size)<br>- Content-hash based IDs<br>- Storage alignment (replace drifted sets) | - Predictable chunk boundaries<br>- Idempotency prevents duplicates<br>- Embedding cleanup before chunk replacement |
| **Storage** | - Content-hash idempotency (DocumentStore)<br>- Foreign key constraints<br>- Transactional writes | - No duplicates on re-ingest<br>- Referential integrity maintained<br>- Atomic operations prevent corruption |
| **Embedding (Optional)** | - Separate backfill step<br>- Content-hash based chunk IDs<br>- Embedding store deletion before chunk replacement | - Embeddings derived from chunks; no independent storage<br>- Clean deletion prevents stranded vectors |

---

## Ingestion Risks & Concerns

| # | Category | Risk | Severity | Evidence |
|---|---|---|---|---|
| I1 | Path Traversal / Directory Escape | `filesystem` source adapter and CLI `--source` flag could potentially allow ingestion of files outside intended directories if paths are not properly validated. The `filesystem` tool in the agent has path validation but CLI ingestion may be less restrictive. | HIGH | `filesystem.py` `SourceAdapter.discover()` uses `rglob("**/*")` patterns; CLI `cmd_ingest` passes paths directly to `SourceResolver` |
| I2 | Ingestion of Sensitive System Files | If a user points ingestion at home directory (`~/`), `/etc/`, or other sensitive locations, the system could ingest passwords, SSH keys, configuration files, etc. | HIGH | CLI `ingest` command accepts arbitrary paths; no built-in whitelist beyond workspace |
| I3 | Chrome History Sensitivity | Chrome history contains URLs with embedded API keys, authentication tokens, session IDs, and sensitive search terms. Ingestion of this data creates a permanent record in the database. | HIGH | Found `AIzaSy\*` Google API keys and `secret=` parameters in `previous_project_and_raw_data/Chrome/Historial.json` |
| I4 | Financial Data Sensitivity | Financial CSVs contain transaction amounts, dates, counterparties, and merchant information. While stable identifiers (IBANs) are redacted, transaction patterns and amounts remain. | HIGH | `sources/financial.py` drops IBAN-like columns but keeps transaction data |
| I5 | Email Content Sensitivity | Email mbox files contain full conversation content, including potentially sensitive personal or financial discussions. | HIGH | Email adapter extracts full body text from messages |
| I6 | Chat Export Sensitivity | ChatGPT/Gemini exports contain full conversation history with timestamps, attachments, and metadata. | HIGH | Chat export loaders preserve full conversation trees |
| I7 | Vision Extraction Permissions | Vision extraction uses the same Ollama endpoint as chat. If the Ollama instance is shared or logged, image content used for vision extraction could be inferred. | LOW | Vision extraction goes to same Ollama endpoint; local-only by default |
| I8 | Ingestion Performance Degradation | Ingesting very large files (large PDFs, large mbox files) could consume significant memory and processing time. While there are size limits, they may be generous for user expectations. | MEDIUM | `ingestion.py` has bounds but they may not align with user expectations for "large" files |
| I9 | Duplicate Ingestion Detection | While content-hash based idempotency prevents exact duplicates, near-duplicates (slightly modified files) will be stored as separate records. No similarity-based deduplication. | LOW | Standard for personal knowledge systems; similarity deduplication would be expensive |
| I10 | Source Adapter Failures | If a source adapter has a bug that causes it to hang or consume excessive resources, it could block the ingestion pipeline. Errors are isolated per source but could still cause delays. | LOW | Each source runs in sequence; a hung source would block subsequent sources |
| I11 | CLI Ingestion Path Validation | The `cli.py` `cmd_ingest` function uses `args.source or []` directly without validating that the paths are reasonable or safe. | MEDIUM | No validation that source paths are within expected directories or not system-sensitive |
| I12 | Recursive Directory Ingestion | When ingesting a directory, the filesystem adapter uses `rglob("**/*")` which could ingest deeply nested directory structures unexpectedly. | LOW | Standard recursive behavior; users typically expect this but should be warned |

---

## Ingestion Strengths

| # | Strength | Description |
|---|---|---|
| S1 | Source Adapter Isolation | Each source adapter fails independently; a bad email mbox won't prevent Chrome history from being ingested. Errors are isolated per source. |
| S2 | Idempotency Throughout | Content-hash based document IDs ensure re-ingesting unchanged files creates zero new rows. Applies to documents, chunks, and embeddings. |
| S3 | Safety by Design | No code execution during ingestion; text extraction is pure parsing; no instruction interpolation or external calls during extraction. |
| S4 | Traceability | Every document chunk traces back to source document and exact position (page number, offset). Provenance is preserved throughout the pipeline. |
| S5 | Privacy-Focused Redactors | Financial adapter actively redacts IBANs, creditor references, and mandate references from transaction data. |
| S6 | Optional Vision Augmentation | Vision extraction is opt-in; image-heavy documents default to store-without-chunks behavior unless explicitly configured. |
| S7 | Deterministic Processing | Given the same input file, the ingestion pipeline produces identical output every time (content-hash based). |
| S8 | Error Isolation | Source-level failures (`SourceError` subclasses) do not abort the whole ingestion process; other sources continue processing. |
| S9 | Format Validation | Each source adapter validates its input format (mbox, JSON, CSV, PDF, etc.) before attempting extraction. |
| S10 | Bounded Resource Usage | File size limits, section limits, and chunk size limits prevent any single file from consuming excessive resources. |

---

## Recommendations (Non-Modification)

**R1 — Ingestion Path Whitelist/Blacklist:** Add a configurable ingestion path policy that allows administrators to define:
- Whitelisted directories where ingestion is permitted
- Blacklisted directories that are never allowed (e.g., `~/`, `/etc/`, `/.ssh/`)
- Path validation that rejects paths attempting to escape whitelisted directories via `..` or symlinks

**R2 — Chrome History Ingestion Guard:** Add a requirement for explicit user confirmation before ingesting Chrome history. Consider adding a `--confirm-chrome` flag to the CLI that must be present when `chrome_history` source is specified.

**R3 — Financial Data Redaction Review:** Audit the financial adapter's redaction patterns (`_IBAN_PATTERN`, `_CREDITOR_REFERENCE_PATTERN`, `_REF_STYLE_PATTERN`) to ensure they comprehensively redact all stable identifiers while preserving transactional utility.

**R4 — Email Attachment Handling:** Document that email attachments are not extracted or stored by the email adapter; only body text is processed. Consider if this is the desired behavior or if attachment metadata should be captured.

**R5 — Ingestion Progress Reporting:** Add more granular progress reporting during ingestion (files processed, bytes read, errors encountered) beyond the current source-level success/failure counts.

**R6 — Ingestion Size Limit Transparency:** Make the ingestion size limits (file size, section count, etc.) configurable and visible in CLI help/defaults. Users should know what constitutes a "too large" file.

**R7 — Source Adapter Failure Isolation Verification:** Verify that the error isolation in the ingestion pipeline works correctly - that a failure in one source adapter does not prevent processing of subsequent sources.

**R8 — CLI Ingestion Path Sanitization:** Add path normalization and validation in `cmd_ingest` to prevent obvious path traversal attempts (`..`, symlinks to sensitive directories).

**R9 — Ingestion Metadata Minimization:** Review what metadata is stored with each source record. Ensure that only necessary provenance metadata is stored; avoid storing unnecessary details that could increase attack surface.

**R10 — Ingestion Test Coverage for Edge Cases:** Increase test coverage for:
- Malformed source files (broken JSON, invalid CSV, corrupted PDF)
- Extremely large files approaching size limits
- Files with unusual encodings or line endings
- Empty files and files containing only whitespace
- Files that trigger validation errors at each pipeline stage

---

## Confidence Estimate

| Finding | Confidence |
|---|---|
| I1-I3 | High (code review, path pattern analysis) |
| I4-I6 | High (data sensitivity confirmed through sampling) |
| I7-I10 | Medium (architectural analysis, behavioral inference) |
| I11-I12 | Low-Medium (CLI-specific, requires runtime verification) |
| S1-S10 | High (code inspection, documentation review) |
| R1-R10 | Medium (requires implementation to verify effectiveness) |

---

## Next Steps (Post-Audit)

1. Add ingestion path whitelist/blacklist mechanism with validation
2. Implement Chrome history ingestion guard (explicit confirmation required)
3. Audit financial data redaction patterns for completeness
4. Document email attachment handling behavior
5. Add granular ingestion progress reporting
6. Make ingestion size limits configurable and transparent
7. Verify source adapter failure isolation works correctly
8. Add CLI ingestion path sanitization for path traversal
9. Review ingestion metadata for minimization opportunities
10. Increase test coverage for ingestion edge cases and error conditions