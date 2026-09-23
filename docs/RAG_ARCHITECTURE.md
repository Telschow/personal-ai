# RAG Architecture

This document describes the Retrieval-Augmented Generation (RAG) architecture used in the Personal AI system.

## Overview

Personal AI implements a localized RAG system that combines document retrieval with local LLM generation to answer questions about the user's personal data. The system is designed to be fully local-first, with no cloud dependencies for core operation.

## Architecture Layers

```
User Query
        ↓
Query Understanding
        ↓
Retrieval Planning
        ↓
Multi-Stage Retrieval:
    ├── Keyword Search (FTS5)
    ├── Vector Search (Optional)
    ├── Memory Search
    └── Conversation Search
        ↓
Result Fusion & Ranking
        ↓
Character Budget Compaction
        ↓
Context Assembly
        ↓
LLM Reasoning (Optional)
        ↓
Final Answer
```

## Core Components

### 1. Query Understanding
The system analyzes the user's query to:
- Extract key terms and concepts
- Identify intent (factual, procedural, exploratory)
- Determine appropriate retrieval strategy
- Apply query expansion techniques (synonyms, related concepts)

### 2. Retrieval Planning
Based on the query understanding, the system creates a retrieval plan that specifies:
- Which data stores to search (memories, corpus, conversations)
- What types of queries to run for each store
- How many results to retrieve from each store
- Any filters or constraints to apply
- The character budget for final context assembly

### 3. Multi-Stage Retrieval
The system executes retrieval against multiple specialized stores:

#### Keyword Search (FTS5)
- **Store**: SQLite FTS5 index over document chunks
- **Query Type**: Literal term matching with Boolean operators
- **Ranking**: BM25 (lower score = better relevance)
- **Characteristics**: Exact match, term proximity, field weighting
- **Fallback**: Always available, no external dependencies

#### Vector Search (Optional)
- **Store**: SQLite table with stored embeddings
- **Query Type**: Cosine similarity between query and document vectors
- **Ranking**: Similarity score (higher = better relevance)
- **Characteristics**: Semantic similarity, concept matching
- **Dependency**: Requires embedding model and pre-computed vectors
- **Fallback**: Gracefully degrades to keyword search only

#### Memory Search
- **Store**: SQLite table of structured memories
- **Query Type**: Lexical matching with importance/confidence weighting
- **Ranking**: Weighted score (0.5×relevance + 0.2×importance + 0.2×confidence + 0.1×recency)
- **Characteristics**: Structured factual data, goal tracking, habit monitoring
- **Fallback**: Always available

#### Conversation Search
- **Store**: SQLite table of chat conversations
- **Query Type**: Lexical matching over message content
- **Ranking**: Recency-weighted lexical similarity
- **Characteristics**: Conversational context, dialogue patterns
- **Fallback**: Always available

### 4. Result Fusion & Ranking
Results from different stores are combined using:
- **Normalization**: Store-specific scores converted to common scale
- **Deduplication**: Content-based deduplication (chunk_id for documents, memory_id for memories)
- **Trust Ranking**: Evidence weighted by verification level (verified > documented > inferred > candidate)
- **Recency Boost**: More recent items receive slight ranking boost
- **Character Budget**: Results truncated to fit within LLM context window

### 5. Context Assembly
The final context for the LLM includes:
- **System Instructions**: Fixed trusted block defining agent behavior
- **Retrieved Evidence**: Bounded excerpts from personal data with provenance
- **Query Restatement**: The original user question
- **Format Instructions**: JSON schema or specific output requirements
- **Length Constraints**: Explicit token limits for each section

### 6. LLM Reasoning (Optional)
When enabled, the system uses a local LLM to:
- Analyze the retrieved context
- Synthesize information from multiple sources
- Generate reasoned answers to complex questions
- Follow strict output schemas when required
- The LLM is always fail-closed: malformed or unsupported output results in deterministic fallback

### 7. Final Answer
The system returns:
- Direct answer to the user's question
- Provenance information (sources, confidence levels)
- Metadata (query time, tokens used, retrieval stats)
- Optional structured data (JSON, tables, etc.)
- Clear indication of uncertainty when appropriate

## Storage Layer Details

### Document Processing Pipeline
```
Raw Document
        ↓
Format Detection (PDF, DOCX, TXT, MD, etc.)
        ↓
Text Extraction (bounded, no execution)
        ↓
Content Hashing (SHA256 for idempotency)
        ↓
Section Detection (headings, paragraphs, etc.)
        ↓
Chunking (semantic or fixed-size boundaries)
        ↓
Metadata Attribution (source, position, etc.)
        ↓
Storage in Normalized Schema
```

