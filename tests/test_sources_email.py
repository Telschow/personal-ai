"""Tests for the email source adapter."""

import mailbox as mailbox_mod
from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pytest

from personal_ai.documents.models import compute_content_hash
from personal_ai.sources.base import SourceAdapter
from personal_ai.sources.email import (
    EmailSourceAdapter,
    EmptyMessageError,
    SourceNotFoundError,
    UnsupportedMessageError,
    _decode_header_value,
    _extract_text_body,
    _normalize_date,
    _strip_html_tags,
    build_message_record,
    compose_message_text,
)


def make_message(
    *,
    from_addr: str = "sender@example.com",
    to_addr: str = "recipient@example.com",
    subject: str = "Test Subject",
    body: str = "Hello, this is a test message.",
    date: str = "Mon, 01 Jan 2026 12:00:00 +0000",
    message_id: str = "<test-message-id@example.com>",
    content_type: str = "text/plain",
    in_reply_to: str = "",
    references: str = "",
) -> Message:
    """Create a simple email message for testing."""
    msg = MIMEText(body, _subtype=content_type.split("/")[-1])
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = date
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    return msg


def make_multipart_message(
    *,
    from_addr: str = "sender@example.com",
    to_addr: str = "recipient@example.com",
    subject: str = "Multipart Test",
    plain_body: str = "Plain text content",
    html_body: str = "<p>HTML content</p>",
    date: str = "Mon, 01 Jan 2026 12:00:00 +0000",
    message_id: str = "<multipart-id@example.com>",
) -> Message:
    """Create a multipart email message for testing."""
    msg = MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = date
    msg["Message-ID"] = message_id

    plain_part = MIMEText(plain_body, "plain")
    html_part = MIMEText(html_body, "html")
    msg.attach(plain_part)
    msg.attach(html_part)

    return msg


def write_mbox(root: Path, mbox_dir_name: str, messages: list[Message]) -> Path:
    """Write messages to an mbox file.

    ``mbox_dir_name`` is the full directory name (e.g. ``INBOX.mbox``).
    """
    mbox_dir = root / mbox_dir_name
    mbox_dir.mkdir(parents=True, exist_ok=True)
    mbox_path = mbox_dir / "mbox"

    mbox = mailbox_mod.mbox(str(mbox_path))
    for msg in messages:
        mbox.add(msg)
    mbox.close()

    return mbox_path


class TestHeaderDecoding:
    def test_decode_plain_ascii(self) -> None:
        assert _decode_header_value("Hello World") == "Hello World"

    def test_decode_none_returns_empty(self) -> None:
        assert _decode_header_value(None) == ""

    def test_decode_empty_string(self) -> None:
        assert _decode_header_value("") == ""

    def test_decode_utf8_encoded(self) -> None:
        # Create a MIME-encoded header
        from email.header import Header

        encoded = Header("Ünïcödé Tëst", "utf-8").encode()
        result = _decode_header_value(encoded)
        assert "Ünïcödé Tëst" in result


class TestDateNormalization:
    def test_parse_valid_date(self) -> None:
        result = _normalize_date("Mon, 01 Jan 2026 12:00:00 +0000")
        assert "2026-01-01" in result
        assert "+00:00" in result

    def test_parse_none_returns_empty(self) -> None:
        assert _normalize_date(None) == ""

    def test_parse_invalid_returns_empty(self) -> None:
        assert _normalize_date("not a date") == ""


class TestHTMLStripping:
    def test_strip_basic_tags(self) -> None:
        html = "<p>Hello <b>world</b></p>"
        result = _strip_html_tags(html)
        assert result == "Hello world"

    def test_strip_script_content(self) -> None:
        html = "Before<script>alert('xss')</script>After"
        result = _strip_html_tags(html)
        assert "alert" not in result
        assert "Before" in result
        assert "After" in result

    def test_strip_style_content(self) -> None:
        html = "Before<style>.foo { color: red; }</style>After"
        result = _strip_html_tags(html)
        assert "color: red" not in result
        assert "Before" in result
        assert "After" in result


class TestBodyExtraction:
    def test_extract_plain_text(self) -> None:
        msg = make_message(body="Simple text message")
        result = _extract_text_body(msg)
        assert result == "Simple text message"

    def test_extract_multipart_prefers_plain(self) -> None:
        msg = make_multipart_message(
            plain_body="Plain content",
            html_body="<p>HTML content</p>",
        )
        result = _extract_text_body(msg)
        assert result == "Plain content"

    def test_extract_html_fallback(self) -> None:
        msg = MIMEText("<p>HTML only content</p>", "html")
        msg["From"] = "test@example.com"
        result = _extract_text_body(msg)
        assert "HTML only content" in result
        assert "<p>" not in result


class TestComposition:
    def test_subject_from_to_date_body(self) -> None:
        msg = make_message(
            subject="Meeting Tomorrow",
            from_addr="alice@example.com",
            to_addr="bob@example.com",
            date="Tue, 02 Jan 2026 10:00:00 +0000",
            body="Let's meet at 3pm.",
        )
        text = compose_message_text(msg)
        assert "Subject: Meeting Tomorrow" in text
        assert "From: alice@example.com" in text
        assert "To: bob@example.com" in text
        assert "Date: Tue, 02 Jan 2026 10:00:00 +0000" in text
        assert "Let's meet at 3pm." in text

    def test_empty_subject_still_includes_other_headers(self) -> None:
        msg = make_message(subject="", body="No subject")
        text = compose_message_text(msg)
        assert "Subject:" not in text
        assert "From:" in text
        assert "No subject" in text


class TestRecordBuilding:
    def test_basic_record_metadata(self) -> None:
        msg = make_message(
            subject="Test Email",
            from_addr="sender@example.com",
            message_id="<abc@example.com>",
        )
        record = build_message_record("test.mbox/message_000000", msg)
        assert record.source_type == "email"
        assert record.source_key == "test.mbox/message_000000"
        assert record.metadata["subject"] == "Test Email"
        assert record.metadata["sender"] == "sender@example.com"
        assert record.metadata["message_id"] == "<abc@example.com>"

    def test_content_hash_matches_payload(self) -> None:
        msg = make_message(body="Test content")
        record = build_message_record("test.mbox/message_000000", msg)
        assert record.payload is not None
        assert record.content_hash == compute_content_hash(record.payload)

    def test_empty_message_raises(self) -> None:
        msg = make_message(body="")
        with pytest.raises(EmptyMessageError):
            build_message_record("test.mbox/message_000000", msg)

    def test_reply_headers_captured(self) -> None:
        msg = make_message(
            in_reply_to="<original@example.com>",
            references="<original@example.com> <reply@example.com>",
        )
        record = build_message_record("test.mbox/message_000000", msg)
        assert record.metadata["in_reply_to"] == "<original@example.com>"
        assert (
            record.metadata["references"]
            == "<original@example.com> <reply@example.com>"
        )


class TestEmailSourceAdapter:
    @pytest.fixture()
    def email_dir(self, tmp_path: Path) -> Path:
        root = tmp_path / "Email"
        root.mkdir(parents=True)

        # Create a simple mbox with 2 messages
        messages = [
            make_message(subject="First", body="First message body"),
            make_message(subject="Second", body="Second message body"),
        ]
        write_mbox(root, "INBOX.mbox", messages)

        # Create another mbox with 1 message
        write_mbox(
            root, "Sent.mbox", [make_message(subject="Sent", body="Sent message")]
        )

        return root

    def test_discovers_messages_deterministically(self, email_dir: Path) -> None:
        adapter = EmailSourceAdapter(email_dir)
        records = adapter.discover()
        keys = [r.source_key for r in records]
        assert keys == sorted(keys)
        assert len(records) == 3  # 2 + 1

    def test_source_type_is_email(self, email_dir: Path) -> None:
        adapter = EmailSourceAdapter(email_dir)
        assert adapter.source_type == "email"

    def test_adapter_satisfies_source_protocol(self, email_dir: Path) -> None:
        adapter = EmailSourceAdapter(email_dir)
        assert isinstance(adapter, SourceAdapter)

    def test_load_record_by_key(self, email_dir: Path) -> None:
        adapter = EmailSourceAdapter(email_dir)
        records = adapter.discover()
        first = records[0]
        loaded = adapter.load_record(first.source_key)
        assert loaded == first

    def test_load_record_invalid_key_raises(self, email_dir: Path) -> None:
        adapter = EmailSourceAdapter(email_dir)
        with pytest.raises(UnsupportedMessageError):
            adapter.load_record("invalid_key")

    def test_load_record_missing_mbox_raises(self, email_dir: Path) -> None:
        adapter = EmailSourceAdapter(email_dir)
        with pytest.raises(SourceNotFoundError):
            adapter.load_record("nonexistent.mbox/message_000000")

    def test_multipart_message_prefers_plain_text(self, email_dir: Path) -> None:
        # Add a multipart message
        multipart_msg = make_multipart_message(
            subject="Multipart",
            plain_body="Plain text version",
            html_body="<p>HTML version</p>",
        )
        write_mbox(email_dir, "Multi.mbox", [multipart_msg])

        adapter = EmailSourceAdapter(email_dir)
        records = adapter.discover()
        multi_records = [r for r in records if "Multi" in r.source_key]
        assert len(multi_records) == 1
        assert b"Plain text version" in multi_records[0].payload
        assert b"HTML version" not in multi_records[0].payload

    def test_empty_messages_are_skipped(self, email_dir: Path) -> None:
        # Add an mbox with one empty and one real message
        write_mbox(
            email_dir,
            "Empty.mbox",
            [
                make_message(subject="Empty", body=""),
                make_message(subject="Real", body="Real content"),
            ],
        )

        adapter = EmailSourceAdapter(email_dir)
        records = adapter.discover()
        empty_subjects = [
            r.metadata["subject"] for r in records if "Empty.mbox" in r.source_key
        ]
        # Only the real message should be present; the empty one was skipped
        assert empty_subjects == ["Real"]

    def test_message_index_is_stable(self, email_dir: Path) -> None:
        adapter = EmailSourceAdapter(email_dir)
        records1 = adapter.discover()
        records2 = adapter.discover()
        assert [r.source_key for r in records1] == [r.source_key for r in records2]

    def test_metadata_includes_mbox_name(self, email_dir: Path) -> None:
        adapter = EmailSourceAdapter(email_dir)
        records = adapter.discover()
        for record in records:
            assert "mbox" in record.metadata
