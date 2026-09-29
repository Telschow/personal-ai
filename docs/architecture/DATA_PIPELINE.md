# Data Pipeline

## Overview

This document details the data pipeline of the personal-ai system, tracing how data flows from raw source material through ingestion, storage, retrieval, and ultimately to the user as actionable insights. The pipeline emphasizes idempotency, safety, traceability, and privacy at every stage.

## Pipeline Stages

### Stage 1: Source Data Acquisition

**Sources**:
- Local files (documents, spreadsheets, presentations, text files)
- Email exports (MBOX format from Google Takeout, Outlook, Thunderbird)
- Chat exports (ChatGPT, Gemini, WhatsApp, Telegram, Signal)
- Browser history (Chrome, Firefox, Safari, Edge)
- Download history and bookmarks
- Workout data (Garmin, Strava, Fitbit, Apple Health, Google Fit)
- Financial data (bank statements, investment accounts, expense tracking)
- Health data (heart rate, steps, sleep, etc.)
- Personal information (contacts, calendars, journals)
- Professional data (resumes, work history, education, certifications, skills)
- Media files (images, videos, audio) - with optional processing
- Archives (ZIP, RAR) - contents extracted and processed

**Acquisition Methods**:
- Manual file placement in designated directories
- Explicit import commands via CLI (`personal-ai ingest --source filesystem /path/to/data`)
- Automated discovery (for configured sources like email directories, Chrome history locations)
- Scheduled ingestion (via cron or systemd timers for regular updates)
- Real-time monitoring (for file system changes in watched directories)
- Manual upload via GUI or API (when implemented)

### Stage 2: Source Adapter Processing

Each source type has a dedicated adapter responsible for parsing and normalizing data:

**Email Adapter** (`sources/email.py`):
- Input: MBOX files (RFC 4155 format)
- Process: Parse mbox structure, extract individual messages, decode headers, extract body text
- Output: SourceRecord objects with:
  - `source_type = "email"`
  - Content: normalized email body text (plain text)
  - Metadata: sender, recipient, subject, date, message-id, thread-id
  - Provenance: file path, message offset within mbox
- Special Handling:
  - HTML to text conversion with link preservation
  - Attachment metadata extraction (filename, type, size)
  - Header normalization (case-folding, whitespace handling)
  - Malformed message handling (skip rather than fail)

**Financial Adapter** (`sources/financial.py`):
- Input: CSV exports from banks, credit cards, investment accounts
- Process: Parse CSV, identify schema (bank/card/investment/portfolio), redact sensitive identifiers
- Output: SourceRecord objects with:
  - `source_type = "financial"`
  - Content: normalized transaction descriptions (with stable identifiers redacted)
  - Metadata: transaction date, amount, type, category (when available)
  - Provenance: file path, record number within file
- Special Handling:
  - IBAN redaction (pattern: `[A-Z]{2}[0-9]{2}[A-Z0-9]{12,29}`)
  - Creditor reference redaction (ISO 11649 SEPA: `RF[0-9]{2}[A-Z0-9]{1,21}`)
  - Mandate reference redaction
  - Purpose field parsing to extract useful information while removing sensitive data
  - Multiple schema support (bank, card, investment_transaction, portfolio)
  - Exact duplicate removal (keeping first occurrence)

**ChatGPT Adapter** (`sources/chatgpt_loader.py`):
- Input: ChatGPT export JSON shard files (`conversations-*.json`)
- Process: Parse JSON, reconstruct conversation trees, preserve branching history
- Output: SourceRecord objects with:
  - `source_type = "chatgpt"`
  - Content: normalized conversation text (chronological order with branching)
  - Metadata: conversation ID, title, timestamps, participant information
  - Provenance: file path, conversation ID within export
- Special Handling:
  - Conversation tree reconstruction from parent-child node pointers
  - Active branch identification (root → current_node path)
  - Inactive branch ordering (DFS after active branch)
  - Per-message epoch timestamp conversion to ISO 8601
  - Thought/reasoning message handling (extract text from thoughts array)
  - Attachment handling (images, PDFs, etc.) with blob path references
  - Speaker label extraction when available

**Chrome History Adapter** (`sources/chrome_history.py`):
- Input: Chrome History JSON files (from Google Takeout)
- Process: Parse JSON, extract visits, searches, downloads
- Output: SourceRecord objects with:
  - `source_type = "chrome_history"`
  - Content: normalized history text (URLs, titles, visit counts)
  - Metadata: visit time, visit count, transition type
  - Provenance: file path, record ID within export
- Special Handling:
  - URL parsing to extract domain, path, query parameters
  - Visit deduplication (same URL within short time window)
  - Transition classification (link, typed, auto_bookmark, auto_subframe, etc.)
  - Segment parsing for navigation events
  - Handling of various visit types (local, server redirect, client redirect, server crypto)

