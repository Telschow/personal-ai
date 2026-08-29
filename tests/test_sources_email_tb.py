"""Tests for Thunderbird flat-mailbox discovery in the email source adapter.

Thunderbird stores local IMAP mailboxes as extensionless mbox files named
after the folder (``INBOX``, ``Sent``) directly in the account directory.
The adapter must discover those alongside the conventional Gmail-style
``*.mbox/mbox`` layout without changing parsing, identity, or loading.
"""

import mailbox as mailbox_mod
from email.message import Message
from email.mime.text import MIMEText
from pathlib import Path

import pytest

from personal_ai.sources.email import (
    EmailSourceAdapter,
    SourceNotFoundError,
    _looks_like_mbox,
)


def make_message(
    *,
    subject: str = "Test Subject",
    body: str = "A test message body.",
    message_id: str = "<message@example.com>",
) -> Message:
    """Create a simple email message for testing."""
    msg = MIMEText(body, "plain")
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 01 Jan 2026 12:00:00 +0000"
    if message_id is None:
        del msg["Message-ID"]
    else:
        msg["Message-ID"] = message_id
    return msg


def write_flat_mbox(root: Path, name: str, messages: list[Message]) -> Path:
    """Write an extensionless Thunderbird-style mailbox file."""
    path = root / name
    mbox = mailbox_mod.mbox(str(path))
    for msg in messages:
        mbox.add(msg)
    mbox.close()
    return path


def write_gmail_mbox(root: Path, mbox_dir_name: str, messages: list[Message]) -> Path:
    """Write messages into a conventional ``*.mbox/mbox`` directory."""
    mbox_dir = root / mbox_dir_name
    mbox_dir.mkdir(parents=True, exist_ok=True)
    mbox_path = mbox_dir / "mbox"
    mbox = mailbox_mod.mbox(str(mbox_path))
    for msg in messages:
        mbox.add(msg)
    mbox.close()
    return mbox_path


def build_thunderbird_dir(tmp_path: Path) -> Path:
    """Create a representative Thunderbird account directory."""
    root = tmp_path / "mail"
    root.mkdir()

    write_flat_mbox(
        root,
        "INBOX",
        [
            make_message(
                subject="One", body="first message", message_id="<one@example.com>"
            ),
            make_message(
                subject="Two", body="second message", message_id="<two@example.com>"
            ),
        ],
    )
    write_flat_mbox(
        root,
        "Sent",
        [
            make_message(
                subject="Sent", body="sent message", message_id="<sent@example.com>"
            )
        ],
    )

    (root / "INBOX.msf").write_bytes(b"bogus summary 1")
    (root / "Sent.msf").write_bytes(b"bogus summary 2")
    (root / "random.dat").write_text("keyword=value\n", encoding="utf-8")
    (root / "metadata.json").write_text("{}", encoding="utf-8")
    (root / ".cache").write_text("state", encoding="utf-8")
    (root / "garbage").write_text("this is not an mbox file\n", encoding="utf-8")

    nested = root / "some-directory"
    nested.mkdir()
    write_flat_mbox(
        nested,
        "nested-mailbox",
        [
            make_message(
                subject="Nested",
                body="nested message",
                message_id="<nested@example.com>",
            )
        ],
    )

    return root


class TestLooksLikeMbox:
    def test_empty_file_is_a_valid_empty_mailbox(self, tmp_path: Path) -> None:
        path = tmp_path / "Empty"
        path.write_bytes(b"")
        assert _looks_like_mbox(path)

    def test_from_line_is_recognized(self, tmp_path: Path) -> None:
        path = tmp_path / "INBOX"
        path.write_bytes(
            b"From sender@example.com Mon Jan 01 12:00:00 2026\n\nHeaders: x\n"
        )
        assert _looks_like_mbox(path)

    def test_utf8_bom_and_leading_blanks_tolerated(self, tmp_path: Path) -> None:
        path = tmp_path / "INBOX"
        path.write_bytes(
            b"\xef\xbb\xbf\n\nFrom someone@example.com Mon Jan 01 12:00:00 2026\n"
        )
        assert _looks_like_mbox(path)

    def test_header_only_file_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "garbage"
        path.write_text(
            "From: sender@example.com\nSubject: not mbox\n", encoding="utf-8"
        )
        assert not _looks_like_mbox(path)

    def test_random_text_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "garbage"
        path.write_text("this is not an mbox file\n", encoding="utf-8")
        assert not _looks_like_mbox(path)

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        assert not _looks_like_mbox(tmp_path / "does-not-exist")


