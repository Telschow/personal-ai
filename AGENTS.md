You are the primary implementation engineer for this repository.

Repository:
    /mnt/immich/projects/personal-ai

Project:
    Personal AI — a local-first personal knowledge and agent system.

Your job is to evolve the existing codebase into a robust personal AI system while preserving the architecture and quality of the existing implementation.

IMPORTANT:
Do NOT rewrite the project from scratch.
Do NOT replace working abstractions just because you would design them differently.
Do NOT implement the entire roadmap in one giant change.
Work incrementally in small vertical slices.
Every slice must have tests.
Keep the repository runnable after every change.
Do not commit unless explicitly asked.

==================================================
CURRENT STATE
==================================================

The repository already contains:

- typed Ollama HTTP client
- Ollama native tool-call parsing
- synchronous Agent loop
- typed ToolRegistry
- sandboxed filesystem tools
- default tool registry
- CLI
- extensive unit/integration tests

Current architecture:

    CLI
     │
     ▼
    Agent
     │
     ▼
    ToolRegistry
     │
     ├── list_directory
     │
     ▼
    FilesystemTool
     │
     ▼
    sandboxed workspace

Ollama is running locally.

The project currently uses Python 3.14+.

Existing quality requirements:

    uv run pytest
    uv run ruff check .
    uv run ruff format --check src tests

must remain clean.

Never introduce tests that require the real Ollama server unless explicitly marked as integration tests and intentionally separated from the normal test suite.

==================================================
VISION
==================================================

Build a local-first personal AI that can:

1. converse with the user
2. use safe tools
3. ingest personal documents
4. understand both text and images
5. extract structured knowledge
6. store durable personal knowledge
7. retrieve relevant knowledge later
8. eventually combine retrieval with agent/tool execution

The system should eventually handle sources such as:

    PDFs
    images
    scanned documents
    notes
    emails
    WhatsApp exports
    ChatGPT exports
    Gemini exports
    text files
    screenshots
    vision boards
    handwritten notes
    other personal data sources

The system should be designed around the idea that a "personal AI" is more than a PDF search engine.

Visual information is first-class information.

Examples:

    vision boards
    goal collages
    handwritten notes
    whiteboards
    diagrams
    mind maps
    financial charts
    photographs
    screenshots
    scanned reports

OCR alone is insufficient for image-heavy documents.

A vision-capable model should be able to interpret semantic information that OCR cannot capture.

Example:

A vision board containing:

    BMW logo
    BCG logo
    family photograph
    €500k
    beach
    dumbbell
    house

should be interpretable as something resembling:

    long-term aspirations involving career progression,
    financial independence, family, fitness, travel, and
    home ownership.

==================================================
TARGET ARCHITECTURE
==================================================

The eventual architecture should resemble:

                        ┌──────────────┐
                        │     CLI      │
                        └──────┬───────┘
                               │
                               ▼
                        ┌──────────────┐
                        │    Agent     │
                        └──────┬───────┘
                               │
                ┌──────────────┼──────────────┐
                │              │              │
                ▼              ▼              ▼
             Tools         Retrieval       Memory
                │              │              │
                │              │              │
                ▼              ▼              ▼
           filesystem      search       structured data
                │              │              │
                └──────────────┼──────────────┘
                               │
                               ▼
                       Personal Knowledge
                               │
                               ▼
                         Document Store
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
              Text-heavy             Image-heavy
                    │                     │
                    ▼                     ▼
                Text model           Vision model
                    │                     │
                    └──────────┬──────────┘
                               ▼
                       Structured extraction
                               │
                               ▼
                        Persistent storage
                               │
                               ▼
                           Embeddings
                               │
                               ▼
                           Retrieval


==================================================
IMPORTANT ARCHITECTURAL PRINCIPLES
==================================================

1. LOCAL FIRST

Prefer local services/models.

Ollama is the current model backend.

Do not introduce cloud APIs unless explicitly requested.

2. PROVIDER INDEPENDENCE

Do not scatter Ollama-specific assumptions throughout the application.

The Ollama client should remain the infrastructure boundary.

Future model providers should be replaceable.

3. TESTABILITY

Business logic must be testable without:

    network
    Ollama
    filesystem outside temporary directories
    external databases

Use dependency injection and fakes/mocks.

4. SECURITY

The filesystem workspace sandbox is a hard security boundary.

Never allow the model to arbitrarily:

    read outside the workspace
    write outside the workspace
    execute shell commands
    execute arbitrary Python
    dynamically import modules
    spawn subprocesses

Do not introduce eval(), exec(), shell execution, or dynamic imports as tool mechanisms.

5. EXPLICIT TOOLS

The model may only execute registered tools.

Never allow model-generated code to become executable code.

6. DATA PRIVACY

Personal data is expected to be sensitive.

Avoid unnecessary logging of:

    document contents
    conversations
    extracted memories
    tool arguments

7. IDEMPOTENT INGESTION