Key characteristics:
- **Idempotent**: Re-ingesting unchanged document creates zero new rows
- **Bounded**: File size, section count, and section length limits
- **Safe**: No code execution, no instruction interpolation
- **Traceable**: Every chunk traces back to source document and position
- **Privatized**: No personal data leaves the local machine

### Memory Modeling
Memories are structured as:
```
{
  "memory_id": "content_hash",
  "kind": "fact|goal|habit|skill|relationship|preference|identity|etc.",
  "content": "The actual memory content",
  "summary": "Brief summary for quick scanning",
  "status": "active|archived|deleted",
  "temporal_scope": "point_in_time|date_range|ongoing|recurring",
  "scope": "global|personal|professional|social|family|health|finance|etc.",
  "confidence": 0.0-1.0,
  "importance": 0.0-1.0,
  "created_at": ISO timestamp,
  "updated_at": ISO timestamp,
  "expires_at": ISO timestamp|null
}
```

Key characteristics:
- **Verified Facts**: Directly stated in user's profile or documents
- **Documented**: Retrieved from trusted sources with attribution
- **Inferred**: Derived from patterns or automation heuristics
- **Candidate**: Present only as a possibility requiring verification
- **Decay**: Importance and confidence can decrease over time
- **Expiration**: Optional automatic expiration for time-sensitive data

### Conversation Storage
Conversations are stored with:
- **Participant Tracking**: Who was involved in the conversation
- **Message Sequencing**: Chronological order of exchanges
- **Content Preservation**: Actual message text (bounded length)
- **Context Attribution**: Source, timestamp, and metadata
- **Privacy Boundaries**: No sharing without explicit user consent

## Retrieval Contract

### Input Validation
All retrieval operations validate input before execution:
- Query length ≤ MAX_SEARCH_QUERY_CHARS (500 characters)
- Limit parameter is non-negative integer ≤ MAX_SEARCH_LIMIT (50)
- Only allow-listed argument keys accepted (query, limit, filter)
- Only allow-listed filter keys accepted in filter objects
- Validation failures raise tool-execution errors
- Only execution failures (store unreachable) become error envelopes

### Output Format
All search tools return a consistent JSON envelope:
```json
{
  "query": "original user query",
  "status": "results|no_matches|error",
  "results": [ { "…provenance…": "…" } ],
  "total_returned": number of items actually returned,
  "truncated": boolean indicating more matches existed,
  "query_length": length of the query (privacy-safe scalar),
  "error": null|string (generic error category, non-null only for error)
}
```

### Status Semantics
The three statuses are mutually exclusive:
- **results**: Query ran and matched at least one item
- **no_matches**: Query ran cleanly but matched nothing (user may reason no relevant data exists)
- **error**: Query could not be executed (operational failure, never as "no documents exist")

## Ranking and Fusion Strategies

### Keyword Search Ranking (FTS5)
- **Algorithm**: BM25 (Best Matching 25)
- **Scoring**: Lower score = better relevance
- **Factors**: Term frequency, inverse document frequency, field length normalization
- **Tie-breaker**: document_id then chunk_id (ascending)
- **Characteristics**: Exact term match, field weighting, proximity boosting

### Vector Search Ranking (Optional)
- **Algorithm**: Cosine similarity
- **Scoring**: Higher score = better relevance (range: -1 to 1, typically 0 to 1 for positive matches)
- **Factors**: Semantic similarity in embedding space
- **Tie-breaker**: document_id then chunk_id (ascending)
- **Characteristics**: Conceptual similarity, synonym matching, topic clustering

### Memory Search Ranking
- **Algorithm**: Weighted linear combination
- **Scoring**: 0.5×relevance + 0.2×importance + 0.2×confidence + 0.1×recency
- **Factors**: Lexical match quality, memory importance, source confidence, time decay
- **Tie-breaker**: memory_id (ascending)
- **Characteristics**: Structured factual recall, goal tracking, habit monitoring

### Conversation Search Ranking
- **Algorithm**: Recency-weighted lexical similarity
- **Scoring**: Combines exact match quality with time decay
- **Factors**: Word overlap, phrase matching, temporal proximity
- **Tie-breaker**: conversation_id then message_index (ascending)
- **Characteristics**: Dialogue recall, context retention, pattern recognition

