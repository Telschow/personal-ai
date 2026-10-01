# Components

## Overview

This document details the major components of the personal-ai system, their responsibilities, interfaces, and interactions. The system is organized into layers, with each layer having specific responsibilities and well-defined interfaces to adjacent layers.

## Component Layers

```
Layer 1: OS / Filesystem Layer
  - Raw data files
  - Configuration files
  - SQLite database files
  - Log files and temporary files

Layer 2: Storage Layer
  - DocumentStore, ChunkStore, EmbeddingStore
  - ExtractionStore, VisionStore
  - MemoryStore, PeopleStore
  - EventStore, ConversationStore
  - OrchestrationStore (Job Agent)

Layer 3: Ingestion Layer
  - Source Adapters (email, chrome, financial, etc.)
  - Document Ingestor (orchestrates extraction → classification → chunking)
  - Extractors (text, vision, structured)
  - Classifier (document type detection)
  - Chunker (text segmentation)
  - Storage connectors

Layer 4: Retrieval Layer
  - ChunkIndex implementations (FTS5 keyword, semantic, hybrid)
  - RetrievalService (unified search across stores)
  - Query understanding and planning
  - Result fusion and ranking
  - Context assembly and budgeting

Layer 5: Agent and Orchestration Layer
  - Agent (tool-calling loop)
  - PolicyEngine (permission gating)
  - ModelRouter (capability → model routing)
  - Orchestrator (researcher → verifier workflow)
  - Executor (task execution with approval context)
  - Verifier (deterministic verification)
  - Approval system (human-in-the-loop)

Layer 6: API and Interface Layer
  - HTTP API server (FastAPI)
  - Command-line interface (CLI)
  - Graphical user interface (GUI) - web dashboard
  - Read-only bridges (Personal AI ↔ Job Agent)

Layer 7: Configuration Layer
  - Personal AI configuration (environment-driven)
  - Job agent configuration (YAML-based)
  - Environment variable override systems
  - Default value providers
```

## Component Details

### 1. Storage Layer Components

#### DocumentStore
- **Purpose**: Persist documents and enable content-hash based idempotency
- **Interface**: `add(document)`, `get(document_id)`, `list()`, `delete(document_id)`
- **Storage**: SQLite table with columns for id, source, mime_type, content_hash, path, filename, metadata
- **Key Features**: 
  - Content-hash based document ID ensures idempotency
  - Metadata storage for provenance and context
  - Foreign key relationships to chunks and extractions

#### ChunkStore
- **Purpose**: Persist document chunks for search and retrieval
- **Interface**: `add_many(chunks)`, `list_for_document(document_id)`, `delete_for_document(document_id)`, `count()`
- **Storage**: SQLite table with columns for id, document_id, chunk_index, text, metadata
- **Key Features**:
  - Chunk-level storage enables fine-grained retrieval
  - Supports both fixed-size and semantic chunking strategies
  - Aligned with embedding storage for vector search

#### EmbeddingStore
- **Purpose**: Persist chunk embeddings for semantic search
- **Interface**: `add(embedding_id, vector, model)`, `get(embedding_id)`, `delete(embedding_id)`
- **Storage**: SQLite table with columns for id, chunk_id, model, vector (as text), dimensions
- **Key Features**:
  - Separate from chunk storage to enable optional vector search
  - Stores vector data as text (space-separated floats)
  - Model and dimension tracking for versioning

#### ExtractionStore
- **Purpose**: Persist structured extractions from documents
- **Interface**: `add(extraction)`, `get(document_id)`, `delete(document_id)`, `list_for_document()`
- **Storage**: SQLite table with columns for id, document_id, kind, summary, people, organizations, projects, goals, topics, metadata
- **Key Features**:
  - Stores LLM-extracted structured data from documents
  - Supports multiple extraction types (text-heavy, vision-augmented)
  - Provenance tracking for extraction source and model version

