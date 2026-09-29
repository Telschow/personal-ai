# System Overview

## Purpose

The personal-ai system is a local-first personal AI platform designed to help users manage, understand, and derive insights from their personal data while maintaining strict privacy boundaries. It consists of two primary subsystems that work together to provide a comprehensive personal intelligence experience:

1. **Personal AI** - A local knowledge/agent system that ingests personal data (documents, emails, browsing history, chats, workouts, financial data) into a structured knowledge base, enabling question-answering and personal insights via a local LLM.

2. **Job Agent** - A career-search and assessment agent that helps users find, evaluate, and apply for jobs that match their skills, experience, and career goals, using evidence from their personal data to ground assessments.

## Core Philosophy

The system is built around several core principles:

### Local-First by Default
- All data processing happens on the user's local machine
- No cloud dependencies for core operation
- Personal data never leaves the local device unless explicitly shared by the user
- Local Ollama instance is used for LLM inference (default: `http://127.0.0.1:11434`)

### Privacy by Design
- Explicit data ingestion (no automatic collection or memorization)
- Fine-grained scoping and access controls for memories and data
- Identifiers-only event logging and audit trails
- Read-only integration between subsystems (Job Agent reads from Personal AI, never writes)
- Anti-fabrication guards to prevent LLM hallucinations from becoming authoritative evidence

### Deterministic Foundations
- Core retrieval (keyword search, memory search, conversation search) works without LLM
- Idempotent data pipelines (content-hash based prevent duplicates)
- Deterministic scoring and assessment in Job Agent
- Fail-closed design: LLM usage is optional and falls back to deterministic output

### Modular and Extensible
- Narrow, isolated source adapters prevent failure propagation
- Clear interfaces for adding new data stores, retrieval strategies, and output formats
- Plugin-like architecture for tools and connectors
- Configuration-driven behavior with sensible defaults

## Primary Use Cases

### Personal Knowledge Management
- Ingest and organize personal documents, notes, and files
- Search and retrieve information from personal emails, chats, and browsing history
- Ask questions about personal history, habits, goals, and experiences
- Track and reflect on personal development over time
- Derive insights from patterns in personal data (workouts, finances, etc.)

### Career Intelligence and Job Search
- Discover relevant job opportunities from multiple sources
- Assess job fit against personal skills, experience, and career goals
- Ground career assessments in evidence from personal data
- Track job applications and interview progress
- Generate tailored CVs and cover letters based on verified experience
- Optimize LinkedIn profiles using evidence-based approach

### Personal Productivity and Reflection
- Understand patterns in work habits, productivity, and energy levels
- Reflect on personal goals, values, and life direction
- Track progress toward personal and professional objectives
- Identify areas for improvement and growth
- Make data-informed decisions about life and career

## Supported Inputs

### Document Types
- PDF, DOCX, ODT, RTF, TXT, MD
- Spreadsheets (CSV, TSV)
- Presentations (PPTX)
- Images (JPG, PNG, BMP, TIFF) - with optional vision extraction
- Archives (ZIP) - contents extracted and processed

### Communication Data
- Email exports (MBOX format from Google Takeout, Outlook, Thunderbird, Apple Mail)
- Chat exports (ChatGPT, Gemini, WhatsApp, Telegram, Signal)
- SMS/text message logs
- Forum and discussion board exports

### Browser and Activity Data
- Browser history (Chrome, Firefox, Safari, Edge)
- Download history
- Bookmarks and favorites
- Search engine history
- YouTube watch and search history
- Google Keep notes
- NotebookLM activity

### Health and Fitness Data
- Workout data (Garmin, Strava, Fitbit, Apple Health, Google Fit)
- Heart rate, steps, calories, distance metrics
- Workout details (exercises, sets, reps, weights)
- Activity summaries and trends

### Financial Data
- Bank statements (CSV, OFX, QFX, QIF)
- Credit card statements
- Investment account statements
- Portfolio snapshots
- Expense tracking data
- Budgets and financial plans

### Personal Information
- Contact information and address books
- Calendar events and schedules
- Goals, habits, and personal metrics
- Journals, diaries, and personal reflections
- Photos and videos with metadata

### Professional Data
- Resumes, CVs, and cover letters
- Work history and employment records
- Education and certification records
- Skills inventories and assessments
- Project histories and accomplishments

## Supported Outputs

### Query Responses
- Direct answers to questions about personal data
- Contextual responses with supporting evidence
- Structured responses (JSON, tables, lists) when requested
- Citations showing which personal data informed the response
- Confidence levels and source attributions
- Clear indication of uncertainty when information is incomplete or ambiguous

### Knowledge Artifacts
- Extracted memories (facts, goals, habits, skills, relationships)
- Identified people with their aliases, roles, and contexts
- Structured extractions from documents (summary, people, organizations, projects, goals, topics)
- Vision extractions from image-heavy documents
- Workout summaries and statistics
- Financial transaction summaries and patterns
- Email conversation threads and participants

### Career Intelligence Outputs
- Job listings with relevance scores and fit assessments
- Detailed job assessments showing strengths, gaps, and recommendations
- Evidence-based career narratives showing how personal experience matches job requirements
- CV and cover letter proposals grounded in verified evidence
- Application tracking and status updates
- Daily intelligence reports showing changes in the job market relevant to the user
- Feedback calibration reports showing how human input improves ranking quality

