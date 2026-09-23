# Data Flow

This document describes how data moves through the Personal AI and Job Agent systems, from ingestion to output.

## Personal AI Data Flow

### 1. Data Ingestion
Personal AI ingests data from various personal sources:

```
Personal Files (PDF, DOCX, TXT, MD)
        ↓
Ingestion Pipeline
        ↓
Parsing & Chunking
        ↓
Embedding Generation (optional)
        ↓
Storage in SQLite
        ↓
Metadata Indexing
        ↓
Retrieval Ready
```

Sources include:
- Documents (PDF, Word, text files)
- Email exports (Outlook, etc.)
- Browser history (Chrome, Firefox)
- Chat exports (WhatsApp, Signal, etc.)
- Financial data (CSV, OFX, etc.)
- Workout data (Garmin, Strava, etc.)
- People/contacts data
- Calendar events
- Notes and journals

### 2. Storage Layer
Data is stored in a normalized SQLite schema:

```
Documents Table
    ├── document_id (content hash)
    ├── filename, mime_type, size_bytes
    ├── content_hash
    └── ingested_at

Chunks Table
    ├── chunk_id
    ├── document_id (FK)
    ├── chunk_index
    ├── text
    └── token_count

Embeddings Table (optional)
    ├── embedding_id
    ├── chunk_id (FK)
    ├── vector (float array)
    └── model_name

Memories Table
    ├── memory_id
    ├── kind (fact, goal, habit, skill, etc.)
    ├── content
    ├── summary
    ├── status
    ├── temporal_scope
    ├── scope
    ├── confidence
    ├── importance
    ├── created_at
    ├── updated_at
    └── expires_at

Conversations Table
    ├── conversation_id
    ├── participants
    ├── messages
    └── timestamps

Events Table
    ├── event_id
    ├── source (chrome, youtube, etc.)
    ├── event_type
    ├── timestamp
    └── payload
```

### 3. Retrieval Pathways
When a user queries the system, data flows through:

```
User Query
        ↓
Query Understanding & Expansion
        ↓
Retrieval Planning (template queries)
        ↓
Execution Against Stores:
    ├── Keyword Search (FTS5 over chunks table)
    ├── Vector Search (cosine similarity over embeddings)
    ├── Memory Search (lexical + importance scoring)
    └── Conversation Search
        ↓
Result Fusion & Ranking
        ↓
Character Budget Compaction
        ↓
Context Assembly for LLM
        ↓
LLM Reasoning (optional)
        ↓
Final Answer
```

### 4. Output Paths
Results from Personal AI flow to:
- Chat responses via the HTTP API
- CLI query results
- Evidence for Job Agent career assessments
- Context for LLM-assisted tasks
- Memory and people suggestions

## Job Agent Data Flow

### 1. Job Discovery
Jobs flow into the system through multiple discovery channels:

```
Configuration (sources.yaml, company_radar.yaml)
        ↓
Source Adapter Instantiation
        ↓
Parallel Fetching:
    ├── ATS Providers (Greenhouse, Lever, Ashby, SmartRecruiters)
    ├── RSS/JSON Feeds
    ├── Sitemap Discovery
    ├── Structured Page Parsing
    ├── Direct Company Pages (JSON-LD)
    └── Search Engine Discovery (via ddgs)
        ↓
Rate Limiting & Pacing
        ↓
Error Isolation (per-source failures)
        ↓
Raw Job Collection
        ↓
Normalization & Canonicalization
        ↓
Deduplication (by canonical_key)
        ↓
Persistence to SQLite
        ↓
Lifecycle Tracking (active/stale/closed)
```

### 2. Scoring and Assessment
Once jobs are stored, they flow through assessment:

```
Stored Job
        ↓
Attribute Extraction (structured job attributes)
        ↓
Career Profile Derivation (from user profile.yaml)
        ↓
Optional Parent Retrieval (Personal AI memory/corpus)
        ↓
Evidence Collection & Compaction
        ↓
Deterministic Scoring:
    ├── Role Match
    ├── Career Direction Match
    ├── Skills Match
    ├── Seniority Match
    ├── Domain Match
    ├── Leadership Match
    ├── Location Match
    ├── Company Match
    └── Compensation Match
        ↓
Score Aggregation (weighted sum)
        ↓
Decision Logic (strong/review/reject thresholds)
        ↓
Persistence to evaluations table
        ↓
Optional Career Fit Assessment:
    ├── Current Fit Calculation
    ├── Career Upside Calculation
    ├── Evidence Coverage
    ├── Strengths/Gaps/Transferables
    ├── Positioning & Risks
    └── Optional LLM Narrative
        ↓
Persistence to career_fit table
```

