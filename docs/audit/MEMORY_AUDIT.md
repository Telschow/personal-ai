# Memory Audit

## Executive Summary

This audit examines the memory architecture of the `personal-ai` repository. The memory system stores structured memories (facts, goals, habits, skills, relationships, preferences, identity, etc.), people records (with emails, roles, sources), and memory evidence (provenance attachments). Memory is a core component of the Personal AI system, providing persistent storage for user-specific information that informs the agent's understanding and enables personalized responses.

**Overall Assessment:** The memory architecture is well-designed with clear separation between memory creation (explicit, controlled), storage (SQLite-backed), retrieval (scope-aware, active-and-unexpired-only), and reconciliation (deterministic candidate application). The system implements important privacy protections: memory creation is explicit, there is no automatic conversation memorization, and memory is treated as untrusted data everywhere. Key risks involve the extensive personal data stored in memories (relationships, psychological profiles, health metrics), the lack of automatic expiration enforcement, and the potential for memory accumulation over time.

---

## Memory Model & Storage

### Memory Structure
```python
class Memory:
    memory_id: str          # content_hash
    kind: MemoryKind        # fact|goal|habit|skill|relationship|preference|identity|etc.
    content: str            # The actual memory content
    summary: str            # Brief summary for quick scanning
    source_type: MemorySourceType  # user|document|email|chrome|youtube|gemini|keep|financial|notebooklm|corpus
    source_id: str          # Source-specific identifier
    scope: MemoryScope      # global|personal|professional|social|family|health|finance|etc.
    scope_id: str | None    # Optional scope refinement
    confidence: float       # 0.0-1.0 (certainty about the memory)
    importance: float       # 0.0-1.0 (priority/weight of the memory)
    status: MemoryStatus    # active|archived|deleted|superseded
    temporal_scope: TemporalScope  # point_in_time|date_range|ongoing|recurring
    created_at: str         # ISO timestamp
    updated_at: str         # ISO timestamp
    last_accessed_at: str   # ISO timestamp (updated by record_access)
    expires_at: str | None  # Optional automatic expiration
```

### People Model
```python
class Person:
    person_id: str          # SHA256 of normalized identity
    identity: str           # Canonical identity (given + last surname)
    display_name: str       # Original observed form
    emails: tuple[str, ...] # Email aliases
    roles: tuple[str, ...]  # Source-derived roles (email/financial)
    sources: tuple[str, ...] # Source types where referenced
    first_seen_at: str      # ISO timestamp
    last_seen_at: str       # ISO timestamp
    evidence_count: int     # Number of evidence rows
```

### Memory Evidence (Provenance)
```python
class MemoryEvidenceRef:
    memory_id: str          # Target memory
    source_type: str        # Source type (document, email, etc.)
    source_id: str          # Source-specific ID
    document_id: str        # Linked document (if applicable)
    excerpt: str            # Extracted text snippet
    seen_at: str            # ISO timestamp when observed
```

### Storage Tables (SQLite)
- `memories` - 104 rows (sample data)
- `memory_events` - 104 rows (event log)
- `people` - 2303 rows (extracted identities)
- `people_evidence` - 19266 rows (person references with context)
- `memory_evidence` - 111 rows (provenance attachments to memories)

---

## Memory Lifecycle

### 1. Creation (Explicit & Controlled)
- **Memory Creation Path:** Only via `MemoryService.create()` or `create_user_memory()`
- **Requires:** `MemoryDraft` with explicit provenance (source_type, source_id)
- **No Automatic Memorization:** Conversations are NOT automatically memorized
- **Agent-Proposed Memories:** Require policy boundary approval (documented future path)
- **Idempotency:** Content-hash based (`memory_id = hash(content)` prevents duplicates)

### 2. Storage & Indexing
- **SQLite Backend:** All memory data stored in SQLite with proper indexing
- **Indexed Fields:** `memory_id` (PK), `kind`, `source_type`, `scope`, `status`, `created_at`, `updated_at`
- **Foreign Keys:** None explicitly declared (but constraints exist in code)
- **Constraints:** Application-level validation of status transitions, confidence/importance bounds

### 3. Retrieval (Scope-Aware & Safe)
- **MemoryService.search():** Primary retrieval method
- **Scope Filtering:** Only searches memories matching requested scope(s)
- **Active-Only:** By default, returns only `MemoryStatus.ACTIVE` memories
- **Unexpired Filter:** Optionally excludes expired memories (`include_expired=False`)
- **Ranking Algorithm:** `0.5×relevance + 0.2×importance + 0.2×confidence + 0.1×recency`
- **Result Format:** Returns `MemoryHit` objects with content-free provenance (identifiers only)
- **No Content Exposure:** `search()` never returns memory content; only identifiers

