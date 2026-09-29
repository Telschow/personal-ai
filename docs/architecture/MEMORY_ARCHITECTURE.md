# Memory Architecture

## Purpose
Persistent structured knowledge about user facts, goals, habits, skills, relationships.

## Model
Memory table: memory_id, kind, content, summary, source_type, source_id, scope, scope_id, confidence, importance, status, temporal_scope, created_at, updated_at, last_accessed_at, expires_at

People table: person_id, identity, display_name, emails, roles, sources, first_seen_at, last_seen_at, evidence_count

Memory evidence: memory_id, source_type, source_id, document_id, excerpt, seen_at

## Lifecycle
Create explicit via MemoryService.create with provenance
Store SQLite
Retrieve scope-aware, active-and-unexpired-only
Access tracking via record_access
Update/archive/supersede/delete
Reconcile deterministically: ADD_EVIDENCE / SUPERSEDE / CONFLICT
No automatic conversation memorization

## Security
Explicit creation only
Untrusted data everywhere
Identifiers-only event log
Scope-aware retrieval
Active-only default
Content-free statistics
Physical purge option

## Risks
Sensitive data in memories: relationships, psychological profiles, health metrics, financial habits
No automatic expiration enforcement
Memory accumulation over time
