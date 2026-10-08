"""Deterministic person-reference extraction from normalized source records.

Extraction is name-anchored and purely mechanical: only references that carry
an explicit display name are emitted, addresses without a name are dropped,
and addresses that are clearly not person mailboxes (``MAILER-DAEMON``,
``no-reply``/``bounce`` style addresses) are skipped via a small fixed
blocklist. No model call, no guessing, no lucene-style name fusion.

Supported sources:

- ``email`` — From / To / Cc are parsed with ``email.utils.getaddresses`` from
  the record metadata. Every named mailbox becomes one ``PersonReference``
  with role ``email`` and the lowercased address as an alias.
- ``financial`` — the canonical payload text is scanned for the labelled
  ``payer:`` / ``payee:`` / ``counterparty_name:`` lines produced by the
  financial schema. Every non-empty labelled value becomes one reference with
  role ``financial`` (merchant-style single tokens are kept verbatim; whether
  a counterparty is a person rather than an organization is deliberately left
  to later, heuristic work).
- Every other source type yields zero references.

References within a single record are deduplicated by ``(name, email)`` so a
person copied on many addresses of one message contributes a single reference.
"""

from __future__ import annotations

import re
import unicodedata
from email.utils import getaddresses

from personal_ai.documents.models import compute_document_id
from personal_ai.people.models import PersonReference
from personal_ai.sources.models import SourceRecord

EMAIL_ROLE = "email"
FINANCIAL_ROLE = "financial"

_EMAIL_FIELDS = ("sender", "to", "cc")

_FINANCIAL_LINE_RE = re.compile(
    r"^(?P<label>payer|payee|counterparty_name): (?P<value>.+)$"
)

_NON_PERSON_LOCAL_PART = frozenset(
    {
        "bounce",
        "donotreply",
        "do-not-reply",
        "mailer-daemon",
        "no-reply",
        "noreply",
        "notifications",
        "postmaster",
        "root",
    }
)


def extract_person_references(record: SourceRecord) -> tuple[PersonReference, ...]:
    """Extract bounded person references from one source record.

    Returns an empty tuple for every unsupported source type. The document
    id is derived through the same stable helpers the document pipeline uses,
    so evidence deduplicates across repeated indexing of an unchanged source.
    """
    document_id = compute_document_id(
        record.source_type, record.source_key, record.content_hash
    )
    if record.source_type == "email":
        return _extract_email_references(record, document_id)
    if record.source_type == "financial":
        return _extract_financial_references(record, document_id)
    return ()


def _extract_email_references(
    record: SourceRecord, document_id: str
) -> tuple[PersonReference, ...]:
    references: list[PersonReference] = []
    seen: set[tuple[str, str]] = set()
    for field in _EMAIL_FIELDS:
        header = record.metadata.get(field)
        if not isinstance(header, str) or not header.strip():
            continue
        for display_name, address in getaddresses([header]):
            name = _clean_name(display_name)
            email = _clean_email(address)
            if not name:
                continue
            if email and _is_non_person_address(email):
                continue
            key = (name, email)
            if key in seen:
                continue
            seen.add(key)
            references.append(
                PersonReference(
                    document_id=document_id,
                    name=name,
                    email=email,
                    role=EMAIL_ROLE,
                    source_type=record.source_type,
                    seen_at=record.created_at,
                )
            )
    return tuple(references)


def _extract_financial_references(
    record: SourceRecord, document_id: str
) -> tuple[PersonReference, ...]:
    if record.payload is None:
        return ()
    text = record.payload.decode("utf-8", errors="replace")
    references: list[PersonReference] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = _FINANCIAL_LINE_RE.match(line)
        if match is None:
            continue
        name = _clean_name(match.group("value"))
        if not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        references.append(
            PersonReference(
                document_id=document_id,
                name=name,
                email="",
                role=FINANCIAL_ROLE,
                source_type=record.source_type,
                seen_at=record.created_at,
            )
        )
    return tuple(references)


def _clean_name(raw: str) -> str:
    """Return a usable display name, or ``""`` when the slot is not a name."""
    name = raw.strip().strip('"').strip()
    if not name:
        return ""
    if "@" in name or ":" in name:
        return ""
    return " ".join(name.split())


def _clean_email(raw: str) -> str:
    return unicodedata.normalize("NFKC", raw).strip().lower()


def _is_non_person_address(email: str) -> bool:
    """True for addresses clearly not belonging to a person mailbox.

    Only the local part is inspected against the fixed blocklist; unknown
    local parts are kept (a person could mail from any address).
    """
    local = email.split("@", 1)[0].lower()
    if local in _NON_PERSON_LOCAL_PART:
        return True
    return local.startswith(("mailer-daemon", "bounce-"))
