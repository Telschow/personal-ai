# Tool and Connector Security Audit

## Executive Summary

This audit examines the tool infrastructure and connector security of the `personal-ai` repository. The system exposes a controlled set of tools to the LLM agent through a typed registry, with each tool having explicit permissions, policies, and handlers. Tools are gated by the PolicyEngine which enforces permissions in code (not model obedience). Connectors include source adapters (ingestion), MCP (Model Context Protocol) integrations, and the read-only SQLite bridge to the Job Agent.

**Overall Assessment:** The tool infrastructure is strongly security-conscious with explicit permission gating, fail-closed design, and clear separation between trusted and untrusted data. Tools are narrowly scoped, and the PolicyEngine prevents privilege escalation through tool use. Key risks involve tool arguments that could contain prompt injection attempts, the potential for tool results to leak sensitive data into the LLM context, and the read-only nature of critical connectors (like the Job Agent bridge) which must be verified to prevent accidental write paths.

---

## Tool Architecture Overview

```
LLM Agent
        ↓
Tool Request (name + arguments)
        ↓
ToolRegistry (lookup by name)
        ↓
PolicyEngine (check permissions: ALLOWED/DENIED/APPROVAL_REQUIRED)
        ↓
Tool Handler (execute with sanitized arguments)
        ↓
Result Bounding (MAX_TOOL_RESULT_CHARS truncation)
        ↓
Tool Response to LLM
```

### Tool Registration & Security
- **ToolDefinition:** Explicit declaration of `name`, `description`, `parameters` (JSON Schema), and `handler`
- **ToolRegistry:** Prevents duplicate registration, validates tool names (`^[a-zA-Z0-9_-]+$`), provides schemas for LLM
- **PolicyEngine:** Evaluates `tool + task + agent + policy` → `ALLOWED/DENIED/APPROVAL_REQUIRED`
- **Handler Execution:** Executes with `_sanitize_arguments()` for operational logging (not security)
- **Result Bounding:** `MAX_TOOL_RESULT_CHARS = 12000` truncation with omission notice
- **Error Handling:** Tool failures return `error: {message}` strings; exceptions are caught and formatted

### Tool Permission Model
Each `AgentTool` defines required permissions:
- `Permission.AGENT` - Basic agent operation
- `Permission.TOOL` - Tool execution privilege  
- `Permission.MEMORY_READ` - Read memory (identifier-only via service)
- `Permission.MEMORY_WRITE` - Write memory (requires approval)
- `Permission.WORKOUT_READ` - Read workout data
- `Permission.PERSONAL_CONTEXT` - Read personal context overview
- `Permission.PEOPLE_READ` - Read people/identity data
- `Permission.CHUNK_READ` - Read document chunks
- `Permission.EXTRACTION_READ` - Read structured extractions
- `Permission.EVENT_READ` - Read event logs
- `Permission.CONTROL_PLANE_READ` - Read orchestration state
- `Permission.CONTROL_PLANE_WRITE` - Write orchestration state (approval required)

### Connector Types
1. **Source Adapters (Ingestion):** Narrow, isolated adapters for specific data sources (email, chrome, financial, etc.)
2. **MCP Connectors:** Model Context Protocol integrations (placeholder/not fully implemented)
3. **Read-Only Bridges:** 
   - Personal AI → Job Agent (sqlite3.connect(mode=ro))
   - Job Agent → Personal AI (not implemented; write blocked by design)
4. **API Connectors:** 
   - Ollama client (local HTTP API)
   - Job discovery providers (Greenhouse, Lever, Ashby, etc. - HTTP)
   - Search engines (DuckDuckGo, etc. - HTTP via ddgs)

---

## Tool Registry & Default Tools