#### MemoryStore
- **Purpose**: Persist structured memories and enable memory-based retrieval
- **Interface**: `save(memory)`, `get(memory_id)`, `list(status)`, `search(query, scopes, limit)`, `delete(memory_id)`
- **Storage**: SQLite table with columns for memory_id, kind, content, summary, source_type, source_id, scope, scope_id, confidence, importance, status, temporal_scope, timestamps
- **Key Features**:
  - Scope-based retrieval (global, personal, professional, etc.)
  - Confidence and importance weighting for ranking
  - Status tracking (active, archived, deleted, superseded)
  - Temporal scope support (point-in-time, date-range, ongoing, recurring)
  - Last accessed timestamp for LRU-like behaviors

#### PeopleStore
- **Purpose**: Persist identified people and enable identity-based retrieval
- **Interface**: `save(person)`, `get(person_id)`, `list()`, `delete(person_id)`
- **Storage**: SQLite table with columns for person_id, identity, display_name, emails_json, roles_json, sources_json, timestamps, evidence_count
- **Key Features**:
  - Name-anchored identity canonicalization (Alice Example / Ali Example → same person)
  - Email aliases and source-derived roles (email/financial) stored per person
  - Source tracking for provenance
  - Evidence count for engagement tracking

#### EventStore
- **Purpose**: Persist structured events for temporal analysis
- **Interface**: `append_event(event_type, entity_id, payload)`, `get_events(entity_id, after_seq)`, `count_events()`
- **Storage**: SQLite table with columns for id, seq, event_type, entity_id, timestamp, payload
- **Key Features**:
  - Sequential event logging for audit trails
  - Entity-scoped retrieval (get all events for a memory/document/person)
  - Timestamp-based ordering for temporal analysis
  - Flexible payload storage (JSON blob)

#### ConversationStore
- **Purpose**: Persist chat conversations and enable conversation-based retrieval
- **Interface**: `save(conversation)`, `get(conversation_id)`, `list()`, `delete(conversation_id)`
- **Storage**: SQLite table with columns for id, title, source_type, created_at, modified_at, metadata
- **Related**: ConversationMessages table for individual messages
- **Key Features**:
  - Conversation-level metadata (title, source, timestamps)
  - Message-level storage with role, content, speaker, timestamps
  - Thread/branch structure preservation (for ChatGPT/Gemini exports)
  - Active branch tracking for identifying canonical conversation flow

#### VisionStore
- **Purpose**: Persist vision extraction results for image-heavy documents
- **Interface**: `save(document_id, page_number, text, model, prompt_version)`, `get(document_id, page_number)`, `delete(document_id, page_number)`
- **Storage**: SQLite table with columns for id, document_id, page_number, text, model, prompt_version
- **Key Features**:
  - Page-level caching to avoid redundant vision extraction
  - Model and prompt version tracking for cache invalidation
  - Coordinates with document storage for persistence

#### OrchestrationStore (Job Agent)
- **Purpose**: Persist plans, tasks, and events for career assessment workflows
- **Interface**: Similar to event store but for plans/tasks
- **Storage**: SQLite tables for plans, tasks, events with appropriate columns
- **Key Features**:
  - Plan/task lifecycle management (created, running, blocked, needing approval, completed, failed, cancelled)
  - Event sourcing for audit trails
  - Approval tracking for human-in-the-loop decisions
  - Work dispatch for task execution

### 2. Ingestion Layer Components