### 3. Feedback Integration
Human feedback flows into the system as:

```
User Interaction (CLI or GUI)
        ↓
Feedback Collection (label + optional note)
        ↓
Validation (against VALID_LABELS)
        ↓
Persistence to feedback table:
    ├── job_id (FK)
    ├── label
    ├── note
    ├── created_at
    └── run_id (optional)
        ↓
Available for:
    ├── Precision@K Calculation
    ├── False Positive/Negative Analysis
    ├── Feedback Distribution Reports
    └── Daily Intelligence Reports
```

### 4. CV Tailoring Process
When generating a tailored proposal:

```
Target Job Selection
        ↓
Job Requirements Extraction
        ↓
User CV Ingestion (PDF/DOCX/TXT/MD)
        ↓
Section-Level Parsing
        ↓
Evidence Extraction from CV Sections
        ↓
Requirement→Capability→Evidence Mapping (deterministic floor)
        ↓
Evidence-Based Positioning Plan
        ↓
Claim Validation (anti-fabrication)
        ↓
Assembly of Base Artifact
        ↓
Optional Semantic Refinement (over existing evidence)
        ↓
Optional LLM Polish (fail-closed to deterministic)
        ↓
Persistence as Append-Only Version
        ↓
Evidence Linking (artifact → evidence)
        ↓
Output as PROPOSAL — NOT APPROVED
```

### 5. Application Tracking
Application state flows through:

```
Job Selection (SAVED status)
        ↓
User Action (CLI or GUI):
    ├── Mark as APPLIED
    ├── Add application notes
    └── Set application stage
        ↓
Persistence to applications table:
    ├── job_id (FK)
    ├── status (NOT_APPLIED → APPLIED → etc.)
    ├── cv_path, letter_path
    ├── submitted_at
    ├── notes
    ├── stage (NOT_APPLIED → APPLIED → RESPONDED → INTERVIEW → OFFER → etc.)
    ├── interview_*
    ├── offer_*
    └── closed_at
        ↓
Available for:
    ├── Application Dashboard Views
    ├── Stage Transition Tracking
    └── Outcome Analysis
```

### 6. Reporting and Intelligence
Data flows to reports through:

```
Stored Data (jobs, evaluations, feedback, applications, artifacts)
        ↓
Report Generation Triggers:
    ├── Manual CLI Commands (job-agent digest, job-agent stats)
    ├── Scheduled Jobs (cron, systemd timers)
    ├── User Requests (GUI actions)
        ↓
Aggregation & Analysis:
    ├── Job Counts by Status
    ├── Score Distributions
    ├── Feedback Impact Analysis
    ├── Precision@K & False Positive/Negative Rates
    ├── Career Direction Trends
    ├── Location Distribution
    ├── Application Funnel Metrics
    └── Temporal Deltas (vs. previous runs)
        ↓
Report Formatting:
    ├── Markdown Output
    ├── JSON Output (--json flag)
    ├── Human-Readable Summaries
    └── Machine-Readable Details
        ↓
Storage:
    ├── output/reports/daily-YYYY-MM-DD.md
    ├── output/reports/feedback_calibration.md
    ├── output/reports/career_report_*.md
    └── output/reports/*.md
```

## Cross-System Data Flow

### Personal AI → Job Agent
The primary data flow between systems is:

```
Personal AI Database (read-only)
        ↓
Job Agent Connection (sqlite3.connect(mode=ro))
        ↓
PersonalAiCareerKnowledge Adapter:
    ├── memory_search()
    ├── corpus_search()
    └── health()
        ↓
Career Evidence Collection (in Job Agent):
    ├── Template query planning based on job requirements
    ├── Bounded retrieval per concept
    ├── Trust-ranked deduplication
    └── Character budget compaction
        ↓
Career Fit Assessment:
    ├── Current Fit Calculation
    ├── Career Upside Calculation
    ├── Evidence Coverage Score
    └── Strengths/Gaps/Transferables/Positioning/Risks
        ↓
Persistence to career_fit table
        ↓
Available for:
    ├── Job Detail Views (CLI/API/GUI)
    ├── Score Explanations
    ├── Ranking Adjustments
    └── Career Intelligence Reports
```

### Key Characteristics
1. **Read-Only**: Job Agent never writes to Personal AI database
2. **No Writes**: Zero data modification flows from Job Agent to Personal AI
3. **Policy Boundary**: No policy or permission changes flow between systems
4. **No Secrets**: No credentials or sensitive data exposed in the connection
5. **No Execution**: No code execution or shell commands allowed through the connection
6. **Local-Only**: Connection is entirely local (Unix socket or localhost TCP)
7. **Failure Tolerance**: If Personal AI is unavailable, Job Agent degrades gracefully to profile-only evidence with explicit risk notes