**Generic File Adapter** (`sources/filesystem.py`):
- Input: Local files (PDF, DOCX, TXT, MD, images, etc.)
- Process: Detect file type, extract text using appropriate libraries
- Output: SourceRecord objects with:
  - `source_type = "filesystem"` or more specific type based on content
  - Content: extracted text (plain text)
  - Metadata: file type, size, page count (when applicable)
  - Provenance: file path, file modification time
- Special Handling:
  - PDF text extraction (using pypdf or similar)
  - DOCX text extraction (using python-docx or similar)
  - ODT text extraction (using appropriate library)
  - Image text extraction (optional, via vision extraction if configured)
  - Text file handling (various encodings, line endings)
  - Archive handling (ZIP contents extracted and processed individually)
  - File type detection via extension and content inspection
  - Size and complexity bounds to prevent resource exhaustion

### Stage 3: Document Ingestion Pipeline

Once source data is normalized into SourceRecord objects, it enters the core ingestion pipeline:

**1. Document Creation**
- Input: SourceRecord
- Process: 
  - Compute content hash (SHA256 of normalized content)
  - Generate document ID (content hash)
  - Check if document already exists (content-hash idempotency)
- Output: Document object (new or existing)
- Special: Zero new rows created if document already exists

**2. Text Extraction**
- Input: Document (via SourceRecord)
- Process:
  - Format detection (PDF, DOCX, TXT, MD, etc.)
  - Text extraction using appropriate format-specific methods
  - Basic text cleaning (whitespace normalization, etc.)
- Output: TextExtractionResult with text, pages, metadata
- Special:
  - Bounded extraction (file size, page count, section length limits)
  - Safe extraction (no code execution, no external calls)
  - Format-specific handling (PDF structure preservation, DOCX styling ignored for text)
  - Image detection for optional vision augmentation path

**3. Document Classification**
- Input: TextExtractionResult
- Process:
  - Measure non-whitespace character count
  - Compare to threshold (`TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS`)
- Output: DocumentClassification (TEXT_HEAVY, IMAGE_HEAVY, OTHER)
- Special:
  - Deterministic and reproducible
  - Based purely on text quantity (not content)
  - TEXT_HEAVY: ≥ threshold → chunking + structured extraction
  - IMAGE_HEAVY: < threshold → vision augmentation path then processing
  - OTHER: < threshold → store without chunking (image-heavy scans, near-empty documents)

**4. Vision Augmentation (Optional for IMAGE_HEAVY)**
- Input: TextExtractionResult (from IMAGE_HEAVY document)
- Process:
  - For each page: render PDF page to image (if applicable)
  - Run vision extraction on rendered image (if vision extractor configured)
  - Append vision text to original page text
  - Normalize combined text (whitespace strip, empty drop, exact dedupe)