Re-running ingestion on the same unchanged document should not create duplicate documents or memories.

Use stable document identifiers/content hashes where appropriate.

8. OBSERVABILITY

Prefer structured metadata and explicit status information over dumping personal content into logs.

9. SMALL COMPONENTS

Prefer narrow interfaces and composable components.

10. AVOID PREMATURE COMPLEXITY

Do not introduce a large framework merely because it exists.

In particular:

DO NOT immediately introduce Chroma, LangChain, LlamaIndex, or another large orchestration framework.

First establish clean internal interfaces.

==================================================
DOCUMENT MODEL
==================================================

Introduce a clear distinction between:

DOCUMENTS

What was ingested.

Example conceptual model:

    Document
        id
        source
        source_type
        path
        filename
        mime_type
        created_at
        modified_at
        content_hash
        metadata

EXTRACTIONS

What the model understood from a document.

Example:

    Extraction
        document_id
        summary
        people
        projects
        goals
        preferences
        entities
        tags

CHUNKS

Searchable portions of a document.

Example:

    DocumentChunk
        id
        document_id
        text
        page_number
        metadata

EMBEDDINGS

Vector representations used for retrieval.

Keep embeddings conceptually separate from documents.

==================================================
STORAGE
==================================================

Initially prefer SQLite for durable metadata and structured knowledge.

Do not prematurely commit the entire architecture to a specific vector database.

Create abstractions/interfaces where appropriate, for example:

    DocumentStore
    ExtractionStore
    ChunkStore
    EmbeddingStore

The initial implementation can be SQLite-backed.

A future vector implementation can be added later.

The application should not need to know which vector database is being used.

==================================================
INGESTION PIPELINE
==================================================

Build toward a unified ingestion pipeline.

Conceptually:

    Source
      │
      ▼
    Document Loader
      │
      ▼
    Document Analyzer
      │
      ├───────────────┐
      │               │
      ▼               ▼
    Text-heavy     Image-heavy
      │               │
      ▼               ▼
    Text model      Vision model
      │               │
      └───────┬───────┘
              ▼
      Structured extraction
              │
              ▼
        Document storage
              │
              ▼
           Chunking
              │
              ▼
         Embeddings
              │
              ▼
        Retrieval index


Do NOT create separate application-level pipelines for:

    PDF
    image
    email
    WhatsApp

Instead create a common document abstraction and source-specific loaders.

==================================================
TEXT QUALITY
==================================================

Do not classify documents merely as:

    has text
    no text

Measure usable extracted text.

For example, a PDF with:

    mostly empty pages
    image-only pages
    tiny OCR fragments

should be considered image-heavy.

The exact heuristic should be encapsulated in a classifier rather than scattered through ingestion code.

==================================================
VISION
==================================================

Vision processing should only happen when useful.

Potential triggers:

    insufficient extracted text
    image-heavy page
    scanned document
    poor OCR quality
    explicit image source

Do not send every normal text document through a vision model.

Vision processing should be represented by an abstraction.

For example:

    VisionExtractor

Do not hard-code the entire application to a particular vision model.

The exact Ollama model can initially be configured through settings.

==================================================
MODELS
==================================================

Different tasks may use different models.

Conceptually:

    Chat model
        agent conversations

    Text extraction model
        structured document extraction

    Vision model
        image understanding

    Embedding model
        embeddings

Do not assume one model must perform every task.

Models should be configurable.

The current working chat model is:

    qwen3.5:9b

Do not change it unnecessarily.

==================================================
STRUCTURED EXTRACTION
==================================================

Extraction should produce structured data rather than only a giant summary string.

Potential fields:

    summary
    people
    organizations
    projects
    goals
    preferences
    locations
    dates
    topics
    tags

Keep the schema intentionally conservative initially.

Do not create a gigantic ontology before real data exists.

Schemas should be typed.

Prefer Pydantic/dataclasses where appropriate.

==================================================
RETRIEVAL
==================================================

Eventually support:

    semantic search
    keyword search
    metadata filtering

Do not assume semantic vector search is always best.

A personal AI should be able to answer:

    "What did I write about X?"

    "Where did I mention Y?"

    "What documents relate to project Z?"

    "What goals did I have around career?"

Hybrid retrieval can be introduced later.

==================================================
AGENT TOOLS
==================================================

Eventually expose retrieval as an agent tool.

For example:

    search_memory
    search_documents
    get_document
    get_memory

These tools must go through ToolRegistry.

The agent must never directly access storage internals.

==================================================
CLI
==================================================

The CLI currently supports:

    --workspace PATH
    PROMPT

Keep the CLI simple.

Eventually consider:

    --workspace
    --model
    --verbose
    --ingest
    --search

But don't add these until their underlying functionality exists.

==================================================
PROJECT STRUCTURE
==================================================

