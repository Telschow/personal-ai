"""Financial CSV export source adapter.

The financial adapter canonicalizes fixed-format CSV exports of the user's
financial accounts into a deterministic, privacy-reduced searchable payload
that flows through the generic ingestion pipeline exactly like any other text
source.

The real corpus this adapter was written against contains four schemas:

* ``bank`` - German Girokonto statement exports (semicolon-delimited, UTF-8
  with BOM, one file per year, preamble lines before the column header).
* ``card`` - card (Revolut-style) statement exports (comma-delimited).
* ``investment_transaction`` - brokerage (Trade Republic-style) transaction
  exports (comma-delimited, one row per executed order).
* ``portfolio`` - aggregate portfolio snapshot exports (Degiro-style,
  comma-delimited, undated).

Record identity is content-derived and independent of filename, file
modification time, BOM presence, line endings, CSV delimiter, column order,
and cosmetic whitespace. It is also independent of the bank/export preamble
lines (account type, IBAN, balance as-of), which are dropped entirely.

Exact duplicate rows inside a single export are collapsed deterministically
(keeping the first occurrence in input order). Brokerage transactions are
deduplicated on their unique ``transaction_id``; rows that merely share an
amount, date, or description but differ in another field are never discarded.
Undated portfolio snapshots are rendered with an explicit
``snapshot_date: unknown`` marker and never receive a fabricated date.

Privacy: canonical searchable text *never* contains stable account identifiers
such as IBAN, counterparty IBAN, Glaeubiger-ID, Mandatsreferenz, or
Kundenreferenz / payment references. Those columns are dropped outright,
never masked. Where an export embeds an identifier inside a free-text column
(for example a brokerage ``description`` such as ``Incoming transfer from ...
DE12...``), the embedded identifier is removed by a word-boundary pattern; the
surrounding words remain searchable. This covers both ISO 11649 SEPA creditor
references (``RF`` check-digit form) and the free-format ``REF``-style
payment-reference codes (three uppercase letters, a dash, ``REF``, then
digits) that appear embedded in a bank export's purpose field.
Counterparty names, purposes/descriptions, dates, amounts, status, type,
product, ticker/ISIN, quantity, price, value, and ``transaction_id`` (as
provenance only) remain searchable.

No network, model, or external service is involved: canonicalization is pure
deterministic local parsing, so the CLI ingests ``financial`` sources without
any model requirement (mirroring the ``email`` source path).
"""

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from personal_ai.sources.base import SourceError
from personal_ai.sources.models import SourceRecord

SOURCE_TYPE = "financial"
CANONICALIZATION_VERSION = "v1"
SUPPORTED_EXTENSIONS = frozenset({".csv"})

_TEXT = "text"
_AMOUNT = "amount"
_DATE = "date"
_DATETIME = "datetime"
_REDACT = "redact"

# IBAN-like identifier embedded in free-text columns (``description``,
# ``purpose``, ...) is dropped entirely, never masked. Word boundaries keep
# ordinary words and short codes (transaction ids, ``XXXX`` symbols) intact.
_IBAN_PATTERN = re.compile(r"(?<![A-Z0-9])[A-Z]{2}[0-9]{2}[A-Z0-9]{12,29}(?![A-Z0-9])")

# ISO 11649 SEPA creditor reference (``RF`` + two check digits + up to 21
# alphanumerics). At a word boundary and always starting with the literal
# ``RF`` marker followed by digits, this never collides with ordinary words or
# legitimate transaction descriptions.
_CREDITOR_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Z0-9])RF[0-9]{2}[A-Z0-9]{1,21}(?![A-Z0-9])"
)

