# AI/LLM Architecture Audit

## Executive Summary

This audit examines the AI/LLM architecture of the `personal-ai` repository. The system uses a local Ollama instance as the default LLM provider, with a strong emphasis on local-first operation and privacy. The LLM is used optionally in the retrieval pipeline (for structured extraction and vision augmentation) and in the agent loop (for reasoning, when enabled). Critical paths like memory search, keyword search, and deterministic scoring operate without LLM involvement.

**Overall Assessment:** The AI/LLM architecture is conservative and privacy-conscious. The system is designed to function fully without an LLM (keyword search, memory search, deterministic scoring all work standalone). When an LLM is used, it is strictly controlled through prompt engineering, output validation, schema constraints, and fail-closed behavior. The primary AI/LLM risks involve prompt injection through retrieved content, context window overflow, and potential hallucination despite safeguards.

---

## Model Configuration & Abstraction

### Provider Abstraction
- **Primary Provider:** Ollama (local HTTP API at `http://127.0.0.1:11434`)
- **Abstraction Layer:** `personal_ai/ollama_client.py` provides minimal typed client for `/api/chat` and `/api/embed`
- **Configuration:** `personal_ai/config.py` defines:
  - `CHAT_MODEL_ENV` (default: `qwen3.5:9b`)
  - `EMBEDDING_MODEL_ENV` (optional, for vector search)
  - `VISION_MODEL_ENV` (optional, for vision extraction)
  - `OLLAMA_BASE_URL_ENV` (override for Docker/containerized deployments)
- **Job Agent Override:** `job_agent/config.py` has separate LLM config (defaults to `host.docker.internal:11434` for Docker)

### Model Routing & Capabilities
- **Model Capabilities:** `personal_ai/agents/routing.py` defines:
  - `SIMPLE` (classification, small decisions)
  - `RESEARCH` (general-purpose research)
  - `REASONING` (complex reasoning)
  - `VISION` (image understanding)
  - `CODING` (software engineering)
  - `VERIFICATION` (independent review)
- **Model Selection:** `ModelRouter` matches `ModelRequest` (capability + complexity hint) to configured models
- **OllamaProvider:** Records configured model per capability; falls back to chat model for unspecified capabilities
- **No Cloud Providers:** Zero cloud LLM dependencies in core code; all model names resolve to local Ollama instances

### Prompt Engineering & Safety
- **System Instructions:** Fixed trusted block defining agent behavior (see `personal_ai/agents/policy.py` and retrieval pipeline)
- **Trusted Data Boundary:** Clear separation between trusted system instructions and untrusted retrieved data
- **Output Validation:** Strict JSON schema validation for structured outputs (see `build_response` in `server.py`)
- **Evidence ID Allow-list:** LLM can only reference evidence IDs that were provided in context (prevents hallucination of sources)
- **No External References:** LLM output is validated against allow-list of provided evidence IDs
- **Think Disabling:** `"think": false` on Ollama requests to prevent reasoning token leakage
- **Length Constraints:** Explicit token limits for each section of the context

### Context Construction & Budgeting
- **Character Budget Allocation:** (from `docs/RAG_ARCHITECTURE.md`)
  - System Instructions: 15-20% (fixed trusted block)
  - Query Restatement: 5-10% (original user question)
  - Retrieved Evidence: 60-70% (bounded excerpts with provenance)
  - Format Instructions: 5-10% (JSON schema or output requirements)
  - Safety Margin: 0-5% (buffer for encoding variations)
- **Evidence Selection Strategy:** Prioritizes:
  1. High-confidence verified facts (directly stated in profile/documents)
  2. Documented evidence (from trusted sources with attribution)
  3. Inferred evidence (derived from patterns with clear methodology)
  4. Recent items (slight recency boost)
  5. Diverse sources (avoid over-reliance on single source type)
  6. Relevance density (highest information per character ratio)
- **Provenance Tracking:** Every evidence item includes source identification, position information, confidence level, timestamps, content hash, and length information

### Structured Outputs & Validation
- **Schema Validation:** All LLM outputs for structured tasks are validated against Pydantic models or JSON schemas
- **Fail-Closed Design:** Any validation failure results in `REQUIRES_REVIEW` status; LLM outputs never become authoritative evidence
- **Anti-Fabrication Guards:** 
  - Numeric claims must have supporting evidence
  - Entity claims (employer, title, dates) must have supporting evidence
  - Generated material never becomes authoritative evidence
- **Citation Mechanisms:** LLM must cite evidence by ID; hallucinated citations are rejected

### Retrieval & Augmentation Paths
- **Keyword Search (FTS5):** Always available; no LLM required
- **Vector Search (Optional):** Requires embedding model; computes query embedding via Ollama `/api/embed`
- **Hybrid Search (Optional):** RRF fusion of keyword and semantic search
- **Memory Search:** Lexical matching over structured memories; no LLM
- **Conversation Search:** Lexical matching over chat messages; no LLM
- **Vision Extraction:** Optional per-page vision extraction for image-heavy documents (uses vision model)
- **Structured Extraction:** LLM-backed extraction over text-heavy documents (uses chat model for extraction)