- Output: Augmented TextExtractionResult
- Special:
  - Only for IMAGE_HEAVY documents
  - Requires vision extractor and vision store to be configured
  - Caching: matched cache rows short-circuit before rendering/model calls
  - Failures propagate (no fake extraction persisted)
  - Classification based on original extracted text (vision doesn't change document kind)
  - Standalone raster images: treated as single page-1 extraction

**5. Structural Extraction (Optional for TEXT_HEAVY)**
- Input: TextExtractionResult (from TEXT_HEAVY or vision-augmented IMAGE_HEAVY)
- Process:
  - Run structured extractor (LLM-backed) on text
  - Apply deterministic normalization post-extraction
  - Save extraction if new or changed (content-based deduplication)
- Output: StructuredExtraction (or None if conditions not met)
- Special:
  - Only for TEXT_HEAVY documents (original or vision-augmented)
  - Requires structured extractor to be configured
  - Deterministic normalization ensures idempotent re-extraction
  - Provenance stamping when vision-augmented (tracks vision model/prompt version)
  - Failures propagate (no fake extraction persisted)
  - Missing/below-threshold/stale extraction triggers re-extraction on config/prompt change

**6. Text Chunking**
- Input: TextExtractionResult (original or vision-augmented)
- Process:
  - Segment text into chunks (fixed/variable size with overlap)
  - Generate content-hash based chunk IDs
  - Align stored chunks with deterministic re-chunking
  - Delete embeddings of removed chunk IDs before chunk replacement
- Output: tuple[DocumentChunk]
- Special:
  - Only for TEXT_HEAVY documents (original or vision-augmented)
  - Fixed chunk size and overlap configurable
  - Overlap preserves context across boundaries
  - Storage alignment prevents stale chunks
  - Embedding cleanup happens before chunk replacement (prevents stranded vectors)
  - Deterministic and reproducible based on input text and parameters
  - Idempotent: re-chunking unchanged text produces identical chunk set

**7. Storage Persistence**
- Input: Document object, StructuredExtraction (optional), tuple[DocumentChunk]
- Process:
  - Persist document if new (content-hash idempotency)
  - Persist structured extraction if new or changed (content-based deduplication)
  - Persist chunks (replace drifted sets, clean embeddings of removed chunks)
- Output: Persisted document, optional extraction, stored chunks
- Special:
  - Document persistence happens first (enables foreign key references)
  - Structured extraction persistence happens second (references document)
  - Chunk persistence happens last (references document, enables embedding storage)
  - Transactional or sequential processing to maintain consistency
  - Error handling preserves partial state where possible
  - Zero data loss on failure (either fully persisted or no change)

**8. Embedding Generation (Optional, Separate Step)**
- Input: Stored document chunks
- Process:
  - Generate embeddings for chunks without embeddings
  - Store embeddings in embedding store
- Output: Persisted chunk embeddings
- Special:
  - Optional separate backfill step (`personal-ai embeddings backfill`)
  - Content-hash based chunk IDs enable alignment with chunk storage
  - Embedding store deletion happens before chunk replacement (during re-chunking)
  - Model and dimension tracking for versioning
  - Graceful degradation: system functions without embeddings (keyword search only)
  - Batch processing possible for efficiency

### Stage 4: Storage and Indexing

Once data is persisted through the ingestion pipeline, it becomes available for retrieval:

**Document Storage**:
- Table: `documents`
- Columns: id (content hash), source, source_type, content_hash, path, filename, mime_type, metadata, created_at, modified_at
- Indexes: Primary key on id, indexes on source_type, content_hash
- Characteristics: Content-hash idempotency, metadata storage for provenance

**Chunk Storage**:
- Table: `document_chunks`
- Columns: chunk_id, document_id (FK), chunk_index, page_number, text, metadata
- Indexes: Primary key on chunk_id, indexes on document_id, chunk_id
- Characteristics: Enables fine-grained retrieval, storage alignment for idempotency

**Embedding Storage** (Optional):
- Table: `chunk_embeddings`
- Columns: embedding_id, chunk_id (FK), model, vector (text), dimensions
- Indexes: Primary key on embedding_id, indexes on chunk_id, embedding_id
- Characteristics: Enables semantic search, model/dimension tracking

**Extraction Storage**:
- Table: `structured_extractions`
- Columns: id, document_id (FK), summary, people, organizations, projects, goals, topics, metadata
- Indexes: Primary key on id, indexes on document_id, id
- Characteristics: Stores LLM-extracted structured data, content-based deduplication

**Memory Storage**:
- Table: `memories`
- Columns: memory_id, kind, content, summary, source_type, source_id, scope, scope_id, confidence, importance, status, temporal_scope, created_at, updated_at, last_accessed_at, expires_at
- Indexes: Primary key on memory_id, indexes on kind, source_type, scope, status
- Characteristics: Scoped retrieval, confidence/importance weighting, temporal scope support

**People Storage**:
- Table: `people`
- Columns: person_id, identity, display_name, emails_json, roles_json, sources_json, first_seen_at, last_seen_at, evidence_count
- Indexes: Primary key on person_id, indexes on identity, display_name
- Characteristics: Name-anchored identity canonicalization, email/role/source tracking

**Event Storage**:
- Table: `memory_events` (and analogous for other entities)
- Columns: id, seq, event_type, entity_id, timestamp, payload
- Indexes: Primary key on id, indexes on seq, entity_id, timestamp
- Characteristics: Sequential logging, entity-scoped retrieval, timestamp ordering

**Conversation Storage**:
- Tables: `conversations` and `conversation_messages`
- Columns: conversations: id, title, source_type, created_at, modified_at, metadata
         conversation_messages: id, conversation_id (FK), message_index, role, content, speaker, timestamp, parent_message_id, is_active_branch
- Indexes: Primary keys, foreign keys, indexes on conversation_id, message_index, timestamp
- Characteristics: Preserves conversation structure, active branch tracking, message-level detail

**Vision Storage**:
- Table: `vision_pages` (or similar)
- Columns: id, document_id, page_number, text, model, prompt_version
- Indexes: Primary key on id, indexes on document_id, page_number, model, prompt_version
- Characteristics: Page-level caching, model/prompt version tracking, coordinates with document storage

**Orchestration Storage** (Job Agent):
- Tables: plans, tasks, events (and analogous)
- Columns: Appropriate for plans/tasks/events with status tracking, timestamps, etc.
- Indexes: Primary keys, foreign keys, indexes on status, timestamps
- Characteristics: Plan/task lifecycle management, event sourcing, approval tracking

### Stage 5: Retrieval Processing

When a user queries the system, data flows through the retrieval pipeline:

**1. Query Validation and Sanitization**
- Input: Raw user query string
- Process:
  - Length check (`MAX_SEARCH_QUERY_CHARS = 500`)
  - Parameter validation (limit must be non-negative integer ≤ `MAX_SEARCH_LIMIT = 50`)
  - Only allow-listed argument keys accepted (query, limit, filter)
  - Only allow-listed filter keys accepted in filter objects
  - Validation failures raise tool-execution errors
- Output: Validated query object
- Special:
  - Operational failures (store unreachable) become error envelopes
  - Execution failures (query could not be executed) are distinguished from "no matches"
  - Prevents resource exhaustion and injection attacks

**2. Query Understanding and Planning**
- Input: Validated query object
- Process:
  - Extract key terms and concepts
  - Identify intent (factual, procedural, exploratory)
  - Determine appropriate retrieval strategy
  - Apply query expansion techniques (synonyms, related concepts)
  - Create retrieval plan specifying:
    - Which data stores to search (memories, corpus, conversations)
    - What types of queries to run for each store
    - How many results to retrieve from each store
    - Any filters or constraints to apply
    - The character budget for final context assembly
- Output: Retrieval plan object
- Special:
  - Deterministic and reproducible
  - Based on lexical analysis and heuristics
  - Designed to work well with available retrieval backends

**3. Multi-Store Retrieval**
- Input: Retrieval plan object
- Process: Execute retrieval against multiple specialized stores:
  
  a) **Keyword Search (FTS5)**:
     - Store: SQLite FTS5 index over document chunks
     - Query Type: Literal term matching with Boolean operators
     - Algorithm: BM25 (Best Matching 25) - lower score = better relevance
     - Characteristics: Exact match, term proximity, field weighting
     - Characteristics: Always available, no external dependencies
     
  b) **Vector Search** (Optional):
     - Store: SQLite table with stored embeddings
     - Query Type: Cosine similarity between query and document vectors
     - Algorithm: Cosine similarity (higher = better similarity)
     - Characteristics: Semantic similarity, concept matching, synonym matching
     - Characteristics: Requires embedding model and pre-computed vectors
     - Characteristics: Gracefully degrades to keyword search only
     
  c) **Memory Search**:
     - Store: SQLite table of structured memories
     - Query Type: Lexical matching with importance/confidence weighting
     - Algorithm: 0.5×relevance + 0.2×importance + 0.2×confidence + 0.1×recency
     - Characteristics: Structured factual data, goal tracking, habit monitoring
     - Characteristics: Always available
     
  d) **Conversation Search**:
     - Store: SQLite table of chat conversations
     - Query Type: Lexical matching over message content
     - Algorithm: Recency-weighted lexical similarity
     - Characteristics: Conversational context, dialogue patterns
     - Characteristics: Always available
     
  e) **Extraction Search**:
     - Store: SQLite table of structured extractions
     - Query Type: Lexical matching over structured extraction text
     - Algorithm: Lexical matching (term frequency, etc.)
     - Characteristics: Structured data from documents (summary, people, etc.)
     - Characteristics: Always available

