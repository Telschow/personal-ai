# Memory

## Overview

Memory stores structured facts, goals, habits, skills, relationships with provenance.

## Model

Each memory has:
* memory_id (content hash)
* kind: fact|goal|habit|skill|relationship|preference|identity|etc.
* content
* summary
* source_type: user|document|email|chrome|...
* source_id
* scope: global|personal|professional|social|family|health|finance|...
* confidence: 0.0-1.0
* importance: 0.0-1.0
* status: active|archived|deleted|superseded
* timestamps: created_at, updated_at, last_accessed_at
* expires_at: optional automatic expiration

## Lifecycle

* Creation: explicit via MemoryService.create with provenance
* Storage: SQLite with content-hash idempotency
* Retrieval: scope-aware, active-and-unexpired-only
* Access tracking: record_access updates last_accessed_at
* Update/archive/supersede/delete: mutable fields
* Reconciliation: deterministic ADD_EVIDENCE/SUPERSEDE/CONFLICT
* No automatic conversation memorization

## Safety

* Explicit creation only
* Untrusted data everywhere
* Identifiers-only event log
* Scope-aware retrieval
* Active-only default by default
* Content-free statistics
* Physical purge option removes record and content

## Usage

CLI: `uv run personal-ai memory list`
API: `/api/memory*`

## Privacy

Memory content is never used for policy decisions. PolicyEngine evaluates tool permissions, not memory content.

## Limitations

* No automatic expiration enforcement
* Memories accumulate unless manually managed