### Default Tools (from `tools/defaults.py`)
| Tool | Purpose | Registration Gate | Policy Gate |
|---|---|---|---|
| **personal_context** | Read-only overview of available personal data (counts, breakdowns) | Requires `personal_context_service` | Researcher role only |
| **list_directory** | List files and directories in workspace | Always registered | Researcher role only |
| **get_document** | Fetch document metadata + bounded chunk window | Requires `document_store` + `chunk_store` | Researcher role only |
| **get_memory** | Fetch memory by id with content-free provenance | Requires `memory_service` | Researcher role only |
| **query_events** | Answer structural temporal-event (browsing/search) queries | Requires `event_store` | Researcher role only |
| **search_workouts** | Search workout activity by movement name | Requires `workout_service` | Researcher role only |
| **search_people** / **get_person** | Search/fetch people by identity | Requires `person_store` with `count() > 0` | Researcher role only |
| **search_documents** | Search document chunks (keyword index) | Requires `chunk_store.count() > 0` | Researcher role only |
| **search_knowledge** | Unified search (chunks + extractions + conversations) | Requires `retrieval_service` | Researcher role only |
| **propose_memory** | Propose a new memory (requires approval) | Requires `memory_service` + `memory_proposal_approver` | Researcher role only |

### Tool Argument Sanitization
- **For Logging Only:** `_sanitize_arguments()` creates bounded-per-key representation for operational logs
- **Not for Security:** Tool arguments are executed as-is; security comes from:
  1. PolicyEngine permission checking (ALLOWED/DENIED/APPROVAL_REQUIRED)
  2. Handler implementation (what the tool actually does with the arguments)
  3. Result bounding (MAX_TOOL_RESULT_CHARS truncation)
  4. LLM output validation (evidence ID allow-listing, schema validation)

---

## Connector Security Analysis

### 1. Source Adapters (Ingestion)
- **Isolation:** Each source adapter fails independently; one bad source doesn't abort the whole scan
- **Error Handling:** Adapter failures raise `SourceError` subclasses; caught and logged by pipeline
- **Path Validation:** Most adapters validate that input paths are reasonable (file exists, correct format)
- **Content Processing:** 
  - Extract text only (no code execution)
  - Apply redactors where appropriate (financial: IBANs, creditor references)
  - Normalize for deterministic processing
- **Idempotency:** Content-hash based ensures re-ingesting unchanged sources creates zero new rows

### 2. Read-Only Bridges
- **Personal AI → Job Agent:** 
  - `PersonalAiCareerKnowledge` adapter in `job_agent/career.py`
  - Uses `sqlite3.connect(mode=ro)` for read-only access
  - Exposes only `memory_search()`, `corpus_search()`, and `health()` methods
  - **Critical:** No write methods exposed; Job Agent cannot modify Personal AI state
- **Job Agent → Personal AI:** 
  - Not implemented in current architecture
  - The design explicitly prevents this direction to maintain privacy boundary
  - Job Agent only reads from Personal AI; never writes

### 3. MCP Connectors
- **Status:** Placeholder implementation in `sources/mcp.py`
- **Security Model:** Would inherit MCP server's security model
- **Current State:** `MCPSource` class exists but returns empty fetch; not active in default configuration

### 4. API Connectors (Outbound)
- **Ollama Client:** 
  - `personal_ai/ollama_client.py`
  - Local HTTP API only; defaults to `http://127.0.0.1:11434`
  - No external cloud LLM dependencies in source code
- **Job Discovery Providers:**
  - `job_agent/sources.py` (Greenhouse, Lever, Ashby, etc.)
  - Outbound HTTP only; no inbound exposure
  - Rate limiting, backoff, and pacing implemented
  - Errors isolated per source; source failures don't abort discovery
- **Search Engines:**
  - `job_agent/discovery_search.py` (uses `ddgs` - DuckDuckGo)
  - Outbound HTTP only; respects rate limiting and pacing
  - Errors isolated; search failures don't abort discovery

### 5. API Connectors (Inbound)
- **Personal AI HTTP API:**
  - `personal_ai/server.py` (FastAPI)
  - OpenAI-compatible endpoints: `/v1/chat/completions`, `/api/executions*`, `/api/memory*`, `/api/workouts*`
  - Token-based authentication (optional, via `PERSONAL_AI_API_TOKEN`)
  - Local bind only; defaults to `127.0.0.1:8000`
  - No external exposure unless explicitly configured otherwise