- Output: Raw results from each store
- Special:
  - Each store returns typed results (ChunkSearchResult, MemoryHit, etc.)
  - Results include provenance information (source, position, etc.)
  - Stores that are not configured or unavailable are skipped
  - Failures in one store don't abort retrieval from other stores
  - Each store enforces its own bounds and limits

**4. Result Fusion and Ranking**
- Input: Raw results from multiple stores
- Process:
  - Normalization: Convert store-specific scores to common scale (0-1 range)
  - Deduplication: Content-based deduplication (chunk_id for documents, memory_id for memories)
  - Trust Ranking: Evidence weighted by verification level (verified > documented > inferred > candidate)
  - Recency Boost: Slight score boost for more recent items
  - Character Budget Application: Truncate results to fit within LLM context window
- Output: Fused, ranked, deduplicated, bounded results
- Special:
  - Score normalization accounts for different ranking scales (BM25 lower=better vs cosine higher=better)
  - Deduplication prevents duplicate content from different stores
  - Trust ranking prioritizes higher quality evidence (direct statements > patterns > inferences)
  - Recency boost gives slight advantage to more recent information
  - Character budget ensures LLM context window is not exceeded
  - Deterministic and repeatable ordering
  - Explainable ranking factors (each component contributes to final score)

**5. Context Assembly**
- Input: Fused, ranked, bounded results
- Process:
  - Allocate character budget:
    * System Instructions: 15-20% (fixed trusted block)
    * Query Restatement: 5-10% (original user question)
    * Retrieved Evidence: 60-70% (bounded excerpts with provenance)
    * Format Instructions: 5-10% (JSON schema or output requirements)
    * Length Constraints: 5-10% (explicit token limits and guidelines)
    * Safety Margin: 0-5% (buffer for encoding variations)
  - Select evidence based on strategy:
    1. High-confidence verified facts (directly stated in profile/documents)
    2. Documented evidence (from trusted sources with attribution)
    3. Inferred evidence (derived from patterns with clear methodology)
    4. Recent items (slight recency boost in ranking)
    5. Diverse sources (avoid over-reliance on single source type)
    6. Relevance density (highest information per character ratio)
  - Assemble final context:
    * System instructions block
    * Query restatement block
    * Retrieved evidence block (with provenance tracking)
    * Format instructions block
    * Length constraints block
