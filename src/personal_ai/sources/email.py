"""Email source adapter: mbox files become normalized source records.

Google Takeout stores email as ``<Folder>.mbox/mbox`` files in RFC 4155
mbox format. Each message is delimited by a ``From `` line and carries
RFC 822 headers plus a MIME body. The adapter discovers ``mbox`` files
inside ``*.mbox/`` directories, parses each message, extracts provenance
headers, and composes a plain-text payload from the best available body
part.
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
from personal_ai.sources.models import SourceRecord

SOURCE_TYPE = "email"

_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".ruff_cache"}
)


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
    text/html is available, a basic tag stripping is applied.
    """
    if msg.is_multipart():
        # Prefer text/plain
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return _safe_decode(payload, part.get_content_charset()).strip()
        # Fallback to text/html
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    html = _safe_decode(payload, part.get_content_charset())
                    return _strip_html_tags(html).strip()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = _safe_decode(payload, msg.get_content_charset())
            if msg.get_content_type() == "text/html":
                return _strip_html_tags(text).strip()
            return text.strip()
    return ""


def _strip_html_tags(html: str) -> str:
    """Basic HTML tag stripping for fallback body extraction."""
    # Remove script and style content
    html = re.sub(
        r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE
    )
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compose_message_text(msg: message.Message) -> str:
    """Compose retrieval-ready plain text from an email message.

    The composition includes headers as metadata context followed by the
    message body. This gives the downstream extractor the full picture
    of who sent what, when, and about what.
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

    body = _extract_text_body(msg)
    if body:
        sections.append(body)

    return "\n\n".join(sections)


def build_message_record(
    source_key: str,
    msg: message.Message,
    *,
    mbox_name: str = "",
) -> SourceRecord:
    """Normalize one email message into the shared source representation.

    The record's payload is the composed plain text (exactly the bytes
    that are hashed), so semantically identical emails yield identical
    identity regardless of header ordering or encoding details. Provenance
    (message-id, sender, subject, date, mailbox) travels in metadata only.
    """
    subject = _decode_header_value(msg.get("Subject"))
    sender = _decode_header_value(msg.get("From"))
    to = _decode_header_value(msg.get("To"))
    message_id = msg.get("Message-ID", "")
    in_reply_to = msg.get("In-Reply-To", "")
    references = msg.get("References", "")

    body = _extract_text_body(msg)
    if not body:
        raise EmptyMessageError(f"Email message {source_key!r} has no usable content")

    text = compose_message_text(msg)

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


def _message_source_key(mbox_name: str, index: int) -> str:
    """Generate a stable source key for a message within an mbox file."""
    return f"{mbox_name}/message_{index:06d}"


class EmailSourceAdapter:
    """Yields email messages from mbox files as source records.

    Discovery scans for ``*.mbox/mbox`` files inside the given directory,
    parses each message, and yields one record per message. Messages are
    keyed by their mbox file name and message index for stability across
    re-exports.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE

    def discover(self) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for mbox_path in self._find_mbox_files():
            mbox_name = mbox_path.parent.name  # e.g., "INBOX.mbox"
            try:
                mbox = mailbox.mbox(str(mbox_path))
            except Exception as exc:
                raise EmailParseError(f"Failed to open mbox file: {mbox_path}") from exc

            for index, msg in enumerate(mbox):
                source_key = _message_source_key(mbox_name, index)
                try:
                    records.append(
                        build_message_record(source_key, msg, mbox_name=mbox_name)
                    )
                except EmptyMessageError:
                    continue

        records.sort(key=lambda record: record.source_key)
        return records

    def load_record(self, source_key: str) -> SourceRecord:
        """Load a single message by its mbox-relative key."""
        # Parse source_key to find mbox and message index
        parts = source_key.split("/")
        if len(parts) != 2 or not parts[1].startswith("message_"):
            raise UnsupportedMessageError(
                f"Not a valid email source key: {source_key!r}"
            )

        mbox_name = parts[0]
        try:
            index = int(parts[1].replace("message_", ""))
        except ValueError:
            raise UnsupportedMessageError(
                f"Not a valid email source key: {source_key!r}"
            )

        # Find the mbox file
        mbox_dir = self.directory / f"{mbox_name}"
        if not mbox_dir.is_dir():
            raise SourceNotFoundError(f"No such mbox directory: {mbox_name!r}")

        mbox_path = mbox_dir / "mbox"
        if not mbox_path.is_file():
            raise SourceNotFoundError(f"No such mbox file: {mbox_path!r}")

        try:
            mbox = mailbox.mbox(str(mbox_path))
        except Exception as exc:
            raise EmailParseError(f"Failed to open mbox file: {mbox_path}") from exc

        if index >= len(mbox):
            raise SourceNotFoundError(
                f"Message index {index} out of range in {mbox_name}"
            )

        msg = mbox[index]
        return build_message_record(source_key, msg, mbox_name=mbox_name)

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

    def _is_contained(self, resolved_path: Path) -> bool:
        try:
            resolved_path.relative_to(self.directory)
        except ValueError:
            return False
        return True