class TestThunderbirdDiscovery:
    def test_discovers_flat_mailbox_files(self, tmp_path: Path) -> None:
        root = build_thunderbird_dir(tmp_path)
        adapter = EmailSourceAdapter(root)

        files = [path.name for path in adapter._find_thunderbird_files()]
        assert files == ["INBOX", "Sent"]

    def test_msf_dat_json_dotfiles_and_garbage_excluded(self, tmp_path: Path) -> None:
        root = build_thunderbird_dir(tmp_path)
        adapter = EmailSourceAdapter(root)

        for candidate in adapter._find_thunderbird_files():
            assert candidate.name not in {
                "INBOX.msf",
                "Sent.msf",
                "random.dat",
                "metadata.json",
                ".cache",
                "garbage",
            }

    def test_directories_are_not_mailboxes(self, tmp_path: Path) -> None:
        root = build_thunderbird_dir(tmp_path)
        adapter = EmailSourceAdapter(root)

        names = {path.name for path in adapter._find_thunderbird_files()}
        assert "some-directory" not in names
        assert "nested-mailbox" not in names

    def test_discovery_ordering_is_deterministic(self, tmp_path: Path) -> None:
        root = build_thunderbird_dir(tmp_path)
        adapter = EmailSourceAdapter(root)

        first = [path.name for path in adapter._find_thunderbird_files()]
        second = [path.name for path in adapter._find_thunderbird_files()]
        assert first == second

    def test_discover_finds_all_flat_messages(self, tmp_path: Path) -> None:
        root = build_thunderbird_dir(tmp_path)
        adapter = EmailSourceAdapter(root)

        records = adapter.discover()
        assert len(records) == 3
        assert [record.source_key for record in records] == sorted(
            record.source_key for record in records
        )
        assert {record.metadata["mbox"] for record in records} == {"INBOX", "Sent"}
        subjects = {record.metadata["subject"] for record in records}
        assert subjects == {"One", "Two", "Sent"}

    def test_nested_and_non_mailbox_content_contributes_nothing(
        self, tmp_path: Path
    ) -> None:
        root = build_thunderbird_dir(tmp_path)
        adapter = EmailSourceAdapter(root)

        subjects = {record.metadata["subject"] for record in adapter.discover()}
        assert "Nested" not in subjects

    def test_empty_flat_file_yields_no_records(self, tmp_path: Path) -> None:
        root = tmp_path / "mail"
        root.mkdir()
        (root / "Empty").write_bytes(b"")

        adapter = EmailSourceAdapter(root)
        assert adapter.discover() == []

    def test_missing_directory_yields_no_thunderbird_files(
        self, tmp_path: Path
    ) -> None:
        adapter = EmailSourceAdapter(tmp_path / "missing")
        assert list(adapter._find_thunderbird_files()) == []