- Output: Final context string ready for LLM consumption
- Special:
  - Every evidence item includes full provenance (source, position, confidence, timestamp, content hash, length)
  - Evidence selection avoids over-reliance on any single source type
  - Character budget enforcement prevents LLM context window overflow
  - Format instructions enable structured outputs when requested
  - Length constraints prevent runaway generation in LLM output
  - Safety margin accounts for encoding variations between measurement and actual LLM processing

**6. LLM Reasoning (Optional)**
- Input: Final context string
- Process:
  - Send context to local Ollama instance via `/api/chat` endpoint
  - Apply schema validation if structured output requested
  - Apply evidence ID allow-listing (LLM can only reference evidence IDs provided in context)
  - Apply length limiting to output
  - Apply `"think": false` to prevent reasoning token leakage
  - Handle failures gracefully (fall back to deterministic output)
- Output: LLM response string (or structured object)
- Special:
  - Fail-closed design: any validation failure results in deterministic output
  - Evidence ID allow-listing prevents hallucination of sources
  - Length limiting prevents excessive output generation
  - `"think": false` prevents reasoning token leakage in model output
  - Error containment preserves system security on LLM failure
  - Local-only processing ensures no personal data leaves the device

**7. Final Answer Preparation**
- Input: LLM response string (or structured object)
- Process:
  - Extract answer text or structured data
  - Attach provenance information (sources, confidence levels)
  - Add metadata (query time, tokens used, retrieval stats)
  - Format output according to request (text, JSON, etc.)
  - Indicate uncertainty when appropriate
- Output: Final answer ready for user consumption
- Special:
  - Provenance information shows which personal data informed the response
  - Confidence levels indicate certainty about the information
  - Metadata provides context for interpretation
  - Formatting options accommodate different use cases (text, JSON, etc.)
  - Uncertainty indication prevents overconfidence in ambiguous situations

### Stage 6: Output and Action

The final output from the pipeline can take various forms depending on the user's request and system configuration:

**Direct Responses**:
- Text answers to questions
- Structured data (JSON, tables, lists)
- Visualizations and charts (when implemented via GUI)
- Audio responses (when implemented via TTS)

**Knowledge Artifacts**:
- Extracted memories (saveable as text files)
- Structured extractions (saveable as JSON)
- People profiles (saveable as contact cards)
- Workout summaries (saveable as reports)
- Financial summaries (saveable as reports)
- Email conversation threads (saveable as text files)

**Career Intelligence Outputs**:
- Job listings with relevance scores and fit assessments
- Detailed job assessments (saveable as reports)
- Evidence-based career narratives (saveable as reports)
- CV and cover letter proposals (saveable as DOCX/PDF)
- Application tracking updates (saveable as reports)
- Daily intelligence reports (saveable as markdown files)
- Feedback calibration reports (saveable as markdown files)

**System and Operational Outputs**:
- System status and health reports (saveable as text/JSON)
- Ingestion statistics and progress reports (saveable as text/JSON)
- Memory and people statistics (saveable as text/JSON)
- Tool usage and performance metrics (saveable as text/JSON)
- Configuration summaries (saveable as text/JSON)
- Diagnostic and troubleshooting information (saveable as text/JSON)

### Pipeline Characteristics

#### Idempotency
- **Document Ingestion**: Content-hash based (same file = same content hash = same document ID = no-op)
- **Structured Extraction**: Claim+source hash based (same claim from same source = same extraction = no-op)
- **Memory Creation**: Content-hash based (same content = same memory ID = no-op)
- **Job Persistence**: Canonical-key based (same job = same canonical key = no-op)
- **Artifact Persistence**: Version-based (append-only, never overwrite)
- **Evidence Attachment**: Source+content hash based (same evidence from same source = same attachment = no-op)

#### Safety
- **No Code Execution**: Retrieved data is never executed or interpreted as code
- **No Instruction Following**: System instructions are fixed and trusted; retrieved data is treated as pure data
- **No External References**: LLM cannot reference external sources or invented evidence
- **No State Mutation**: Retrieval never modifies stored data (read-only operations)
- **No Privilege Escalation**: Retrieval runs with same privileges as the rest of the system
- **Input Sanitization**: Query length limiting, parameter validation, result bounding
- **Output Protection**: Schema validation, evidence restriction, length limiting, think disabling
- **Attack Surface Reduction**: No code execution, no instruction following, no external references, no state mutation, no privilege escalation

