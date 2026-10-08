"""Typed models for the people / identity layer.

The identity layer is a deterministic, name-anchored view of the people who
appear in the user's personal corpus. Identity is deliberately conservative:
a person is keyed by the canonical identity of their display name, so only
name-anchored variants merge. The deterministic folding rules live in
``people/canonicalize.py``, so ``Alice Example`` / ``Dani Example`` /
``Example, Alice`` fuse while distinct people stay separate. Email
addresses are stored as aliases and source-derived roles (``email`` /
``financial``) are kept per reference, but identity is NEVER fused by email
alone and merchants/opaque usernames never fold into people. Everything here
is plain data; no model call is ever involved.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

_WS_COLLAPSE = re.compile(r"\s+")


def normalize_identity(name: str) -> str:
    """Normalize a name into a case-insensitive identity key.

    Applies NFKC (so composed/decomposed and exotic-width spellings unify),
    casefolding, and whitespace collapsing. Accents are preserved (``Müller``
    stays distinct from ``Muller``), mirroring the Unicode tokenization
    guarantees used elsewhere in the system.
    """
    folded = unicodedata.normalize("NFKC", name).casefold()
    return _WS_COLLAPSE.sub(" ", folded).strip()


def person_id_for(identity: str) -> str:
    """Derive the stable person id from a normalized identity key."""
    material = f"people\x00{identity}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PersonReference:
    """A name-anchored person reference extracted from one source record.

    ``document_id`` is the stable content-derived document identity (the same
    value the document store uses), so evidence deduplicates across re-runs.
    ``name`` is the original observed display-name form (never normalized);
    ``email`` is the lowercased address alias or ``""``.
    """

    document_id: str
    name: str
    email: str
    role: str
    source_type: str
    seen_at: str


@dataclass(frozen=True, slots=True)
class PersonEvidence:
    """One bounded provenance row proving where a person was referenced."""

    person_id: str
    document_id: str
    name: str
    email: str
    role: str
    source_type: str
    seen_at: str


@dataclass(frozen=True, slots=True)
class Person:
    """A canonical derived person identity with content-free aggregates."""

    person_id: str
    identity: str
    display_name: str
    emails: tuple[str, ...]
    roles: tuple[str, ...]
    sources: tuple[str, ...]
    first_seen_at: str
    last_seen_at: str
    evidence_count: int


@dataclass(frozen=True, slots=True)
class PersonIndexReport:
    """Aggregate-only outcome of one indexing pass (never content-bearing)."""

    source_type: str
    records: int
    references: int
    people_before: int
    people_after: int
    new_people: int

    def summary(self) -> dict[str, int]:
        """Return the count-only, JSON-serializable report shape."""
        return {
            "records": self.records,
            "references": self.references,
            "people_before": self.people_before,
            "people_after": self.people_after,
            "new_people": self.new_people,
        }


@dataclass(frozen=True, slots=True)
class PeopleFoldReport:
    """Aggregate-only outcome of one identity-canonicalization pass.

    ``people_rekeyed`` counts stored identities whose canonical key differs
    from their stored key; ``merged_groups`` counts canonical identities that
    absorb evidence from two or more stored identities. Neither field ever
    names a person.
    """

    people_before: int
    people_after: int
    people_rekeyed: int
    merged_groups: int
    evidence_before: int
    evidence_after: int
    applied: bool

    def summary(self) -> dict[str, object]:
        """Return the count-only, JSON-serializable report shape."""
        return {
            "people_before": self.people_before,
            "people_after": self.people_after,
            "people_rekeyed": self.people_rekeyed,
            "merged_groups": self.merged_groups,
            "evidence_before": self.evidence_before,
            "evidence_after": self.evidence_after,
            "applied": self.applied,
        }