### System and Operational Outputs
- System status and health reports
- Ingestion statistics and progress reports
- Memory and people statistics
- Tool usage and performance metrics
- Configuration summaries
- Diagnostic and troubleshooting information

## System Boundaries

### What Stays Local (Never Leaves the Device Unless Explicitly Shared)
- All Personal AI memories, documents, and embeddings
- User CVs, cover letters, and career documents
- Extracted evidence, claims, and sections from personal documents
- Tailored CV/cover letter artifacts (until explicitly shared)
- Feedback, application decisions, and private notes
- Local LLM inputs, outputs, and reasoning traces
- IP addresses, network traffic, and system logs
- Raw source data (emails, documents, chats, etc.) after ingestion

### What May Leave Local (With User Discretion)
- Generalized job market analytics (anonymized, aggregated)
- Non-personal career trends and statistics
- Publicly available job information (titles, companies, locations)
- Explicitly shared CV/cover letter artifacts (user-initiated share)
- Explicitly shared application outcomes (user-initiated share)
- Aggregated, non-personal usage statistics (opt-in only)

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

## Key System Characteristics

### Data Scale (Typical Personal User)
- Documents: 1,000 - 100,000+ files
- Email messages: 1,000 - 100,000+ messages
- Chat messages: 1,000 - 50,000+ messages
- Browser history entries: 10,000 - 500,000+ entries
- Workout sessions: 100 - 2,000+ sessions
- Financial transactions: 1,000 - 10,000+ transactions
- Memories: 100 - 10,000+ structured memories
- People: 100 - 5,000+ identified individuals
- Document chunks: 10,000 - 500,000+ chunks
- Embeddings: 10,000 - 500,000+ vectors (if enabled)

### Performance Characteristics
- **Fast Paths** (Deterministic, Local):
  - Document ingestion and chunking
  - Keyword search (FTS5) over document chunks
  - Memory search over structured memories
  - Conversation search over chat history
  - Deterministic scoring and fit assessment
  - Evidence extraction and mapping from documents
  - Anti-fabrication validation
  - Artifact assembly and persistence
  - Report generation from stored data

- **Variable Paths** (External Dependencies):
  - Job discovery: provider API latency and availability
  - Search-engine discovery: network latency and rate limiting
  - LLM reasoning: model load time and response latency (10-60 seconds per call)
  - Evidence retrieval: Personal AI database query complexity
  - File parsing: document size and complexity (PDF/DOCX processing)
  - Complex evidence retrieval: Personal AI queries over large datasets

### Primary Bottlenecks
1. **LLM Reasoning**: 10-60 seconds per call on local hardware (varies by model and prompt complexity)
2. **Provider APIs**: Variable response times (seconds to minutes) for job discovery
3. **Search Engines**: Rate limiting and network variability (seconds to minutes per request)
4. **Large Document Parsing**: PDF/DOCX processing time (seconds to minutes per large file)
5. **Complex Evidence Retrieval**: Personal AI queries over large datasets (seconds to complex queries)

### Data Freshness and Latency
- **Ingestion Latency**: Seconds to minutes per file depending on size and type
- **Data Availability**: Immediately available after ingestion completes
- **Job Discovery Freshness**: Configurable (default: 30 days before considering stale)
- **Memory Availability**: Immediately available after creation
- **LLM Reasoning Availability**: Dependent on local Ollama instance responsiveness

## Evolution and Extensibility

The system is designed to grow with the user's needs and evolving technology:

### Horizontal Extension (New Data Sources)
- Add new source adapters for additional data types
- Extend the ingestion pipeline with new parsers and normalizers
- Leverage existing storage, retrieval, and agent infrastructure

### Vertical Extension (Deeper Insights)
- Enhance existing components with more sophisticated analysis
- Add new memory types or reasoning capabilities
- Improve extraction and structuring of personal data
- Develop more sophisticated personal models and predictions

### Technical Extension (Technology Updates)
- Update underlying libraries and dependencies
- Adopt new LLM models as they become available locally
- Update storage formats and indexing strategies as needed
- Migrate to new architectures while preserving data and user experience

## System Limitations and Assumptions

### Known Limitations
- **Single User Focus**: Designed for individual personal use; multi-user collaboration requires careful consideration of privacy boundaries
- **Local Resource Constraints**: Performance depends on local hardware capabilities (RAM, CPU, storage)
- **LLM Quality Dependence**: Quality of LLM-assisted features depends on the local Ollama model quality
- **Ingestion Complexity**: Some data types may be challenging to ingest (proprietary formats, DRM-protected content)
- **Memory Scale**: Very large personal corpora (100k+ documents) may require optimization for retrieval performance

### Core Assumptions
- **Local Trust Environment**: The user's local machine is trusted and secure
- **Intentional Data Ingestion**: Users consciously choose what data to ingest
- **Privacy-Conscious User**: User values data privacy and prefers local processing
- **Iterative Refinement**: Users will refine their personal knowledge base over time through use
- **Evidence-Based Thinking**: Users value grounding decisions in verifiable evidence from their personal data
- **Local LLM Availability**: A local Ollama instance is available and running for LLM features

This system overview provides a foundation for understanding the personal-ai platform's purpose, architecture, and capabilities. The subsequent architecture documents dive deeper into specific components and their interactions.