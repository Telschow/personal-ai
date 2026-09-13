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

    def test_nickname_aliases_map_to_daniel(self) -> None:
        assert GIVEN_NAME_ALIASES == {
            "daniel": "daniel",
            "dani": "daniel",
            "dan": "daniel",
        }
        assert canonical_parts("Daniel Telschow") == (
            "daniel",
            frozenset({"telschow"}),
        )
        assert canonical_parts("Dani Telschow") == ("daniel", frozenset({"telschow"}))
        assert canonical_parts("Dan Telschow") == ("daniel", frozenset({"telschow"}))

    def test_alias_token_is_hoisted_to_front(self) -> None:
        assert canonical_parts("Telschow Arjona Daniel") == (
            "daniel",
            frozenset({"telschow", "arjona"}),
        )
        assert canonical_parts("Telschow, Daniel") == (
            "daniel",
            frozenset({"telschow"}),
        )

    def test_quote_artifacts_stripped(self) -> None:
        assert canonical_parts("'Daniel Telschow'") == (
            "daniel",
            frozenset({"telschow"}),
        )
        assert canonical_parts('"Daniel Telschow"') == (
            "daniel",
            frozenset({"telschow"}),
        )

    def test_leading_n_tilde_artifact_stripped(self) -> None:
        assert canonical_parts("ñDaniel Telschow") == (
            "daniel",
            frozenset({"telschow"}),
        )
        assert canonical_parts("'ñDaniel Telschow'") == (
            "daniel",
            frozenset({"telschow"}),
        )

    def test_salutation_prefixes_stripped(self) -> None:
        assert canonical_parts("Guten Tag Daniel Telschow") == (
            "daniel",
            frozenset({"telschow"}),
        )
        assert canonical_parts("Herr Telschow Arjona") == (
            "telschow",
            frozenset({"arjona"}),
        )

    def test_parenthetical_annotations_stripped(self) -> None:
        assert canonical_parts("Telschow Arjona Daniel (über TUM)") == (
            "daniel",
            frozenset({"telschow", "arjona"}),
        )
        assert canonical_parts("WG Grau Suárez Telschow (via Google Drive)") == (
            "wg",
            frozenset({"grau", "suárez", "telschow"}),
        )
        assert canonical_parts("WG Grau Suárez Telschow") == (
            "wg",
            frozenset({"grau", "suárez", "telschow"}),
        )

    def test_plus_joins_to_space(self) -> None:
        assert canonical_parts("Daniel Telschow+Arjona") == (
            "daniel",
            frozenset({"telschow", "arjona"}),
        )

    def test_room_code_annotation_stripped(self) -> None:
        assert canonical_parts("Telschow Daniel, EF-703") == (
            "daniel",
            frozenset({"telschow"}),
        )

    def test_dot_username_forms(self) -> None:
        assert canonical_parts("daniel.telschow") == (
            "daniel",
            frozenset({"telschow"}),
        )
        assert canonical_parts("dani.telschow") == ("daniel", frozenset({"telschow"}))
        assert canonical_parts("telschow.daniel") == (
            "daniel",
            frozenset({"telschow"}),
        )
        # non-alias local part is opaque, never folded by structure alone
        assert canonical_parts("dirk.telschow") is None
        assert canonical_parts("d.telschow") is None

    def test_digit_only_suffix_is_opaque(self) -> None:
        assert canonical_parts("telschow113") is None
        assert canonical_parts("reddit.com (telschow)") is None

    def test_family_names_with_aliases_fold(self) -> None:
        # Nicolas and Dirks fold among themselves by exact given names; they
        # never fold into Daniel because given differs.
        assert canonical_parts("Nicolas Telschow") == (
            "nicolas",
            frozenset({"telschow"}),
        )
        assert canonical_parts("Nicolas Telschow Arjona") == (
            "nicolas",
            frozenset({"telschow", "arjona"}),
        )
        assert canonical_parts("Dirk Telschow") == (
            "dirk",
            frozenset({"telschow"}),
        )
        assert canonical_parts("'Dirk Telschow'") == ("dirk", frozenset({"telschow"}))

    def test_accents_preserved_not_transliterated(self) -> None:
        assert canonical_parts("Müller Telschow") == (
            "müller",
            frozenset({"telschow"}),
        )
        assert canonical_parts("Muller Telschow") == (
            "muller",
            frozenset({"telschow"}),
        )
        assert canonical_parts("Müller Telschow") != canonical_parts("Muller Telschow")

    def test_single_token_has_empty_surname_set(self) -> None:
        assert canonical_parts("Telschow") == ("telschow", frozenset())
        assert canonical_parts("Daniel") == ("daniel", frozenset())

    def test_empty_or_junk_name_is_none(self) -> None:
        assert canonical_parts("") is None
        assert canonical_parts("   ") is None


