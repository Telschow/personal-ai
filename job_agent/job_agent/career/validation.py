"""Deterministic claim validation: a proposed CV line is trustworthy only to
the extent the underlying evidence supports it.

Validation is mechanical and anti-fabrication by construction:

- every metric number in a proposed line must already appear in the user's
  own evidence (numbers are never manufactured),
- the line must share substantive wording with at least one evidence line
  (every claimed fact must trace to a stored one),
- entity names (companies/organisations) in a line must appear in evidence,
- unverifiable entities (job title, awards, publications) surface as
  UNKNOWN (never silently SUPPORTED).

It returns a per-claim verdict — nothing here writes, calls a model, or
edits user claims.
"""

from __future__ import annotations

import itertools
import re

from pydantic import BaseModel, Field

from .evidence import CareerEvidence, VerificationLevel, level_index

_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{3,}")

_EMPLOYER_RE = re.compile(r"\b(?:at|@)\s+([A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+)*)")

_TITLE_AFTER_AS_RE = re.compile(
    r"\b(?:[Ww]orked as|[Aa]s an?)\s+"
    r"((?:[A-Z][A-Za-z'’-]+)(?:\s+[A-Z][A-Za-z'’-]+)*?)"
    r"(?=\s+[a-z]|[,;]|$)"
)

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_RANGE_RE = re.compile(
    r"\b((?:19|20)\d{2})\s*(?:-|–|—|to)\s*((?:19|20)\d{2}|present)\b",
    re.IGNORECASE,
)

_SIZE_UNIT_RE = re.compile(
    r"\b(\d{1,3})\s*(?:-|\+)?\s*"
    r"(?:engineers?|developers?|people|persons|employees?|specialists"
    r"|members|staff|customers?|users?|candidates?)\b",
    re.IGNORECASE,
)
_TEAM_OF_RE = re.compile(
    r"\b(?:team|squad|group|org(?:anization)?)\s+of\s+(\d{1,3})\b",
    re.IGNORECASE,
)

_SCOPE_TERMS = frozenset(
    {
        "global",
        "worldwide",
        "company-wide",
        "enterprise-wide",
        "multi-country",
        "multi-site",
        "cross-functional",
        "cross-border",
        "international",
    }
)

_TECHNOLOGIES = frozenset(
    {
        "python",
        "java",
        "javascript",
        "typescript",
        "c++",
        "rust",
        "golang",
        "kubernetes",
        "docker",
        "aws",
        "gcp",
        "azure",
        "tensorflow",
        "pytorch",
        "react",
        "kafka",
        "spark",
        "hadoop",
        "sql",
        "postgres",
        "mongo",
        "graphql",
        "terraform",
        "jenkins",
        "ci/cd",
        "git",
        "linux",
        "android",
        "ios",
        "opencv",
        "ros2",
        "microservices",
        "mlflow",
    }
)

_CREDENTIALS_RE = re.compile(
    r"\b(MSc|PhD|BSc|MBA|PMP|CFA|certified|certification|license|patent)\b",
    re.IGNORECASE,
)

_UNVERIFIED_SIGNAL_RE = re.compile(
    r"\b(award(?:ed)?|winner of|recipient of|published(?:d)?"
    r"|publication|peer-reviewed|paper(?:s)?\s+(?:in|at|presented)"
    r"|patent(?:ed)?|granted?)\b",
    re.IGNORECASE,
)

_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "for",
        "with",
        "from",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "as",
        "is",
        "was",
        "be",
        "been",
        "have",
        "has",
        "had",
        "we",
        "you",
        "they",
        "it",
        "my",
        "our",
        "your",
        "their",
        "this",
        "that",
        "these",
        "those",
        "its",
        "experience",
        "worked",
        "leading",
        "led",
        "working",
        "product",
        "driving",
        "autonomous",
    ]
)


class EntityFinding(BaseModel):
    """A deterministic, rule-based entity extraction result."""

    entity: str  # employer | job_title | dates | team_size | scope | technology | credential | award | publication
    value: str
    status: str  # "unmatched" | "unknown"