Move toward a structure similar to:

    src/personal_ai/
        __init__.py

        agent.py
        cli.py
        ollama_client.py

        tools/
            __init__.py
            registry.py
            filesystem.py
            defaults.py

        documents/
            __init__.py
            models.py
            loader.py
            classifier.py
            extractor.py
            pipeline.py

        storage/
            __init__.py
            database.py
            documents.py
            memories.py
            chunks.py

        embeddings/
            __init__.py
            models.py
            ollama.py

        retrieval/
            __init__.py
            search.py

        config.py

Do NOT create empty speculative modules merely to match this tree.

Create modules when their functionality is actually implemented.

==================================================
CONFIGURATION
==================================================

Use environment/configuration rather than hard-coding everything.

Potential settings:

    OLLAMA_BASE_URL
    PERSONAL_AI_CHAT_MODEL
    PERSONAL_AI_TEXT_MODEL
    PERSONAL_AI_VISION_MODEL
    PERSONAL_AI_EMBEDDING_MODEL
    PERSONAL_AI_DATABASE
    PERSONAL_AI_WORKSPACE

But introduce configuration incrementally.

Do not over-engineer configuration before it is needed.

==================================================
TESTING REQUIREMENTS
==================================================

Every new component requires tests.

Normal test suite must not require:

    running Ollama
    internet
    real personal documents

Use:

    pytest
    tmp_path
    httpx.MockTransport
    fake clients
    deterministic fixtures

Integration tests should test boundaries between components.

Examples:

    Agent + ToolRegistry
    Agent + filesystem
    ingestion + temporary files
    storage + temporary SQLite database

==================================================
CURRENT ROADMAP
==================================================

Implement in approximately these phases.

PHASE 1 — CURRENT

DONE:

    Ollama client
    tool calls
    ToolRegistry
    Agent
    filesystem sandbox
    default registry
    CLI
    tests

Do not rewrite these.

PHASE 2 — DOCUMENT FOUNDATION

Implement:

    Document model
    DocumentChunk model
    source metadata
    stable IDs/content hashes
    document store abstraction
    SQLite implementation

Tests first.

PHASE 3 — FILE DISCOVERY

Implement a filesystem document loader capable of discovering supported files inside the workspace.

Initially support a small set, for example:

    .txt
    .md
    .pdf
    .png
    .jpg
    .jpeg

Do not implement every format immediately.

Security:

    discovery must remain inside workspace.

PHASE 4 — TEXT EXTRACTION

Implement extraction for text-based documents.

For PDFs, separate:

    file loading
    page extraction
    text quality measurement

Do not mix model calls into basic file parsing.

PHASE 5 — DOCUMENT CLASSIFICATION

Determine whether a document is:

    text-heavy
    image-heavy
    mixed

Use explicit measurable heuristics.

Test this thoroughly.

PHASE 6 — MODEL EXTRACTION

Implement structured extraction through an abstraction.

For text-heavy documents:

    text model

For image-heavy documents:

    vision model

Do not send unnecessary data to the vision model.

PHASE 7 — CHUNKING + EMBEDDINGS

Introduce chunks.

Then embedding generation.

Keep embedding provider abstract.

Initial provider can be Ollama.

PHASE 8 — RETRIEVAL

Implement:

    semantic search
    metadata filtering
    document retrieval

Initially a simple implementation is preferable.

PHASE 9 — RETRIEVAL TOOLS

Expose retrieval through ToolRegistry.

For example:

    search_documents

Then the existing Agent can reason over the user's personal knowledge.

PHASE 10 — MEMORY

Only after documents/retrieval work well, introduce durable "memories" as a distinct concept.

Memories should not simply be copies of document chunks.

A memory represents extracted durable knowledge about the user.

==================================================
IMPORTANT IMPLEMENTATION WORKFLOW
==================================================

Before changing code:

1. Inspect the existing implementation.
2. Inspect relevant tests.
3. Identify the smallest coherent slice.
4. Implement it.
5. Add/update tests.
6. Run:

       uv run pytest
       uv run ruff check .
       uv run ruff format --check src tests

7. Review the diff.

Do not proceed to an unrelated phase if the current phase is broken.

Keep each change small enough to review.

Do not silently change APIs without updating tests.

Do not delete existing tests merely because an implementation changes.

==================================================
GIT
==================================================

Do not commit changes automatically.

At the end of each coherent implementation slice, report:

    What changed
    Why
    Tests added
    Test results
    Ruff results
    Files changed
    Suggested commit message
    Next recommended slice

The human will decide when to commit.

==================================================
FIRST TASK
==================================================

Do NOT start implementing the entire architecture.

Start with PHASE 2:

    Document model
    DocumentChunk model
    stable document identity/content hash
    minimal SQLite-backed document storage

Before writing code:

    inspect the existing repository
    inspect current tests
    propose the minimal design

Then implement only that slice.

Requirements:

    typed
    testable
    local-first
    no network
    no Ollama dependency
    no vector database yet
    no LangChain/LlamaIndex
    no speculative abstractions

When complete, run the full test/lint/format suite and report the result.

STOP after completing this slice and wait for further instructions.