### Hybrid Search (When Enabled)
When both keyword and vector search are available, the system can use Hybrid Search:

#### Reciprocal Rank Fusion (RRF)
- **Algorithm**: RRF(c) = Σ_i 1/(k + rank_i(c))
- **Parameters**: k = 60 (dampening constant), equal weights
- **Inputs**: 0-based rank positions from each backend
- **Outputs**: Fused score (higher = better relevance)
- **Characteristics**: Scale-free, deterministic, monotone, explainable
- **Benefits**: Combines lexical precision with semantic recall
- **Fallback**: Degrades to available backend if one fails

## Context Assembly and Budgeting

### Character Budget Allocation
The system allocates a fixed character budget for LLM context:
- System Instructions: 15-20% (fixed trusted block)
- Query Restatement: 5-10% (original user question)
- Retrieved Evidence: 60-70% (bounded excerpts with provenance)
- Format Instructions: 5-10% (JSON schema or output requirements)
- Length Constraints: 5-10% (explicit token limits and guidelines)
- Safety Margin: 0-5% (buffer for encoding variations)

### Evidence Selection Strategy
Within the evidence budget, the system prioritizes:
1. **High-confidence verified facts** (directly stated in profile/documents)
2. **Documented evidence** (from trusted sources with attribution)
3. **Inferred evidence** (derived from patterns with clear methodology)
4. **Recent items** (more recent gets slight boost in ranking)
5. **Diverse sources** (avoid over-reliance on single source type)
6. **Relevance density** (highest information per character ratio)

### Provenance Tracking
Every evidence item in the context includes:
- **Source Identification**: Where the information came from
- **Position Information**: Where in the source it was located
- **Confidence Level**: How certain we are about the information
- **Timestamps**: When the information was created/observed
- **Content Hash**: For idempotency and change detection
- **Length Information**: How much of the source was included

## LLM Integration Contract

### Input Constraints
When passing context to the LLM, the system enforces:
- **Schema Validation**: Strict JSON schema for structured outputs
- **Length Caps**: Maximum length for each field in the output
- **Evidence ID Allow-list**: LLM can only reference evidence IDs that were provided in context
- **No External References**: LLM cannot invent new sources or references
- **Format Compliance**: Output must match requested format (JSON, text, etc.)

### Safety Mechanisms
The system implements multiple layers of protection:
1. **System Instructions**: Fixed trusted block defining agent behavior
2. **Trusted Data Boundary**: Clear separation between trusted system and untrusted data
3. **Output Validation**: Post-validate LLM output against schema and allow-list
4. **Error Handling**: Graceful fallback to deterministic output on any failure
5. **Length Limiting**: Hard caps on output length to prevent runaway generation
6. **Think Disabling**: `"think": false` on Ollama requests to prevent reasoning token leakage

### Failure Modes
LLM integration is designed to fail closed:
- **Malformed Output**: Falls back to deterministic output
- **Schema Violations**: Falls back to deterministic output
- **External References**: Falls back to deterministic output
- **Length Violations**: Truncates to maximum allowed length
- **Timeouts**: Falls back to deterministic output
- **Model Errors**: Falls back to deterministic output
- **Any Failure**: Deterministic output stands, LLM usage flag set to false

## Performance Characteristics

### Deterministic Operations (Fast, Predictable)
- Query understanding and planning
- Keyword search (FTS5) over document chunks
- Memory search over structured memories
- Conversation search over chat history
- Result fusion, ranking, and deduplication
- Context assembly and length limiting
- Schema validation and output checking

### Variable Operations (Depend on External Factors)
- Vector search (requires embedding computation and similarity calculation)
- LLM reasoning (model-dependent latency and quality)
- Large document processing (PDF/DOCX parsing and text extraction)
- Complex query planning (many concepts requiring multiple queries)

### Primary Bottlenecks
1. **LLM Reasoning**: 10-60 seconds per call on local hardware (varies by model and prompt complexity)
2. **Embedding Computation**: If vector search is enabled, computing query embedding
3. **Large File Processing**: PDF/DOCX text extraction can take seconds for large files
4. **Complex Queries**: Queries requiring many retrieval steps or complex fusion

## Privacy and Security Guarantees