#### Traceability
- **Document Provenance**: Every document tracks source, path, content hash, timestamps
- **Chunk Provenance**: Every chunk tracks source document, position within document, page number
- **Extraction Provenance**: Every extraction tracks source document, extraction method, model/prompt version (when applicable)
- **Memory Provenance**: Every memory tracks source type, source ID, seen at timestamp, confidence, importance
- **People Provenance**: Every person tracks source document, name, email, role, seen at timestamp
- **Event Provenance**: Every event tracks entity ID, event type, timestamp, payload
- **Conversation Provenance**: Every conversation tracks source, timestamps, participants; every message tracks role, content, speaker, timestamp, parent/child relationships
- **Vision Provenance**: Every vision extraction tracks document ID, page number, vision model, prompt version
- **Job Provenance**: Every job tracks source, discovery method, run ID, discovered at timestamp
- **Evaluation Provenance**: Every evaluation tracks scoring version, profile version, evaluated at timestamp
- **Feedback Provenance**: Every feedback tracks creation timestamp, optional run ID
- **Report Provenance**: Every report tracks generation timestamp, input data hashes, parameters used

#### Privacy
- **Data Locality**: Zero data egress during processing; all processing happens on localhost
- **Minimal Data Retention**: Only necessary metadata is stored for provenance; raw source data can be discarded after ingestion
- **Access Controls**: Scope-based retrieval for memories; source-type filtering for extractions; document filtering for jobs
- **Anonymization**: Aggregated statistics and general trends can be shared without exposing personal data
- **Explicit Sharing**: Personal data only leaves the system when explicitly shared by the user (CV export, report export, etc.)
- **Secure Deletion**: Physical purge option removes record and content while preserving safe event log
- **Bounds and Limits**: Query length limits, result limits, section limits, file size limits prevent resource exhaustion

### Pipeline Resilience

#### Error Handling
- **Source Failures**: Isolated per source; other sources continue processing
- **Storage Failures**: Application-level error handling; clear error messages
- **Retrieval Failures**: Distinguishes between "no matches" (verified outcome) and "execution failures" (operational failure)
- **LLM Failures**: Fail-closed design; falls back to deterministic output
- **Configuration Errors**: Clear error messages at startup; system fails fast rather than operating in degraded state
- **Network Failures**: Affected sources marked as failed; other sources continue processing
- **Memory Corruption**: Detected at startup; system fails to start rather than operating with corrupted data
- **Disk Full**: Applications should handle ENOSPC errors gracefully; existing data remains accessible
- **Invalid Inputs**: Validation failures raise tool-execution errors rather than silent incorrect behavior

#### Recovery
- **Restart Safety**: System can be safely restarted at any point; no partial state corruption
- **State Consistency**: Transactional or sequential processing maintains consistency where possible
- **Idempotent Recovery**: Re-ingesting unchanged data creates zero new rows; recovery is safe
- **Backup and Restore**: Standard SQLite backup/restore procedures work; system designed to work with restored data
- **Migration Support**: Storage format migrations possible through versioned schemas
- **Diagnostic Information**: Error messages and logs provide sufficient information for troubleshooting

### Performance Characteristics

#### Fast Paths (Deterministic, Local)
- Source adapter parsing and normalization
- Document creation (content-hash computation)
- Text extraction (format-specific parsing)
- Document classification (character count comparison)
- Structural extraction (when configured - LLM-dependent but deterministic normalization)
- Chunking (deterministic segmentation)
- Storage persistence (SQLite writes)
- Retrieval planning (query understanding and planning)
- Keyword search (FTS5 BM25 over document chunks)
- Memory search (lexical matching with importance/confidence weighting)
- Conversation search (recency-weighted lexical similarity)
- Extraction search (lexical matching over structured extractions)
- Result fusion and ranking (score normalization, deduplication, trust ranking, recency boost, character budget)
- Context assembly (fixed allocations, evidence selection, length constraints)
- LLM reasoning (when enabled - model-dependent latency)
- Final answer preparation (answer extraction, provenance attachment, metadata addition, formatting)

#### Variable Paths (External Dependencies)
- Job discovery: provider API latency and availability (seconds to minutes per request)
- Search-engine discovery: network latency and rate limiting (seconds to minutes per request)
- LLM reasoning: model load time and response latency (10-60 seconds per call on local hardware)
- Evidence retrieval: Personal AI database query complexity (depends on query complexity and indices)
- File parsing: document size and complexity (PDF/DOCX processing time varies with size and format)
- Complex evidence retrieval: Personal AI queries over large datasets (depends on query complexity and indices)
- Structural extraction: LLM latency and quality (depends on model and prompt complexity)
- Vision extraction: LLM latency and quality (depends on model and prompt complexity, plus image processing)