class TestCanonicalIdentity:
    def test_clean_name_identity_is_unchanged(self) -> None:
        assert canonical_identity("Alice Anders") == normalize_identity("Alice Anders")

    def test_nickname_variants_share_identity(self) -> None:
        identities = {
            canonical_identity(name)
            for name in ("Daniel Telschow", "Dani Telschow", "Dan Telschow")
        }
        assert identities == {normalize_identity("Daniel Telschow")}

    def test_arjona_surname_variants_share_identity(self) -> None:
        assert canonical_identity("Daniel Telschow") == canonical_identity(
            "DANIEL TELSCHOW ARJONA"
        )
        assert canonical_identity("Daniel Telschow") == canonical_identity(
            "Daniel Telschow+Arjona"
        )
        assert canonical_identity("Daniel Telschow") == canonical_identity(
            "Telschow Arjona Daniel (über TUM)"
        )
        assert canonical_identity("Daniel Telschow") == canonical_identity(
            "Guten Tag Daniel Telschow"
        )

    def test_opaque_names_keep_normalized_identity(self) -> None:
        for name in ("telschow113", "d.telschow", "reddit.com (telschow)"):
            assert canonical_identity(name) == normalize_identity(name)

    def test_distinct_family_members_stay_distinct(self) -> None:
        assert canonical_identity("Daniel Telschow") != canonical_identity(
            "Dirk Telschow"
        )
        assert canonical_identity("Daniel Telschow") != canonical_identity(
            "Nicolas Telschow"
        )
        assert canonical_identity("Daniel Telschow") != canonical_identity(
            "WG Grau Suárez Telschow"
        )

    def test_identity_is_stable_across_repeated_calls(self) -> None:
        name = "Dani Telschow"
        assert canonical_identity(canonical_identity(name)) == canonical_identity(name)

    def test_person_id_derives_from_canonical_identity(self) -> None:
        assert person_id_for(canonical_identity("Dani Telschow")) == person_id_for(
            canonical_identity("Daniel Telschow")
        )


# ------------------------------------------------------------ store behavior