### 4. Access Tracking & Auditing
- **record_access():** Updates `last_accessed_at` when memory is retrieved via `get()`
- **Event Logging:** Every memory operation logs to `memory_events` table (identifiers only)
- **Safe Event Log:** `memory_events` table contains only identifiers, never content
- **Statistics:** `memory_service.statistics()` returns content-free aggregates

### 5. Lifecycle Management
- **update():** Mutable fields (content, summary, confidence, importance, expires_at)
- **archive():** Moves to `MemoryStatus.ARCHIVED` (removed from active retrieval)
- **supersede():** Marks as replaced by newer record (kept for auditability)
- **delete():** Logical delete (sets status to `DELETED`, keeps record for auditability)
- **purge():** Physical removal (record and content gone; safe event log entry remains)
- **No Automatic Cleanup:** No background process to purge old or expired memories

### 6. Reconciliation (Deterministic)
- **MemoryCandidate:** Validated candidate statement from policy engine
- **MemoryReconciler:** Applies deterministic rules:
  - `ADD_EVIDENCE`: Attach evidence to existing memory (if not contradicting)
  - `SUPERSEDE`: Replace existing memory with newer record
  - `CONFLICT`: Ambiguous related fact - requires human review (exception raised)
- **Policy Boundary:** Reconciler never consults policy; separation keeps policy authoritative

---

## Memory Privacy Protections

| Protection | Description | Implementation |
|---|---|---|
| **Explicit Creation Only** | No automatic memorization of conversations or passive data collection | `MemoryService.create()` requires explicit `MemoryDraft` |
| **Untrusted Data Everywhere** | Memory content is never treated as trusted for policy decisions | Policy engine evaluates `AgentTool` permissions, not memory content |
| **Identifiers-Only Event Log** | `memory_events` table never contains memory content | `_safe_payload()` returns identifiers only |
| **Scope-Aware Retrieval** | Memories are scoped; retrieval requires explicit scope matching | `MemoryService.search()` requires `scopes` parameter |
| **Active-Only Default** | `list()` and `search()` default to active memories only | `MemoryService.list(status=None)` → `MemoryStatus.ACTIVE` |
| **Content-Free Statistics** | `statistics()` method returns aggregates, never content | Returns counts, not content samples |
| **Physical Purge Option** | `purge()` removes record and content; safe event log remains | For privacy-sensitive deletion when required |
| **No External References** | Memory system never calls external services or networks | Pure SQLite operations; no network dependencies |
| **Read-Only Access Pattern** | `MemoryService.get()` returns full content but is read-only; no mutation path | All mutation goes through explicit `update()`, `archive()`, etc. |

---

## Memory Risks & Concerns

| # | Category | Risk | Severity | Evidence |
|---|---|---|---|---|
| M1 | Sensitive Data Storage | Memories table stores highly sensitive personal data: relationship details ("Shared headline focusing on building an undestroyable foundation"), psychological profiles ("fear of fat loss/gain, obsession with abs"), health metrics ("More than 10k completed sets totaling roughly 5 million kg in volume"), financial habits ("Extreme saving rate to empty accounts monthly"). | HIGH | Found in `structured_extractions` table and sampled from `memories` table during discovery |
| M2 | No Automatic Expiration Enforcement | `expires_at` field exists but no background process checks and purges expired memories. Memories accumulate indefinitely unless manually managed. | MEDIUM | `MemoryService` has no periodic cleanup mechanism; relies on manual `purge()` calls |
| M3 | Memory Accumulation Over Time | No automatic archiving or deletion of old memories. System is designed for accumulation, which could lead to performance degradation over years of use. | MEDIUM | `memories` table grows with every `create()` call; no TTL or archiving policy |
| M4 | Scope Creep Risk | Memories can be assigned any `scope` value (string). While the system has conventional scopes (global, personal, professional, etc.), there's no enforcement against arbitrary scope values that could bypass retrieval filters. | LOW | `MemoryScope` is an enum but `MemoryService.search()` accepts arbitrary `ScopeFilter` objects |
| M5 | Evidence Attachment Proliferation | `memory_evidence` table can grow indefinitely with attachments to memories. Each `apply_candidate()` that chooses `ADD_EVIDENCE` adds rows. | LOW | `memory_evidence` has 111 rows in sample; could grow with usage |
| M6 | Reconciliation Conflict Handling | When `MemoryReconciler.plan()` returns `CONFLICT`, the system raises `MemoryConflictError` which must be caught and handled by the caller. Unhandled conflicts could crash memory operations. | LOW | `apply_candidate()` raises `MemoryConflictError` for ambiguous facts |
| M7 | Memory ID Predictability | `memory_id = SHA256(content)` means identical content always produces same ID. While this enables idempotency, it could potentially allow content guessing if an attacker knows the hash function. | LOW | Standard content-addressable storage; requires pre-knowledge of content to guess ID |
| M8 | People Table Growth | `people` table (2303 rows) and `people_evidence` (19266 rows) grow with every new person reference encountered. No automatic merging beyond name-anchored canonicalization. | LOW | Reflects intended behavior for a personal knowledge system; growth expected with usage |