#### Primary Bottlenecks
1. **LLM Reasoning**: 10-60 seconds per call on local hardware (varies by model and prompt complexity)
2. **Provider APIs**: Variable response times (seconds to minutes) for job discovery (Greenhouse, Lever, Ashby, etc.)
3. **Search Engines**: Rate limiting and network variability (seconds to minutes per request) for DuckDuckGo, etc.
4. **Large Document Parsing**: PDF/DOCX processing time (seconds to minutes per large file depending on size and complexity)
5. **Complex Evidence Retrieval**: Personal AI queries over large datasets (seconds to complex queries depending on indices and query complexity)
6. **Structured Extraction**: LLM latency and quality (depends on model and prompt complexity)
7. **Vision Extraction**: LLM latency and quality (depends on model and prompt complexity, plus image processing)

### Data Retention and Lifecycle

#### Document Lifecycle
- **Creation**: Via ingestion pipeline (content-hash idempotency)
- **Storage**: Persisted until explicitly deleted
- **Deletion**: Explicit delete operation (removes document, chunks, extractions, embeddings)
- **Idempotency**: Re-ingesting unchanged document creates zero new rows
- **Archiving**: Not typically applied to documents; deletion is preferred for removal
- **Access**: Available for retrieval immediately after persistence

#### Memory Lifecycle
- **Creation**: Via explicit MemoryDraft (content-hash idempotency)
- **Storage**: Persisted until explicit action (update, archive, delete, purge)
- **Update**: Modifies mutable fields (content, summary, confidence, importance, expires_at)
- **Archive**: Moves to archived status (removed from active retrieval)
- **Delete**: Logical delete (sets status to deleted; record kept for auditability)
- **Purge**: Physical removal (record and content gone; safe event log entry remains)
- **Access**: Active memories available for retrieval; archived memories available via explicit request
- **Expiration**: Optional automatic expiration based on expires_at timestamp

#### People Lifecycle
- **Creation**: Via person reference extraction from source records
- **Storage**: Persisted until explicit deletion
- **Update**: Modifies mutable fields (display_name, emails, roles, sources, timestamps)
- **Deletion**: Explicit delete operation (removes person record and evidence references)
- **Access**: Available for retrieval immediately after persistence

#### Extraction Lifecycle
- **Creation**: Via ingestion pipeline (content-based deduplication)
- **Storage**: Persisted until explicit deletion (when source document deleted)
- **Update**: Re-extraction when source document changes or extraction config/prompt changes
- **Deletion**: Implicit when source document is deleted
- **Idempotency**: Re-extraction of unchanged source with same config/prompt creates identical extraction
- **Access**: Available for retrieval immediately after persistence

#### Chunk Lifecycle
- **Creation**: Via ingestion pipeline (deterministic chunking)
- **Storage**: Persisted until explicit deletion (when source document deleted)
- **Update**: Re-chunking when source document changes or chunking parameters change
- **Deletion**: Implicit when source document is deleted
- **Idempotency**: Re-chunking of unchanged source with same parameters produces identical chunk set
- **Access**: Available for retrieval immediately after persistence

#### Embedding Lifecycle (Optional)
- **Creation**: Via separate backfill step (content-hash based)
- **Storage**: Persisted until explicit deletion (when source chunk deleted)
- **Update**: Re-embedding when source chunk changes or embedding model changes
- **Deletion**: Implicit when source chunk is deleted
- **Idempotency**: Re-embedding of unchanged chunk with same model produces identical embedding
- **Access**: Available for retrieval immediately after persistence

#### Event Lifecycle
- **Creation**: Via explicit event logging
- **Storage**: Persisted until explicit deletion (typically never deleted for auditability)
- **Update**: Not applicable (events are immutable facts)
- **Deletion**: Logical delete (sets status to deleted; record kept for auditability) or physical purge
- **Access**: Available for retrieval immediately after creation
- **Expiration**: Optional automatic expiration based on timestamp or retention policy

### Pipeline Extensibility

#### Adding New Data Sources
1. Implement new SourceAdapter subclass with `discover()` method
2. Register new source type in source resolver
3. Ensure adapter follows security guidelines (path validation, error isolation, etc.)
4. Test with representative data samples
5. Document supported formats and limitations

#### Adding New Document Types
1. Extend TextExtractor to handle new format
2. Extend Classifier to recognize new format via text analysis
3. Ensure extractor follows safety guidelines (no code execution, bounded processing)
4. Test with representative data samples
5. Update format detection logic as needed

#### Adding New Retrieval Strategies
1. Implement new ChunkIndex subclass with `search()` method
2. Integrate new strategy into RetrievalService
3. Ensure strategy follows safety guidelines (read-only, bounded results, validation)
4. Test with representative queries and data
5. Document ranking characteristics and dependencies

