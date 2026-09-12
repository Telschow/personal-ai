"""SQLite-backed storage for the people / identity layer."""

import json
import sqlite3
from types import TracebackType
from typing import Self

from personal_ai.people.models import (
    Person,
    PersonEvidence,
    PersonReference,
    normalize_identity,
    person_id_for,
)

_PEOPLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    identity TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    emails_json TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
)
"""

_EVIDENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS people_evidence (
    person_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    source_type TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (person_id, document_id),
    FOREIGN KEY (person_id) REFERENCES people(person_id) ON DELETE CASCADE
)
"""

_EVIDENCE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_people_evidence_person "
    "ON people_evidence (person_id)"
)

_INSERT_PERSON_SQL = """
INSERT INTO people (
    person_id, identity, display_name, emails_json, roles_json, sources_json,
    first_seen_at, last_seen_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(person_id) DO UPDATE SET
    first_seen_at = MIN(people.first_seen_at, excluded.first_seen_at),
    last_seen_at = MAX(people.last_seen_at, excluded.last_seen_at)
"""

_INSERT_EVIDENCE_SQL = """
INSERT OR IGNORE INTO people_evidence (
    person_id, document_id, name, email, role, source_type, seen_at
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_PEOPLE_COLUMNS = (
    "person_id, identity, display_name, emails_json, roles_json, "
    "sources_json, first_seen_at, last_seen_at"
)

_EVIDENCE_COUNT_SQL = (
    "(SELECT COUNT(*) FROM people_evidence e "
    "WHERE e.person_id = p.person_id) AS evidence_count"
)


def _person_from_row(row: tuple[object, ...]) -> Person:
    return Person(
        person_id=str(row[0]),
        identity=str(row[1]),
        display_name=str(row[2]),
        emails=tuple(json.loads(str(row[3]))),
        roles=tuple(json.loads(str(row[4]))),
        sources=tuple(json.loads(str(row[5]))),
        first_seen_at=str(row[6]),
        last_seen_at=str(row[7]),
        evidence_count=int(row[8]),
    )


def _evidence_from_row(row: tuple[object, ...]) -> PersonEvidence:
    return PersonEvidence(
        person_id=str(row[0]),
        document_id=str(row[1]),
        name=str(row[2]),
        email=str(row[3]),
        role=str(row[4]),
        source_type=str(row[5]),
        seen_at=str(row[6]),
    )


class PersonStore:
    """Durable people storage keyed by normalized identity.

    ``upsert_reference`` is idempotent: re-routing an already-seen reference
    (same stable ``document_id``) for an identity updates the person's
    aggregates deterministically on first sight and leaves them untouched
    afterwards. The cached aggregates (``display_name``, ``emails``,
    ``roles``, ``sources``, ``first_seen_at``, ``last_seen_at``) are always
    recomputed from the stored evidence, so drift cannot accumulate.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.execute(_PEOPLE_SCHEMA)
        self._connection.execute(_EVIDENCE_SCHEMA)
        self._connection.execute(_EVIDENCE_INDEX)
        self._connection.commit()

    def upsert_reference(self, reference: PersonReference) -> Person:
        """Store one reference, fusing it into its identity's aggregates."""
        identity = normalize_identity(reference.name)
        person_id = person_id_for(identity)
        self._connection.execute(
            _INSERT_PERSON_SQL,
            (
                person_id,
                identity,
                reference.name,
                json.dumps([]),
                json.dumps([]),
                json.dumps([]),
                reference.seen_at or "",
                reference.seen_at or "",
            ),
        )
        inserted = self._connection.execute(
            _INSERT_EVIDENCE_SQL,
            (
                person_id,
                reference.document_id,
                reference.name,
                reference.email,
                reference.role,
                reference.source_type,
                reference.seen_at or "",
            ),
        )
        if inserted.rowcount:
            self._connection.execute(
                "UPDATE people SET first_seen_at = MIN(first_seen_at, ?) "
                "WHERE person_id = ?",
                (reference.seen_at or "", person_id),
            )
            self._connection.execute(
                "UPDATE people SET last_seen_at = MAX(last_seen_at, ?) "
                "WHERE person_id = ?",
                (reference.seen_at or "", person_id),
            )
        self._refresh_aggregates(person_id)
        self._connection.commit()
        person = self.get(person_id)
        assert person is not None
        return person

    def get(self, person_id: str) -> Person | None:
        """Return the person with this id, or None when absent."""
        row = self._connection.execute(
            f"""
            SELECT {_PEOPLE_COLUMNS}, {_EVIDENCE_COUNT_SQL}
            FROM people p WHERE p.person_id = ?
            """,
            (person_id,),
        ).fetchone()
        return _person_from_row(row) if row is not None else None

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        role: str | None = None,
    ) -> list[Person]:
        """Return people whose name/identity/email matches a substring.

        Leading/trailing whitespace is stripped and SQL wildcards are treated
        literally. Ordering is deterministic: descending evidence count, then
        display name ascending, then person id ascending.
        """
        clause = (
            "WHERE p.display_name LIKE ? ESCAPE '\\' "
            "OR p.identity LIKE ? ESCAPE '\\' "
            "OR p.emails_json LIKE ? ESCAPE '\\'"
        )
        params: list[object] = [
            _escape_like(query),
            _escape_like(query),
            _escape_like(query),
        ]
        if role is not None:
            clause += " AND p.roles_json LIKE ? ESCAPE '\\'"
            params.append(_escape_like(role))
        sql = (
            f"SELECT {_PEOPLE_COLUMNS}, {_EVIDENCE_COUNT_SQL} FROM people p "
            f"{clause} ORDER BY evidence_count DESC, p.display_name ASC, "
            f"p.person_id ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
            if offset:
                sql += " OFFSET ?"
                params.append(offset)
        rows = self._connection.execute(sql, params).fetchall()
        return [_person_from_row(row) for row in rows]

    def list(self, *, limit: int | None = 50, offset: int = 0) -> list[Person]:
        """Return people in deterministic (evidence-count, name, id) order."""
        sql = (
            f"SELECT {_PEOPLE_COLUMNS}, {_EVIDENCE_COUNT_SQL} FROM people p "
            f"ORDER BY evidence_count DESC, p.display_name ASC, p.person_id ASC"
        )
        params: list[object] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
            if offset:
                sql += " OFFSET ?"
                params.append(offset)
        rows = self._connection.execute(sql, params).fetchall()
        return [_person_from_row(row) for row in rows]

    def count(self) -> int:
        """Number of stored people identities."""
        row = self._connection.execute("SELECT COUNT(*) FROM people").fetchone()
        return int(row[0] if row is not None else 0)

    def evidence_for(
        self, person_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[PersonEvidence]:
        """Bound the provenance rows for one person, newest-first."""
        sql = (
            "SELECT person_id, document_id, name, email, role, source_type, "
            "seen_at FROM people_evidence WHERE person_id = ? "
            "ORDER BY seen_at DESC, document_id ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            if offset:
                sql += " OFFSET ?"
            rows = self._connection.execute(
                sql, (person_id, limit) if offset == 0 else (person_id, limit, offset)
            ).fetchall()
        else:
            sql += " OFFSET ?"
            rows = self._connection.execute(sql, (person_id, offset)).fetchall()
        return [_evidence_from_row(row) for row in rows]

    def _refresh_aggregates(self, person_id: str) -> None:
        """Recompute the cached person aggregates from stored evidence."""
        rows = self._connection.execute(
            "SELECT name, email, role, source_type, seen_at "
            "FROM people_evidence WHERE person_id = ?",
            (person_id,),
        ).fetchall()
        names: list[str] = []
        emails: set[str] = set()
        roles: set[str] = set()
        sources: set[str] = set()
        seen_ats: list[str] = []
        for row in rows:
            name, email, role, source_type, seen_at = (str(value) for value in row)
            names.append(name)
            if email:
                emails.add(email)
            roles.add(role)
            sources.add(source_type)
            if seen_at:
                seen_ats.append(seen_at)
        self._connection.execute(
            "UPDATE people SET display_name = ?, emails_json = ?, "
            "roles_json = ?, sources_json = ?, first_seen_at = ?, "
            "last_seen_at = ? WHERE person_id = ?",
            (
                self._best_display_name(names),
                json.dumps(sorted(emails)),
                json.dumps(sorted(roles)),
                json.dumps(sorted(sources)),
                min(seen_ats) if seen_ats else "",
                max(seen_ats) if seen_ats else "",
                person_id,
            ),
        )

    @staticmethod
    def _best_display_name(names) -> str:
        """Longest original name form, ties broken lexicographically."""
        pool = [name for name in names if name]
        return min(pool, key=lambda name: (-len(name), name))

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcards so matches are literal substrings."""
    return (
        "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )
