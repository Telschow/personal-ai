"""People and identity layer: deterministic person extraction and storage."""

from personal_ai.people.canonicalize import (
    GIVEN_NAME_ALIASES,
    canonical_identity,
    canonical_parts,
)
from personal_ai.people.chat import (
    ChatPeople,
    PeopleChatResult,
    PeopleContext,
    render_untrusted_people_context,
)
from personal_ai.people.extract import (
    EMAIL_ROLE,
    FINANCIAL_ROLE,
    extract_person_references,
)
from personal_ai.people.indexer import PersonIndexer
from personal_ai.people.models import (
    PeopleFoldReport,
    Person,
    PersonEvidence,
    PersonIndexReport,
    PersonReference,
    normalize_identity,
    person_id_for,
)
from personal_ai.people.store import PersonStore

__all__ = [
    "EMAIL_ROLE",
    "FINANCIAL_ROLE",
    "GIVEN_NAME_ALIASES",
    "ChatPeople",
    "PeopleChatResult",
    "PeopleContext",
    "PeopleFoldReport",
    "Person",
    "PersonEvidence",
    "PersonIndexReport",
    "PersonIndexer",
    "PersonReference",
    "PersonStore",
    "canonical_identity",
    "canonical_parts",
    "extract_person_references",
    "normalize_identity",
    "person_id_for",
    "render_untrusted_people_context",
]
