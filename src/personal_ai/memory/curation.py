"""Durable, resumable, policy-gated full-corpus memory curation.

Curation turns already-ingested material (conversation exports today; email,
documents, workouts, and activity through the Slice 7 source adapters in
:mod:`personal_ai.memory.adapters`) into durable memories using exactly the
same policy-gated write path as every other memory feature. It adds **no new
write route**: every candidate flows through the deterministic
:class:`~personal_ai.memory.policy.MemoryPolicy`, the automatic curator's
``propose_memory`` ``memory.write`` approval gate, and
:meth:`MemoryService.apply_candidate` (with deterministic reconciliation).
The module performs no raw SQL against the memory tables and never touches
SQLite directly for writes.

The runner is:

* **idempotent** — re-running the same bounded window merges evidence onto the
  same memories via reconciliation; it never duplicates facts;
* **resumable** — interrupted runs leave checkpoint rows behind and the resume
  path completes exactly the unfinished units (completed units are skipped,
  stale ``running`` units are recovered, ``failed`` units retried with an
  incremented attempt count);
* **durable** — unit progress is checkpointed before any model call, and a
  failed unit never aborts the run;
* **review-safe** — candidates the policy escalates (``require_approval``)
  *and* reconciliation conflicts are parked in the explicit exception review
  queue (categories ``require_approval`` / ``conflict``), never auto-written;
  the review queue is the *only* table in this module that stores statement
  content, and its evidence stays id-only;
* **content-free elsewhere** — run and unit checkpoints store identifiers,
  counts, hashes, timestamps, and statuses only.

Reports are aggregate-only: totals, decision counts, and error counters —
never statements, unit identifiers, or prompt/response text.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, cast

from personal_ai.agents.policy import ApprovalRequiredError
from personal_ai.documents.conversations import ConversationMessage
from personal_ai.memory.conversations import (
    CONVERSATION_SOURCE_TYPES,
    ConversationMemoryExtractor,
)
from personal_ai.memory.corpus import OutcomeTally
from personal_ai.memory.models import MemoryCandidate, now_iso
from personal_ai.memory.policy import MemoryDecision, MemoryPolicy
from personal_ai.memory.proposals import (
    DEFAULT_MAX_CANDIDATES_PER_UNIT,
    DEFAULT_MAX_MESSAGES,
    DEFAULT_MAX_RETRIES,
    MAX_PROMPT_CHARS,
    SYSTEM_PROPOSAL_PROMPT,
    LLMMemoryProposalExtractor,
)
from personal_ai.memory.reconcile import MemoryReconciler, ReconcileAction
from personal_ai.memory.service import MemoryService
from personal_ai.storage.conversations import ConversationStore

# ---------------------------------------------------------------------------
# Versioning and hard defaults
# ---------------------------------------------------------------------------

DETERMINISTIC_EXTRACTOR_VERSION = "conversation-deterministic-v1"
LLM_EXTRACTOR_VERSION = "conversation-llm-proposals-v1"
POLICY_VERSION = "memory-policy-v1"
# The prompt is content-like, so only its hash is ever recorded.
PROMPT_VERSION = hashlib.sha256(SYSTEM_PROPOSAL_PROMPT.encode("utf-8")).hexdigest()[:12]

DEFAULT_MAX_MODEL_CALLS = 0  # 0 == unlimited; the CLI default is also 0
DEFAULT_UNIT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_PROMPT_CHARS = MAX_PROMPT_CHARS

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_DRY = "dry_run"

# Review queue lifecycle (the exception-only human surface).
STATUS_REVIEW_PENDING = "pending"
STATUS_REVIEW_APPROVED = "approved"
STATUS_REVIEW_REJECTED = "rejected"
STATUS_REVIEW_EXPIRED = "expired"

# Escalation categories for the review queue.
REVIEW_CATEGORY_APPROVAL = "require_approval"
REVIEW_CATEGORY_CONFLICT = "conflict"

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CurationError(RuntimeError):
    """Base class for memory curation failures."""


class CurationConfigError(CurationError):
    """Invalid curation configuration."""


class CurationSourceError(CurationError):
    """The configured adapter cannot curate the requested source."""


class CurationResumeError(CurationError):
    """No resumable run exists for the requested source/extraction mode."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class CurationExtractionMode(Enum):
    """How candidates are proposed for one curation run."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"

    @classmethod
    def coerce(cls, value: object) -> CurationExtractionMode:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            for mode in cls:
                if mode.value == value:
                    return mode
            raise CurationConfigError(f"unknown extraction mode {value!r}")
        raise CurationConfigError(f"invalid extraction mode {value!r}")


@dataclass(frozen=True, slots=True)
class CurationConfig:
    """Bounded, validated settings for one curation run.

    ``dry_run`` and ``resume`` are mutually exclusive. All limits are strict
    upper bounds; ``max_model_calls == 0`` means unlimited model calls.
    """

    source_type: str
    extraction: CurationExtractionMode = CurationExtractionMode.DETERMINISTIC
    limit: int = 100
    offset: int = 0
    batch_size: int = 500
    max_messages: int = DEFAULT_MAX_MESSAGES
    max_prompt_chars: int = MAX_PROMPT_CHARS
    max_candidates_per_unit: int = DEFAULT_MAX_CANDIDATES_PER_UNIT
    max_retries: int = DEFAULT_MAX_RETRIES
    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    unit_timeout_seconds: float = DEFAULT_UNIT_TIMEOUT_SECONDS
    min_signal: int = 0
    sample: int = 0
    dry_run: bool = False
    resume: bool = False
    model_name: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.extraction, str):
            object.__setattr__(
                self, "extraction", CurationExtractionMode.coerce(self.extraction)
            )

    def validate(self) -> None:
        if self.dry_run and self.resume:
            raise CurationConfigError("dry_run and resume are mutually exclusive")
        if not self.source_type.strip():
            raise CurationConfigError("source_type must not be empty")
        for label, value in (
            ("limit", self.limit),
            ("max_messages", self.max_messages),
            ("max_candidates_per_unit", self.max_candidates_per_unit),
            ("max_retries", self.max_retries),
        ):
            if value < 1:
                raise CurationConfigError(f"{label} must be >= 1")
        if self.offset < 0:
            raise CurationConfigError("offset must be >= 0")
        # 0 == unlimited model calls.
        if self.max_model_calls < 0:
            raise CurationConfigError("max_model_calls must be >= 0")
        if self.min_signal < 0:
            raise CurationConfigError("min_signal must be >= 0")
        if self.sample < 0:
            raise CurationConfigError("sample must be >= 0")
        if self.batch_size < 1:
            raise CurationConfigError("batch_size must be >= 1")
        if self.max_prompt_chars < 1:
            raise CurationConfigError("max_prompt_chars must be >= 1")
        if self.unit_timeout_seconds <= 0:
            raise CurationConfigError("unit_timeout_seconds must be > 0")
        if self.dry_run:
            return


def _config_record(config: CurationConfig) -> dict[str, object]:
    """Content-free config snapshot for durable checkpoints."""
    return {
        "source_type": config.source_type,
        "extraction": config.extraction.value,
        "limit": config.limit,
        "offset": config.offset,
        "batch_size": config.batch_size,
        "max_messages": config.max_messages,
        "max_prompt_chars": config.max_prompt_chars,
        "max_candidates_per_unit": config.max_candidates_per_unit,
        "max_retries": config.max_retries,
        "max_model_calls": config.max_model_calls,
        "min_signal": config.min_signal,
        "sample": config.sample,
        "unit_timeout_seconds": config.unit_timeout_seconds,
    }


def _versions(
    extraction: CurationExtractionMode, adapter: CurationAdapter
) -> dict[str, str]:
    """Adapter-aware content-free version snapshot for durable checkpoints."""
    extractor, prompt = adapter.version(extraction)
    return {
        "extractor": extractor,
        "policy": POLICY_VERSION,
        "prompt": prompt,
    }


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CurationUnit:
    """One bounded, immutable processing unit for a curation run.

    ``context`` and ``records`` are adapter-defined and opaque to the runner:
    a conversation unit carries a :class:`Conversation` plus bounded messages,
    an email unit carries a domain context plus bounded email windows, a
    generic document unit carries a :class:`Document` plus bounded chunk text,
    and so on. ``source_id`` is the stable unit identity, ``source_version``
    is a deterministic content-derived version (foundation for incremental
    curation), and ``signal`` is a deterministic strength hint used to gate
    LLM processing adaptively.
    """

    unit_id: str
    index: int
    source_type: str
    extraction: CurationExtractionMode
    context: object
    records: tuple[object, ...]
    source_id: str = ""
    source_version: str = ""
    signal: int = 0
    max_messages: int = DEFAULT_MAX_MESSAGES
    max_prompt_chars: int = MAX_PROMPT_CHARS
    max_candidates_per_unit: int = DEFAULT_MAX_CANDIDATES_PER_UNIT
    max_retries: int = DEFAULT_MAX_RETRIES

    def __post_init__(self) -> None:
        if not self.source_id:
            object.__setattr__(self, "source_id", self.unit_id)
        if not self.source_version:
            object.__setattr__(self, "source_version", "v0")
        if self.signal < 0:
            object.__setattr__(self, "signal", 0)


@dataclass(frozen=True, slots=True)
class UnitExtraction:
    """Outcome of processing one unit (pure in-memory; never writes)."""

    candidates: tuple[MemoryCandidate, ...]
    messages_scanned: int
    model_calls: int
    proposals_parsed: int = 0
    proposals_dropped: int = 0
    failed: bool = False
    failure_reason: str = ""


class CurationAdapter(Protocol):  # pragma: no cover
    """The per-source surface the runner depends on.

    Adapters own source-specific bounded discovery and in-memory extraction.
    ``discover`` returns immutable, bounded units (one per conversation,
    per recurring email domain, per document, ...); ``process`` is pure and
    must never touch the database or write memory itself (the runner calls it
    in a worker thread under a wall-clock budget). ``version`` yields
    deterministic ``(extractor_version, prompt_version)`` hashes recorded on
    run checkpoints.
    """

    llm_enabled: bool

    def supports(self, source_type: str) -> bool: ...

    def discover(self, config: CurationConfig) -> tuple[CurationUnit, ...]: ...

    def process(self, unit: CurationUnit) -> UnitExtraction: ...

    def version(self, extraction: CurationExtractionMode) -> tuple[str, str]: ...


class CurationAdapterRegistry:
    """Ordered adapter registry, resolved per source at run time."""

    def __init__(self, *adapters: CurationAdapter) -> None:
        self._adapters: list[CurationAdapter] = list(adapters)

    def register(self, adapter: CurationAdapter) -> None:
        self._adapters.append(adapter)

    def supports(self, source_type: str) -> bool:
        return any(adapter.supports(source_type) for adapter in self._adapters)

    def for_source(self, source_type: str) -> CurationAdapter | None:
        for adapter in self._adapters:
            if adapter.supports(source_type):
                return adapter
        return None


class ConversationCurationAdapter:
    """Bounded conversation adapter for both deterministic and LLM modes.

    ``discover`` pages through stored conversations with bounded reads and
    captures an immutable unit per conversation (context plus at most the
    configured ``max_messages`` active-branch messages). ``process`` is pure
    in-memory extraction and never touches the database, so it can safely run
    in a worker thread.
    """

    def __init__(
        self, conversation_store: ConversationStore, *, client: object | None = None
    ) -> None:
        self._store = conversation_store
        self._client = client
        self.llm_enabled = client is not None

    def supports(self, source_type: str) -> bool:
        return source_type in CONVERSATION_SOURCE_TYPES

    def version(self, extraction: CurationExtractionMode) -> tuple[str, str]:
        if extraction is CurationExtractionMode.LLM:
            return LLM_EXTRACTOR_VERSION, PROMPT_VERSION
        return DETERMINISTIC_EXTRACTOR_VERSION, ""

    def discover(self, config: CurationConfig) -> tuple[CurationUnit, ...]:
        units: list[CurationUnit] = []
        offset = config.offset
        while len(units) < config.limit:
            batch = self._store.list_conversations(
                source_type=config.source_type, limit=config.batch_size, offset=offset
            )
            if not batch:
                break
            for conversation in batch:
                messages = self._store.list_messages(
                    conversation.id,
                    include_inactive=False,
                    limit=config.max_messages,
                    offset=0,
                )
                units.append(
                    CurationUnit(
                        unit_id=conversation.id,
                        index=len(units) + 1,
                        source_type=config.source_type,
                        extraction=config.extraction,
                        context=conversation,
                        records=messages,
                        source_id=conversation.id,
                        source_version=_conversation_window_version(
                            conversation.id, messages
                        ),
                        signal=_user_message_count(messages),
                        max_messages=config.max_messages,
                        max_prompt_chars=config.max_prompt_chars,
                        max_candidates_per_unit=config.max_candidates_per_unit,
                        max_retries=config.max_retries,
                    )
                )
            if len(batch) < config.batch_size:
                break
            offset += len(batch)
        return tuple(units[: config.limit])

    def process(self, unit: CurationUnit) -> UnitExtraction:
        if unit.extraction is CurationExtractionMode.LLM:
            return self._process_llm(unit)
        return self._process_deterministic(unit)

    def _process_deterministic(self, unit: CurationUnit) -> UnitExtraction:
        extractor = ConversationMemoryExtractor(
            max_messages=unit.max_messages,
            max_candidates=unit.max_candidates_per_unit,
        )
        candidates = extractor.extract(unit.context, unit.records)
        return UnitExtraction(
            candidates=tuple(candidates),
            messages_scanned=len(unit.records),
            model_calls=0,
        )

    def _process_llm(self, unit: CurationUnit) -> UnitExtraction:
        extractor = LLMMemoryProposalExtractor(
            self._client,  # type: ignore[arg-type]
            max_messages=unit.max_messages,
            max_prompt_chars=unit.max_prompt_chars,
            max_candidates_per_unit=unit.max_candidates_per_unit,
            max_retries=unit.max_retries,
        )
        result = extractor.extract(unit.context, unit.records)
        if result.failed:
            return UnitExtraction(
                candidates=(),
                messages_scanned=result.messages_scanned,
                model_calls=result.model_calls,
                failed=True,
                failure_reason=result.failure_reason or "model_error",
            )
        return UnitExtraction(
            candidates=result.candidates,
            messages_scanned=result.messages_scanned,
            model_calls=result.model_calls,
            proposals_parsed=result.proposals_parsed,
            proposals_dropped=result.proposals_dropped,
        )


def _conversation_window_version(
    conversation_id: str, messages: tuple[ConversationMessage, ...]
) -> str:
    """Deterministic content-derived version of a bounded conversation window.

    The hash covers the message identities, roles, and content actually loaded
    into the window, so a run over the same unchanged window yields the same
    version and reruns are comparable without trusting timestamps.
    """
    parts = [conversation_id]
    for message in messages:
        parts.append(f"{message.id}|{message.role}|{message.content_text}")
    canonical = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _user_message_count(messages: tuple[ConversationMessage, ...]) -> int:
    """Deterministic LLM-worthiness signal for a conversation window."""
    return sum(
        1 for message in messages if (message.role or "").strip().lower() == "user"
    )


# ---------------------------------------------------------------------------
# Durable checkpoint store
# ---------------------------------------------------------------------------

_RUN_COLUMNS = (
    "run_id",
    "source_type",
    "extraction",
    "status",
    "dry_run",
    "extractor_version",
    "policy_version",
    "prompt_version",
    "model_name",
    "started_at",
    "finished_at",
    "failure_reason",
    "config_json",
    "counters_json",
)

_UNIT_COLUMNS = (
    "run_id",
    "unit_id",
    "unit_index",
    "source_type",
    "source_version",
    "extraction",
    "model_name",
    "status",
    "attempts",
    "started_at",
    "finished_at",
    "failure_reason",
    "counters_json",
)

_REVIEW_COLUMNS = (
    "id",
    "run_id",
    "unit_id",
    "source_type",
    "kind",
    "temporal_scope",
    "confidence",
    "importance",
    "statement",
    "candidate_json",
    "evidence_json",
    "category",
    "reason",
    "status",
    "created_at",
    "reviewed_at",
    "review_note",
)


_NO_UPDATE = object()


class CurationStore:
    """SQLite checkpoints for runs, units, and the explicit review queue."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_curation_runs (
                run_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                extraction TEXT NOT NULL,
                status TEXT NOT NULL,
                dry_run INTEGER NOT NULL DEFAULT 0,
                extractor_version TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                model_name TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                failure_reason TEXT,
                config_json TEXT NOT NULL,
                counters_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_curation_runs_source
                ON memory_curation_runs (source_type, extraction, started_at);
            CREATE TABLE IF NOT EXISTS memory_curation_units (
                run_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                unit_index INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                source_version TEXT NOT NULL DEFAULT '',
                extraction TEXT NOT NULL,
                model_name TEXT,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                failure_reason TEXT,
                counters_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (run_id, unit_id)
            );
            CREATE INDEX IF NOT EXISTS idx_curation_units_run
                ON memory_curation_units (run_id, status);
            CREATE TABLE IF NOT EXISTS memory_curation_review (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                kind TEXT NOT NULL,
                temporal_scope TEXT NOT NULL,
                confidence REAL NOT NULL,
                importance REAL NOT NULL,
                statement TEXT NOT NULL,
                candidate_json TEXT NOT NULL DEFAULT '{}',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                category TEXT NOT NULL DEFAULT 'require_approval',
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                review_note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_curation_review_status
                ON memory_curation_review (status, run_id);
            """
        )
        # Backward-compatible migration: databases created before Slice 7
        # lack the unit source_version column added for incremental curation.
        columns = {
            row[1]
            for row in self._connection.execute(
                "PRAGMA table_info(memory_curation_units)"
            )
        }
        if "source_version" not in columns:
            self._connection.execute(
                "ALTER TABLE memory_curation_units "
                "ADD COLUMN source_version TEXT NOT NULL DEFAULT ''"
            )
        # Phase 18: databases created before the exception-only review model
        # lack the review category/reason escalation labels and the candidate_json
        # column used by the review service to faithfully reconstruct candidates.
        review_columns = {
            row[1]
            for row in self._connection.execute(
                "PRAGMA table_info(memory_curation_review)"
            )
        }
        if "category" not in review_columns:
            self._connection.execute(
                "ALTER TABLE memory_curation_review "
                "ADD COLUMN category TEXT NOT NULL DEFAULT 'require_approval'"
            )
        if "candidate_json" not in review_columns:
            self._connection.execute(
                "ALTER TABLE memory_curation_review "
                "ADD COLUMN candidate_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "reason" not in review_columns:
            self._connection.execute(
                "ALTER TABLE memory_curation_review "
                "ADD COLUMN reason TEXT NOT NULL DEFAULT ''"
            )
        self._connection.commit()

    # ---- runs ----------------------------------------------------------

    def create_run(
        self,
        *,
        run_id: str,
        source_type: str,
        extraction: str,
        status: str,
        dry_run: bool,
        extractor_version: str,
        policy_version: str,
        prompt_version: str,
        model_name: str | None,
        started_at: str,
        config_json: str,
        counters_json: str = "{}",
    ) -> None:
        self._connection.execute(
            f"INSERT INTO memory_curation_runs "
            f"({', '.join(_RUN_COLUMNS)}) VALUES "
            f"({', '.join('?' for _ in _RUN_COLUMNS)})",
            (
                run_id,
                source_type,
                extraction,
                status,
                1 if dry_run else 0,
                extractor_version,
                policy_version,
                prompt_version,
                model_name,
                started_at,
                None,
                None,
                config_json,
                counters_json,
            ),
        )
        self._connection.commit()

    def get_run(self, run_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM memory_curation_runs "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return _run_to_dict(row) if row is not None else None

    def latest_run(
        self, *, source_type: str, extraction: str
    ) -> dict[str, object] | None:
        row = self._connection.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM memory_curation_runs "
            "WHERE source_type = ? AND extraction = ? "
            "ORDER BY started_at DESC, run_id DESC LIMIT 1",
            (source_type, extraction),
        ).fetchone()
        return _run_to_dict(row) if row is not None else None

    def list_runs(
        self, *, source_type: str | None = None, limit: int = 50
    ) -> tuple[dict[str, object], ...]:
        where = " WHERE source_type = ?" if source_type is not None else ""
        params: list[object] = [source_type] if source_type is not None else []
        rows = self._connection.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM memory_curation_runs"
            f"{where} ORDER BY started_at DESC, run_id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return tuple(_run_to_dict(row) for row in rows)

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        finished_at: str | None | object = _NO_UPDATE,
        failure_reason: str | None | object = _NO_UPDATE,
        counters_json: str | object = _NO_UPDATE,
    ) -> None:
        sets: list[str] = []
        params: list[object] = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if finished_at is not _NO_UPDATE:
            sets.append("finished_at = ?")
            params.append(finished_at)
        if failure_reason is not _NO_UPDATE:
            sets.append("failure_reason = ?")
            params.append(failure_reason)
        if counters_json is not _NO_UPDATE:
            sets.append("counters_json = ?")
            params.append(counters_json)
        if not sets:
            return
        params.append(run_id)
        self._connection.execute(
            f"UPDATE memory_curation_runs SET {', '.join(sets)} WHERE run_id = ?",
            params,
        )
        self._connection.commit()

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str,
        failure_reason: str | None,
        counters_json: str,
    ) -> None:
        self.update_run(
            run_id,
            status=status,
            finished_at=finished_at,
            failure_reason=failure_reason,
            counters_json=counters_json,
        )

    # ---- units ---------------------------------------------------------

    def save_unit(
        self,
        *,
        run_id: str,
        unit_id: str,
        unit_index: int,
        source_type: str,
        source_version: str = "",
        extraction: str = "",
        model_name: str | None,
        status: str,
        attempts: int,
        started_at: str,
    ) -> None:
        self._connection.execute(
            f"INSERT INTO memory_curation_units "
            f"({', '.join(_UNIT_COLUMNS)}) VALUES "
            f"({', '.join('?' for _ in _UNIT_COLUMNS)}) "
            "ON CONFLICT(run_id, unit_id) DO UPDATE SET "
            "unit_index = excluded.unit_index, "
            "source_type = excluded.source_type, "
            "source_version = excluded.source_version, "
            "extraction = excluded.extraction, "
            "model_name = excluded.model_name, "
            "status = excluded.status, "
            "attempts = excluded.attempts, "
            "started_at = excluded.started_at, "
            "finished_at = excluded.finished_at, "
            "failure_reason = excluded.failure_reason, "
            "counters_json = excluded.counters_json",
            (
                run_id,
                unit_id,
                unit_index,
                source_type,
                source_version,
                extraction,
                model_name,
                status,
                attempts,
                started_at,
                None,
                None,
                "{}",
            ),
        )
        self._connection.commit()

    def finish_unit(
        self,
        run_id: str,
        unit_id: str,
        *,
        status: str,
        finished_at: str,
        failure_reason: str | None,
        counters_json: str,
    ) -> None:
        self._connection.execute(
            "UPDATE memory_curation_units SET status = ?, finished_at = ?, "
            "failure_reason = ?, counters_json = ? "
            "WHERE run_id = ? AND unit_id = ?",
            (status, finished_at, failure_reason, counters_json, run_id, unit_id),
        )
        self._connection.commit()

    def delete_unit(self, run_id: str, unit_id: str) -> None:
        self._connection.execute(
            "DELETE FROM memory_curation_units WHERE run_id = ? AND unit_id = ?",
            (run_id, unit_id),
        )
        self._connection.commit()

    def list_units(self, run_id: str) -> tuple[dict[str, object], ...]:
        rows = self._connection.execute(
            f"SELECT {', '.join(_UNIT_COLUMNS)} FROM memory_curation_units "
            "WHERE run_id = ? ORDER BY unit_index, unit_id",
            (run_id,),
        ).fetchall()
        return tuple(_unit_to_dict(row) for row in rows)

    # ---- review queue (the only content-bearing table in this module) ---

    def enqueue_review(
        self,
        *,
        run_id: str,
        unit_id: str,
        source_type: str,
        kind: str,
        temporal_scope: str,
        confidence: float,
        importance: float,
        statement: str,
        evidence_json: str,
        candidate_json: str = "{}",
        created_at: str,
        category: str = REVIEW_CATEGORY_APPROVAL,
        reason: str = "",
    ) -> int:
        cursor = self._connection.execute(
            "INSERT INTO memory_curation_review "
            "(run_id, unit_id, source_type, kind, temporal_scope, confidence, "
            " importance, statement, candidate_json, evidence_json, category, "
            " reason, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                run_id,
                unit_id,
                source_type,
                kind,
                temporal_scope,
                confidence,
                importance,
                statement,
                candidate_json,
                evidence_json,
                category,
                reason,
                created_at,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def get_review(self, review_id: int) -> dict[str, object] | None:
        row = self._connection.execute(
            f"SELECT {', '.join(_REVIEW_COLUMNS)} FROM memory_curation_review "
            "WHERE id = ?",
            (review_id,),
        ).fetchone()
        return _review_to_dict(row) if row is not None else None

    def list_review(
        self, *, run_id: str | None = None, limit: int = 200
    ) -> tuple[dict[str, object], ...]:
        where = " WHERE run_id = ?" if run_id is not None else ""
        params: list[object] = [run_id] if run_id is not None else []
        rows = self._connection.execute(
            f"SELECT {', '.join(_REVIEW_COLUMNS)} FROM memory_curation_review"
            f"{where} ORDER BY created_at, id LIMIT ?",
            (*params, limit),
        ).fetchall()
        return tuple(_review_to_dict(row) for row in rows)

    def list_pending_review(
        self,
        *,
        run_id: str | None = None,
        category: str | None = None,
        limit: int = 200,
    ) -> tuple[dict[str, object], ...]:
        where = [" status = 'pending'"]
        params: list[object] = []
        if run_id is not None:
            where.append(" run_id = ?")
            params.append(run_id)
        if category is not None:
            where.append(" category = ?")
            params.append(category)
        rows = self._connection.execute(
            f"SELECT {', '.join(_REVIEW_COLUMNS)} FROM memory_curation_review"
            f"WHERE {' AND'.join(where)} ORDER BY created_at, id LIMIT ?",
            (*params, limit),
        ).fetchall()
        return tuple(_review_to_dict(row) for row in rows)

    def review_counts(self) -> dict[str, object]:
        """Aggregate-only review queue counts (never content).

        Global totals plus per-category and per-status breakdowns so the
        default review surface and reports stay content-free.
        """
        by_status: dict[str, int] = {}
        for status, count in self._connection.execute(
            "SELECT status, COUNT(*) FROM memory_curation_review GROUP BY status"
        ):
            by_status[str(status)] = int(count)
        pending_by_category: dict[str, int] = {}
        for category, count in self._connection.execute(
            "SELECT category, COUNT(*) FROM memory_curation_review "
            "WHERE status = 'pending' GROUP BY category"
        ):
            pending_by_category[str(category)] = int(count)
        return {
            "by_status": by_status,
            "pending": by_status.get("pending", 0),
            "incomplete": int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM memory_curation_review "
                    "WHERE status = 'pending'"
                ).fetchone()[0]
            ),
            "by_category": pending_by_category,
            "approved": by_status.get(STATUS_REVIEW_APPROVED, 0),
            "rejected": by_status.get(STATUS_REVIEW_REJECTED, 0),
            "expired": by_status.get(STATUS_REVIEW_EXPIRED, 0),
        }

    def set_review_status(
        self,
        review_id: int,
        status: str,
        *,
        reviewed_at: str,
        note: str = "",
    ) -> bool:
        """Transition a review row; returns False when it was not pending.

        Only ``pending`` rows may transition, so a double approval or a
        decision on an already-decided row is a no-op (callers verify the
        returned flag before trusting the outcome).
        """
        cursor = self._connection.execute(
            "UPDATE memory_curation_review SET status = ?, reviewed_at = ?, "
            "review_note = CASE WHEN ? = '' THEN review_note ELSE ? END "
            "WHERE id = ? AND status = 'pending'",
            (status, reviewed_at, note, note, review_id),
        )
        self._connection.commit()
        return cursor.rowcount == 1


def _run_to_dict(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    raw = dict(zip(_RUN_COLUMNS, row))
    return {
        "run_id": raw["run_id"],
        "source_type": raw["source_type"],
        "extraction": str(raw["extraction"]),
        "status": str(raw["status"]),
        "dry_run": bool(raw["dry_run"]),
        "extractor_version": raw["extractor_version"],
        "policy_version": raw["policy_version"],
        "prompt_version": raw["prompt_version"],
        "model_name": raw["model_name"],
        "started_at": raw["started_at"],
        "finished_at": raw["finished_at"],
        "failure_reason": raw["failure_reason"],
        "config_json": _json_load(raw["config_json"]),
        "counters_json": _json_load(raw["counters_json"]),
    }


def _unit_to_dict(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    raw = dict(zip(_UNIT_COLUMNS, row))
    return {
        "run_id": raw["run_id"],
        "unit_id": raw["unit_id"],
        "unit_index": int(raw["unit_index"]),
        "source_type": raw["source_type"],
        "source_version": str(raw["source_version"] or ""),
        "extraction": str(raw["extraction"]),
        "model_name": raw["model_name"],
        "status": str(raw["status"]),
        "attempts": int(raw["attempts"]),
        "started_at": raw["started_at"],
        "finished_at": raw["finished_at"],
        "failure_reason": raw["failure_reason"],
        "counters_json": _json_load(raw["counters_json"]),
    }


def _review_to_dict(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    raw = dict(zip(_REVIEW_COLUMNS, row))
    return {
        "id": int(raw["id"]),
        "run_id": raw["run_id"],
        "unit_id": raw["unit_id"],
        "source_type": raw["source_type"],
        "kind": raw["kind"],
        "temporal_scope": raw["temporal_scope"],
        "confidence": raw["confidence"],
        "importance": raw["importance"],
        "statement": raw["statement"],
        "candidate_json": _json_load(raw["candidate_json"]) if raw["candidate_json"] else {},
        "evidence_json": _json_load(raw["evidence_json"]),
        "category": str(raw["category"] or REVIEW_CATEGORY_APPROVAL),
        "reason": str(raw["reason"] or ""),
        "status": raw["status"],
        "created_at": raw["created_at"],
        "reviewed_at": raw["reviewed_at"],
        "review_note": raw["review_note"],
    }


def _json_load(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value


# ---------------------------------------------------------------------------
# Counters and reports
# ---------------------------------------------------------------------------


@dataclass
class RunCounters:
    """Observability counters for one run; no content ever."""

    units_discovered: int = 0
    units_completed: int = 0
    units_failed: int = 0
    units_skipped: int = 0
    units_retried: int = 0
    units_recovered_stale: int = 0
    units_signal_skipped: int = 0
    messages_scanned: int = 0
    model_calls: int = 0
    proposals_parsed: int = 0
    proposals_dropped: int = 0
    errors: dict[str, int] = field(default_factory=dict)
    tally: OutcomeTally = field(default_factory=OutcomeTally)
    evidence_added: int = 0
    superseded: int = 0
    review_queued: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "units": {
                "discovered": self.units_discovered,
                "completed": self.units_completed,
                "failed": self.units_failed,
                "skipped": self.units_skipped,
                "retried": self.units_retried,
                "recovered_stale": self.units_recovered_stale,
                "signal_skipped": self.units_signal_skipped,
            },
            "messages_scanned": self.messages_scanned,
            "model_calls": self.model_calls,
            "proposals_parsed": self.proposals_parsed,
            "proposals_dropped": self.proposals_dropped,
            "evidence_added": self.evidence_added,
            "superseded": self.superseded,
            "review_queued": self.review_queued,
            "errors": dict(self.errors),
            "tally": self.tally.to_dict(),
        }


@dataclass
class DryRunTally:
    """Count-only view of what a dry run *would* do (no writes at all)."""

    candidates: int = 0
    accepted: int = 0
    deferred: int = 0
    rejected: int = 0
    require_approval: int = 0
    would_create: int = 0
    would_update: int = 0
    would_supersede: int = 0
    would_conflict: int = 0

    def add(
        self,
        decision: MemoryDecision,
        action: ReconcileAction | None,
    ) -> None:
        self.candidates += 1
        if decision is MemoryDecision.ACCEPT:
            self.accepted += 1
        elif decision is MemoryDecision.DEFER:
            self.deferred += 1
        elif decision is MemoryDecision.REJECT:
            self.rejected += 1
        elif decision is MemoryDecision.REQUIRE_APPROVAL:
            self.require_approval += 1
        if action is ReconcileAction.CREATE:
            self.would_create += 1
        elif action is ReconcileAction.ADD_EVIDENCE:
            self.would_update += 1
        elif action is ReconcileAction.SUPERSEDE:
            self.would_supersede += 1
        elif action is ReconcileAction.CONFLICT:
            self.would_conflict += 1

    def to_dict(self) -> dict[str, int]:
        return {
            "candidates": self.candidates,
            "accepted": self.accepted,
            "deferred": self.deferred,
            "rejected": self.rejected,
            "require_approval": self.require_approval,
            "would_create": self.would_create,
            "would_update": self.would_update,
            "would_supersede": self.would_supersede,
            "would_conflict": self.would_conflict,
        }


@dataclass(frozen=True, slots=True)
class MemoryStats:
    """Aggregate, content-free memory and review statistics.

    Counts by ``MemoryKind``, ``MemoryStatus``, and ``TemporalScope`` plus the
    evidence record count and review-queue totals. Never statements.
    """

    total: int
    by_kind: dict[str, int]
    by_status: dict[str, int]
    by_temporal: dict[str, int]
    evidence: int
    review: dict[str, object]

    @classmethod
    def from_stores(
        cls,
        memory_service: MemoryService | None,
        curation_store: CurationStore | None,
    ) -> MemoryStats:
        if memory_service is None:
            return cls(
                total=0, by_kind={}, by_status={}, by_temporal={}, evidence=0,
                review={},
            )
        stats = memory_service.statistics()
        by_status = dict(stats.get("by_status", {}))  # type: ignore[arg-type]
        review = curation_store.review_counts() if curation_store is not None else {}
        return cls(
            total=int(by_status.get("memories", 0)),
            by_kind=dict(stats.get("by_kind", {})),  # type: ignore[arg-type]
            by_status=by_status,
            by_temporal=dict(stats.get("by_temporal", {})),  # type: ignore[arg-type]
            evidence=int(stats.get("evidence", 0)),
            review=dict(review),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "by_kind": dict(self.by_kind),
            "by_status": dict(self.by_status),
            "by_temporal": dict(self.by_temporal),
            "evidence": self.evidence,
            "review": dict(self.review),
        }


@dataclass(frozen=True, slots=True)
class CurationReport:
    """Aggregate-only outcome of one curation run.

    ``summary()`` is deliberately content-free: totals, decision counts,
    versions, memory/review distributions, and error reasons — never
    statements, unit identifiers, or prompt/response text.
    """

    run_id: str
    source_type: str
    extraction: str
    dry_run: bool
    status: str
    versions: dict[str, str]
    model_name: str
    counters: RunCounters
    dry_tally: DryRunTally | None = None
    memory_stats: MemoryStats | None = None
    elapsed_seconds: float = 0.0

    def summary(self) -> dict[str, object]:
        units: dict[str, int] = dict(self.counters.to_dict()["units"])  # type: ignore[arg-type]
        return {
            "run_id": self.run_id,
            "source_type": self.source_type,
            "extraction": self.extraction,
            "dry_run": self.dry_run,
            "status": self.status,
            "model_name": self.model_name,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "versions": dict(self.versions),
            "units": units,
            "messages_scanned": self.counters.messages_scanned,
            "model_calls": self.counters.model_calls,
            "proposals_parsed": self.counters.proposals_parsed,
            "proposals_dropped": self.counters.proposals_dropped,
            "evidence_added": self.counters.evidence_added,
            "superseded": self.counters.superseded,
            "review_queued": self.counters.review_queued,
            "errors": dict(self.counters.errors),
            "tally": self.counters.tally.to_dict(),
            "dry_tally": self.dry_tally.to_dict() if self.dry_tally else None,
            "memory": self.memory_stats.to_dict() if self.memory_stats else None,
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class MemoryCurationRunner:
    """Runs bounded, resumable curation through the policy-gated write path.

    Every auto-write goes through :class:`AutomaticMemoryCurator` (→ the
    ``propose_memory`` ``memory.write`` approval gate → ``MemoryService``).
    No second write route exists here, and a denied gate raises
    ``ApprovalRequiredError`` and writes nothing.
    """

    def __init__(
        self,
        *,
        store: CurationStore,
        adapter: CurationAdapter | CurationAdapterRegistry,
        memory_service: MemoryService | None = None,
        policy: MemoryPolicy | None = None,
        reconciler: MemoryReconciler | None = None,
        curator: object | None = None,
    ) -> None:
        self._store = store
        self._adapter: CurationAdapter | CurationAdapterRegistry = adapter
        self._service = memory_service
        self._policy = policy or MemoryPolicy()
        self._reconciler = reconciler or MemoryReconciler(memory_service)  # type: ignore[arg-type]
        self._curator = curator or _default_curator(memory_service, self._policy)
        self._started: float = 0.0

    def run(self, config: CurationConfig) -> CurationReport:
        """Execute one curation run, returning an aggregate-only report.

        * dry run  -> analyses but writes nothing and creates no rows;
        * resume   -> completes exactly the unfinished units of the latest
          failed/interrupted run for this source and extraction mode;
        * otherwise -> a fresh, idempotent run over the configured window.
        """
        config.validate()
        self._started = time.monotonic()

        if isinstance(self._adapter, CurationAdapterRegistry):
            adapter = self._adapter.for_source(config.source_type)
            if adapter is None:
                raise CurationSourceError(
                    f"no curation adapter supports source {config.source_type!r}"
                )
            self._adapter = adapter

        if not self._adapter.supports(config.source_type):
            raise CurationSourceError(
                f"the configured adapter does not support source {config.source_type!r}"
            )
        if (
            config.extraction is CurationExtractionMode.LLM
            and not self._adapter.llm_enabled
        ):
            raise CurationConfigError(
                f"LLM extraction is not available for source "
                f"{config.source_type!r} (the adapter has no model client)"
            )
        if config.resume:
            return self._run_resume(config)
        if config.dry_run:
            return self._run_dry(config)
        return self._run_fresh(config)

    def _run_fresh(self, config: CurationConfig) -> CurationReport:
        if self._curator is None:
            raise CurationConfigError("a memory service is required to run curation")
        versions = _versions(config.extraction, self._adapter)
        run_id = f"cur-{uuid.uuid4().hex}"
        self._store.create_run(
            run_id=run_id,
            source_type=config.source_type,
            extraction=config.extraction.value,
            status=STATUS_RUNNING,
            dry_run=False,
            extractor_version=versions["extractor"],
            policy_version=versions["policy"],
            prompt_version=versions["prompt"],
            model_name=config.model_name,
            started_at=now_iso(),
            config_json=json.dumps(_config_record(config), sort_keys=True),
        )
        counters = RunCounters()
        try:
            self._execute_units(config, run_id, counters)
        except ApprovalRequiredError:
            self._store.finish_run(
                run_id,
                status=STATUS_FAILED,
                finished_at=now_iso(),
                failure_reason="approval_required",
                counters_json=json.dumps(counters.to_dict(), sort_keys=True),
            )
            raise
        except Exception:
            self._store.finish_run(
                run_id,
                status=STATUS_FAILED,
                finished_at=now_iso(),
                failure_reason="internal_error",
                counters_json=json.dumps(counters.to_dict(), sort_keys=True),
            )
            raise
        self._finish_normal(run_id, counters)
        return self._build_report(config, run_id, _normal_status(counters), counters)

    def _run_resume(self, config: CurationConfig) -> CurationReport:
        if self._curator is None:
            raise CurationConfigError("a memory service is required to resume curation")
        row = self._store.latest_run(
            source_type=config.source_type, extraction=config.extraction.value
        )
        if row is None:
            raise CurationResumeError(
                f"no resumable run for source {config.source_type!r} "
                f"(extraction {config.extraction.value!r})"
            )
        run_id = str(row["run_id"])
        units = self._store.list_units(run_id)
        if units and all(str(u["status"]) == STATUS_COMPLETED for u in units):
            raise CurationResumeError(
                f"run {run_id} is already complete (all units finished)"
            )
        stored = row["config_json"]
        settings = stored if isinstance(stored, Mapping) else {}
        discovery = dataclasses.replace(
            config,
            limit=int(settings.get("limit") or config.limit),
            offset=int(settings.get("offset") or config.offset),
            batch_size=int(settings.get("batch_size") or config.batch_size),
        )
        self._store.update_run(
            run_id,
            status=STATUS_RUNNING,
            finished_at=None,
            failure_reason=None,
            counters_json="{}",
        )
        counters = RunCounters()
        try:
            self._execute_units(discovery, run_id, counters, resume=True)
        except ApprovalRequiredError:
            self._store.finish_run(
                run_id,
                status=STATUS_FAILED,
                finished_at=now_iso(),
                failure_reason="approval_required",
                counters_json=json.dumps(counters.to_dict(), sort_keys=True),
            )
            raise
        except Exception:
            self._store.finish_run(
                run_id,
                status=STATUS_FAILED,
                finished_at=now_iso(),
                failure_reason="internal_error",
                counters_json=json.dumps(counters.to_dict(), sort_keys=True),
            )
            raise
        self._finish_normal(run_id, counters)
        return self._build_report(
            discovery,
            run_id,
            _normal_status(counters),
            counters,
            model_name=config.model_name,
        )

    def _finish_normal(self, run_id: str, counters: RunCounters) -> None:
        """Mark a run finished with the status its unit outcomes imply.

        A run that finished with any failed units is recorded as ``failed``
        (so it remains resumable); a wholly successful run is ``completed``
        (terminal, nothing left to resume).
        """
        self._store.finish_run(
            run_id,
            status=_normal_status(counters),
            finished_at=now_iso(),
            failure_reason="unit_failures" if counters.units_failed else None,
            counters_json=json.dumps(counters.to_dict(), sort_keys=True),
        )

    def _run_dry(self, config: CurationConfig) -> CurationReport:
        """Analyze the window without writing or creating any rows."""
        units = self._adapter.discover(config)
        counters = RunCounters(units_discovered=len(units))
        dry = DryRunTally()
        llm_used = 0
        for unit in units:
            unit, skipped = self._apply_gate(config, unit, llm_used)
            if skipped:
                counters.units_signal_skipped += 1
            elif unit.extraction is CurationExtractionMode.LLM:
                llm_used += 1
            result = self._safe_process(config, unit)
            counters.messages_scanned += result.messages_scanned
            counters.model_calls += result.model_calls
            counters.proposals_parsed += result.proposals_parsed
            counters.proposals_dropped += result.proposals_dropped
            if result.failed:
                reason = result.failure_reason or "extractor_error"
                counters.errors[reason] = counters.errors.get(reason, 0) + 1
                counters.units_failed += 1
                continue
            counters.units_completed += 1
            for candidate in result.candidates:
                decision = self._policy.evaluate(candidate).decision
                action = (
                    self._reconciler.plan(candidate).action
                    if self._reconciler is not None
                    else None
                )
                dry.add(decision, action)
        return self._build_report(config, "", STATUS_DRY, counters, dry=dry)

    def _execute_units(
        self,
        config: CurationConfig,
        run_id: str,
        counters: RunCounters,
        *,
        resume: bool = False,
    ) -> None:
        units = self._adapter.discover(config)
        counters.units_discovered = len(units)
        existing: dict[str, dict[str, object]] = (
            {str(row["unit_id"]): row for row in self._store.list_units(run_id)}
            if resume
            else {}
        )
        llm_used = 0
        for unit in units:
            if (
                config.max_model_calls
                and counters.model_calls >= config.max_model_calls
            ):
                break
            effective, skipped = self._apply_gate(config, unit, llm_used)
            if skipped:
                counters.units_signal_skipped += 1
            elif effective.extraction is CurationExtractionMode.LLM:
                llm_used += 1
            attempts = 1
            row = existing.get(unit.unit_id)
            if resume and row is not None:
                state = str(row["status"])
                if state == STATUS_COMPLETED:
                    counters.units_skipped += 1
                    continue
                if state == STATUS_RUNNING:
                    self._store.delete_unit(run_id, unit.unit_id)
                    counters.units_recovered_stale += 1
                else:
                    counters.units_retried += 1
                    attempts = int(row["attempts"]) + 1
            self._store.save_unit(
                run_id=run_id,
                unit_id=unit.unit_id,
                unit_index=unit.index,
                source_type=unit.source_type,
                source_version=unit.source_version,
                extraction=effective.extraction.value,
                model_name=config.model_name,
                status=STATUS_RUNNING,
                attempts=attempts,
                started_at=now_iso(),
            )
            result = self._safe_process(config, effective)
            self._accumulate(result, counters)
            if result.failed:
                reason = result.failure_reason or "extractor_error"
                counters.errors[reason] = counters.errors.get(reason, 0) + 1
                counters.units_failed += 1
                self._store.finish_unit(
                    run_id,
                    unit.unit_id,
                    status=STATUS_FAILED,
                    finished_at=now_iso(),
                    failure_reason=reason,
                    counters_json=json.dumps(_unit_counters(result), sort_keys=True),
                )
                continue
            for candidate in result.candidates:
                self._curate_candidate(unit, candidate, run_id, counters)
            self._store.finish_unit(
                run_id,
                unit.unit_id,
                status=STATUS_COMPLETED,
                finished_at=now_iso(),
                failure_reason=None,
                counters_json=json.dumps(_unit_counters(result), sort_keys=True),
            )
            counters.units_completed += 1

    def _apply_gate(
        self, config: CurationConfig, unit: CurationUnit, llm_used: int
    ) -> tuple[CurationUnit, bool]:
        """Adaptive LLM gating: downgrade low-signal or out-of-budget units.

        In LLM mode a unit whose deterministic signal is below
        ``config.min_signal`` — or that exceeds the run-wide ``config.sample``
        budget — is processed deterministically instead of calling the model.
        Returns the effective unit and whether it was downgraded.
        """
        if (
            config.extraction is not CurationExtractionMode.LLM
            or unit.extraction is not CurationExtractionMode.LLM
        ):
            return unit, False
        if unit.signal < config.min_signal:
            return _downgrade_unit(unit), True
        if config.sample and llm_used >= config.sample:
            return _downgrade_unit(unit), True
        return unit, False

    def _safe_process(
        self, config: CurationConfig, unit: CurationUnit
    ) -> UnitExtraction:
        holder: dict[str, object] = {}

        def worker() -> None:
            try:
                holder["result"] = self._adapter.process(unit)
            except Exception as exc:  # noqa: BLE001 - unit isolation boundary
                holder["error"] = exc
                del exc

        thread = threading.Thread(target=worker, name="curation-unit", daemon=True)
        thread.start()
        thread.join(timeout=config.unit_timeout_seconds)
        if thread.is_alive():
            return UnitExtraction(
                candidates=(),
                messages_scanned=0,
                model_calls=0,
                failed=True,
                failure_reason="timeout",
            )
        if "error" in holder:
            return UnitExtraction(
                candidates=(),
                messages_scanned=0,
                model_calls=0,
                failed=True,
                failure_reason="extractor_error",
            )
        return cast(UnitExtraction, holder["result"])

    def _curate_candidate(
        self,
        unit: CurationUnit,
        candidate: MemoryCandidate,
        run_id: str,
        counters: RunCounters,
    ) -> None:
        """Route exactly one candidate through the policy-gated write path."""
        result = self._curator.curate(candidate)  # type: ignore[union-attr]
        counters.tally.add(result)
        if str(result.get("status", "")) == "conflict":
            # The curator handler surfaced a reconciliation conflict; it is
            # counted, never silently written, and parked in the exception
            # review queue so a human can adjudicate the contradiction.
            counters.tally.conflicts += 1
            self._enqueue_review(
                unit,
                candidate,
                run_id,
                category=REVIEW_CATEGORY_CONFLICT,
                reason=str(result.get("reason") or "ambiguous_related_fact"),
            )
            counters.review_queued += 1
            return
        decision = str(result.get("decision", ""))
        if decision == MemoryDecision.REQUIRE_APPROVAL.value:
            self._enqueue_review(
                unit,
                candidate,
                run_id,
                category=REVIEW_CATEGORY_APPROVAL,
                reason=str(result.get("reason") or "sensitive_content"),
            )
            counters.review_queued += 1
            return
        if not result.get("applied"):
            return
        counters.evidence_added += int(result.get("evidence_added", 0) or 0)
        if result.get("superseded_id"):
            counters.superseded += 1

    def _enqueue_review(
        self,
        unit: CurationUnit,
        candidate: MemoryCandidate,
        run_id: str,
        *,
        category: str,
        reason: str,
    ) -> None:
        """Park one escalated candidate in the explicit review queue.

        The review queue is the *only* content-bearing table in this module;
        evidence stays id-only. The candidate is never written here — approval
        flows through the same policy-gated write path in
        :class:`~personal_ai.memory.review.MemoryReviewService`.
        """
        self._store.enqueue_review(
            run_id=run_id,
            unit_id=unit.unit_id,
            source_type=unit.source_type,
            kind=str(candidate.kind.value),
            temporal_scope=str(candidate.temporal_scope.value),
            confidence=candidate.confidence,
            importance=candidate.utility,
            statement=candidate.statement,
            candidate_json=json.dumps(candidate.to_dict(), sort_keys=True),
            evidence_json=json.dumps(
                [ref.to_dict() for ref in candidate.evidence],
                sort_keys=True,
            ),
            created_at=now_iso(),
            category=category,
            reason=reason,
        )

    def _accumulate(self, result: UnitExtraction, counters: RunCounters) -> None:
        counters.messages_scanned += result.messages_scanned
        counters.model_calls += result.model_calls
        counters.proposals_parsed += result.proposals_parsed
        counters.proposals_dropped += result.proposals_dropped

    def _build_report(
        self,
        config: CurationConfig,
        run_id: str,
        status: str,
        counters: RunCounters,
        *,
        dry: DryRunTally | None = None,
        model_name: str | None = None,
    ) -> CurationReport:
        return CurationReport(
            run_id=run_id,
            source_type=config.source_type,
            extraction=config.extraction.value,
            dry_run=config.dry_run,
            status=status,
            versions=_versions(config.extraction, self._adapter),
            model_name=model_name or config.model_name or "",
            counters=counters,
            dry_tally=dry,
            memory_stats=MemoryStats.from_stores(self._service, self._store),
            elapsed_seconds=max(0.0, time.monotonic() - self._started),
        )


def _downgrade_unit(unit: CurationUnit) -> CurationUnit:
    """Return the unit forced into deterministic extraction."""
    return dataclasses.replace(unit, extraction=CurationExtractionMode.DETERMINISTIC)


def _default_curator(
    memory_service: MemoryService | None, policy: MemoryPolicy
) -> object | None:
    # Imported lazily to avoid a module-level import cycle through the tools
    # layer (tools.memory -> agents -> memory).
    from personal_ai.tools.memory import AutomaticMemoryCurator

    if memory_service is None:
        return None
    return AutomaticMemoryCurator(memory_service, policy)


def _normal_status(counters: RunCounters) -> str:
    """'failed' when a run finished with failed units, else 'completed'."""
    return STATUS_FAILED if counters.units_failed else STATUS_COMPLETED


def _unit_counters(result: UnitExtraction) -> dict[str, int]:
    return {
        "messages_scanned": result.messages_scanned,
        "model_calls": result.model_calls,
        "proposals_parsed": result.proposals_parsed,
        "proposals_dropped": result.proposals_dropped,
        "candidates": len(result.candidates),
    }


__all__: tuple[str, ...] = (
    "DEFAULT_MAX_CANDIDATES_PER_UNIT",
    "DEFAULT_MAX_MESSAGES",
    "DEFAULT_MAX_MODEL_CALLS",
    "DEFAULT_MAX_PROMPT_CHARS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_UNIT_TIMEOUT_SECONDS",
    "REVIEW_CATEGORY_APPROVAL",
    "REVIEW_CATEGORY_CONFLICT",
    "STATUS_COMPLETED",
    "STATUS_DRY",
    "STATUS_FAILED",
    "STATUS_REVIEW_APPROVED",
    "STATUS_REVIEW_EXPIRED",
    "STATUS_REVIEW_PENDING",
    "STATUS_REVIEW_REJECTED",
    "STATUS_RUNNING",
    "ConversationCurationAdapter",
    "CurationAdapter",
    "CurationAdapterRegistry",
    "CurationConfig",
    "CurationConfigError",
    "CurationError",
    "CurationExtractionMode",
    "CurationReport",
    "CurationResumeError",
    "CurationSourceError",
    "CurationStore",
    "CurationUnit",
    "DryRunTally",
    "MemoryCurationRunner",
    "MemoryStats",
    "RunCounters",
    "UnitExtraction",
)