class ClaimValidation(BaseModel):
    """Verdict for a single proposed line against evidence."""

    claim: str
    problem: str | None = None
    matched_evidence_ids: list[str] = Field(default_factory=list)
    unmatched_numbers: list[str] = Field(default_factory=list)
    unmatched_entities: list[EntityFinding] = Field(default_factory=list)
    unknown_entities: list[EntityFinding] = Field(default_factory=list)


def _entity_in_evidence(finding: EntityFinding, evidence_text: str) -> bool:
    """Check whether a verifiable entity is present in evidence text.

    - dates: each year number must appear in evidence
    - team_size: number AND unit stem must appear
    - others: casefolded value must be substring of evidence
    """
    if finding.entity == "dates":
        numbers = _YEAR_RE.findall(finding.value)
        return all(n in evidence_text for n in numbers)

    if finding.entity == "team_size":
        size_match = _SIZE_UNIT_RE.search(finding.value) or _TEAM_OF_RE.search(finding.value)
        if not size_match:
            return False
        number = size_match.group(1)
        unit_words = re.findall(r"[a-z]+", finding.value.casefold())
        return number in evidence_text and any(w in evidence_text for w in unit_words if len(w) > 2)

    # employer / technology / credential / scope: substring match
    return finding.value.casefold() in evidence_text


def extract_entities(
    claim: str,
) -> tuple[list[EntityFinding], list[EntityFinding]]:
    """Deterministic, rule-based entity extraction over a proposed CV line.

    Returns (verifiable, unverified) findings.
    """
    verifiable: list[EntityFinding] = []
    unverified: list[EntityFinding] = []
    seen: set[tuple[str, str]] = set()

    # dates
    range_match = _RANGE_RE.search(claim)
    if range_match:
        start, end = range_match.group(1), range_match.group(2)
        value = f"{start}-{end}" if end != "present" else f"{start}-present"
        if ("dates", value) not in seen:
            verifiable.append(EntityFinding(entity="dates", value=value, status="unmatched"))
            seen.add(("dates", value))
    else:
        for y in _YEAR_RE.findall(claim):
            if ("dates", y) not in seen:
                verifiable.append(EntityFinding(entity="dates", value=y, status="unmatched"))
                seen.add(("dates", y))

    # team/org size
    #
    # Both patterns must be scanned. `finditer()` returns an iterator, which is
    # always truthy, so `a.finditer(c) or b.finditer(c)` short-circuits after `a`
    # and never reaches `b` -- the "team of N" spelling was silently unvalidated
    # while the matching helper below did handle it.
    #
    # The two patterns overlap ("a team of 10 engineers" matches both), so
    # overlapping spans are collapsed and the longest match wins. Reporting the
    # same claim twice would inflate the unverified-claim count.
    size_matches = sorted(
        itertools.chain(_SIZE_UNIT_RE.finditer(claim), _TEAM_OF_RE.finditer(claim)),
        key=lambda m: (m.start(), -m.end()),
    )
    consumed_to = -1
    for m in size_matches:
        if m.start() < consumed_to:
            continue
        consumed_to = m.end()
        value = m.group(0)
        if ("team_size", value) not in seen:
            verifiable.append(EntityFinding(entity="team_size", value=value, status="unmatched"))
            seen.add(("team_size", value))

    # scope
    for term in _SCOPE_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", claim, re.IGNORECASE) and ("scope", term) not in seen:
            verifiable.append(EntityFinding(entity="scope", value=term, status="unmatched"))
            seen.add(("scope", term))

    # technology
    claim_tokens = set(_TOKEN_RE.findall(claim.casefold()))
    for tech in _TECHNOLOGIES:
        tech_clean = tech.replace("/", "").replace(".", "")
        if (tech_clean in claim_tokens or tech in claim_tokens) and ("technology", tech) not in seen:
            verifiable.append(EntityFinding(entity="technology", value=tech, status="unmatched"))
            seen.add(("technology", tech))

    # credential
    for m in _CREDENTIALS_RE.finditer(claim):
        rest = claim[m.end() :].strip()
        # capture "MSc Data Science" style compound credentials
        compound = re.match(r"([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+)*)", rest)
        value = f"{m.group(1)} {compound.group(1)}".strip() if compound else m.group(1)
        if ("credential", value) not in seen:
            verifiable.append(EntityFinding(entity="credential", value=value, status="unmatched"))
            seen.add(("credential", value))

    # employer
    for m in _EMPLOYER_RE.finditer(claim):
        value = m.group(1).strip()
        if ("employer", value) not in seen:
            verifiable.append(EntityFinding(entity="employer", value=value, status="unmatched"))
            seen.add(("employer", value))

    # job title (as <Role> marker only — low false-positive heuristic)
    for m in _TITLE_AFTER_AS_RE.finditer(claim):
        value = m.group(1).strip()
        if ("job_title", value) not in seen:
            verifiable.append(EntityFinding(entity="job_title", value=value, status="unmatched"))
            seen.add(("job_title", value))

    # unverified signals (award / publication — no extractor yet)
    for m in _UNVERIFIED_SIGNAL_RE.finditer(claim):
        term = m.group(1).lower()
        entity = "award" if "award" in term or "winner" in term or "recipient" in term else "publication"
        if (entity, term) not in seen:
            unverified.append(EntityFinding(entity=entity, value=term, status="unknown"))
            seen.add((entity, term))

    return verifiable, unverified