---

## Memory Strengths

| # | Strength | Description |
|---|---|---|
| S1 | Explicit Creation Boundary | No automatic memorization; all memories require explicit `MemoryDraft` with provenance. Eliminates passive data collection risks. |
| S2 | Untrusted Data Everywhere | Memory content is never used for policy decisions; the PolicyEngine evaluates `AgentTool` permissions, not memory content. Eliminates privilege escalation via memory content. |
| S3 | Identifiers-Only Audit Trail | `memory_events` table logs operations with identifiers only, never content. Enables accountability without exposing sensitive data in logs. |
| S4 | Scope-Aware Retrieval | Memories are scoped by default; retrieval requires explicit scope matching. Prevents accidental cross-scope data leakage. |
| S5 | Deterministic Reconciliation | Memory updates follow deterministic rules (ADD_EVIDENCE/SUPERSEDE/CONFLICT) with clear conflict resolution requiring human review. |
| S6 | Fail-Closed Design | Memory operations that fail (validation, reconciliation) raise exceptions rather than silently corrupting data. |
| S7 | Physical Purge Option | `purge()` provides cryptographic erasure (record and content gone) while preserving the safe event log entry for auditability. |
| S8 | Content-Free Statistics | `statistics()` method returns aggregates for observability without exposing sensitive content. |
| S9 | Idempotent Creation | `memory_id = SHA256(content)` ensures re-creating identical content produces identical ID, preventing duplicates from re-ingestion. |
| S10 | Source Attribution | Every memory tracks `source_type` and `source_id`, enabling provenance tracking and source-based filtering. |

---

## Recommendations (Non-Modification)

**R1 — Memory Sensitivity Classification:** Add a `sensitivity` field to the `Memory` model (e.g., `public|sensitive|highly_sensitive`) that can be set during creation based on `kind` or content analysis. This would enable automatic redaction in list/search operations for sensitive memories unless explicitly requested.

**R2 — Automatic Expiration Enforcement:** Add a periodic cleanup job (CLI command or cron) that:
1. Purges memories where `expires_at` is set and in the past
2. Archives memories older than a configurable threshold (e.g., 2 years)
3. Provides a `--dry-run` flag to preview actions before execution

**R3 — Scope Validation:** Add validation to `MemoryScope` enum that prevents arbitrary string scopes from being used in production. While the enum exists, the service layer currently accepts any `ScopeFilter`.

**R4 — Memory Growth Monitoring:** Add observability metrics for memory table growth rates over time. Monitor `memories.count()`, `people.count()`, and `people_evidence.count()` trends.

**R5 — Conflict Handling Documentation:** Clearly document that `MemoryConflictError` must be caught and handled by callers of `apply_candidate()`. The system intentionally surfaces conflicts for human review rather than auto-resolving.

**R6 — Memory Access Pattern Review:** Review all code paths that call `MemoryService.get()` to ensure they are necessary and appropriate. Consider adding read-only views or proxies for contexts that only need identifiers.

**R7 — People Table Maintenance:** Document the intended growth pattern for `people` and `people_evidence` tables. These are expected to grow with usage as new people are encountered in the personal corpus.

**R8 — Memory Backup Strategy:** Document that memory backups contain sensitive personal data and should be treated with the same protections as the primary database. Consider encrypting backups or storing them in secure locations.

**R9 — Memory Service Interface Review:** Verify that all paths through `MemoryService` maintain the untrusted data boundary. No path should allow memory content to influence policy decisions or trust levels.

**R10 — Memory Event Log Retention:** The `memory_events` table grows with every memory operation. Consider adding a retention policy for the event log (e.g., keep 90 days of events) while preserving auditability for critical operations.

---

## Confidence Estimate

| Finding | Confidence |
|---|---|
| M1-M3 | High (direct evidence from database sampling and code review) |
| M4-M8 | Medium (architectural analysis, some inference) |
| S1-S10 | High (code inspection, documentation review) |
| R1-R10 | Medium (requires implementation to verify effectiveness) |

---

## Next Steps (Post-Audit)

1. Add memory sensitivity classification field and redaction logic
2. Implement automatic expiration enforcement CLI command
3. Add scope validation to prevent arbitrary scope strings
4. Add memory growth observability metrics
5. Document conflict handling requirements for memory reconciliation
6. Review memory access patterns for necessity and appropriateness
7. Document people table growth expectations
8. Create memory backup and encryption strategy guide
9. Review MemoryService interface for untrusted data boundary violations
10. Add memory event log retention policy