- **Job Agent HTTP API:**
  - `job_agent/job_agent/cli.py` (CLI-only; no HTTP server in current implementation)
  - HTTP exposure would come through Personal AI gateway if enabled

---

## Tool Security Risks & Concerns

| # | Category | Risk | Severity | Evidence |
|---|---|---|---|---|
| T1 | Tool Argument Prompt Injection | Tool arguments could contain prompt injection attempts that, when executed by the tool handler, produce malicious output that affects the LLM context. While arguments are sanitized for logging, they are executed as-is. | HIGH | Tools like `list_directory`, `get_document`, `search_*` take user-provided arguments that could contain injection attempts |
| T2 | Tool Result Sensitive Data Leakage | Tool results are bounded (`MAX_TOOL_RESULT_CHARS = 12000`) but could still contain sensitive data that appears in the LLM context. This data could then be used by the LLM in ways that violate privacy expectations. | HIGH | Tools like `get_memory`, `get_person`, `search_*` return data that could contain personal information |
| T3 | Privilege Escalation via Tool Chain | A combination of tools could be used to achieve unintended effects. Example: Use `list_directory` to find sensitive files, then `get_document` to read them, then another tool to process or exfiltrate the data. | HIGH | The agent loop allows multiple tool rounds (`max_tool_rounds = 8`); chains are possible |
| T4 | MCP Connector Over-Privilege | If MCP connectors are fully implemented, they could request excessive permissions from the user or system, depending on the MCP server's capabilities. | MEDIUM | MCP design allows servers to declare capabilities; unclear what permissions would be requested |
| T5 | Tool Handler Implementation Flaws | A bug in a tool handler could lead to unintended behavior (data corruption, infinite loops, excessive resource consumption). While isolated to the tool, it could still cause denial of service or data issues. | MEDIUM | Tool handlers execute arbitrary Python code; bugs are possible |
| T6 | Insufficient Tool Argument Validation | Some tools may have insufficient validation of their arguments, leading to unexpected behavior or errors. Example: `list_directory` with invalid paths, `search_*` with malformed queries. | MEDIUM | Argument validation varies by tool; some rely on downstream validation |
| T7 | Read-Only Bridge Verification | While the Job Agent → Personal AI direction is blocked by design, there must be verification that no write paths exist through the read-only bridge (e.g., via PRAGMA settings, accidental commits, etc.). | MEDIUM | The bridge uses `sqlite3.connect(mode=ro)` but must be verified that no write operations are possible |
| T8 | Tool Result Bounding Bypass | While results are bounded at 12,000 characters, a determined user could potentially engineer a tool that returns exactly 12,000 characters of sensitive data in each round, chaining multiple rounds to exfiltrate larger amounts. | LOW | Would require multiple tool rounds and user cooperation; the agent has other limitations |
| T9 | Tool Permission Granularity | Some tool permissions may be too coarse-grained. Example: `MEMORY_READ` gives access to all memory identifiers; finer-grained control (by scope or kind) might be desirable in some contexts. | LOW | Current permissions match the system's trust boundaries; finer granularity would add complexity |
| T10 | Insufficient Audit Logging | While tool execution is logged via the Agent observer (`_notify`), the logs may not contain sufficient detail for forensic analysis in case of misuse. | LOW | `_sanitize_arguments()` provides bounded logging; event logging could be enhanced |

---

## Tool Security Strengths

