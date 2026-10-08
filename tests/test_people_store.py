"""Tests for the SQLite-backed people store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from personal_ai.people.models import (
    PersonReference,
    normalize_identity,
    person_id_for,
)
from personal_ai.people.store import PersonStore
from personal_ai.storage.documents import connect_database


def make_reference(
    *,
    name: str,
    email: str = "",
    role: str = "email",
    source_type: str = "email",
    document_id: str = "doc-1",
    seen_at: str = "2026-01-01T00:00:00+00:00",
) -> PersonReference:
    return PersonReference(
        document_id=document_id,
        name=name,
        email=email,
        role=role,
        source_type=source_type,
        seen_at=seen_at,
    )


@pytest.fixture
def store(tmp_path: Path) -> PersonStore:
    connection = connect_database(tmp_path / "people.sqlite")
    s = PersonStore(connection)
    try:
        yield s
    finally:
        s.close()


def test_upsert_and_get_roundtrip(store: PersonStore) -> None:
    reference = make_reference(name="Alice Anders", email="alice@example.com")
    person = store.upsert_reference(reference)
    assert person.identity == normalize_identity("Alice Anders")
    assert person.person_id == person_id_for(person.identity)
    assert person.display_name == "Alice Anders"
    assert person.emails == ("alice@example.com",)
    assert person.roles == ("email",)
    assert person.sources == ("email",)
    assert person.first_seen_at == reference.seen_at
    assert person.last_seen_at == reference.seen_at
    assert person.evidence_count == 1
    assert store.get(person.person_id) == person


def test_get_unknown_returns_none(store: PersonStore) -> None:
    assert store.get("nope") is None


def test_identity_is_casefold_whitespace_normalized(store: PersonStore) -> None:
    first = store.upsert_reference(make_reference(name="Alice  ANDERS"))
    second = store.upsert_reference(
        make_reference(name="alice anders", document_id="doc-2")
    )
    assert first.person_id == second.person_id


def test_accents_preserved_distinct_identities(store: PersonStore) -> None:
    a = store.upsert_reference(make_reference(name="Müller", document_id="doc-1"))
    b = store.upsert_reference(make_reference(name="Muller", document_id="doc-2"))
    assert a.person_id != b.person_id


def test_display_name_prefers_longest_then_lexicographic(store: PersonStore) -> None:
    store.upsert_reference(make_reference(name="Bob", document_id="doc-1"))
    store.upsert_reference(make_reference(name="Bob Berger", document_id="doc-2"))
    person = store.get(person_id_for(normalize_identity("Bob Berger")))
    assert person is not None
    assert person.display_name == "Bob Berger"

    store.upsert_reference(make_reference(name="Zack", document_id="doc-3"))
    store.upsert_reference(make_reference(name="Zack", document_id="doc-4"))
    store.upsert_reference(make_reference(name="Adam", document_id="doc-5"))
    tiebreak = store.get(person_id_for(normalize_identity("Zack")))
    assert tiebreak is not None and tiebreak.display_name == "Zack"


def test_aggregates_are_sorted_and_deduped(store: PersonStore) -> None:
    store.upsert_reference(
        make_reference(
            name="Alice Anders",
            email="alice@example.com",
            role="email",
            document_id="doc-1",
        )
    )
    store.upsert_reference(
        make_reference(
            name="Alice Anders",
            email="alice@work.com",
            role="email",
            source_type="email",
            document_id="doc-2",
        )
    )
    store.upsert_reference(
        make_reference(
            name="Alice Anders",
            email="",
            role="financial",
            source_type="financial",
            document_id="doc-3",
        )
    )
    person = store.get(person_id_for(normalize_identity("Alice Anders")))
    assert person is not None
    assert person.emails == ("alice@example.com", "alice@work.com")
    assert person.roles == ("email", "financial")
    assert person.sources == ("email", "financial")
    assert person.evidence_count == 3


def test_first_and_last_seen_over_multiple_references(store: PersonStore) -> None:
    store.upsert_reference(
        make_reference(
            name="Bob", seen_at="2026-03-01T00:00:00+00:00", document_id="doc-1"
        )
    )
    store.upsert_reference(
        make_reference(
            name="Bob", seen_at="2026-01-01T00:00:00+00:00", document_id="doc-2"
        )
    )
    store.upsert_reference(
        make_reference(
            name="Bob", seen_at="2026-05-01T00:00:00+00:00", document_id="doc-3"
        )
    )
    person = store.get(person_id_for(normalize_identity("Bob")))
    assert person is not None
    assert person.first_seen_at == "2026-01-01T00:00:00+00:00"
    assert person.last_seen_at == "2026-05-01T00:00:00+00:00"


def test_evidence_dedup_is_idempotent(store: PersonStore) -> None:
    reference = make_reference(name="Bob Berger", document_id="doc-same")
    store.upsert_reference(reference)
    store.upsert_reference(reference)
    person = store.get(person_id_for(normalize_identity("Bob Berger")))
    assert person is not None
    assert person.evidence_count == 1
    assert len(store.evidence_for(person.person_id)) == 1


def test_search_substring_case_insensitive(store: PersonStore) -> None:
    store.upsert_reference(make_reference(name="Alice Anders", document_id="doc-1"))
    store.upsert_reference(make_reference(name="Bob Berger", document_id="doc-2"))
    hits = store.search("alice")
    assert [p.display_name for p in hits] == ["Alice Anders"]
    hits = store.search("berg")
    assert [p.display_name for p in hits] == ["Bob Berger"]
    hits = store.search("alicee")
    assert hits == []


def test_search_matches_email_alias(store: PersonStore) -> None:
    store.upsert_reference(
        make_reference(name="Alice", email="alice@example.com", document_id="doc-1")
    )
    hits = store.search("alice@example.com")
    assert [p.display_name for p in hits] == ["Alice"]


def test_search_wildcards_are_literal(store: PersonStore) -> None:
    store.upsert_reference(make_reference(name="Bob_100", document_id="doc-1"))
    store.upsert_reference(make_reference(name="BobX100", document_id="doc-2"))
    assert {p.display_name for p in store.search("bob_100")} == {"Bob_100"}
    assert {p.display_name for p in store.search("bob%100")} == set()


def test_search_orders_by_evidence_count_then_name_then_id(store: PersonStore) -> None:
    for name, count in (("Bob Berger", 3), ("Alice", 5), ("Zoe", 3)):
        for index in range(count):
            store.upsert_reference(
                make_reference(name=name, document_id=f"doc-{name}-{index}")
            )
    hits = store.search("")
    # Alice (5) first; Bob Berger then Zoe (both 3) by display name.
    assert [p.display_name for p in hits] == ["Alice", "Bob Berger", "Zoe"]
    assert hits[0].evidence_count == 5


def test_search_respects_role_filter(store: PersonStore) -> None:
    store.upsert_reference(make_reference(name="Bob", document_id="doc-1"))
    store.upsert_reference(
        make_reference(
            name="Bob", role="financial", source_type="financial", document_id="doc-2"
        )
    )
    email_only = store.search("bob", role="email")
    assert len(email_only) == 1
    assert email_only[0].roles == ("email", "financial")


def test_search_limit(store: PersonStore) -> None:
    for index in range(5):
        store.upsert_reference(
            make_reference(name=f"Name {index}", document_id=f"doc-{index}")
        )
    assert len(store.search("name", limit=2)) == 2


def test_list_deterministic_paging(store: PersonStore) -> None:
    for index in reversed(range(5)):
        store.upsert_reference(
            make_reference(name=f"Name {index}", document_id=f"doc-{index}")
        )
    first = store.list(limit=3)
    second = store.list(limit=3, offset=3)
    assert len(first) == 3
    assert len(second) == 2
    assert [p.display_name for p in first + second] == sorted(
        [p.display_name for p in first + second]
    )
    page_a = store.list(limit=3)
    page_b = store.list(limit=3, offset=3)
    assert [p.person_id for p in page_a + page_b] == [
        p.person_id for p in store.list(limit=10)
    ]


def test_count(store: PersonStore) -> None:
    assert store.count() == 0
    store.upsert_reference(make_reference(name="Alice", document_id="doc-1"))
    store.upsert_reference(make_reference(name="Bob", document_id="doc-2"))
    assert store.count() == 2


def test_evidence_for_is_newest_first_and_bounded(store: PersonStore) -> None:
    store.upsert_reference(
        make_reference(
            name="Bob", seen_at="2026-01-01T00:00:00+00:00", document_id="doc-1"
        )
    )
    store.upsert_reference(
        make_reference(
            name="Bob", seen_at="2026-03-01T00:00:00+00:00", document_id="doc-2"
        )
    )
    store.upsert_reference(
        make_reference(
            name="Bob", seen_at="2026-02-01T00:00:00+00:00", document_id="doc-3"
        )
    )
    person_id = person_id_for(normalize_identity("Bob"))
    rows = store.evidence_for(person_id)
    assert [row.document_id for row in rows] == ["doc-2", "doc-3", "doc-1"]
    assert len(store.evidence_for(person_id, limit=2)) == 2
    assert len(store.evidence_for(person_id, offset=100)) == 0


def test_person_persists_across_connections(tmp_path: Path) -> None:
    db = tmp_path / "persist.sqlite"
    with PersonStore(connect_database(db)) as store:
        store.upsert_reference(make_reference(name="Alice", document_id="doc-1"))
    with PersonStore(connect_database(db)) as store:
        assert store.count() == 1
        person = store.get(person_id_for(normalize_identity("Alice")))
        assert person is not None
        assert person.display_name == "Alice"


def test_store_close_releases_connection(tmp_path: Path) -> None:
    store = PersonStore(connect_database(tmp_path / "close.sqlite"))
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        store.count()


def test_person_aggregate_fields_are_tuple_of_str(store: PersonStore) -> None:
    store.upsert_reference(
        make_reference(name="Alice", email="a@example.com", document_id="doc-1")
    )
    person = store.get(person_id_for(normalize_identity("Alice")))
    assert person is not None
    assert isinstance(person.emails, tuple)
    assert all(isinstance(item, str) for item in person.emails)
    assert isinstance(person.roles, tuple)
    assert isinstance(person.sources, tuple)