### Hallucination Controls
1. **Evidence Restriction:** LLM can only reference evidence IDs provided in context
2. **Schema Validation:** Output must match requested format exactly
3. **Length Limiting:** Hard caps prevent runaway generation
4. **Failure Fallback:** Malformed output → deterministic fallback
5. **Think Disabling:** Prevents reasoning token leakage
6. **Anti-Fabrication Validation:** Post-generation validation of claims against evidence

### Failure Handling
- **Malformed Output:** Falls back to deterministic output
- **Schema Violations:** Falls back to deterministic output
- **External References:** Falls back to deterministic output
- **Length Violations:** Truncates to maximum allowed length
- **Timeouts:** Falls back to deterministic output
- **Model Errors:** Falls back to deterministic output
- **Any Failure:** Deterministic output stands; LLM usage flag set to false

---

## Deterministic vs Probabilistic Components

### Deterministic (Always Available, No LLM)
| Component | Description | Use Case |
|---|---|---|
| **Keyword Search (FTS5)** | BM25 ranking over document chunks | Primary retrieval path; always works |
| **Memory Search** | Lexical matching with importance/confidence weighting | Facts, goals, habits, skills |
| **Conversation Search** | Recency-weighted lexical similarity | Chat history retrieval |
| **Deterministic Scoring** (Job Agent) | Role match, skills, seniority, domain, leadership, location, compensation | Career fit assessment without LLM |
| **Normalization & Canonicalization** | Job titles, companies, locations, salary conversion | Job discovery pipeline |
| **Document Ingestion Pipeline** | Extract → classify → chunk → (optional embed) | Pure deterministic text processing |
| **Structured Extraction Normalization** | Deterministic text normalization post-LLM | Ensures idempotent re-extraction |
| **Policy Engine** | Permission gating: ALLOWED/DENIED/APPROVAL_REQUIRED | Tool execution decisions |
| **Orchestrator** | Researcher → verifier workflow (deterministic tasks) | Agent planning and execution |
| **Memory Reconciliation** | Deterministic candidate application (ADD_EVIDENCE/SUPERSEDE/CONFLICT) | Memory updates without LLM |

### Probabilistic (LLM-Dependent, Optional)
| Component | Description | Use Case |
|---|---|---|
| **Vector Search** | Cosine similarity over persisted embeddings | Semantic similarity search (opt-in) |
| **Hybrid Search** | RRF fusion of keyword + semantic search | Best of lexical + semantic (opt-in) |
| **Vision Extraction** | Page-level vision understanding for images | Scanned PDFs, screenshots, photos |
| **Structured Extraction** | LLM-backed information extraction | Text-heavy documents (financial, legal, etc.) |
| **LLM Reasoning** | Complex reasoning over retrieved context | Complex questions requiring synthesis |
| **CV Tailoring Semantic Refinement** | Optional LLM polish over deterministic output | Enhances readability without changing facts |
| **LinkedIn Optimization** | Optional LLM-based profile suggestions | Enhances wording of evidence-based suggestions |
| **Job Agent Career Knowledge** | Optional LLM narrative for fit assessment (fail-closed) | Adds explanatory text to deterministic scores |

---

## AI/LLM Architecture Risks

| # | Category | Risk | Severity | Evidence |
|---|---|---|---|---|
| AI1 | Prompt Injection | Retrieved content could contain malicious prompts that hijack the LLM (e.g., "Ignore previous instructions and..."). The system treats retrieved data as untrusted but does not sanitize prompt injection attempts. | HIGH | Retrieved documents/chunks are inserted directly into LLM context without prompt sanitization |
| AI2 | Context Window Overflow | No hard character budget enforcement at the top level; relies on section-level allocation which could theoretically exceed model context window if one section is oversized. | MEDIUM | Character budget allocation in docs assumes perfect adherence; no enforcement mechanism |
| AI3 | Hallucination Despite Safeguards | Despite evidence ID allow-listing and schema validation, LLMs can still hallucinate facts within the allowed evidence boundaries or produce plausible but incorrect syntheses. | MEDIUM | Fail-closed design catches gross violations but subtle hallucinations may pass validation |
| AI4 | Vision Extraction Prompt Injection | Vision model could receive malicious prompts embedded in image pixels (e.g., steganographic text). Vision extraction uses the same Ollama endpoint. | LOW | Theoretical risk; requires malicious image content in personal data |
| AI5 | Embedding Model Data Leakage | If vector search is enabled, the embedding model (via Ollama) sees raw text chunks. If the Ollama instance is shared or logged, chunk content could be exposed. | LOW | Embedding calls go to same Ollama endpoint; local-only by default but worth noting |
| AI6 | LLM as Single Point of Failure | If Ollama is unavailable, the LLM-dependent features (vision extraction, structured extraction, LLM reasoning) fail. The system gracefully degrades but loses functionality. | LOW | Documented in RAG architecture as "gracefully degrades to keyword search only" |
| AI7 | Prompt Leakage via Tool Results | Tool results are bounded (`MAX_TOOL_RESULT_CHARS = 12000`) but could still contain sensitive data that appears in LLM context. Tool output is not scanned for prompt injection. | LOW | Tool results are appended to conversation history as untrusted data |
| AI8 | Temperature & Sampling Non-Determinism | The LLM uses `temperature: 0.15` (low but not zero). This introduces slight non-determinism in outputs. For agent loops, this could cause different tool choices on identical inputs. | LOW | Low temperature minimizes but doesn't eliminate randomness |