#### Source Adapters
- **Purpose**: Parse raw source data into normalized SourceRecord objects
- **Interface**: `discover() → list[SourceRecord]` (return all currently available records)
- **Examples**: EmailSource, ChromeHistorySource, FinancialSource, ChatGPTSource, etc.
- **Key Features**:
  - Narrow and isolated (one adapter per source type)
  - Format-specific parsing (mbox, JSON, CSV, PDF, etc.)
  - Normalization to common SourceRecord format
  - Error isolation (failures don't abort whole scan)
  - Path validation and security considerations
  - Metadata extraction for provenance

#### DocumentIngestor
- **Purpose**: Orchestrate the full ingestion pipeline from SourceRecord to stored documents
- **Interface**: `ingest(record) → IngestionResult`
- **Key Features**:
  - Connects extraction, classification, and storage components
  - Ensures document is persisted before structured extraction
  - Handles vision augmentation for image-heavy documents
  - Aligns stored chunks with deterministic re-chunking
  - Coordinates embedding storage (optional separate step)
  - Idempotent processing (content-hash based deduplication)
  - Bounded processing (file size, section count, length limits)
  - Safe processing (no code execution, no external calls)

#### Extractors
- **TextExtractor**: Extract text from various document formats
  - Uses appropriate libraries (pypdf, python-docx, etc.)
  - Returns TextExtractionResult with text, pages, metadata
- **VisionExtractor**: Extract text from images using vision models
  - Uses Ollama vision models via `/api/embed` endpoint
  - Returns VisionExtractionResult with text and metadata
  - Includes caching to avoid redundant model calls
- **StructuredExtractor**: Extract structured information from text using LLMs
  - Uses Ollama chat models via `/api/chat` endpoint
  - Returns StructuredExtraction with summary, people, organizations, etc.
  - Includes deterministic normalization post-extraction
  - Provenance stamping (vision model/prompt version when applicable)

#### Classifier
- **Purpose**: Determine document kind (TEXT_HEAVY, IMAGE_HEAVY, OTHER) for processing routing
- **Interface**: `classify(extracted_text) → DocumentClassification`
- **Key Features**:
  - Based on non-whitespace character count threshold
  - TEXT_HEAVY documents get chunked and structured extraction
  - IMAGE_HEAVY documents get vision augmentation then processing
  - OTHER documents are stored without chunking
  - Deterministic and reproducible based on text content

#### Chunker
- **Purpose**: Segment text into chunks for storage and retrieval
- **Interface**: `chunk_document(text, size, overlap) → tuple[DocumentChunk]`
- **Key Features**:
  - Fixed-size or variable-size chunking strategies
  - Overlap to preserve context across boundaries
  - Content-hash based chunk IDs for idempotency
  - Storage alignment (replace drifted sets, clean embeddings)
  - Deterministic and reproducible based on input text and parameters

### 3. Retrieval Layer Components

#### ChunkIndex Implementations
- **SQLiteChunkIndex (FTS5 Keyword Search)**:
  - **Purpose**: Lexical term matching over document chunks
  - **Interface**: `search(query, limit, filters) → tuple[ChunkSearchResult]`
  - **Algorithm**: BM25 (Best Matching 25) - lower score = better relevance
  - **Features**: Term frequency, inverse document frequency, field length normalization
  - **Dependencies**: None (built into SQLite FTS5)
  - **Fallback**: Always available; production default
  
- **SemanticChunkIndex (Cosine Similarity Search)**:
  - **Purpose**: Semantic similarity search over persisted embeddings
  - **Interface**: `search(query, limit, filters) → tuple[ChunkSearchResult]`
  - **Algorithm**: Cosine similarity between query and document vectors
  - **Features**: Conceptual similarity, synonym matching, topic clustering
  - **Dependencies**: Requires embedding model and pre-computed vectors
  - **Fallback**: Gracefully degrades to keyword search only
  
- **HybridChunkIndex (RRF Fusion)**:
  - **Purpose**: Fuse keyword and semantic search results using Reciprocal Rank Fusion
  - **Interface**: `search(query, limit, filters) → tuple[ChunkSearchResult]`
  - **Algorithm**: RRF(c) = Σ_i 1/(k + rank_i(c)) with k=60
  - **Features**: Scale-free, deterministic, monotone, explainable
  - **Benefits**: Combines lexical precision with semantic recall
  - **Fallback**: Degrades to available backend if one fails

#### RetrievalService
- **Purpose**: Provide unified search across document chunks, structured extractions, and conversation messages
- **Interface**: `search_documents(request) → SearchResult`, `search_knowledge(request) → SearchResult`
- **Key Features**:
  - Validates and sanitizes input queries (length limits, parameter validation)
  - Routes queries to appropriate ChunkIndex implementation
  - Executes multi-store retrieval (chunks, extractions, conversations)
  - Fuses and ranks results from different stores
  - Applies document filters (metadata-based constraints)
  - Implements character budget allocation for LLM context
  - Returns typed results with provenance information
  - Implements safety measures (result bounding, length limiting)

#### Query Understanding and Planning
- **Purpose**: Analyze user queries to determine intent and retrieval strategy
- **Components**:
  - Query validation (length limits, parameter checks)
  - Term extraction and concept identification
  - Intent classification (factual, procedural, exploratory)
  - Query expansion (synonyms, related concepts)
  - Retrieval planning (which stores to search, how many results, filters)
- **Key Features**:
  - Deterministic and repeatable
  - Based on lexical analysis and heuristics
  - Designed to work well with the available retrieval backends
  - Prepares for efficient execution against ChunkIndex implementations

#### Result Fusion and Ranking
- **Purpose**: Combine and rank results from different retrieval stores
- **Components**:
  - Normalization (convert store-specific scores to common scale)
  - Deduplication (content-based: chunk_id for documents, memory_id for memories)
  - Trust ranking (evidence weighted by verification level: verified > documented > inferred > candidate)
  - Recency boost (slight boost for more recent items)
  - Character budget application (truncate to fit LLM context window)
- **Key Features**:
  - Evidence-based prioritization (verified facts first)
  - Diversity encouragement (avoid over-reliance on single source type)
  - Relevance density optimization (highest information per character ratio)
  - Deterministic and repeatable ordering
  - Explainable ranking factors

#### Context Assembly
- **Purpose**: Prepare final context for LLM consumption
- **Components**:
  - System instructions (fixed trusted block defining agent behavior)
  - Query restatement (original user question)
  - Retrieved evidence (bounded excerpts with provenance)
  - Format instructions (JSON schema or output requirements)
  - Length constraints (explicit token limits for each section)
  - Safety margin (buffer for encoding variations)
- **Key Features**:
  - Fixed allocation percentages (system: 15-20%, query: 5-10%, evidence: 60-70%, format: 5-10%, margin: 0-5%)
  - Evidence selection strategy (prioritizes verified facts, documented evidence, inferred evidence, recent items, diverse sources, relevance density)
  - Provenance tracking (source identification, position, confidence, timestamps, content hash, length)
  - Length capping and truncation with clear markers
  - Format compliance for structured outputs

### 4. Agent and Orchestration Layer Components

#### Agent
- **Purpose**: Run synchronous tool-calling loop against local Ollama model
- **Interface**: `run(messages: Sequence[ChatMessage]) → str`
- **Key Features**:
  - Tool-calling loop with max rounds (default: 8)
  - PolicyEngine integration for permission gating
  - Observer pattern for operational logging
  - Result bounding (MAX_TOOL_RESULT_CHARS = 12000 truncation)
  - Error handling and formatting
  - Conversation history management
  - Schema validation for structured outputs
  - Evidence ID allow-listing (LLM can only reference provided evidence IDs)
  - Think disabling (`"think": false` to prevent reasoning token leakage)

#### PolicyEngine
- **Purpose**: Evaluate and gate tool execution based on permissions
- **Interface**: `check_tool(agent: Agent, tool_name: str) → PolicyCheck`
- **Key Features**:
  - Permission-based: tool + task + agent + policy → allowed / denied / approval required
  - Explicit decisions: ALLOWED, DENIED, APPROVAL_REQUIRED
  - Decisions recorded for auditability
  - Tool existence checked separately (typos surfaced, not treated as policy decisions)
  - Approver interface for human-in-the-loop decisions
  - Supports complex permission combinations (AND/OR logic)

#### ModelRouter
- **Purpose**: Route capability requests to configured models via ModelProvider
- **Interface**: `select(request: ModelRequest) → str` (returns model name)
- **Key Features**:
  - Capability-based routing (SIMPLE, RESEARCH, REASONING, VISION, CODING, VERIFICATION)
  - Complexity hints (low | normal | high)
  - Context size awareness
  - Local-only constraint enforcement
  - Tool requirement specification
  - Fallback to chat model for unspecified capabilities
  - Provider abstraction (OllamaProvider is one implementation)

#### Orchestrator
- **Purpose**: Tie planner, executor, and verifier into a workflow
- **Interface**: `create_research_plan(objective: str, execution_id: str | None) → Plan`, `resume_execution(execution_id: str) → Plan`
- **Key Features**:
  - Owns plan/task lifecycle and emits orchestration events
  - Does not contain recursive autonomous loops
  - Creates deterministic researcher → verifier workflow
  - Handles plan resumption after interruption
  - Manages approval gating for tasks requiring human input
  - Separation of concerns: planning, execution, verification, approval
  - Event sourcing for audit trails (plan created, task started, task completed, etc.)
  - Deterministic and repeatable workflows

#### Executor
- **Purpose**: Execute one task with a scoped approval context
- **Interface**: `execute(task: Task) → Task`
- **Key Features**:
  - Scoped approval context (cleared on exit)
  - Agent and tool dispatch via ModelRouter
  - Policy engine integration for permission checking
  - Model selection via ModelRouter
  - Work dispatch function (default: work_dispatch)
  - Event emission for task lifecycle
  - Error handling and status updates
  - Delegates actual work to agents and tools

#### Verifier
- **Purpose**: Deterministically verify task outputs
- **Interface**: Inherits from TaskExecutor, adds deterministic verification step
- **Key Features**:
  - Inherits execution capabilities from TaskExecutor
  - Adds deterministic verification after task execution
  - Used in researcher → verifier workflow
  - Ensures task outputs meet quality standards before proceeding
  - Works with orchestrator to create verified plans

#### Approval System
- **Purpose**: Gate tasks that require human approval before proceeding
- **Interface**: Through PolicyEngine (APPROVAL_REQUIRED decisions)
- **Key Features**:
  - Explicit human approval required for certain operations
  - Task- and permission-scoped approval (not blanket permission)
  - Approval state tracking (pending, approved, rejected)
  - Integration with orchestrator and executor
  - Visible in CLI and API for human interaction
  - Clear audit trail of approval decisions

### 5. API and Interface Layer Components

#### HTTP API Server
- **Purpose**: Expose Personal AI Agent and Job Agent functionality over HTTP
- **Interface**: REST/JSON API with WebSocket/server-sent events for streaming
- **Key Features**:
  - OpenAI-compatible chat completions endpoint (`/v1/chat/completions`)
  - Control plane endpoints (`/api/executions*`, `/api/executions*/{id}` for board/events/approvals)
  - Memory endpoints (`/api/memory*` for list/search/show)
  - Workout endpoints (`/api/workouts*` for list/one/exercises/history/stats)
  - Job Agent endpoints (`/api/job-agent/*` for jobs, fit, digest, stats, decision)
  - Static file serving for GUI (`/`)
  - Token-based authentication (optional)
  - OpenAPI/Swagger-compatible response formats
  - Proper HTTP status codes and error handling
  - CORS handling as needed

#### Command-Line Interface (CLI)
- **Purpose**: Provide comprehensive command-line access to system functionality
- **Interface**: argparse-based subcommands and options
- **Key Features**:
  - Ingestion commands (`scan`, `ingest`) for adding data sources
  - Profile management (`profile`) for viewing/editing career profile
  - Source management (`sources`) for listing/checking configured sources
  - Job management (`jobs`) for listing jobs in database
  - Career assessment (`fit`) for career fit intelligence for stored jobs
  - Career document management (`career`) for ingesting/listing career documents and evidence
  - CV tailoring (`tailor`) for proposal-only tailored CVs
  - Digest generation (`digest`) for writing daily digest markdown
  - Statistics (`stats`) for showing aggregate statistics
  - Decision recording (`decision`) for recording human decisions on jobs
  - Subcommand-specific options and flags
  - Help system and error handling
  - Integration with all system components

#### Graphical User Interface (GUI)
- **Purpose**: Provide local web dashboard for system interaction
- **Interface**: Web-based (HTML/CSS/JS) served via FastAPI static files
- **Key Features**:
  - Served from `src/personal_ai/static/` directory
  - Base interface for both Personal AI and Job Agent systems
  - Local-only access (default bind to 127.0.0.1:8000)
  - Token-authenticated if API token configured
  - Responsive design for various screen sizes
  - Interactive components for data exploration and visualization
  - Real-time updates via polling or websockets (as implemented)
  - Access to same functionality as CLI and API (subset based on implementation)

#### Read-Only Bridges
- **Personal AI → Job Agent**:
  - **Purpose**: Enable Job Agent to read Personal AI data for evidence-based career assessments
  - **Interface**: Read-only SQLite connection via `PersonalAiCareerKnowledge` adapter
  - **Key Features**:
    - `sqlite3.connect(mode=ro)` for guaranteed read-only access
    - Exposes only `memory_search()`, `corpus_search()`, and `health()` methods
    - No write capabilities exposed to Job Agent
    - Bounded retrieval (max_corpus_evidence=40, max_memory_evidence=20)
    - Trust-ranked deduplication of evidence
    - Character budget compaction for LLM context
    - Health check for Personal AI availability
    - Fail-closed to profile-only evidence when Personal AI unavailable
  - **Security**: Explicitly prevents Job Agent from modifying Personal AI state
  
- **Job Agent → Personal AI**:
  - **Purpose**: Not implemented by design
  - **Key Features**:
    - Deliberate omission to maintain privacy boundary
    - Prevents career assessment data from flowing back into personal knowledge base
    - Maintains unidirectional data flow (Personal AI → Job Agent only)
    - Preserves integrity of personal knowledge base
    - Avoids potential feedback loops or corruption risks

### 6. Configuration Layer Components

#### Personal AI Configuration
- **Purpose**: Configure runtime behavior of Personal AI system
- **Interface**: Environment variables and dataclass-based validation
- **Key Features**:
  - Environment-driven: `PERSONAL_AI_EMBEDDING_MODEL`, `PERSONAL_AI_RETRIEVAL_MODE`, etc.
  - Sensible defaults for all configuration values
  - Validation and clear error messages for invalid values
  - No secrets stored in configuration (expected from environment)
  - Override precedence: CLI args > environment variables > defaults
  - Specific variables for embedding model, retrieval mode, vision, chat model, API host/port, Ollama base URL
  - Graceful degradation when optional features are not configured

#### Job Agent Configuration
- **Purpose**: Configure behavior of Job Agent subsystem
- **Interface**: YAML-based configuration with Pydantic validation
- **Key Features**:
  - Hierarchical structure: llm, search, jobs, ranking, digest, projects, application, career, sources
  - Pydantic BaseModel with Field constraints and model validators
  - Environment variable override system (`with_env_overrides()`)
  - Sensible defaults for all sections
  - Validation of weight sums (must equal 1.0)
  - Validation of positive values for counts, thresholds, etc.
  - Specific sections for LLM, search behavior, job handling, ranking weights, career assessment, sources
  - Clear documentation of what each section does
  - Example configuration file (`config.example.yaml`)
  - No secrets stored in configuration (expected from environment or .env file)

## Component Interactions

### Data Flow: Ingestion → Storage → Retrieval → Agent

```
Source Data
        ↓
Source Adapter (email, chrome, financial, etc.)
        ↓
SourceRecord (normalized format)
        ↓
document_from_source_record (compute content hash, create Document)
        ↓
extract_text (text extraction, optional vision augmentation)
        ↓
classify_document (determine TEXT_HEAVY/IMAGE_HEAVY/OTHER)
        ↓
structured_extraction (LLM-backed extraction over text-heavy text, optional)
        ↓
chunk_document (deterministic chunking with overlap)
        ↓
DocumentStore.add (content-hash idempotency)
        ↓
(chunk storage, optional embedding storage)
        ↓
Storage ready for retrieval

Query → RetrievalService
        ↓
Input validation and sanitization
        ↓
Query understanding and planning
        ↓
Multi-store retrieval:
          ├── ChunkIndex.search (FTS5 keyword / semantic cosine / hybrid RRF)
          ├── MemorySearch.search (lexical matching with importance/confidence)
          ├── ConversationSearch.search (recency-weighted lexical similarity)
          └── ExtractionSearch.search (lexical matching over structured extractions)
        ↓
Result fusion and ranking:
          ├── Score normalization (to common scale)
          ├── Deduplication (content-based: chunk_id/memory_id)
          ├── Trust ranking (verified > documented > inferred > candidate)
          ├── Recency boost (slight boost for more recent items)
          └── Character budget application (truncate to fit LLM context)
        ↓
Context assembly:
          ├── System instructions (fixed trusted block)
          ├── Query restatement (original user question)
          ├── Retrieved evidence (bounded excerpts with provenance)
          ├── Format instructions (JSON schema or output requirements)
          └── Length constraints (explicit token limits + safety margin)
        ↓
LLM reasoning (optional, with safety measures)
        ↓
Final answer with provenance and metadata
```

### Control Flow: Agent Loop with Policy Gating

```
User Query
        ↓
Agent receives messages
        ↓
For each round (max 8):
  ↓
  Agent requests tools from Ollama (with schemas)
  ↓
  PolicyEngine checks tool permissions (ALLOWED/DENIED/APPROVAL_REQUIRED)
  ↓
  IF DENIED → error returned to LLM
  ↓
  IF APPROVAL_REQUIRED → pause execution pending human approval
  ↓
  IF ALLOWED → execute tool handler
  ↓
  Tool handler returns result (bounded to 12,000 chars)
  ↓
  Result appended to conversation history as tool message
  ↓
  Loop continues until final answer or max rounds reached
        ↓
Final answer returned to user (with optional memory/people provenance)
```

### Integration Flow: Personal AI → Job Agent

```
Job Agent needs career evidence
        ↓
Job Agent connects to Personal AI DB (read-only)
        ↓
PersonalAiCareerKnowledge Adapter:
  - memory_search() → memories table (bounded, trust-ranked)
  - corpus_search() → documents/chunks tables (bounded, trust-ranked)
  - health() → system status (for degradation handling)
        ↓
Career Evidence Collection:
  - Template query planning based on job requirements
  - Bounded retrieval per concept (max_corpus_evidence=40, max_memory_evidence=20)
  - Trust-ranked deduplication (evidence weighted by verification level)
  - Character budget compaction (fit within LLM context)
        ↓
Career Fit Assessment:
  - Deterministic scoring (role match, skills, seniority, domain, leadership, location, compensation)
  - Weighted sum (career fit weights: role_family, ai_relevance, leadership, etc.)
  - Decision logic (strong/review/reject thresholds)
  - Evidence collection and compaction
        ↓
Optional LLM Narrative (fail-closed to deterministic)
        ↓
Persistence to evaluations table (Job Agent database)
        ↓
Available for:
  - Job detail views (CLI/API/GUI)
  - Score explanations
  - Ranking adjustments
  - Career intelligence reports
```

## Cross-Cutting Concerns

### Idempotency
- **Document Ingestion**: Content-hash based (same file = same ID = no-op)
- **Job Persistence**: Canonical-key based (same job = same key = no-op)
- **Artifact Persistence**: Version-based (append-only, never overwrite)
- **Evidence Extraction**: Claim+source hash based (idempotent re-extraction)
- **Memory Creation**: Content-hash based (same content = same memory_id = no-op)

### Immutability
- **Raw Source Data**: Never modified after ingestion
- **Evaluations**: Never updated after creation (point-in-time assessment)
- **Feedback**: Never overwritten (append-only historical record)
- **Artifacts**: Append-only versioning (each tailor() creates new version)
- **Evidence**: Never modified after extraction (immutable facts)
- **Memories**: Never modified after extraction (immutable facts; updates create new versions)

### Provenance Tracking
- **Jobs**: source, discovery_method, run_id, discovered_at
- **Evaluations**: scoring_version, profile_version, evaluated_at
- **Feedback**: created_at, optional run_id
- **Evidence**: source, source_type, created_at, confidence
- **Artifacts**: base_document_id, version, created_at, llm_used
- **Reports**: generation timestamp, input data hashes, parameters used
- **Memories**: source_type, source_id, seen_at, confidence, importance
- **People**: document_id, name, email, role, source_type, seen_at

### Privacy and Security Boundaries
- **What Stays Local**: All Personal AI memories, documents, embeddings; user CVs/career documents; extracted evidence/claims/sections; tailored CV/cover letter artifacts; feedback/application notes/private notes; local LLM inputs/outputs/reasoning traces; IP addresses/network traffic/system logs
- **What May Leave Local**: Generalized job market analytics (anonymized); non-personal career trends/stats; public job info (titles/companies/locations); explicitly shared CV/cover letter artifacts; explicitly shared application outcomes; aggregated non-personal usage stats (opt-in)
- **Network Boundaries**: Personal AI: input=None, output=None (unless shared), LLM=local Ollama, network=zero required; Job Agent: input=outbound HTTP to providers/search engines, output=None (unless shared), LLM=local Ollama, network=required for discovery, persistence=local SQLite only, integration=local read-only SQLite (Personal AI → Job Agent)

### Failure Modes and Degradation
- **LLM Unavailable**: Falls back to deterministic output; agent continues to function without LLM reasoning
- **Personal AI Unavailable (for Job Agent)**: Job Agent degrades gracefully to profile-only evidence with explicit risk notes
- **Source Failures**: Isolated per source; other sources continue processing
- **Storage Failures**: Application-level error handling; system may become unavailable until resolved
- **Configuration Errors**: Clear error messages at startup; system fails fast rather than operating in degraded state
- **Network Failures (Job Agent)**: Affected sources marked as failed; other sources continue; discovery continues with available sources
- **Memory Corruption**: SQLite corruption detected at startup; system fails to start rather than operating with corrupted data
- **Disk Full**: Applications should handle ENOSPC errors gracefully; ingestion may fail but existing data remains accessible

## Component Responsibility Summary

| Layer | Component | Primary Responsibility |
|---|---|---|
| Storage | DocumentStore | Persist documents with content-hash idempotency |
| Storage | ChunkStore | Persist document chunks for search/retrieval |
| Storage | EmbeddingStore | Persist chunk embeddings for semantic search |
| Storage | ExtractionStore | Persist structured extractions from documents |
| Storage | MemoryStore | Persist structured memories with scoping and weighting |
| Storage | PeopleStore | Persist identified people with emails/roles/sources |
| Storage | EventStore | Persist structured events for temporal audit |
| Storage | ConversationStore | Persist chat conversations with message structure |
| Storage | VisionStore | Persist vision extraction results for images |
| Storage | OrchestrationStore | Persist plans/tasks/events for workflows (Job Agent) |
| Ingestion | Source Adapters | Parse raw data into normalized SourceRecords |
| Ingestion | DocumentIngestor | Orchestrate full ingestion pipeline |
| Ingestion | Extractors | Extract text/structured data from sources |
| Ingestion | Classifier | Determine document type for processing routing |
| Ingestion | Chunker | Segment text into chunks for storage/retrieval |
| Retrieval | ChunkIndex Implementations | Provide search over chunks (keyword/semantic/hybrid) |
| Retrieval | RetrievalService | Unified search across stores with validation/fusion |
| Retrieval | Query Understanding | Analyze queries to determine intent/strategy |
| Retrieval | Result Fusion/Ranking | Combine/rank results from different stores |
| Retrieval | Context Assembly | Prepare final context for LLM consumption |
| Agent | Agent | Synchronous tool-calling loop against local Ollama |
| Agent | PolicyEngine | Evaluate and gate tool execution based on permissions |
| Agent | ModelRouter | Route capability requests to configured models |
| Agent | Orchestrator | Tie planner/executor/verifier into workflow |
| Agent | Executor | Execute tasks with scoped approval context |
| Agent | Verifier | Deterministically verify task outputs |
| Agent | Approval System | Gate tasks requiring human approval |
| API | HTTP API Server | Expose functionality over OpenAI-compatible HTTP API |
| API | CLI | Comprehensive command-line interface to system |
| API | GUI | Local web dashboard for system interaction |
| API | Read-Only Bridges | Enable controlled data flow between subsystems |
| Config | Personal AI Configuration | Environment-driven configuration of runtime behavior |
| Config | Job Agent Configuration | YAML-based configuration of Job Agent behavior |

This component architecture provides a solid foundation for understanding how the personal-ai system is organized and how its parts work together to deliver personal intelligence capabilities while maintaining strong privacy boundaries and data integrity.