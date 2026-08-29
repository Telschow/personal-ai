"""Email source adapter: mbox files become normalized source records.

Google Takeout stores email as ``<Folder>.mbox/mbox`` files in RFC 4155
mbox format. Each message is delimited by a ``From `` line and carries
RFC 822 headers plus a MIME body. The adapter discovers ``mbox`` files
inside ``*.mbox/`` directories, parses each message, extracts provenance
headers, and composes a plain-text payload from the best available body
part.

Thunderbird stores local IMAP mailboxes the same way but names the files
after each folder (``INBOX``, ``Sent``) directly in the account directory,
without an extension or a ``*.mbox/`` wrapper. The adapter additionally
discovers those flat mailbox files, restricted to regular top-level files
that actually look like mbox, so arbitrary state files are never treated
as mailboxes.
"""

import datetime as dt
import mailbox
import re
from email import message
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath

from personal_ai.documents.models import compute_content_hash
from personal_ai.sources.base import SourceError
from personal_ai.sources.email_normalization import (
    fallback_message_key,
    html_text_with_breaks,
    normalize_email_body,
    normalize_message_id,
)
from personal_ai.sources.models import SourceRecord

SOURCE_TYPE = "email"

_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".ruff_cache"}
)


def _looks_like_mbox(path: Path) -> bool:
    """Conservative check that a file is parseable as an mbox mailbox.

    A well-formed mbox file begins with the ``From `` envelope separator of
    its first message (possibly after a UTF-8 BOM and leading blank lines).
    An empty file is treated as a valid empty mailbox. Anything else is
    rejected so no arbitrary extensionless file becomes a mailbox.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(8192)
    except OSError:
        return False
    if not head.strip():
        return True
    text = head.decode("utf-8", errors="replace").lstrip("\ufeff \t\r\n")
    return text.startswith("From ")


class EmailParseError(SourceError):
    """Raised when a message payload cannot be decoded or parsed."""


class EmptyMessageError(SourceError):
    """Raised when a message carries no usable text content."""


class PathOutsideExportError(SourceError):
    """Raised when a requested source key escapes the export directory."""


class SourceNotFoundError(SourceError):
    """Raised when a requested message does not exist in the export."""


class UnsupportedMessageError(SourceError):
    """Raised when a requested file is not a valid mbox file."""


def _decode_header_value(raw: str | None) -> str:
    """Decode a MIME-encoded header value into plain text."""
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded: list[str] = []
    for data, charset in parts:
        if isinstance(data, bytes):
            try:
                decoded.append(data.decode(charset or "utf-8", errors="replace"))
            except LookupError, UnicodeDecodeError:
                decoded.append(data.decode("utf-8", errors="replace"))
        else:
            decoded.append(data)
    return "".join(decoded).strip()


def _normalize_date(raw: str | None) -> str:
    """Parse an RFC 2822 date string into UTC ISO format."""
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
    except ValueError, TypeError:
        return ""
    return parsed.astimezone(dt.UTC).isoformat()


def _safe_decode(data: bytes, charset: str | None) -> str:
    """Decode bytes with a fallback to utf-8 if the charset is unknown."""
    try:
        return data.decode(charset or "utf-8", errors="replace")
    except LookupError, UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _extract_text_body(msg: message.Message) -> str:
    """Extract the best plain-text body from a MIME message.

    Preference order:
    1. text/plain part (first found)
    2. text/html part (fallback, tags stripped)

    For multipart messages, text/plain parts are preferred. If only
    text/html is available, block-level markup is turned into line breaks
    before tags are stripped. The result is passed through deterministic
    body normalization (quoted tails, signatures, blank runs).
    """
    if msg.is_multipart():
        # Prefer text/plain
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    text = _safe_decode(payload, part.get_content_charset())
                    return normalize_email_body(text)
        # Fallback to text/html
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    html = _safe_decode(payload, part.get_content_charset())
                    return normalize_email_body(_html_to_text(html))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = _safe_decode(payload, msg.get_content_charset())
            if msg.get_content_type() == "text/html":
                return normalize_email_body(_html_to_text(text))
            return normalize_email_body(text)
    return ""


def _html_to_text(html: str) -> str:
    """Turn HTML into line-structured plain text with block boundaries.

    Semantic blocks (paragraphs, list items, table cells, headings, ``<br>``)
    become line breaks before generic tag stripping, so consecutive lines of
    prose stay distinct words lines rather than one run-on string.
    """
    return _strip_html_tags(html_text_with_breaks(html))


def _strip_html_tags(html: str) -> str:
    """Basic HTML tag stripping for fallback body extraction.

    Script and style content is removed entirely. Remaining tags become
    single spaces and each line is trimmed and re-collapsed, preserving the
    line breaks introduced by block boundaries without keeping ragged
    whitespace.
    """
    html = re.sub(
        r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE
    )
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def compose_message_text(msg: message.Message, body: str | None = None) -> str:
    """Compose retrieval-ready plain text from an email message.

    The composition includes headers as metadata context followed by the
    message body. This gives the downstream extractor the full picture
    of who sent what, when, and about what. ``body`` may be supplied to
    reuse an already-extracted body.
    """
    sections: list[str] = []

    subject = _decode_header_value(msg.get("Subject"))
    if subject:
        sections.append(f"Subject: {subject}")

    sender = _decode_header_value(msg.get("From"))
    if sender:
        sections.append(f"From: {sender}")

    to = _decode_header_value(msg.get("To"))
    if to:
        sections.append(f"To: {to}")

    date_raw = msg.get("Date")
    if date_raw:
        sections.append(f"Date: {date_raw}")

    body_text = _extract_text_body(msg) if body is None else body
    if body_text:
        sections.append(body_text)

    return "\n\n".join(sections)


def message_source_key(msg: message.Message, payload: bytes) -> str:
    """Deterministic identity key for an email message.

    The normalized ``Message-ID`` header is the key when present. Messages
    without one fall back to a digest of the composed payload, so identical
    missing-ID messages share a key while different payloads do not.
    """
    message_id = normalize_message_id(msg.get("Message-ID"))
    if message_id:
        return message_id
    return fallback_message_key(payload)


def build_message_record(
    source_key: str | None,
    msg: message.Message,
    *,
    mbox_name: str = "",
) -> SourceRecord:
    """Normalize one email message into the shared source representation.

    The record's payload is the composed plain text (exactly the bytes
    that are hashed), so semantically identical emails yield identical
    identity regardless of header ordering or encoding details. Provenance
    (message-id, sender, subject, date, mailbox) travels in metadata only.

    ``source_key`` derives from the normalized ``Message-ID`` when omitted,
    so discovery keys messages independently of mbox position.
    """
    subject = _decode_header_value(msg.get("Subject"))
    sender = _decode_header_value(msg.get("From"))
    to = _decode_header_value(msg.get("To"))
    message_id = msg.get("Message-ID", "")
    in_reply_to = msg.get("In-Reply-To", "")
    references = msg.get("References", "")

    body = _extract_text_body(msg)
    if not body:
        label = (
            source_key
            or normalize_message_id(message_id)
            or mbox_name
            or "an unnamed message"
        )
        raise EmptyMessageError(f"Email message {label!r} has no usable content")

    text = compose_message_text(msg, body=body)
    materialized = text.encode("utf-8")
    if source_key is None:
        source_key = message_source_key(msg, materialized)

    date_raw = msg.get("Date")
    created_at = _normalize_date(date_raw) if date_raw else ""

    metadata: dict[str, object] = {
        "filename": PurePosixPath(source_key).name,
        "mime_type": "message/rfc822",
        "subject": subject,
        "sender": sender,
        "to": to,
        "message_id": message_id,
    }
    if in_reply_to:
        metadata["in_reply_to"] = in_reply_to
    if references:
        metadata["references"] = references
    if mbox_name:
        metadata["mbox"] = mbox_name

    materialized = text.encode("utf-8")
    return SourceRecord(
        source_type=SOURCE_TYPE,
        source_key=source_key,
        content_hash=compute_content_hash(materialized),
        created_at=created_at,
        modified_at=created_at,
        payload=materialized,
        metadata=metadata,
    )


class EmailSourceAdapter:
    """Yields email messages from mbox files as source records.

    Discovery scans for ``*.mbox/mbox`` files inside the given directory
    (Gmail Takeout layout) and for flat, extensionless mailbox files living
    directly in the directory itself (Thunderbird layout), parses each
    message, and yields one record per message. Messages are keyed by their
    normalized ``Message-ID`` (with a payload digest fallback) so identity
    is stable across re-exports, mailbox files, and ordering.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE

    def discover(self) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for mbox_path in self._iter_mailbox_files():
            mbox_name = self._mailbox_label(mbox_path)
            try:
                mbox = mailbox.mbox(str(mbox_path))
            except Exception as exc:
                raise EmailParseError(f"Failed to open mbox file: {mbox_path}") from exc

            for msg in mbox:
                try:
                    records.append(build_message_record(None, msg, mbox_name=mbox_name))
                except EmptyMessageError:
                    continue

        records.sort(key=lambda record: record.source_key)
        return records

    def load_record(self, source_key: str) -> SourceRecord:
        """Load a single message by its message-id-derived source key.

        The key is matched against each message's computed identity, so a
        lookup is independent of which mbox file originally held the message.
        """
        if not source_key:
            raise UnsupportedMessageError(
                f"Not a valid email source key: {source_key!r}"
            )

        for mbox_path in self._iter_mailbox_files():
            mbox_name = self._mailbox_label(mbox_path)
            try:
                mbox = mailbox.mbox(str(mbox_path))
            except Exception as exc:
                raise EmailParseError(f"Failed to open mbox file: {mbox_path}") from exc

            for msg in mbox:
                try:
                    candidate = build_message_record(None, msg, mbox_name=mbox_name)
                except EmptyMessageError:
                    continue
                if candidate.source_key == source_key:
                    return candidate

        raise SourceNotFoundError(
            f"No email message with source key {source_key!r} in {self.directory}"
        )

    def _find_mbox_files(self) -> list[Path]:
        """Find all mbox files inside *.mbox directories."""
        mbox_files: list[Path] = []
        for candidate in sorted(self.directory.rglob("mbox")):
            if not candidate.is_file():
                continue
            if not self._is_contained(candidate.resolve()):
                continue
            # Must be inside a *.mbox directory
            if not candidate.parent.name.endswith(".mbox"):
                continue
            # Skip excluded directories
            if any(
                part in _EXCLUDED_DIRECTORY_NAMES
                for part in candidate.relative_to(self.directory).parts[:-1]
            ):
                continue
            mbox_files.append(candidate)
        return mbox_files

    def _find_thunderbird_files(self) -> list[Path]:
        """Find flat mailbox files living directly in the account directory.

        Thunderbird names each local mailbox after its folder (``INBOX``,
        ``Sent``) with no extension, in the account directory itself. Only
        regular top-level files count: anything with an extension, a dotfile,
        or a subdirectory is left alone, and candidates are validated to
        actually look like mbox so arbitrary extensionless files never become
        mailboxes.
        """
        if not self.directory.is_dir():
            return []
        mailbox_files: list[Path] = []
        for candidate in sorted(self.directory.iterdir()):
            if not candidate.is_file():
                continue
            if candidate.name.startswith("."):
                continue
            if candidate.suffix:
                continue
            if not _looks_like_mbox(candidate):
                continue
            mailbox_files.append(candidate)
        return mailbox_files

    def _iter_mailbox_files(self) -> list[Path]:
        """All discoverable mailbox files, deduplicated and sorted.

        Combines the conventional ``*.mbox/mbox`` layout with the flat
        Thunderbird layout so discovery and loading see the same set.
        """
        ordered: dict[Path, None] = {}
        for mbox_path in [*self._find_mbox_files(), *self._find_thunderbird_files()]:
            ordered.setdefault(mbox_path.resolve(), None)
        return sorted(ordered)

    def _mailbox_label(self, mbox_path: Path) -> str:
        """Human-friendly mailbox name for metadata and error messages."""
        if mbox_path.name == "mbox" and mbox_path.parent.name.endswith(".mbox"):
            return mbox_path.parent.name  # e.g., "INBOX.mbox"
        return mbox_path.name  # e.g., "INBOX"

    def _is_contained(self, resolved_path: Path) -> bool:
        try:
            resolved_path.relative_to(self.directory)
        except ValueError:
            return False
        return True