class TestGmailStyleRegression:
    def test_conventional_mbox_layout_still_discovered(self, tmp_path: Path) -> None:
        root = tmp_path / "export"
        root.mkdir()
        write_gmail_mbox(
            root,
            "INBOX.mbox",
            [
                make_message(
                    subject="First", body="first", message_id="<first@example.com>"
                )
            ],
        )
        write_gmail_mbox(
            root,
            "Sent.mbox",
            [
                make_message(
                    subject="Second", body="second", message_id="<second@example.com>"
                )
            ],
        )
        (root / "INBOX.mbox" / "mbox.msf").write_bytes(b"bogus")

        adapter = EmailSourceAdapter(root)
        records = adapter.discover()
        assert len(records) == 2
        assert [record.source_key for record in records] == sorted(
            record.source_key for record in records
        )
        assert {record.metadata["mbox"] for record in records} == {
            "INBOX.mbox",
            "Sent.mbox",
        }

    def test_thunderbird_discovery_finds_nothing_in_gmail_tree(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "export"
        root.mkdir()
        write_gmail_mbox(
            root,
            "INBOX.mbox",
            [
                make_message(
                    subject="First", body="first", message_id="<first@example.com>"
                )
            ],
        )

        adapter = EmailSourceAdapter(root)
        assert list(adapter._find_thunderbird_files()) == []

    def test_mixed_layout_finds_both_kinds(self, tmp_path: Path) -> None:
        root = tmp_path / "mixed"
        root.mkdir()
        write_gmail_mbox(
            root,
            "Folder.mbox",
            [
                make_message(
                    subject="Gmail", body="gmail", message_id="<gmail@example.com>"
                )
            ],
        )
        write_flat_mbox(
            root,
            "INBOX",
            [
                make_message(
                    subject="Flat", body="flat", message_id="<flat@example.com>"
                )
            ],
        )

        adapter = EmailSourceAdapter(root)
        records = adapter.discover()
        assert len(records) == 2
        assert {record.metadata["mbox"] for record in records} == {
            "Folder.mbox",
            "INBOX",
        }


class TestMessageIdentity:
    def test_same_message_id_reuses_key_across_flat_mailboxes(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "mail"
        root.mkdir()
        msg = make_message(
            subject="Shared", body="identical bytes", message_id="<shared@example.com>"
        )
        write_flat_mbox(root, "INBOX", [msg])
        write_flat_mbox(root, "Sent", [msg])

        adapter = EmailSourceAdapter(root)
        records = adapter.discover()
        assert len(records) == 2
        assert {record.source_key for record in records} == {"shared@example.com"}
        assert len({record.content_hash for record in records}) == 1

    def test_message_id_domain_case_normalized(self, tmp_path: Path) -> None:
        root = tmp_path / "mail"
        root.mkdir()
        write_flat_mbox(
            root,
            "INBOX",
            [
                make_message(
                    subject="Case", body="body", message_id="<UPPER.Local@Example.ORG>"
                )
            ],
        )

        adapter = EmailSourceAdapter(root)
        records = adapter.discover()
        assert records[0].source_key == "UPPER.Local@example.org"

    def test_same_message_id_different_bodies_both_survive(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "mail"
        root.mkdir()
        original = make_message(
            subject="Edited", body="version one", message_id="<edited@example.com>"
        )
        edited = make_message(
            subject="Edited", body="version two", message_id="<edited@example.com>"
        )
        write_flat_mbox(root, "INBOX", [original, edited])

        adapter = EmailSourceAdapter(root)
        records = adapter.discover()
        assert len(records) == 2
        assert {record.source_key for record in records} == {"edited@example.com"}
        assert len({record.content_hash for record in records}) == 2

    def test_missing_message_id_fallback_is_deterministic(self, tmp_path: Path) -> None:
        root = tmp_path / "mail"
        root.mkdir()
        msg = make_message(
            subject="No id", body="payload without an identifier", message_id=None
        )
        write_flat_mbox(root, "INBOX", [msg])
        write_flat_mbox(root, "Sent", [msg])

        adapter = EmailSourceAdapter(root)
        records1 = adapter.discover()
        records2 = adapter.discover()
        assert len(records1) == 2
        assert records1[0].source_key == records1[1].source_key
        assert records1[0].source_key == records2[0].source_key
        assert records1[0].source_key.startswith("noid/")

    def test_fallback_distinguishes_different_payloads(self, tmp_path: Path) -> None:
        root = tmp_path / "mail"
        root.mkdir()
        write_flat_mbox(
            root,
            "INBOX",
            [
                make_message(subject="No id", body="payload one", message_id=None),
                make_message(subject="No id", body="payload two", message_id=None),
            ],
        )

        adapter = EmailSourceAdapter(root)
        records = adapter.discover()
        keys = [record.source_key for record in records]
        assert len(set(keys)) == 2
        assert all(key.startswith("noid/") for key in keys)

    def test_fallback_does_not_mimic_a_real_message_id(self, tmp_path: Path) -> None:
        root = tmp_path / "mail"
        root.mkdir()
        write_flat_mbox(
            root,
            "INBOX",
            [
                make_message(subject="No id", body="fallback payload", message_id=None),
                make_message(
                    subject="Real",
                    body="real payload",
                    message_id="<real-id@example.com>",
                ),
            ],
        )

        adapter = EmailSourceAdapter(root)
        records = adapter.discover()
        fallback_key = next(
            record.source_key
            for record in records
            if record.source_key.startswith("noid/")
        )
        real_key = next(
            record.source_key
            for record in records
            if not record.source_key.startswith("noid/")
        )
        assert fallback_key != real_key
        assert "noid/" not in real_key


class TestLoadRecordFlat:
    def test_load_record_by_key(self, tmp_path: Path) -> None:
        root = tmp_path / "mail"
        root.mkdir()
        write_flat_mbox(
            root,
            "INBOX",
            [
                make_message(
                    subject="One", body="first", message_id="<one@example.com>"
                ),
                make_message(
                    subject="Two", body="second", message_id="<two@example.com>"
                ),
            ],
        )

        adapter = EmailSourceAdapter(root)
        records = adapter.discover()
        for record in records:
            assert adapter.load_record(record.source_key) == record

    def test_load_record_by_normalized_message_id(self, tmp_path: Path) -> None:
        root = tmp_path / "mail"
        root.mkdir()
        write_flat_mbox(
            root,
            "INBOX",
            [
                make_message(
                    subject="Only", body="only body", message_id="<X@Example.com>"
                )
            ],
        )

        adapter = EmailSourceAdapter(root)
        loaded = adapter.load_record("X@example.com")
        assert loaded.source_key == "X@example.com"
        assert b"only body" in loaded.payload

    def test_load_record_by_fallback_key(self, tmp_path: Path) -> None:
        root = tmp_path / "mail"
        root.mkdir()
        write_flat_mbox(
            root,
            "INBOX",
            [make_message(subject="No id", body="fallback body", message_id=None)],
        )

        adapter = EmailSourceAdapter(root)
        discovered = adapter.discover()
        key = discovered[0].source_key
        assert key.startswith("noid/")
        assert adapter.load_record(key) == discovered[0]

    def test_load_record_mixed_layout(self, tmp_path: Path) -> None:
        root = tmp_path / "mixed"
        root.mkdir()
        write_gmail_mbox(
            root,
            "Folder.mbox",
            [
                make_message(
                    subject="Gmail", body="gmail", message_id="<gmail@example.com>"
                )
            ],
        )
        write_flat_mbox(
            root,
            "INBOX",
            [
                make_message(
                    subject="Flat", body="flat", message_id="<flat@example.com>"
                )
            ],
        )

        adapter = EmailSourceAdapter(root)
        records = adapter.discover()
        assert len(records) == 2
        for record in records:
            assert adapter.load_record(record.source_key) == record

    def test_load_record_unknown_key_raises(self, tmp_path: Path) -> None:
        root = tmp_path / "mail"
        root.mkdir()
        write_flat_mbox(root, "INBOX", [make_message(message_id="<one@example.com>")])

        adapter = EmailSourceAdapter(root)
        with pytest.raises(SourceNotFoundError):
            adapter.load_record("does-not-exist@example.com")

    def test_mailbox_label_distinguishes_layouts(self, tmp_path: Path) -> None:
        root = tmp_path / "mixed"
        root.mkdir()
        gmail_path = write_gmail_mbox(
            root,
            "Folder.mbox",
            [make_message(message_id="<gmail@example.com>")],
        )
        flat_path = write_flat_mbox(
            root,
            "INBOX",
            [make_message(message_id="<flat@example.com>")],
        )

        adapter = EmailSourceAdapter(root)
        assert adapter._mailbox_label(gmail_path) == "Folder.mbox"
        assert adapter._mailbox_label(flat_path) == "INBOX"