---

## Strengths

| # | Strength | Description |
|---|---|---|
| S1 | Local-First by Default | Zero required external network calls for core operation. Ollama runs locally; no cloud API keys needed in source code. |
| S2 | Deterministic Fallback | All LLM-dependent features have deterministic fallbacks (keyword search, memory search, rule-based extraction). The system functions fully without an LLM. |
| S3 | Strict Output Validation | LLM outputs are validated against schemas and evidence ID allow-lists. Fail-closed design prevents hallucinated evidence from becoming authoritative. |
| S4 | Privacy-Preserving | `"think": false` prevents reasoning token leakage. No personal data leaves the local machine during LLM inference. |
| S5 | Conservative Tool Use | The agent loop has `max_tool_rounds = 8` and tools are strictly gated by the PolicyEngine. No unrestricted tool use. |
| S6 | Provenance Tracking | Every LLM-generated output can be traced back to specific evidence IDs via provenance tracking. |
| S7 | Vision Cache Isolation | Vision extraction results are cached per page keyed by model and prompt version, preventing redundant model calls. |
| S8 | Anti-Fabrication Guards | Numeric and entity claims in LLM output must have supporting evidence; hallucinated claims are rejected during validation. |

---

## Recommendations (Non-Modification)

**R1 — Prompt Injection Defense:** Add prompt sanitization or delimiting for retrieved content before inserting into LLM context. Consider wrapping retrieved evidence in clear delimiters (e.g., `<<EVIDENCE_START>>` ... `<<EVIDENCE_END>>`) and instructing the model to ignore content that looks like instructions.

**R2 — Hard Context Budget Cap:** Add a top-level character budget enforcement mechanism in the RetrievalService or agent run path. Currently the budget is allocated across sections but there's no guarantee the total won't exceed the model's context window.

**R3 — Vision Extraction Input Validation:** Add basic validation that vision extraction input (image bytes) is actually an image before sending to the Ollama vision model. Prevents accidental sending of text or other data as images.

**R4 — Embedding Model Isolation Note:** Document that if vector search is enabled, the embedding model (via Ollama) sees the raw text chunks. In high-security environments, consider disabling vector search or using a separate, isolated Ollama instance for embeddings.

**R5 — LLM Failure Observability:** Add metrics or logging for LLM failure rates (malformed output, schema violations, timeouts). Currently failures fall back silently to deterministic output with no observability.

**R6 — Temperature for Determinism:** Consider setting `temperature: 0.0` for tasks requiring absolute determinism (structured extraction, vision extraction). The current `0.15` introduces slight non-determinism that may be unnecessary for extraction tasks.

**R7 — Tool Result Prompt Scanning:** Add lightweight prompt injection detection for tool results before they are appended to conversation history. Simple pattern matching for common injection attempts.

**R8 — Structured Extraction Confidence Scoring:** Add confidence scoring to structured extraction outputs. Currently the extraction is deterministic but no confidence metric is produced to indicate extraction quality.

**R9 — Job Agent LLM Documentation:** Clearly document which Job Agent features use the LLM (optional narrative, semantic refinement) and which are purely deterministic (scoring, fitting, application tracking).

**R10 — Prompt Injection Testing:** Add security tests that attempt prompt injection through retrieved content, tool results, and vision inputs. Verify the system's resistance to common injection techniques.

---

## Confidence Estimate

| Finding | Confidence |
|---|---|
| AI1-AI3 | High (architecture review, threat modeling) |
| AI4-AI8 | Medium (theoretical risks, some require specific conditions) |
| S1-S10 | High (code inspection, documentation review) |
| R1-R10 | Medium (requires implementation to verify effectiveness) |

---

## Next Steps (Post-Audit)

1. Add prompt injection defense for retrieved content in LLM context
2. Implement hard context budget cap enforcement
3. Add vision extraction input validation
4. Document embedding model data exposure risk
5. Add LLM failure observability/metrics
6. Consider temperature: 0.0 for extraction tasks
7. Add tool result prompt scanning
8. Add confidence scoring to structured extraction
9. Document Job Agent LLM usage boundaries
10. Add prompt injection security tests