## Data Transformation Principles

### Idempotency
- Document ingestion: content-hash based (unchanged file = no-op)
- Job persistence: canonical_key based (duplicate detection)
- Artifact persistence: version-based (append-only, never overwrite)
- Evidence extraction: claim+source hash based (idempotent re-extraction)

### Immutability
- Raw source data: never modified after ingestion
- Evaluations: never updated after creation (score is point-in-time assessment)
- Feedback: never overwritten (append-only historical record)
- Artifacts: append-only versioning (each tailor() creates new version)
- Evidence: never modified after extraction (immutable facts)

### Provenance Tracking
Every significant data element carries provenance:
- Jobs: source, discovery_method, run_id, discovered_at
- Evaluations: scoring_version, profile_version, evaluated_at
- Feedback: created_at, optional run_id
- Evidence: source, source_type, created_at, confidence
- Artifacts: base_document_id, version, created_at, llm_used
- Reports: generation timestamp, input data hashes, parameters used

## Privacy and Security Boundaries

### What Stays Local
- All Personal AI memories, documents, and embeddings
- User CVs, cover letters, and career documents
- Extracted evidence, claims, and sections
- Tailored CV/cover letter artifacts (until explicitly shared)
- Feedback, application decisions, and private notes
- Local LLM inputs, outputs, and reasoning traces
- IP addresses, network traffic, and system logs

### What May Leave Local (With Discretion)
- Generalized job market analytics (anonymized, aggregated)
- Non-personal career trends and statistics
- Publicly available job information (titles, companies, locations)
- Explicitly shared CV/cover letter artifacts (user-initiated)
- Explicitly shared application outcomes (user-initiated)
- Aggregated, non-personal usage statistics (opt-in)

### Network Boundaries
**Personal AI**:
- Input: None (purely local file ingestion)
- Output: None (unless user explicitly shares via clipboard/file)
- LLM: Local Ollama instance (default: http://127.0.0.1:11434)
- Network: Zero required for core operation (filesystem and localhost only)

**Job Agent**:
- Input: Outbound HTTP to job providers and search engines
- Output: None (unless user explicitly exports/shares)
- LLM: Local Ollama instance (same as Personal AI)
- Network: Required for discovery (providers, search engines)
- Persistence: Local SQLite only
- Integration: Local read-only SQLite (Personal AI → Job Agent)

## Performance and Bottlenecks

### Fast Paths (Deterministic, Local)
- Job normalization and deduplication
- Deterministic scoring and fit assessment
- Evidence extraction and mapping from documents
- Anti-fabrication validation
- Artifact assembly and persistence
- Report generation from stored data

### Variable Paths (External Dependencies)
- Job discovery: provider API latency and availability
- Search-engine discovery: network latency and rate limiting
- LLM reasoning: model load time and response latency
- Evidence retrieval: Personal AI database query complexity
- File parsing: document size and complexity

### Primary Bottlenecks
1. **LLM Reasoning**: 10-60 seconds per call on local hardware
2. **Provider APIs**: Variable response times (seconds to minutes)
3. **Search Engines**: Rate limiting and network variability
4. **Large Document Parsing**: PDF/DOCX processing time
5. **Complex Evidence Retrieval**: Personal AI queries over large datasets

## Validation and Integrity Checks

### Schema Integrity
- Foreign key constraints enforced where appropriate
- Not-null constraints on required fields
- Unique constraints where deduplication is needed
- Check constraints for enumerated values

### Business Logic Integrity
- Feedback labels validated against VALID_LABELS
- Score thresholds validated to be in 0-100 range
- Weights validated to sum exactly to 1.0
- Evidence levels validated against VerificationLevel enum
- Artifact statuses validated against ArtifactStatus enum
- Salary conversion validated against fixed FX table

### Anti-Fabrication Guards
- Numeric claims in artifacts must have supporting evidence
- Entity claims (employer, title, dates) must have supporting evidence
- Generated material never becomes authoritative evidence
- LLM outputs strictly schema-validated and evidence-id restricted
- Any validation failure results in REQUIRES_REVIEW status

### Consistency Checks
- Canonical key matches company-title-location hash
- Salary min ≤ salary max (when both present)
- EUR salaries derived from original salaries using fixed FX
- Dates follow chronological order (posted → discovered → last_seen)
- Feedback timestamps are monotonically increasing per job
- Artifact versions are strictly increasing per job