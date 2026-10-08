"""Tests for conservative name-anchored identity canonicalization (Phase 50A).

Deterministic, hermetic: pure name math plus a tmp-directory SQLite store.
No Ollama, no network, no production data.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from personal_ai.people.canonicalize import (
    GIVEN_NAME_ALIASES,
    canonical_identity,
    canonical_parts,
)
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
    document_id: str = "doc-1",
    seen_at: str = "2026-01-01T00:00:00+00:00",
) -> PersonReference:
    return PersonReference(
        document_id=document_id,
        name=name,
        email=email,
        role="email",
        source_type="email",
        seen_at=seen_at,
    )


@pytest.fixture
def store(tmp_path: Path) -> PersonStore:
    connection = connect_database(tmp_path / "canonical.sqlite")
    s = PersonStore(connection)
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------- pure rules


class TestCanonicalParts:
    def test_clean_name_is_unchanged(self) -> None:
        assert canonical_parts("Alice Anders") == ("alice", frozenset({"anders"}))

    def test_nickname_aliases_map_to_alice(self) -> None:
        assert GIVEN_NAME_ALIASES == {
            "alice": "alice",
            "ali": "alice",
            "al": "alice",
        }
        assert canonical_parts("Alice Example") == (
            "alice",
            frozenset({"example"}),
        )
        assert canonical_parts("Ali Example") == ("alice", frozenset({"example"}))
        assert canonical_parts("Al Example") == ("alice", frozenset({"example"}))

    def test_alias_token_is_hoisted_to_front(self) -> None:
        assert canonical_parts("Example Arjona Alice") == (
            "alice",
            frozenset({"example", "arjona"}),
        )
        assert canonical_parts("Example, Alice") == (
            "alice",
            frozenset({"example"}),
        )

    def test_quote_artifacts_stripped(self) -> None:
        assert canonical_parts("'Alice Example'") == (
            "alice",
            frozenset({"example"}),
        )
        assert canonical_parts('"Alice Example"') == (
            "alice",
            frozenset({"example"}),
        )

    def test_leading_n_tilde_artifact_stripped(self) -> None:
        assert canonical_parts("ñAlice Example") == (
            "alice",
            frozenset({"example"}),
        )
        assert canonical_parts("'ñAlice Example'") == (
            "alice",
            frozenset({"example"}),
        )

    def test_salutation_prefixes_stripped(self) -> None:
        assert canonical_parts("Guten Tag Alice Example") == (
            "alice",
            frozenset({"example"}),
        )
        assert canonical_parts("Herr Example Arjona") == (
            "example",
            frozenset({"arjona"}),
        )

    def test_parenthetical_annotations_stripped(self) -> None:
        assert canonical_parts("Example Arjona Alice (über TUM)") == (
            "alice",
            frozenset({"example", "arjona"}),
        )
        assert canonical_parts("WG Grau Suárez Example (via Google Drive)") == (
            "wg",
            frozenset({"grau", "suárez", "example"}),
        )
        assert canonical_parts("WG Grau Suárez Example") == (
            "wg",
            frozenset({"grau", "suárez", "example"}),
        )

    def test_plus_joins_to_space(self) -> None:
        assert canonical_parts("Alice Example+Arjona") == (
            "alice",
            frozenset({"example", "arjona"}),
        )

    def test_room_code_annotation_stripped(self) -> None:
        assert canonical_parts("Example Alice, EF-703") == (
            "alice",
            frozenset({"example"}),
        )

    def test_dot_username_forms(self) -> None:
        assert canonical_parts("alice.example") == (
            "alice",
            frozenset({"example"}),
        )
        assert canonical_parts("ali.example") == ("alice", frozenset({"example"}))
        assert canonical_parts("example.alice") == (
            "alice",
            frozenset({"example"}),
        )
        # non-alias local part is opaque, never folded by structure alone
        assert canonical_parts("dirk.example") is None
        assert canonical_parts("d.example") is None

    def test_digit_only_suffix_is_opaque(self) -> None:
        assert canonical_parts("example113") is None
        assert canonical_parts("reddit.com (example)") is None

    def test_family_names_with_aliases_fold(self) -> None:
        # Nicolas and Dirks fold among themselves by exact given names; they
        # never fold into Alice because given differs.
        assert canonical_parts("Nicolas Example") == (
            "nicolas",
            frozenset({"example"}),
        )
        assert canonical_parts("Nicolas Example Arjona") == (
            "nicolas",
            frozenset({"example", "arjona"}),
        )
        assert canonical_parts("Dirk Example") == (
            "dirk",
            frozenset({"example"}),
        )
        assert canonical_parts("'Dirk Example'") == ("dirk", frozenset({"example"}))

    def test_accents_preserved_not_transliterated(self) -> None:
        assert canonical_parts("Müller Example") == (
            "müller",
            frozenset({"example"}),
        )
        assert canonical_parts("Muller Example") == (
            "muller",
            frozenset({"example"}),
        )
        assert canonical_parts("Müller Example") != canonical_parts("Muller Example")

    def test_single_token_has_empty_surname_set(self) -> None:
        assert canonical_parts("Example") == ("example", frozenset())
        assert canonical_parts("Alice") == ("alice", frozenset())

    def test_empty_or_junk_name_is_none(self) -> None:
        assert canonical_parts("") is None
        assert canonical_parts("   ") is None


class TestCanonicalIdentity:
    def test_clean_name_identity_is_unchanged(self) -> None:
        assert canonical_identity("Alice Anders") == normalize_identity("Alice Anders")

    def test_nickname_variants_share_identity(self) -> None:
        identities = {
            canonical_identity(name)
            for name in ("Alice Example", "Ali Example", "Al Example")
        }
        assert identities == {normalize_identity("Alice Example")}

    def test_arjona_surname_variants_share_identity(self) -> None:
        assert canonical_identity("Alice Example") == canonical_identity(
            "ALICE EXAMPLE ARJONA"
        )
        assert canonical_identity("Alice Example") == canonical_identity(
            "Alice Example+Arjona"
        )
        assert canonical_identity("Alice Example") == canonical_identity(
            "Example Arjona Alice (über TUM)"
        )
        assert canonical_identity("Alice Example") == canonical_identity(
            "Guten Tag Alice Example"
        )

    def test_opaque_names_keep_normalized_identity(self) -> None:
        for name in ("example113", "d.example", "reddit.com (example)"):
            assert canonical_identity(name) == normalize_identity(name)

    def test_distinct_family_members_stay_distinct(self) -> None:
        assert canonical_identity("Alice Example") != canonical_identity("Dirk Example")
        assert canonical_identity("Alice Example") != canonical_identity(
            "Nicolas Example"
        )
        assert canonical_identity("Alice Example") != canonical_identity(
            "WG Grau Suárez Example"
        )

    def test_identity_is_stable_across_repeated_calls(self) -> None:
        name = "Ali Example"
        assert canonical_identity(canonical_identity(name)) == canonical_identity(name)

    def test_person_id_derives_from_canonical_identity(self) -> None:
        assert person_id_for(canonical_identity("Ali Example")) == person_id_for(
            canonical_identity("Alice Example")
        )


# ------------------------------------------------------------ store behavior


class TestStoreCanonicalIdentity:
    def test_upsert_folds_nickname_variants_into_one_person(
        self, store: PersonStore
    ) -> None:
        store.upsert_reference(make_reference(name="Alice Example", document_id="d1"))
        store.upsert_reference(make_reference(name="Ali Example", document_id="d2"))
        store.upsert_reference(
            make_reference(name="ALICE EXAMPLE ARJONA", document_id="d3")
        )
        assert store.count() == 1
        person = store.get(person_id_for(canonical_identity("Alice Example")))
        assert person is not None
        assert person.evidence_count == 3
        assert person.display_name == "ALICE EXAMPLE ARJONA"

    def test_upsert_keeps_distinct_family_members(self, store: PersonStore) -> None:
        store.upsert_reference(make_reference(name="Alice Example", document_id="d1"))
        store.upsert_reference(make_reference(name="Dirk Example", document_id="d2"))
        store.upsert_reference(make_reference(name="Nicolas Example", document_id="d3"))
        assert store.count() == 3

    def test_existing_unfragmented_names_unchanged(self, store: PersonStore) -> None:
        person = store.upsert_reference(
            make_reference(name="Alice Anders", document_id="d1")
        )
        assert person.identity == normalize_identity("Alice Anders")
        assert person.person_id == person_id_for(normalize_identity("Alice Anders"))


def _seed_legacy(store: PersonStore, names: list[tuple[str, str]]) -> None:
    """Insert rows the way the pre-50A store would have (normalize identity).

    ``canonicalize()`` exists to re-key exactly such legacy rows, so these
    tests seed them directly instead of via ``upsert_reference`` (which now
    already folds through ``canonical_identity``).
    """
    connection = store._connection
    for document_id, name in names:
        identity = normalize_identity(name)
        pid = person_id_for(identity)
        connection.execute(
            "INSERT INTO people (person_id, identity, display_name, emails_json, "
            "roles_json, sources_json, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, identity, name, "[]", "[]", "[]", "", ""),
        )
        connection.execute(
            "INSERT INTO people_evidence (person_id, document_id, name, email, "
            "role, source_type, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, document_id, name, "", "email", "email", ""),
        )
    connection.commit()


class TestStoreCanonicalize:
    def test_updates_store_identity_via_upsert(self, store: PersonStore) -> None:
        store.upsert_reference(make_reference(name="Ali Example", document_id="d1"))
        person = store.get(person_id_for(canonical_identity("Alice Example")))
        assert person is not None
        assert person.identity == canonical_identity("Alice Example")

    def test_canonicalize_dry_run_writes_nothing(self, store: PersonStore) -> None:
        _seed_legacy(
            store,
            [
                ("d0", "Alice Example"),
                ("d1", "Ali Example"),
                ("d2", "Example, Alice"),
                ("d3", "Dirk Example"),
            ],
        )
        report = store.canonicalize()
        assert not report.applied
        assert report.people_before == 4
        assert report.people_after == 2
        assert report.people_rekeyed == 2
        assert report.merged_groups == 1
        assert report.evidence_before == 4
        assert report.evidence_after == 4
        assert store.count() == 4

    def test_canonicalize_apply_folds_people(self, store: PersonStore) -> None:
        _seed_legacy(
            store,
            [
                ("d0", "Alice Example"),
                ("d1", "Ali Example"),
                ("d2", "Example, Alice"),
                ("d3", "Dirk Example"),
            ],
        )
        report = store.canonicalize(apply=True)
        assert report.applied
        assert store.count() == 2
        alice = store.get(person_id_for(canonical_identity("Alice Example")))
        assert alice is not None
        assert alice.evidence_count == 3
        # longest observed original form wins (16 chars), including artifacts.
        assert alice.display_name == "Example, Alice"
        dirk = store.get(person_id_for(canonical_identity("Dirk Example")))
        assert dirk is not None and dirk.evidence_count == 1

    def test_canonicalize_apply_is_idempotent(self, store: PersonStore) -> None:
        _seed_legacy(
            store,
            [
                ("d0", "Alice Example"),
                ("d1", "Ali Example"),
            ],
        )
        store.canonicalize(apply=True)
        again = store.canonicalize(apply=True)
        assert store.count() == 1
        assert again.people_after == 1
        assert again.people_rekeyed == 0
        assert again.merged_groups == 0
        alice = store.get(person_id_for(canonical_identity("Alice Example")))
        assert alice is not None and alice.evidence_count == 2

    def test_canonicalize_empty_store(self, store: PersonStore) -> None:
        report = store.canonicalize(apply=True)
        assert report.people_before == 0
        assert report.people_after == 0
        assert report.evidence_before == 0
        assert store.count() == 0


class TestStoreCanonicalizeCollection:
    """The 35 production fragment rows collapse to a small safe set."""

    FRAGMENTS: ClassVar[list[str]] = [
        "'Alice Example'",
        "'Dirk Example'",
        "'Maike Example'",
        "'Nicolas Example'",
        "'Nicolas Example Arjona'",
        "'dirk.example'",
        "'ñAlice Example'",
        "ALICE EXAMPLE ARJONA",
        "Al Example",
        "Ali Example",
        "Alice Example",
        "Alice Example+Arjona",
        "Alice, Example",
        "Dirk Example",
        "Guten Tag Alice Example",
        "Herr Example Arjona",
        "Maike Example",
        "Nicolas Example",
        "Nicolas Example Arjona",
        "Example",
        "Example Arjona",
        "Example Arjona Alice",
        "Example Arjona Alice (über TUM)",
        "Example Arjona, Alice",
        "Example Alice, EF-703",
        "Example, Alice",
        "WG Grau Suárez Example",
        "WG Grau Suárez Example (via Google Drive)",
        "d.example",
        "ali.example",
        "alice.example",
        "reddit.com (example)",
        "example.alice",
        "example113",
    ]

    @pytest.fixture
    def fragment_store(self, store: PersonStore) -> PersonStore:
        for index, name in enumerate(self.FRAGMENTS):
            store.upsert_reference(make_reference(name=name, document_id=f"f{index}"))
        return store

    def test_fragments_fold_to_bounded_identities(
        self, fragment_store: PersonStore
    ) -> None:
        canonical = {canonical_identity(name) for name in self.FRAGMENTS}
        assert len(canonical) < len(self.FRAGMENTS)
        alice_forms = {
            "Alice Example",
            "Ali Example",
            "Al Example",
            "ALICE EXAMPLE ARJONA",
            "Alice Example+Arjona",
            "Alice, Example",
            "'Alice Example'",
            "'ñAlice Example'",
            "ñAlice Example",
            "Guten Tag Alice Example",
            "Example Arjona Alice",
            "Example Arjona Alice (über TUM)",
            "Example Arjona, Alice",
            "Example Alice, EF-703",
            "Example, Alice",
            "ali.example",
            "alice.example",
            "example.alice",
        }
        alice_ids = {canonical_identity(n) for n in alice_forms}
        assert len(alice_ids) == 1