# Free-format REF-style payment reference embedded in a bank export's purpose
# field. Observed shape: three uppercase letters, a dash, the ``REF`` marker,
# then a solid run of digits (e.g. ``ABC-REF1234567890``). The ``-REF`` marker
# preceded by an uppercase run and followed directly by a long digit run is
# unambiguous, so the surrounding description remains searchable while the
# reference code is dropped. ``REF`` must be the literal marker (so
# ``ABC-DEF1234...`` and ordinary words are untouched) and must be followed by
# digits (so tokens with further letters after ``REF`` are not references).
_REF_STYLE_PATTERN = re.compile(r"(?<![A-Z0-9])[A-Z]{3}-REF[0-9]{10,}(?![A-Z0-9])")

_SECTIONS = {
    "bank": "TRANSACTION",
    "card": "CARD_PAYMENT",
    "investment_transaction": "TRADE",
    "portfolio": "POSITION",
}

_CURRENCY_DEFAULTS = {"bank": "EUR", "portfolio": "EUR"}

_PRIMARY_DATE = {"bank": "date", "card": "date", "investment_transaction": "date"}

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d.%m.%y")


class FinancialSourceError(SourceError):
    """Base class for errors raised by the financial source adapter."""


class FinancialParseError(FinancialSourceError):
    """Raised when a financial CSV cannot be parsed into known columns."""


class UnknownFinancialSchemaError(FinancialSourceError):
    """Raised when a CSV does not match any known financial export schema."""


def _normalize_header(field: str) -> str:
    """Normalize a header field for deterministic schema fingerprinting."""
    folded = field.replace("\u20ac", "eur").replace("\u00a0", " ")
    return " ".join(folded.lower().split())


def _normalize_text(value: str) -> str:
    """Collapse whitespace and newlines, then drop embedded identifiers.

    Embedded payment references (SEPA creditor references and the REF-style
    codes) and IBANs are removed; the surrounding words are preserved.
    """
    collapsed = " ".join(value.split())
    collapsed = _CREDITOR_REFERENCE_PATTERN.sub("", collapsed)
    collapsed = _REF_STYLE_PATTERN.sub("", collapsed)
    return _IBAN_PATTERN.sub("", collapsed)


_EMPTY_AMOUNT_MARKERS = frozenset({"-", "+", "\u2013", "\u2212", ".", "--"})


def _normalize_amount(value: str) -> str | None:
    """Normalize a German or US decimal number to a fixed decimal string.

    Thousands separators are not semantically significant: ``1.234,56`` and
    ``1,234.56`` both normalize to ``1234.56``, and the decimal comma forms
    ``12,50`` and ``12.50`` both normalize to ``12.50``. The fixed decimal
    form is produced with :class:`decimal.Decimal` so no binary floating
    point ever appears; the digit string you type is preserved verbatim
    (``Decimal('12.50')`` stays ``12.50``).
    """
    cleaned = value.strip().replace("\u00a0", " ")
    cleaned = cleaned.replace(" ", "").replace("\u2212", "-").replace("\u2013", "-")
    if cleaned in _EMPTY_AMOUNT_MARKERS:
        return None
    sign = ""
    if cleaned.startswith("-"):
        sign, cleaned = "-", cleaned[1:]
    elif cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rindex(".") < cleaned.rindex(","):
            # German: 1.234,56
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US: 1,234.56
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return str(Decimal(sign + cleaned))
    except InvalidOperation:
        # A currency-code-like cell (for example ``EUR`` in a value column)
        # is preserved verbatim as a searchable token rather than rejected.
        return _normalize_text(value)


_DATE_PATTERN = re.compile(
    r"^(?P<right>\d{4})-(?P<rm>\d{1,2})-(?P<rd>\d{1,2})$"
    r"|^(?P<sys>\d{4})/(?P<sm>\d{1,2})/(?P<sd>\d{1,2})$"
    r"|^(?P<ge>\d{1,2})\.(?P<gm>\d{1,2})\.(?P<gy>\d{2}|\d{4})$"
)