#### Adding New Output Formats
1. Define format specification (schema, structure, constraints)
2. Implement validation logic for the format
3. Specify any special LLM prompting needed for the format
4. Define how to parse and verify LLM output in the format
5. Add any necessary post-processing or transformation
6. Test with representative inputs and expected outputs
7. Document format usage and limitations

#### Adding New Memory Types
1. Extend MemoryKind enum with new kind
2. Update any kind-specific logic (ranking, processing, etc.)
3. Ensure new kind follows memory model constraints
4. Test with representative memory content
5. Update documentation and examples

### Pipeline Monitoring and Observability

#### Metrics to Consider
- Ingestion rate (documents/sec, bytes/sec)
- Storage utilization (document count, chunk count, memory count, etc.)
- Retrieval latency (query to response time)
- LLM usage (calls/sec, success rate, average latency)
- Error rates (ingestion failures, retrieval failures, LLM failures)
- Resource utilization (CPU, memory, disk I/O, network)
- Cache hit rates (vision cache, embedding cache, etc.)
- Queue lengths (if using async processing)
- Job discovery statistics (sources checked, jobs found, etc.)

#### Logging Considerations
- Structured logging for machine parsing
- Error logging with sufficient context for debugging
- Performance logging for bottleneck identification
- Security logging for audit trails (access attempts, failures, etc.)
- Usage analytics (aggregated, non-personal)
- Debug logging for development and troubleshooting

#### Health Checks
- Startup validation (configuration, dependencies, storage integrity)
- Runtime health (responsiveness, error rates, resource utilization)
- Dependency health (Ollama availability, source accessibility, etc.)
- Data integrity (content-hash verification, foreign key constraints, etc.)
- Functional health (basic ingestion, retrieval, agent operation)

## Pipeline Guarantees

### What the Pipeline Guarantees
- **Idempotency**: Re-ingesting unchanged data creates zero new rows
- **Safety**: No code execution, no instruction following, no external calls during ingestion
- **Privacy**: No personal data leaves the local machine during processing
- **Traceability**: Every data element can be traced back to its source
- **Determinism**: Same input always produces same output (where applicable)
- **Error Handling**: Failures are caught, reported, and handled appropriately
- **Bounds and Limits**: Resource consumption is kept within safe limits
- **Provenance Tracking**: Every inference or extraction can be traced to source data
- **Fault Isolation**: Failures in one component don't necessarily abort the whole system
- **Deterministic Fallbacks**: LLM-dependent features have deterministic alternatives
- **Fail-Closed Design**: LLM failures result in deterministic output rather than incorrect output
- **Read-Only Integration**: Job Agent cannot modify Personal AI state
- **Explicit Sharing**: Personal data only leaves system when explicitly shared by user

### What the Pipeline Does Not Guarantee
- **Real-Time Processing**: Ingestion and retrieval have latency depending on data size and system load
- **Instant Availability**: Newly ingested data is available only after processing completes
- **Unlimited Scale**: System resources (RAM, CPU, disk) impose practical limits on data volume
- **Perfect Accuracy**: Extraction and inference quality depends on source quality and system configuration
- **Complete Exhaustion**: Some data may be resistant to ingestion (proprietary formats, DRM, corruption)
- **Universal Compatibility**: Not every possible file format or data source is supported
- **Guaranteed Performance**: Performance depends on hardware, data characteristics, and concurrent load
- **Absolute Security**: While designed with privacy in mind, absolute security requires proper deployment and maintenance
- **Unbounded Retention**: Data accumulates over time unless explicitly managed (archiving, deletion, purging)
- **Universal Understanding**: The system's interpretation of data may not always match user intent
- **Perfect Recall**: Retrieval depends on indexing quality and query formulation
- **Immutable Beliefs**: System outputs are based on current data and configuration; beliefs can change with new data
- **External Trust**: Trust in the system's outputs depends on trust in the system itself and its configuration

## Pipeline Evolution

The data pipeline is designed to evolve with:
- **New Data Sources**: As users accumulate new types of personal data
- **New Technologies**: As new file formats, communication methods, and sensing technologies emerge
- **Improved Techniques**: As better methods for text extraction, chunking, embedding, and structuring are developed
- **Changing Needs**: As users' personal information management needs evolve over time
- **Technological Updates**: As underlying libraries, databases, and languages are updated
- **Performance Optimizations**: As bottlenecks are identified and addressed through optimization
- **Security Enhancements**: As new threats emerge and countermeasures are developed
- **Usability Improvements**: As user feedback indicates areas for improvement in the user experience
- **Regulatory Compliance**: As data protection regulations evolve and require new considerations
- **Integration Opportunities**: As new ways to integrate with other systems and tools emerge while maintaining privacy boundaries

This data pipeline documentation provides a comprehensive view of how data flows through the personal-ai system from raw source material to actionable insights, emphasizing the system's commitment to idempotency, safety, traceability, and privacy at every stage.