| # | Strength | Description |
|---|---|---|
| S1 | Explicit Permission Galling | The PolicyEngine enforces tool permissions in code, not model obedience. ALLOWED/DENIED/APPROVAL_REQUIRED are explicit and auditable. |
| S2 | Fail-Closed Design | Tool failures return error strings; exceptions are caught and formatted. The agent loop handles tool errors gracefully. |
| S3 | Result Bounding | `MAX_TOOL_RESULT_CHARS = 12000` prevents tool results from overwhelming the LLM context with excessive data. |
| S4 | Tool Isolation | Each tool is narrowly scoped to a specific function; no tool has overly broad capabilities. |
| S5 | Handler Accountability | Tool handlers are explicit Python functions that can be audited for security and correctness. |
| S6 | No Dynamic Tool Registration | Tools are registered at startup; no mechanism for runtime tool addition from untrusted sources. |
| S7 | Argument Schema Validation | Tools declare explicit JSON Schema parameters; the registry validates that arguments conform before execution. |
| S8 | Read-Only Critical Connectors | The Personal AI → Job Agent bridge is explicitly read-only; Job Agent cannot modify Personal AI state. |
| S9 | Error Isolation per Source | Source adapters fail independently; one bad source (email, chrome, etc.) doesn't abort the whole ingestion or discovery process. |
| S10 | Bounded Agent Loops | `max_tool_rounds = 8` prevents infinite tool-calling loops; the agent will eventually terminate or fail. |

---

## Recommendations (Non-Modification)

**R1 — Tool Argument Prompt Scanning:** Add lightweight prompt injection detection for tool arguments before they are passed to tool handlers. Simple pattern matching for common injection attempts (e.g., "Ignore previous instructions", "System:", etc.) that could affect tool behavior.

**R2 — Tool Result Sanitization for LLM Context:** Consider adding a sanitization step for tool results before they are appended to the LLM conversation history. This could strip or mask obvious sensitive data patterns (API keys, tokens, IBANs, etc.) while preserving utility.

**R3 — Tool Chain Privilege Analysis:** Perform a formal analysis of potential tool chains that could lead to privilege escalation or unintended effects. Document which combinations are safe and which require additional scrutiny.

**R4 — MCP Connector Security Review:** Before enabling MCP connectors, thoroughly review the security model and permissions requested by any MCP server. Ensure the system only grants necessary permissions.

**R5 — Tool Handler Security Review:** Conduct security reviews of critical tool handlers (especially those with file system or database access) for common vulnerabilities (path traversal, SQL injection, command injection, etc.).

**R6 — Tool Argument Validation Enhancement:** Enhance tool argument validation where possible to reject obviously dangerous or nonsensical inputs before handler execution.

**R7 — Read-Only Bridge Verification Script:** Create a verification script or test that confirms the Personal AI → Job Agent bridge is truly read-only and cannot be used to modify Personal AI state.

**R8 — Tool Result Bounding Monitoring:** Add observability for how often tool results are truncated (percentage of tool calls that hit the `MAX_TOOL_RESULT_CHARS` limit). This could indicate when tools are returning excessively large data.

**R9 — Tool Permission Least Privilege Review:** Review each tool's permission requirements to ensure they follow the principle of least privilege. Remove any unnecessary permissions from tool definitions.

**R10 — Tool Error Logging Enhancement:** Improve tool error logging to capture more context for debugging while still avoiding sensitive data leakage. Consider error categorization without exposing details.

**R11 — Tool Execution Timeouts:** Add per-tool execution timeouts to prevent a single tool from hanging the agent indefinitely. Currently `max_tool_rounds = 8` limits rounds but has no time limit per tool.

**R12 — Tool Result Size Reporting:** Consider adding metadata about tool result size (original size vs bounded size) to help users understand when truncation is occurring.

---

## Confidence Estimate

| Finding | Confidence |
|---|---|
| T1-T3 | High (architecture review, threat modeling) |
| T4-T6 | Medium (speculative risks, some require implementation details) |
| T7-T10 | Low-Medium (requires specific code paths or configurations to verify) |
| S1-S10 | High (code inspection, documentation review) |
| R1-R12 | Medium (requires implementation to verify effectiveness) |

---

## Next Steps (Post-Audit)

1. Add tool argument prompt scanning for injection attempts
2. Consider tool result sanitization for LLM context (privacy filter)
3. Perform tool chain privilege analysis
4. Review MCP connector security before enabling
5. Conduct security reviews of critical tool handlers
6. Enhance tool argument validation where possible
7. Create read-only bridge verification script
8. Add tool result bounding monitoring and observability
9. Review tool permissions for least privilege compliance
10. Enhance tool error logging for better debugging
11. Add per-tool execution timeouts
12. Consider tool result size reporting metadata