def _normalize_date(value: str) -> str:
    """Normalize a date cell to ``YYYY-MM-DD``.

    Day-first slash dates such as ``13/02/2024`` are rejected: the export
    formats in this corpus use either dot-separated German dates or ISO
    dates, and guessing the day/month meaning of a slash date can silently
    produce the wrong calendar day.
    """
    cleaned = value.strip()
    match = _DATE_PATTERN.match(cleaned)
    if match is None:
        raise FinancialParseError(
            f"Invalid date {value!r}: not a recognized date format"
        )
    parts = match.groupdict()
    if parts["right"] is not None:
        year, month, day = int(parts["right"]), int(parts["rm"]), int(parts["rd"])
    elif parts["sys"] is not None:
        year, month, day = int(parts["sys"]), int(parts["sm"]), int(parts["sd"])
    else:
        day, month = int(parts["ge"]), int(parts["gm"])
        two_digit = int(parts["gy"])
        year = two_digit + (2000 if two_digit < 100 else 0)
    try:
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise FinancialParseError(
            f"Invalid date {value!r}: no such calendar day"
        ) from exc


def _normalize_datetime(value: str) -> str:
    """Normalize an ISO-like timestamp to a UTC string with fixed precision."""
    cleaned = value.strip()
    if cleaned.endswith(("Z", "z")):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise FinancialParseError(
            f"Invalid timestamp {value!r}: not an ISO-like date-time"
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
        return parsed.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return parsed.strftime("%Y-%m-%dT%H:%M:%S")


@dataclass(frozen=True, slots=True)
class _ColumnSpec:
    label: str
    kind: str


@dataclass(frozen=True, slots=True)
class _Schema:
    kind: str
    section: str
    specs: dict[str, _ColumnSpec] = field(compare=False)
    signature: tuple[str, ...]
    currency_default: str | None = None
    primary_date: str | None = None
    identity: str | None = None


_SCHEMAS: dict[str, _Schema] = {}


def _register_schema(
    kind: str,
    section: str,
    columns: list[tuple[str, str, str]],
    *,
    currency_default: str | None = None,
    primary_date: str | None = None,
    identity: str | None = None,
) -> None:
    specs: dict[str, _ColumnSpec] = {}
    signature: list[str] = []
    for header, label, column_kind in columns:
        normalized = _normalize_header(header)
        specs[normalized] = _ColumnSpec(label, column_kind)
        signature.append(normalized)
    _SCHEMAS[kind] = _Schema(
        kind=kind,
        section=section,
        specs=specs,
        signature=tuple(sorted(signature)),
        currency_default=currency_default,
        primary_date=primary_date,
        identity=identity,
    )


_BANK_COLUMNS = [
    ("Buchungsdatum", "date", _DATE),
    ("Wertstellung", "value_date", _DATE),
    ("Status", "status", _TEXT),
    ("Zahlungspflichtige*r", "payer", _TEXT),
    ("Zahlungsempfänger*in", "payee", _TEXT),
    ("Verwendungszweck", "purpose", _TEXT),
    ("Umsatztyp", "booking_type", _TEXT),
    ("IBAN", "", _REDACT),
    ("Betrag (€)", "amount", _AMOUNT),
    ("Gläubiger-ID", "", _REDACT),
    ("Mandatsreferenz", "", _REDACT),
    ("Kundenreferenz", "", _REDACT),
]

_CARD_COLUMNS = [
    ("Type", "type", _TEXT),
    ("Product", "product", _TEXT),
    ("Started Date", "date", _DATETIME),
    ("Completed Date", "completed_date", _DATETIME),
    ("Description", "description", _TEXT),
    ("Amount", "amount", _AMOUNT),
    ("Fee", "fee", _AMOUNT),
    ("Currency", "currency", _TEXT),
    ("State", "state", _TEXT),
    ("Balance", "balance", _AMOUNT),
]

_INVESTMENT_COLUMNS = [
    ("datetime", "datetime", _DATETIME),
    ("date", "date", _DATE),
    ("account_type", "account_type", _TEXT),
    ("category", "category", _TEXT),
    ("type", "type", _TEXT),
    ("asset_class", "asset_class", _TEXT),
    ("name", "name", _TEXT),
    ("symbol", "symbol", _TEXT),
    ("shares", "shares", _AMOUNT),
    ("price", "price", _AMOUNT),
    ("amount", "amount", _AMOUNT),
    ("fee", "fee", _AMOUNT),
    ("tax", "tax", _AMOUNT),
    ("currency", "currency", _TEXT),
    ("original_amount", "original_amount", _AMOUNT),
    ("original_currency", "original_currency", _TEXT),
    ("fx_rate", "fx_rate", _AMOUNT),
    ("description", "description", _TEXT),
    ("transaction_id", "transaction_id", _TEXT),
    ("counterparty_name", "counterparty_name", _TEXT),
    ("counterparty_iban", "", _REDACT),
    ("payment_reference", "", _REDACT),
    ("mcc_code", "mcc_code", _TEXT),
]

_PORTFOLIO_COLUMNS = [
    ("Produkt", "product", _TEXT),
    ("Symbol/ISIN", "isin", _TEXT),
    ("Anzahl", "quantity", _AMOUNT),
    ("Schlußkurs", "price", _AMOUNT),
    ("Wert", "value", _AMOUNT),
    ("Wert in EUR", "value_eur", _AMOUNT),
]

_register_schema(
    "bank", "TRANSACTION", _BANK_COLUMNS, currency_default="EUR", primary_date="date"
)
_register_schema("card", "CARD_PAYMENT", _CARD_COLUMNS, primary_date="date")
_register_schema(
    "investment_transaction",
    "TRADE",
    _INVESTMENT_COLUMNS,
    primary_date="date",
    identity="transaction_id",
)
_register_schema(
    "portfolio",
    "POSITION",
    _PORTFOLIO_COLUMNS,
    currency_default="EUR",
)

_SCHEMAS_BY_SIGNATURE: dict[tuple[str, ...], _Schema] = {
    schema.signature: schema for schema in _SCHEMAS.values()
}


@dataclass(frozen=True, slots=True)
class CanonicalFinancialDocument:
    """Canonicalized, deterministic, privacy-reduced form of one CSV export."""

    kind: str
    source_key: str
    content_hash: str
    created_at: str
    modified_at: str
    metadata: dict[str, object]
    canonical_text: str


def _parse_candidates(text: str) -> list[tuple[str, object | None]]:
    """Return deterministic (delimiter, dialect) parse candidates."""
    candidates: list[tuple[str, object | None]] = []
    seen: set[str] = set()
    try:
        dialect = csv.Sniffer().sniff(text[:65536])
        # The sniffer is only advisory: accept its conclusion when it lands
        # on a delimiter this adapter understands, otherwise rely on the
        # deterministic candidate list below.
        if dialect.delimiter in (",", ";", "\t"):
            candidates.append((dialect.delimiter, dialect))
            seen.add(dialect.delimiter)
    except csv.Error:
        pass
    for delim in (",", ";", "\t"):
        if delim not in seen:
            candidates.append((delim, None))
            seen.add(delim)
    return candidates


def _parse_records(text: str) -> list[tuple[str, object | None, list[list[str]]]]:
    """Parse ``text`` under each candidate dialect, preserving order."""
    parsed: list[tuple[str, object | None, list[list[str]]]] = []
    for delim, dialect in _parse_candidates(text):
        if dialect is None:
            records = list(
                csv.reader(
                    io.StringIO(text),
                    delimiter=delim,
                    quotechar='"',
                    doublequote=True,
                    skipinitialspace=True,
                )
            )
        else:
            records = list(csv.reader(io.StringIO(text), dialect))
        parsed.append((delim, dialect, records))
    return parsed


def _locate_schema(
    records: list[list[str]],
) -> tuple[int, _Schema, list[str]] | None:
    """Find the first record whose normalized header matches a known schema."""
    for index, record in enumerate(records):
        normalized = [_normalize_header(field) for field in record if field.strip()]
        if not normalized:
            continue
        schema = _SCHEMAS_BY_SIGNATURE.get(tuple(sorted(normalized)))
        if schema is not None:
            return index, schema, normalized
    return None


def _dominant_currency(rows: list[list[tuple[str, str]]]) -> str | None:
    counts: Counter[str] = Counter()
    for items in rows:
        for label, value in items:
            if label == "currency":
                counts[value] += 1
    if not counts:
        return None
    highest = max(counts.values())
    best = min(value for value, count in counts.items() if count == highest)
    return best


def _bucket_for_date_range(date_min: str | None, date_max: str | None) -> str:
    if date_min is None:
        return "undated"
    year_min = date_min[:4]
    year_max = (date_max or date_min)[:4]
    return year_min if year_min == year_max else f"{year_min}-{year_max}"


def _deduplicate_rows(
    rows: list[list[tuple[str, str]]], identity_label: str | None
) -> list[list[tuple[str, str]]]:
    """Collapse exact duplicate rows deterministically.

    A schema may nominate a unique identity anchor (``transaction_id`` for
    brokerage transactions) which takes precedence; rows without a value for
    that anchor fall back to the full canonical row fingerprint. The first
    occurrence in input order is kept and output order is preserved. Rows
    that merely share an amount, date, or description but differ in some
    other emitted field have distinct fingerprints and are all kept.
    """
    seen: set[object] = set()
    kept: list[list[tuple[str, str]]] = []
    for items in rows:
        if identity_label is not None:
            anchor = next(
                (value for label, value in items if label == identity_label), None
            )
        else:
            anchor = None
        key: object = tuple(items) if anchor is None else anchor
        if key in seen:
            continue
        seen.add(key)
        kept.append(items)
    return kept


def _canonicalize(records: list[list[str]], location) -> CanonicalFinancialDocument:
    index, schema, _ = location
    header = records[index]
    colmap: dict[int, _ColumnSpec] = {}
    for position, header_field in enumerate(header):
        normalized = _normalize_header(header_field)
        if not normalized:
            continue
        spec = schema.specs.get(normalized)
        if spec is None:
            raise UnknownFinancialSchemaError(
                f"Header {header_field!r} in a recognized {schema.kind!r} export "
                "has no canonical mapping"
            )
        colmap[position] = spec

    rows: list[list[tuple[str, str]]] = []
    for record in records[index + 1 :]:
        if not any(field.strip() for field in record):
            continue
        if len(record) != len(header):
            raise FinancialParseError(
                f"Financial export row has {len(record)} fields; expected {len(header)}"
            )
        item_map: dict[str, str] = {}
        for position, cell in enumerate(record):
            spec = colmap.get(position)
            if spec is None:
                continue
            label, kind = spec.label, spec.kind
            stripped = cell.strip()
            if not stripped or kind == _REDACT:
                continue
            if kind == _TEXT:
                value = _normalize_text(cell)
            elif kind == _AMOUNT:
                value = _normalize_amount(cell)
                if value is None:
                    continue
            elif kind == _DATE:
                value = _normalize_date(cell)
            else:
                value = _normalize_datetime(cell)
            item_map[label] = value
        ordered = [
            (spec.label, item_map[label])
            for spec in schema.specs.values()
            if (label := spec.label) in item_map
        ]
        if ordered:
            rows.append(ordered)

    rows = _deduplicate_rows(rows, schema.identity)

    row_count = len(rows)

    dates: list[str] = []
    primary_label = schema.primary_date
    for items in rows:
        for label, value in items:
            if label == primary_label:
                dates.append(value[:10])
                break
    date_min = min(dates) if dates else None
    date_max = max(dates) if dates else None

    currency = schema.currency_default
    if currency is None and schema.kind in ("card", "investment_transaction"):
        currency = _dominant_currency(rows)
    if currency is None and schema.kind == "investment_transaction":
        currency = "EUR"

    bucket = _bucket_for_date_range(date_min, date_max)

    header_lines = [
        "FINANCIAL RECORD",
        f"kind: {schema.kind}",
        f"canonicalization_version: {CANONICALIZATION_VERSION}",
    ]
    if schema.kind == "portfolio":
        header_lines.append("snapshot_date: unknown")
    if currency:
        header_lines.append(f"currency: {currency}")
    header_lines.append(f"row_count: {row_count}")
    if date_min:
        header_lines.append(f"date_min: {date_min}")
        header_lines.append(f"date_max: {date_max}")

    blocks = ["\n".join(header_lines)]
    for items in rows:
        block = [schema.section]
        block.extend(f"{label}: {value}" for label, value in items)
        blocks.append("\n".join(block))
    canonical_text = "\n\n".join(blocks) + "\n"
    payload = canonical_text.encode("utf-8")
    content_hash = hashlib.sha256(payload).hexdigest()

    metadata: dict[str, object] = {
        "mime_type": "text/csv",
        "provider": schema.kind,
        "financial_kind": schema.kind,
        "schema_fingerprint": hashlib.sha256(
            "\x00".join(schema.signature).encode("utf-8")
        ).hexdigest(),
        "source_format": "csv",
        "canonicalization_version": CANONICALIZATION_VERSION,
        "row_count": row_count,
    }
    if schema.kind == "portfolio":
        metadata["snapshot_date"] = "unknown"
    if currency:
        metadata["currency"] = currency
    if date_min:
        metadata["date_min"] = date_min
        metadata["date_max"] = date_max

    source_key = f"financial/{schema.kind}/{bucket}/{content_hash[:12]}"
    return CanonicalFinancialDocument(
        kind=schema.kind,
        source_key=source_key,
        content_hash=content_hash,
        created_at="",
        modified_at="",
        metadata=metadata,
        canonical_text=canonical_text,
    )


def canonicalize_csv(
    raw: bytes, *, filename: str | None = None
) -> CanonicalFinancialDocument:
    """Canonicalize one CSV export payload (deterministic, no I/O)."""
    if not raw:
        raise FinancialParseError("Empty financial CSV payload")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FinancialParseError("Financial CSV is not valid UTF-8") from exc

    for _delimiter, _dialect, records in _parse_records(text):
        location = _locate_schema(records)
        if location is None:
            continue
        document = _canonicalize(records, location)
        if filename is not None:
            document.metadata["filename"] = filename
        return document

    raise UnknownFinancialSchemaError(
        "Could not identify a known financial CSV export schema"
    )


class FinancialSourceAdapter:
    """Discover and load financial CSV exports inside a workspace directory.

    Discovery stays entirely within the given directory and considers only
    ``.csv`` files. Any CSV that does not match a known financial schema
    raises a ``SourceError`` so a mislabelled export is surfaced instead of
    being silently ingested as-is.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE

    def discover(self) -> list[SourceRecord]:
        if not self.directory.is_dir():
            raise FinancialSourceError(
                f"Source path is not a directory: {self.directory}"
            )
        files = sorted(
            (
                path
                for path in self.directory.rglob("*")
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
            ),
            key=lambda path: path.relative_to(self.directory).as_posix(),
        )
        records = [self._build_record(path) for path in files]
        records.sort(key=lambda record: record.source_key)
        return records

    def load_record(self, source_key: str) -> SourceRecord:
        for record in self.discover():
            if record.source_key == source_key:
                return record
        raise FinancialSourceError(f"No such financial source record: {source_key!r}")

    def _build_record(self, path: Path) -> SourceRecord:
        document = canonicalize_csv(path.read_bytes())
        metadata = dict(document.metadata)
        metadata["filename"] = path.name
        return SourceRecord(
            source_type=SOURCE_TYPE,
            source_key=document.source_key,
            content_hash=document.content_hash,
            created_at="",
            modified_at="",
            payload=document.canonical_text.encode("utf-8"),
            metadata=metadata,
        )


__all__ = [
    "CANONICALIZATION_VERSION",
    "SOURCE_TYPE",
    "FinancialParseError",
    "FinancialSourceAdapter",
    "FinancialSourceError",
    "UnknownFinancialSchemaError",
    "canonicalize_csv",
]