### Data Locality
- **Zero Data Egress**: No personal data leaves the local machine during retrieval
- **Local Processing**: All retrieval, ranking, and assembly happens on localhost
- **Local LLM**: When used, the LLM runs on local hardware (Ollama)
- **No Cloud Dependencies**: Zero required external network calls for core retrieval

### Input Sanitization
- **Query Length Limiting**: Prevents excessively long queries
- **Parameter Validation**: Prevents injection attacks through input validation
- **Result Bounding**: Prevents memory exhaustion through result limits
- **Content Hashing**: Uses cryptographic hashes for idempotency without storing raw data
- **Length Caps**: Prevents context window overflow through strict limits

### Output Protection
- **Schema Validation**: Prevents injection through structured output validation
- **Evidence Restriction**: Prevents hallucination by limiting LLM to provided evidence IDs
- **Length Limiting**: Prevents excessive output generation
- **Think Disabling**: Prevents reasoning token leakage in model output
- **Error Containment**: Fail-closed design prevents error state leakage

### Attack Surface Reduction
- **No Code Execution**: Retrieved data is never executed or interpreted as code
- **No Instruction Following**: System instructions are fixed and trusted; retrieved data is treated as pure data
- **No External References**: LLM cannot reference external sources or invented evidence
- **No State Mutation**: Retrieval never modifies stored data (read-only operations)
- **No Privilege Escalation**: Retrieval runs with same privileges as the rest of the system

## Performance Optimization Strategies

### Caching
- **Query Planning Cache**: Cache retrieval plans for similar queries
- **Result Cache**: Cache recent query results for identical queries
- **Embedding Cache**: Cache computed embeddings for frequent terms
- **File Parse Cache**: Cache parsed document structures for re-use

### Indexing
- **FTS5 Indexes**: Optimized for keyword search over document chunks
- **Memory Indexes**: Optimized for lexical search over structured memories
- **Conversation Indexes**: Optimized for chat history search
- **Temporal Indexes**: Optimized for time-based queries (recent items first)

### Batch Processing
- **Multi-Query Execution**: Execute multiple retrieval queries in parallel where possible
- **Vector Batch Processing**: Process multiple query embeddings in single batch when beneficial
- **Result Merge Optimization**: Optimize merging of results from multiple sources

### Resource Management
- **Connection Pooling**: Efficient reuse of SQLite connections
- **Memory Boundaries**: Strict limits on memory usage per query
- **Timeout Enforcement**: Hard timeouts on potentially long-running operations
- **Priority Queuing**: Prioritize interactive queries over background processing

## Extensibility Points

### Adding New Data Stores
To add a new data store to the retrieval system:
1. Implement the store interface (get items by query with bounds)
2. Add the store to the retrieval planning logic
3. Define how results from the store contribute to the final context
4. Specify any special handling needed for the store's data type
5. Add validation and error handling for the new store

### Adding New Retrieval Strategies
To add a new retrieval strategy:
1. Implement the strategy interface (transform query to store queries)
2. Define how results are scored and ranked
3. Specify how results fuse with existing strategies
4. Add any necessary validation or error handling
5. Update the character budget allocation if needed

### Adding New Output Formats
To add a new output format:
1. Define the format specification (schema, structure, constraints)
2. Implement validation logic for the format
3. Specify any special LLM prompting needed for the format
4. Define how to parse and verify LLM output in the format
5. Add any necessary post-processing or transformation

## Validation and Testing

### Contract Tests
- Input validation rejects invalid queries and parameters
- Output format matches specification exactly
- Status values are correctly set based on execution results
- Error handling distinguishes between no matches and execution failures
- Character budget limits are strictly enforced
- Provenance information is correctly attached to results
- Deduplication works correctly across result types
- Ranking produces deterministic, repeatable ordering
- Fusion strategies produce correct combined scores when applicable

### Performance Tests
- Query latency stays within acceptable bounds for typical queries
- Memory usage stays within defined limits per query
- Throughput handles expected query volume
- Scaling behavior is predictable with increased data size
- Bottlenecks are identified and documented

### Security Tests
- No personal data leaks in query parameters or results
- No injection vulnerabilities through query or parameters
- No path traversal or file system access vulnerabilities
- No privilege escalation through retrieval operations
- No information side channels through timing or errors
- Fail-closed behavior preserves system security on LLM failure

### Privacy Tests
- No personal data appears in query parameters
- No personal data appears in error messages (except generic types)
- No personal data is transmitted over network during retrieval
- All data processing stays within local machine boundaries
- No external service calls are made for core retrieval functionality