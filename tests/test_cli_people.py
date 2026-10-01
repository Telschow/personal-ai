"""CLI tests for the ``personal-ai people`` verb (index/list).

Hermetic only: synthetic mbox + financial CSVs and private ``tmp_path``
SQLite databases. No Ollama, no network, no production data.
"""

from __future__ import annotations

import csv
import io
import mailbox as mailbox_mod
from email.message import EmailMessage
from pathlib import Path

import pytest

from personal_ai import cli
from personal_ai.documents.models import Document
from personal_ai.people.canonicalize import canonical_identity
from personal_ai.people.models import normalize_identity, person_id_for
from personal_ai.storage import DocumentStore, connect_database


def _write_mbox(root: Path, messages: list[EmailMessage]) -> Path:
    mbox_dir = root / "INBOX.mbox"
    mbox_dir.mkdir(parents=True, exist_ok=True)
    mbox_path = mbox_dir / "mbox"
    mbox = mailbox_mod.mbox(str(mbox_path))
    for msg in messages:
        mbox.add(msg)
    mbox.close()
    return mbox_path


def _make_message(
    *,
    sender: str,
    to: str,
    cc: str = "",
    subject: str = "Hello",
    body: str = "A short plain body.",
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = "Mon, 01 Jan 2026 12:00:00 +0000"
    msg["Message-ID"] = f"<{abs(hash((sender, to, subject)))}-{len(body)}@example.com>"
    msg.set_content(body)
    return msg


BANK_COLUMNS = [
    "Buchungsdatum",
    "Wertstellung",
    "Status",
    "Zahlungspflichtige*r",
    "Zahlungsempfänger*in",
    "Verwendungszweck",
    "Umsatztyp",
    "IBAN",
    "Betrag (€)",
    "Gläubiger-ID",
    "Mandatsreferenz",
    "Kundenreferenz",
]

BANK_PREAMBLE = [
    'Girokonto;"DE16120300001085646543"',
    "Zeitraum:;2024",
    "Kontostand vom 31.12.2024:;1234,56",
]

BANK_ROWS = [
    [
        "02.01.2024",
        "03.01.2024",
        "Gebucht",
        "Max Mustermann",
        "Landlord GmbH",
        "Miete",
        "Umsatz",
        "DE02120300000000202051",
        "-1000,00",
        "DE98ZZZ09999999999",
        "DE987654321",
        "REF-KND-2024",
    ],
    [
        "31.01.2024",
        "31.01.2024",
        "Gebucht",
        "Max Mustermann",
        "Netflix",
        "Abo",
        "Umsatz",
        "DE02120300000000202051",
        "-12,99",
        "",
        "",
        "",
    ],
]


def _bank_csv() -> bytes:
    output = io.StringIO()
    for line in BANK_PREAMBLE:
        output.write(f"{line}\n")
    output.write("\n")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow(BANK_COLUMNS)
    writer.writerows(BANK_ROWS)
    return output.getvalue().encode("utf-8")


def _seed_email_documents(path: Path) -> None:
    connection = connect_database(path)
    try:
        store = DocumentStore(connection)
        store.add(
            Document(
                id="email-1",
                source="msg-1@example.com",
                source_type="email",
                content_hash="hash-email-1",
                created_at="2026-01-01T10:00:00+00:00",
                modified_at="2026-01-01T10:00:00+00:00",
                path="INBOX.mbox",
                filename="msg-1",
                mime_type="message/rfc822",
                metadata={
                    "subject": "Hello",
                    "sender": "Alice Anders <alice@example.com>",
                    "to": "Bob Berger <bob@example.com>",
                    "cc": "Claire Chen <claire@example.com>",
                },
            )
        )
        store.add(
            Document(
                id="email-2",
                source="msg-2@example.com",
                source_type="email",
                content_hash="hash-email-2",
                created_at="2026-02-01T09:00:00+00:00",
                modified_at="2026-02-01T09:00:00+00:00",
                path="INBOX.mbox",
                filename="msg-2",
                mime_type="message/rfc822",
                metadata={
                    "subject": "Re: Hello",
                    "sender": "Bob Berger <bob@example.com>",
                    "to": "alice@example.com",
                    "cc": "",
                },
            )
        )
    finally:
        connection.close()


def test_people_index_email_from_mbox(tmp_path: Path) -> None:
    _write_mbox(
        tmp_path,
        [
            _make_message(
                sender="Alice Anders <alice@example.com>",
                to="Bob Berger <bob@example.com>",
                cc="Claire Chen <claire@example.com>",
            )
        ],
    )
    db = tmp_path / "people.db"
    code = cli.run_people(
        cli.parse_args(
            [
                "people",
                "index",
                "--database",
                str(db),
                "--source",
                "email",
                "--path",
                str(tmp_path),
            ]
        )
    )
    assert code == 0
    code = cli.run_people(cli.parse_args(["people", "list", "--database", str(db)]))
    assert code == 0


def test_people_index_email_without_path_uses_stored_documents(tmp_path: Path) -> None:
    db = tmp_path / "people.db"
    _seed_email_documents(db)
    code = cli.run_people(
        cli.parse_args(["people", "index", "--database", str(db), "--source", "email"])
    )
    assert code == 0
    connection = connect_database(db)
    try:
        from personal_ai.people.store import PersonStore

        store = PersonStore(connection)
        alice = store.get(person_id_for(normalize_identity("Alice Anders")))
        assert alice is not None
        assert alice.emails == ("alice@example.com",)
        assert alice.evidence_count == 1
        bob = store.get(person_id_for(normalize_identity("Bob Berger")))
        assert bob is not None
        assert bob.emails == ("bob@example.com",)
        assert bob.evidence_count == 2
        claire = store.get(person_id_for(normalize_identity("Claire Chen")))
        assert claire is not None
    finally:
        connection.close()


def test_people_list_filters_by_query(tmp_path: Path) -> None:
    db = tmp_path / "people.db"
    _seed_email_documents(db)
    cli.run_people(cli.parse_args(["people", "index", "--database", str(db)]))
    code = cli.run_people(
        cli.parse_args(["people", "list", "--database", str(db), "--query", "bobby"])
    )
    assert code == 0
    connection = connect_database(db)
    try:
        from personal_ai.people.store import PersonStore

        assert PersonStore(connection).search("bobby") == []
        assert len(PersonStore(connection).search("bob")) == 1
    finally:
        connection.close()


def test_people_index_financial_from_csv(tmp_path: Path) -> None:
    directory = tmp_path / "finance"
    directory.mkdir()
    (directory / "umsatz.csv").write_bytes(_bank_csv())
    db = tmp_path / "people.db"
    code = cli.run_people(
        cli.parse_args(
            [
                "people",
                "index",
                "--database",
                str(db),
                "--source",
                "financial",
                "--path",
                str(directory),
            ]
        )
    )
    assert code == 0
    connection = connect_database(db)
    try:
        from personal_ai.people.store import PersonStore

        store = PersonStore(connection)
        maxm = store.get(person_id_for(normalize_identity("Max Mustermann")))
        assert maxm is not None
        assert "financial" in maxm.roles
        landlord = store.get(person_id_for(normalize_identity("Landlord GmbH")))
        assert landlord is not None
        netflix = store.get(person_id_for(normalize_identity("Netflix")))
        assert netflix is not None
        assert store.count() == 3
    finally:
        connection.close()


def test_people_index_financial_requires_path(tmp_path: Path) -> None:
    db = tmp_path / "people.db"
    with pytest.raises(SystemExit):
        cli.run_people(
            cli.parse_args(
                ["people", "index", "--database", str(db), "--source", "financial"]
            )
        )


def test_people_index_requires_database(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.run_people(cli.parse_args(["people", "index", "--source", "email"]))


def test_people_index_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "people.db"
    _seed_email_documents(db)
    args = ["people", "index", "--database", str(db)]
    first = cli.run_people(cli.parse_args(args))
    second = cli.run_people(cli.parse_args(args))
    assert first == 0
    assert second == 0
    connection = connect_database(db)
    try:
        from personal_ai.people.store import PersonStore

        store = PersonStore(connection)
        assert store.count() == 3
        alice = store.get(person_id_for(normalize_identity("Alice Anders")))
        assert alice is not None and alice.evidence_count == 1
    finally:
        connection.close()


def test_people_index_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "people.db"
    _seed_email_documents(db)
    code = cli.run_people(
        cli.parse_args(["people", "index", "--database", str(db), "--json"])
    )
    assert code == 0
    payload = capsys.readouterr().out.strip()
    assert '"source"' in payload
    assert '"records"' in payload
    assert '"references"' in payload
    assert '"people_after"' in payload


def test_people_unknown_verb_exits(tmp_path: Path) -> None:
    db = tmp_path / "people.db"
    with pytest.raises(SystemExit):
        cli.run_people(cli.parse_args(["people", "frobnicate", "--database", str(db)]))


def _seed_legacy_people(db: Path, names: list[str]) -> None:
    """Seed pre-50A-style (normalize-keyed) people rows for canonicalize tests."""
    from personal_ai.people.store import PersonStore

    connection = connect_database(db)
    try:
        store = PersonStore(connection)
        for i, name in enumerate(names):
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
                (pid, f"d{i}", name, "", "email", "email", ""),
            )
        connection.commit()
        assert store.count() == len(names)
    finally:
        connection.close()


def test_people_canonicalize_dry_run_and_apply(tmp_path: Path) -> None:
    from personal_ai.people.store import PersonStore

    db = tmp_path / "people.db"
    _seed_legacy_people(db, ["Alice Example", "Ali Example", "Example, Alice"])

    dry = cli.run_people(
        cli.parse_args(["people", "canonicalize", "--database", str(db)])
    )
    assert dry == 0
    connection = connect_database(db)
    try:
        assert PersonStore(connection).count() == 3
    finally:
        connection.close()

    applied = cli.run_people(
        cli.parse_args(["people", "canonicalize", "--database", str(db), "--apply"])
    )
    assert applied == 0
    connection = connect_database(db)
    try:
        store = PersonStore(connection)
        assert store.count() == 1
        alice = store.get(person_id_for(canonical_identity("Alice Example")))
        assert alice is not None and alice.evidence_count == 3
    finally:
        connection.close()


def test_people_canonicalize_json_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "people.db"
    _seed_legacy_people(db, ["Alice Example", "Ali Example"])
    code = cli.run_people(
        cli.parse_args(["people", "canonicalize", "--database", str(db), "--json"])
    )
    assert code == 0
    payload = capsys.readouterr().out.strip()
    assert '"command"' in payload
    assert '"people_before"' in payload
    assert '"applied"' in payload