class TestStoreCanonicalIdentity:
    def test_upsert_folds_nickname_variants_into_one_person(
        self, store: PersonStore
    ) -> None:
        store.upsert_reference(make_reference(name="Daniel Telschow", document_id="d1"))
        store.upsert_reference(make_reference(name="Dani Telschow", document_id="d2"))
        store.upsert_reference(
            make_reference(name="DANIEL TELSCHOW ARJONA", document_id="d3")
        )
        assert store.count() == 1
        person = store.get(person_id_for(canonical_identity("Daniel Telschow")))
        assert person is not None
        assert person.evidence_count == 3
        assert person.display_name == "DANIEL TELSCHOW ARJONA"

    def test_upsert_keeps_distinct_family_members(self, store: PersonStore) -> None:
        store.upsert_reference(make_reference(name="Daniel Telschow", document_id="d1"))
        store.upsert_reference(make_reference(name="Dirk Telschow", document_id="d2"))
        store.upsert_reference(
            make_reference(name="Nicolas Telschow", document_id="d3")
        )
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
        store.upsert_reference(make_reference(name="Dani Telschow", document_id="d1"))
        person = store.get(person_id_for(canonical_identity("Daniel Telschow")))
        assert person is not None
        assert person.identity == canonical_identity("Daniel Telschow")

    def test_canonicalize_dry_run_writes_nothing(self, store: PersonStore) -> None:
        _seed_legacy(
            store,
            [
                ("d0", "Daniel Telschow"),
                ("d1", "Dani Telschow"),
                ("d2", "Telschow, Daniel"),
                ("d3", "Dirk Telschow"),
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
                ("d0", "Daniel Telschow"),
                ("d1", "Dani Telschow"),
                ("d2", "Telschow, Daniel"),
                ("d3", "Dirk Telschow"),
            ],
        )
        report = store.canonicalize(apply=True)
        assert report.applied
        assert store.count() == 2
        daniel = store.get(person_id_for(canonical_identity("Daniel Telschow")))
        assert daniel is not None
        assert daniel.evidence_count == 3
        # longest observed original form wins (16 chars), including artifacts.
        assert daniel.display_name == "Telschow, Daniel"
        dirk = store.get(person_id_for(canonical_identity("Dirk Telschow")))
        assert dirk is not None and dirk.evidence_count == 1

    def test_canonicalize_apply_is_idempotent(self, store: PersonStore) -> None:
        _seed_legacy(
            store,
            [
                ("d0", "Daniel Telschow"),
                ("d1", "Dani Telschow"),
            ],
        )
        store.canonicalize(apply=True)
        again = store.canonicalize(apply=True)
        assert store.count() == 1
        assert again.people_after == 1
        assert again.people_rekeyed == 0
        assert again.merged_groups == 0
        daniel = store.get(person_id_for(canonical_identity("Daniel Telschow")))
        assert daniel is not None and daniel.evidence_count == 2

    def test_canonicalize_empty_store(self, store: PersonStore) -> None:
        report = store.canonicalize(apply=True)
        assert report.people_before == 0
        assert report.people_after == 0
        assert report.evidence_before == 0
        assert store.count() == 0


class TestStoreCanonicalizeCollection:
    """The 35 production fragment rows collapse to a small safe set."""

    FRAGMENTS: ClassVar[list[str]] = [
        "'Daniel Telschow'",
        "'Dirk Telschow'",
        "'Maike Telschow'",
        "'Nicolas Telschow'",
        "'Nicolas Telschow Arjona'",
        "'dirk.telschow'",
        "'ñDaniel Telschow'",
        "DANIEL TELSCHOW ARJONA",
        "Dan Telschow",
        "Dani Telschow",
        "Daniel Telschow",
        "Daniel Telschow+Arjona",
        "Daniel, Telschow",
        "Dirk Telschow",
        "Guten Tag Daniel Telschow",
        "Herr Telschow Arjona",
        "Maike Telschow",
        "Nicolas Telschow",
        "Nicolas Telschow Arjona",
        "Telschow",
        "Telschow Arjona",
        "Telschow Arjona Daniel",
        "Telschow Arjona Daniel (über TUM)",
        "Telschow Arjona, Daniel",
        "Telschow Daniel, EF-703",
        "Telschow, Daniel",
        "WG Grau Suárez Telschow",
        "WG Grau Suárez Telschow (via Google Drive)",
        "d.telschow",
        "dani.telschow",
        "daniel.telschow",
        "reddit.com (telschow)",
        "telschow.daniel",
        "telschow113",
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
        daniel_forms = {
            "Daniel Telschow",
            "Dani Telschow",
            "Dan Telschow",
            "DANIEL TELSCHOW ARJONA",
            "Daniel Telschow+Arjona",
            "Daniel, Telschow",
            "'Daniel Telschow'",
            "'ñDaniel Telschow'",
            "ñDaniel Telschow",
            "Guten Tag Daniel Telschow",
            "Telschow Arjona Daniel",
            "Telschow Arjona Daniel (über TUM)",
            "Telschow Arjona, Daniel",
            "Telschow Daniel, EF-703",
            "Telschow, Daniel",
            "dani.telschow",
            "daniel.telschow",
            "telschow.daniel",
        }
        daniel_ids = {canonical_identity(n) for n in daniel_forms}
        assert len(daniel_ids) == 1