def validate_claim(
    claim: str,
    evidence: list[CareerEvidence],
    *,
    min_evidence_level: VerificationLevel = VerificationLevel.DOCUMENTED,
) -> ClaimValidation:
    """Cross-check one proposed line against the evidence store.

    ``problem`` is None (supported) or one of ``numeric_unmatched``,
    ``no_evidence``, ``entity_unmatched``, ``entity_unknown``.
    ``matched_evidence_ids`` always references stored evidence.
    """
    numbers = _NUMBER_RE.findall(claim)
    all_text = " ".join(e.claim for e in evidence).casefold()

    if numbers:
        unmatched = [n for n in numbers if n not in all_text]
        if unmatched:
            verifiable, unverified = extract_entities(claim)
            return ClaimValidation(
                claim=claim,
                problem="numeric_unmatched",
                matched_evidence_ids=[],
                unmatched_numbers=unmatched,
                unmatched_entities=verifiable,
                unknown_entities=unverified,
            )

    acceptable = [e for e in evidence if level_index(e.level) >= level_index(min_evidence_level)]
    tokens = {t for t in _TOKEN_RE.findall(claim.casefold())}
    sig = {t for t in tokens if t not in _STOPWORDS}

    matched: list[CareerEvidence] = []
    for e in acceptable:
        ev_tokens = set(_TOKEN_RE.findall(e.claim.casefold()))
        overlap = sig & ev_tokens
        if len(overlap) >= 2 or (len(overlap) == 1 and numbers and bool(_NUMBER_RE.search(e.claim))):
            matched.append(e)

    if not matched:
        verifiable, unverified = extract_entities(claim)
        return ClaimValidation(
            claim=claim,
            problem="no_evidence",
            matched_evidence_ids=[],
            unmatched_numbers=[],
            unmatched_entities=verifiable,
            unknown_entities=unverified,
        )

    # entity-level verification over matched evidence only
    verifiable, unverified = extract_entities(claim)
    evidence_text = " ".join(e.claim for e in matched).casefold()
    unmatched_entities = [f for f in verifiable if not _entity_in_evidence(f, evidence_text)]

    problem: str | None = None
    if unmatched_entities:
        problem = "entity_unmatched"
    elif unverified:
        problem = "entity_unknown"

    return ClaimValidation(
        claim=claim,
        problem=problem,
        matched_evidence_ids=[e.evidence_id for e in matched[:5]],
        unmatched_entities=unmatched_entities,
        unknown_entities=unverified,
    )


__all__ = [
    "EntityFinding",
    "ClaimValidation",
    "validate_claim",
    "validate_artifact_claims",
    "extract_entities",
]


def validate_artifact_claims(
    claims: list[str],
    evidence: list[CareerEvidence],
) -> list[ClaimValidation]:
    """Validate a whole artifact; one verdict per proposed line."""
    return [validate_claim(c, evidence) for c in claims]
