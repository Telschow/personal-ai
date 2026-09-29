# Storage Architecture

## Databases
Personal AI: data/personal-ai.sqlite3, data/personal-ai.db, knowledge.db, previous_project_and_raw_data/data_handling/private_storage/*.db
Job Agent: job_agent/jobs.sqlite3

## Tables Personal AI
documents, document_chunks, chunk_embeddings, structured_extractions, memories, memory_events, memory_evidence, people, people_evidence, conversations, conversation_messages, events, vision_pages, workflows, tasks, executions

## Schema
SQLite with WAL mode
Content-hash IDs for idempotency
Foreign keys enforced
JSON columns for metadata

## Indexing
FTS5 virtual tables for keyword search
B-tree indexes on foreign keys, timestamps, scopes
Vector store optional

## Persistence Guarantees
Idempotent writes via content hash
Atomic transactions
No overwrites, append-only versioning

## Privacy
Local files only
chmod 600 recommended
No encryption at rest currently
Read-only bridge to Job Agent

## Risks
No filesystem encryption
Unbounded growth of chunk_embeddings 82k rows
No automatic cleanup of expired memories
People table growth 2303 rows, evidence 19266 rows
