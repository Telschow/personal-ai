"""Tests for the financial CSV source adapter.

The adapter canonicalizes fixed-format financial CSV exports into a
deterministic, privacy-reduced searchable payload. These tests use synthetic
fixtures matching the audited schemas of the real corpus; they never touch
real personal data.
"""

import csv
import io

import pytest

from personal_ai.sources.financial import (
    CANONICALIZATION_VERSION,
    SOURCE_TYPE,
    FinancialParseError,
    FinancialSourceAdapter,
    UnknownFinancialSchemaError,
    canonicalize_csv,
)
from personal_ai.sources.models import SourceRecord

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

BANK_ROWS = [
    [
        "02.01.2024",
        "03.01.2024",
        "Gebucht",
        "Anna Beispiel",
        "Supermarkt GmbH",
        "Lebensmittel",
        "Umsatz",
        "DE02120300000000202051",
        "-42,50",
        "DE98ZZZ09999999999",
        "DE987654321",
        "REF-KND-2024",
    ],
    [
        "31.01.2024",
        "31.01.2024",
        "Gebucht",
        "Anna Beispiel",
        "Arbeitgeber GmbH",
        "Gehalt Januar",
        "Eingang",
        "DE02120300000000202051",
        "2500,00",
        "",
        "",
        "",
    ],
]

BANK_PREAMBLE = [
    'Girokonto;"DE16120300001085646543"',
    "Zeitraum:;2024",
    "Kontostand vom 31.12.2024:;1234,56",
]

CARD_COLUMNS = [
    "Type",
    "Product",
    "Started Date",
    "Completed Date",
    "Description",
    "Amount",
    "Fee",
    "Currency",
    "State",
    "Balance",
]

CARD_ROWS = [
    [
        "CARD_PAYMENT",
        "Current",
        "2018-10-13T10:00:00.000Z",
        "2018-10-13T10:00:05.000Z",
        "Coffee Shop",
        "-3.50",
        "0",
        "EUR",
        "COMPLETED",
        "100.00",
    ],
]

BROKERAGE_COLUMNS = [
    "datetime",
    "date",
    "account_type",
    "category",
    "type",
    "asset_class",
    "name",
    "symbol",
    "shares",
    "price",
    "amount",
    "fee",
    "tax",
    "currency",
    "original_amount",
    "original_currency",
    "fx_rate",
    "description",
    "transaction_id",
    "counterparty_name",
    "counterparty_iban",
    "payment_reference",
    "mcc_code",
]

BROKERAGE_ROWS = [
    [
        "2022-04-04T14:10:00.000Z",
        "2022-04-04",
        "depot",
        "REIT",
        "BUY",
        "Equity",
        "Some ETF",
        "XXXX",
        "2",
        "123.45",
        "-246.90",
        "0",
        "0",
        "EUR",
        "-246.90",
        "EUR",
        "1.0000",
        "Kauf Some ETF",
        "TR-2022-0001",
        "Broker X",
        "DE00000000000000000000",
        "REF-2022-0001",
        "5311",
    ],
]

PORTFOLIO_COLUMNS = [
    "Produkt",
    "Symbol/ISIN",
    "Anzahl",
    "Schlußkurs",
    "Wert",
    "",
    "Wert in EUR",
]

PORTFOLIO_ROWS = [
    ["Apple Inc.", "US0378331005", "10", "188.42", "1884.20", "", "1884.20"],
]


def bank_csv_builder(rows=BANK_ROWS, columns=BANK_COLUMNS, preamble=BANK_PREAMBLE):
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    for line in preamble:
        output.write(f"{line}\n")
    if preamble:
        output.write("\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def _render(columns, rows, delimiter, line_ending="\n"):
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter, lineterminator=line_ending)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def write_file(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def build_directory(tmp_path, files=None):
    directory = tmp_path / "finance"
    directory.mkdir()
    for name, content in (files or {}).items():
        write_file(directory, name, content)
    return directory


class TestBankCanonicalization:
    def test_recognizes_bank_schema_and_kind(self) -> None:
        raw = bank_csv_builder().encode()
        result = canonicalize_csv(raw, filename="Umsatzliste.csv")
        assert result.kind == "bank"
        assert result.source_key.startswith("financial/bank/")
        assert result.metadata["mime_type"] == "text/csv"
        assert result.metadata["financial_kind"] == "bank"
        assert result.metadata["canonicalization_version"] == CANONICALIZATION_VERSION
        assert result.metadata["source_format"] == "csv"
        assert result.metadata["currency"] == "EUR"
        assert result.metadata["row_count"] == 2
        assert result.metadata["date_min"] == "2024-01-02"
        assert result.metadata["date_max"] == "2024-01-31"
        assert result.created_at == ""
        assert result.modified_at == ""

    def test_preamble_is_excluded_from_canonical_content(self) -> None:
        result = canonicalize_csv(bank_csv_builder().encode(), filename="u.csv")
        assert "Girokonto" not in result.canonical_text
        assert "DE16120300001085646543" not in result.canonical_text
        assert "Zeitraum" not in result.canonical_text
        assert "Kontostand" not in result.canonical_text

    def test_deterministic_canonical_text_components(self) -> None:
        result = canonicalize_csv(bank_csv_builder().encode())
        text = result.canonical_text
        assert "FINANCIAL RECORD" in text
        assert "kind: bank" in text
        assert "canonicalization_version: v1" in text
        assert "date: 2024-01-02" in text
        assert "value_date: 2024-01-03" in text
        assert "status: Gebucht" in text
        assert "payer: Anna Beispiel" in text
        assert "payee: Supermarkt GmbH" in text
        assert "purpose: Lebensmittel" in text
        assert "booking_type: Umsatz" in text
        assert "amount: -42.50" in text
        assert "amount: 2500.00" in text

    def test_row_count_matches_emitted_rows_not_preamble(self) -> None:
        result = canonicalize_csv(bank_csv_builder().encode())
        assert result.metadata["row_count"] == 2
        assert result.canonical_text.count("TRANSACTION") == 2

    def test_exact_duplicate_rows_are_emitted_once(self) -> None:
        rows = BANK_ROWS + [BANK_ROWS[0]]
        result = canonicalize_csv(bank_csv_builder(rows=rows).encode())
        assert result.metadata["row_count"] == 2
        assert result.canonical_text.count("TRANSACTION") == 2
        assert result.canonical_text.count("amount: -42.50") == 1

    def test_genuinely_different_rows_are_never_collapsed(self) -> None:
        first = [x if i != 5 else "Lebensmittel" for i, x in enumerate(BANK_ROWS[0])]
        second = [x if i != 5 else "Gehalt Januar" for i, x in enumerate(BANK_ROWS[0])]
        result = canonicalize_csv(bank_csv_builder(rows=[first, second]).encode())
        assert result.metadata["row_count"] == 2
        assert result.canonical_text.count("TRANSACTION") == 2
        assert "purpose: Lebensmittel" in result.canonical_text
        assert "purpose: Gehalt Januar" in result.canonical_text

    def test_empty_text_cell_is_omitted_not_fabricated(self) -> None:
        row = ["" if i == 4 else x for i, x in enumerate(BANK_ROWS[0])]
        result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert "payee:" not in result.canonical_text
        assert "payer: Anna Beispiel" in result.canonical_text

    def test_empty_amount_cell_is_omitted(self) -> None:
        row = ["" if i == 8 else x for i, x in enumerate(BANK_ROWS[0])]
        result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert result.metadata["row_count"] == 1
        assert "amount:" not in result.canonical_text

    def test_duplicate_stable_identity_across_variations(self) -> None:
        base = canonicalize_csv(bank_csv_builder().encode(), filename="a.csv")
        variations = [
            (["a.csv"], b"\xef\xbb\xbf" + bank_csv_builder().encode()),
            (
                ["b.csv"],
                (
                    'Girokonto;"DE16120300001085646543"\r\nZeitraum:;2024\r\n'
                    + _render(BANK_COLUMNS, BANK_ROWS, ",", line_ending="\r\n")
                ).encode("utf-8"),
            ),
            (
                ["c.csv"],
                _render(
                    [
                        "Verwendungszweck",
                        "Status",
                        "Wertstellung",
                        "Betrag (€)",
                        "Zahlungsempfänger*in",
                        "Buchungsdatum",
                        "Zahlungspflichtige*r",
                        "Umsatztyp",
                        "IBAN",
                        "Gläubiger-ID",
                        "Mandatsreferenz",
                        "Kundenreferenz",
                    ],
                    [
                        [
                            r[5],
                            r[2],
                            r[1],
                            r[8],
                            r[4],
                            r[0],
                            r[3],
                            r[6],
                            r[7],
                            r[9],
                            r[10],
                            r[11],
                        ]
                        for r in BANK_ROWS
                    ],
                    ";",
                ).encode("utf-8"),
            ),
        ]
        expected_key = base.source_key
        expected_hash = base.content_hash
        expected_text = base.canonical_text
        for name, payload in variations:
            result = canonicalize_csv(payload, filename=name)
            assert result.source_key == expected_key, name
            assert result.content_hash == expected_hash, name
            assert result.canonical_text == expected_text, name

    def test_whitespace_and_header_padding_independent(self) -> None:
        columns = [" " + c + " " for c in BANK_COLUMNS]
        rows = [[f"  {cell}  " for cell in row] for row in BANK_ROWS]
        base = canonicalize_csv(bank_csv_builder().encode())
        padded = canonicalize_csv(bank_csv_builder(rows=rows, columns=columns).encode())
        assert padded.source_key == base.source_key
        assert padded.content_hash == base.content_hash

    def test_bom_crlf_and_filename_do_not_change_identity(self) -> None:
        base = canonicalize_csv(bank_csv_builder().encode(), filename="one.csv")
        bom = b"\xef\xbb\xbf" + bank_csv_builder().encode()
        renamed = canonicalize_csv(bom, filename="totally_different_name.csv")
        assert renamed.source_key == base.source_key
        assert renamed.content_hash == base.content_hash
        assert renamed.metadata["filename"] == "totally_different_name.csv"

    def test_materially_different_content_changes_identity(self) -> None:
        base = canonicalize_csv(bank_csv_builder().encode())
        row = [x if i != 8 else "-99,00" for i, x in enumerate(BANK_ROWS[0])]
        altered = canonicalize_csv(bank_csv_builder(rows=[row, BANK_ROWS[1]]).encode())
        assert altered.source_key != base.source_key
        assert altered.content_hash != base.content_hash
        assert "amount: -99.00" in altered.canonical_text


class TestAmountAndDateNormalization:
    def test_decimal_variants_collapse_to_fixed_representation(self) -> None:
        def run(rows):
            return canonicalize_csv(bank_csv_builder(rows=rows).encode())

        a = run([[x if i != 8 else "12,50" for i, x in enumerate(BANK_ROWS[0])]])
        b = run([[x if i != 8 else "12.50" for i, x in enumerate(BANK_ROWS[0])]])
        c = run([[x if i != 8 else "+12,50" for i, x in enumerate(BANK_ROWS[0])]])
        d = run([[x if i != 8 else "1.234,56" for i, x in enumerate(BANK_ROWS[0])]])
        e = run([[x if i != 8 else "-42,00" for i, x in enumerate(BANK_ROWS[0])]])
        assert a.source_key == b.source_key == c.source_key
        assert "amount: 12.50" in a.canonical_text
        assert "amount: 1234.56" in d.canonical_text
        assert "amount: -42.00" in e.canonical_text

    def test_non_numeric_amount_token_is_preserved_not_rejected(self) -> None:
        row = list(BANK_ROWS[0])
        row[8] = "EUR"
        result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert "amount: EUR" in result.canonical_text

    def test_various_date_formats_normalize(self) -> None:
        variants = [
            "02.01.2024",
            "2.1.2024",
            "2024-01-02",
            "2024/01/02",
        ]
        for variant in variants:
            row = [x if i != 0 else variant for i, x in enumerate(BANK_ROWS[0])]
            result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
            assert "date: 2024-01-02" in result.canonical_text, variant
            assert result.metadata["date_min"] == "2024-01-02", variant

    def test_invalid_date_raises(self) -> None:
        row = list(BANK_ROWS[0])
        row[0] = "31.02.2024"
        with pytest.raises(FinancialParseError):
            canonicalize_csv(bank_csv_builder(rows=[row]).encode())

    def test_ambiguous_dayfirst_slash_date_is_rejected(self) -> None:
        row = list(BANK_ROWS[0])
        row[0] = "13/02/2024"
        with pytest.raises(FinancialParseError):
            canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        row = list(BANK_ROWS[0])
        row[0] = "02/01/2024"
        with pytest.raises(FinancialParseError):
            canonicalize_csv(bank_csv_builder(rows=[row]).encode())


class TestRedaction:
    def test_bank_sensitive_fields_never_canonicalized(self) -> None:
        result = canonicalize_csv(bank_csv_builder().encode())
        text = result.canonical_text
        assert "DE02120300000000202051" not in text
        assert "DE98ZZZ09999999999" not in text
        assert "DE987654321" not in text
        assert "REF-KND-2024" not in text
        assert "iban" not in text
        assert "gläubiger" not in text
        assert "mandatsreferenz" not in text
        assert "kundenreferenz" not in text

    def test_brokerage_iban_and_payment_reference_never_canonicalized(self) -> None:
        raw = _render(BROKERAGE_COLUMNS, BROKERAGE_ROWS, ",").encode()
        result = canonicalize_csv(raw)
        text = result.canonical_text
        assert "DE00000000000000000000" not in text
        assert "REF-2022-0001" not in text
        assert "counterparty_iban" not in text
        assert "payment_reference" not in text

    def test_sensitive_values_not_exposed_in_metadata(self) -> None:
        result = canonicalize_csv(bank_csv_builder().encode())
        assert "DE02120300000000202051" not in repr(result.metadata)

    def test_safe_fields_remain_searchable(self) -> None:
        result = canonicalize_csv(bank_csv_builder().encode())
        assert "payee: Supermarkt GmbH" in result.canonical_text
        assert "purpose: Lebensmittel" in result.canonical_text

    def test_brokerage_transaction_id_retained_as_provenance(self) -> None:
        raw = _render(BROKERAGE_COLUMNS, BROKERAGE_ROWS, ",").encode()
        result = canonicalize_csv(raw)
        assert "transaction_id: TR-2022-0001" in result.canonical_text
        assert "counterparty_name: Broker X" in result.canonical_text
        assert "mcc_code: 5311" in result.canonical_text

    def test_embedded_iban_in_free_text_is_stripped(self) -> None:
        row = list(BANK_ROWS[0])
        row[5] = "Incoming transfer from DE02120300000000202051 at Bank"
        result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert "DE02120300000000202051" not in result.canonical_text
        assert "Incoming transfer from" in result.canonical_text
        assert "at Bank" in result.canonical_text

    def test_embedded_ref_style_payment_reference_never_canonicalized(self) -> None:
        row = list(BANK_ROWS[0])
        row[5] = "Miete Januar ABC-REF1234567890"
        result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert "ABC-REF1234567890" not in result.canonical_text
        assert "Miete Januar" in result.canonical_text

    def test_exact_ref_style_payment_reference_never_canonicalized(self) -> None:
        row = list(BANK_ROWS[0])
        row[5] = "ABC-REF1234567890"
        result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert "ABC-REF1234567890" not in result.canonical_text
        assert "ABC" not in result.canonical_text
        assert "REF1234567890" not in result.canonical_text

    def test_embedded_ref_style_reference_in_middle_of_description_is_stripped(
        self,
    ) -> None:
        row = list(BANK_ROWS[0])
        row[5] = "Beginn ABC-REF1234567890 Ende"
        result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert "ABC-REF1234567890" not in result.canonical_text
        assert "Beginn" in result.canonical_text
        assert "Ende" in result.canonical_text

    def test_multiple_ref_style_references_are_stripped(self) -> None:
        row = list(BANK_ROWS[0])
        row[5] = "ABC-REF1234567890 xyz DEF-REF0987654321"
        result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert "ABC-REF1234567890" not in result.canonical_text
        assert "DEF-REF0987654321" not in result.canonical_text
        assert "xyz" in result.canonical_text

    def test_ref_like_but_not_payment_reference_is_kept(self) -> None:
        for purpose in (
            "ABC-DEF1234567890",
            "ABC-REFMAZ4100101128",
            "ABC-REF123456789",
        ):
            row = list(BANK_ROWS[0])
            row[5] = purpose
            result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
            assert purpose in result.canonical_text, purpose

    def test_embedded_sepa_creditor_reference_is_stripped(self) -> None:
        row = list(BANK_ROWS[0])
        row[5] = "Rechnung RF1853902347034 vom Mai"
        result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert "RF1853902347034" not in result.canonical_text
        assert "Rechnung" in result.canonical_text
        assert "vom Mai" in result.canonical_text

    def test_ordinary_words_that_resemble_references_are_kept(self) -> None:
        for purpose in (
            "REFUND supermarket",
            "REFINANCE home loan",
            "Referenz Rechnung",
            "RFID tag 1234",
            "RefNr 12345",
        ):
            row = list(BANK_ROWS[0])
            row[5] = purpose
            result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
            assert purpose in result.canonical_text, purpose

    def test_legitimate_description_with_digits_remains_searchable(self) -> None:
        row = list(BANK_ROWS[0])
        row[5] = "Gehalt Januar 2025"
        result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert "Gehalt Januar 2025" in result.canonical_text

    def test_embedded_payment_reference_keeps_redaction_deterministic(self) -> None:
        row = list(BANK_ROWS[0])
        row[5] = "Zahlung ABC-REF1234567890"
        first = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        second = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert first.source_key == second.source_key
        assert first.content_hash == second.content_hash
        assert first.canonical_text == second.canonical_text
        assert "ABC-REF1234567890" not in first.canonical_text


class TestSchemaRecognition:
    def _adapter(self, tmp_path, files):
        directory = build_directory(tmp_path, files)
        return FinancialSourceAdapter(directory)

    def test_card_schema_recognized(self) -> None:
        raw = _render(CARD_COLUMNS, CARD_ROWS, ",").encode()
        result = canonicalize_csv(raw)
        assert result.kind == "card"
        assert result.metadata["financial_kind"] == "card"
        assert "type: CARD_PAYMENT" in result.canonical_text
        assert "product: Current" in result.canonical_text
        assert "description: Coffee Shop" in result.canonical_text
        assert "completed_date: 2018-10-13T10:00:05+00:00" in result.canonical_text
        assert "amount: -3.50" in result.canonical_text
        assert result.source_key.startswith("financial/card/2018/")

    def test_brokerage_schema_recognized(self) -> None:
        raw = _render(BROKERAGE_COLUMNS, BROKERAGE_ROWS, ",").encode()
        result = canonicalize_csv(raw)
        assert result.kind == "investment_transaction"
        assert result.source_key.startswith("financial/investment_transaction/")
        assert "date: 2022-04-04" in result.canonical_text
        assert "symbol: XXXX" in result.canonical_text
        assert "shares: 2" in result.canonical_text

    def test_brokerage_duplicate_transaction_id_is_deduplicated(self) -> None:
        second = list(BROKERAGE_ROWS[0])
        second[18] = "TR-2022-0001"
        second[17] = "Kauf Some ETF zweites Los"
        raw = _render(BROKERAGE_COLUMNS, [BROKERAGE_ROWS[0], second], ",").encode()
        result = canonicalize_csv(raw)
        assert result.metadata["row_count"] == 1
        assert result.canonical_text.count("TRADE") == 1
        assert "transaction_id: TR-2022-0001" in result.canonical_text
        assert "zweites Los" not in result.canonical_text

    def test_brokerage_distinct_transaction_ids_are_both_kept(self) -> None:
        second = list(BROKERAGE_ROWS[0])
        second[18] = "TR-2022-0002"
        raw = _render(BROKERAGE_COLUMNS, [BROKERAGE_ROWS[0], second], ",").encode()
        result = canonicalize_csv(raw)
        assert result.metadata["row_count"] == 2
        assert result.canonical_text.count("TRADE") == 2
        assert "transaction_id: TR-2022-0002" in result.canonical_text

    def test_portfolio_schema_recognized_and_undated(self) -> None:
        raw = _render(PORTFOLIO_COLUMNS, PORTFOLIO_ROWS, ",").encode()
        result = canonicalize_csv(raw)
        assert result.kind == "portfolio"
        assert result.source_key.startswith("financial/portfolio/undated/")
        assert "date_min" not in result.metadata
        assert "quantity: 10" in result.canonical_text
        assert "price: 188.42" in result.canonical_text
        assert "value: 1884.20" in result.canonical_text
        assert "value_eur: 1884.20" in result.canonical_text
        assert "isin: US0378331005" in result.canonical_text

    def test_portfolio_snapshot_date_is_explicitly_unknown(self) -> None:
        raw = _render(PORTFOLIO_COLUMNS, PORTFOLIO_ROWS, ",").encode()
        result = canonicalize_csv(raw)
        assert result.canonical_text.strip().count("snapshot_date: unknown") == 1
        assert result.metadata["snapshot_date"] == "unknown"
        assert "date_min" not in result.metadata
        assert "date_max" not in result.metadata

    def test_unknown_schema_raises(self, tmp_path) -> None:
        directory = build_directory(tmp_path, {"notes.csv": "foo,bar,baz\n1,2,3\n"})
        with pytest.raises(UnknownFinancialSchemaError):
            FinancialSourceAdapter(directory).discover()

    def test_empty_export_yields_zero_rows(self) -> None:
        raw = bank_csv_builder(rows=[]).encode()
        result = canonicalize_csv(raw)
        assert result.metadata["row_count"] == 0
        assert "FINANCIAL RECORD" in result.canonical_text

    def test_multiline_value_is_collapsed(self) -> None:
        row = list(BANK_ROWS[0])
        row[5] = "Line one\nLine two"
        result = canonicalize_csv(bank_csv_builder(rows=[row]).encode())
        assert "purpose: Line one Line two" in result.canonical_text


class TestAdapterDiscovery:
    def test_discover_returns_sorted_financial_records(self, tmp_path) -> None:
        directory = build_directory(
            tmp_path,
            {
                "Umsatzliste_2024.csv": bank_csv_builder(),
                "Revolut.csv": _render(CARD_COLUMNS, CARD_ROWS, ","),
                "Traderepublic.csv": _render(BROKERAGE_COLUMNS, BROKERAGE_ROWS, ","),
                "Degiro.csv": _render(PORTFOLIO_COLUMNS, PORTFOLIO_ROWS, ","),
            },
        )
        adapter = FinancialSourceAdapter(directory)
        records = adapter.discover()
        assert len(records) == 4
        assert all(r.source_type == SOURCE_TYPE for r in records)
        assert all(isinstance(r, SourceRecord) for r in records)
        keys = [r.source_key for r in records]
        assert keys == sorted(keys)
        assert adapter.discover() == records

    def test_discover_skips_non_csv_files(self, tmp_path) -> None:
        directory = build_directory(
            tmp_path,
            {
                "statements.pdf": "x",
                "notes.md": "hello",
                "Umsatzliste.csv": bank_csv_builder(),
            },
        )
        adapter = FinancialSourceAdapter(directory)
        records = adapter.discover()
        assert len(records) == 1
        assert records[0].source_type == SOURCE_TYPE

    def test_discover_stays_inside_workspace(self, tmp_path) -> None:
        directory = build_directory(tmp_path, {"inside.csv": bank_csv_builder()})
        outside = tmp_path / "outside.csv"
        outside.write_text(bank_csv_builder(), encoding="utf-8")
        records = FinancialSourceAdapter(directory).discover()
        assert len(records) == 1
        assert records[0].metadata["filename"] == "inside.csv"

    def test_nonexistent_directory_raises_source_error(self, tmp_path) -> None:
        adapter = FinancialSourceAdapter(tmp_path / "missing")
        with pytest.raises(Exception) as excinfo:
            adapter.discover()
        assert "not a directory" in str(excinfo.value)

    def test_load_record_roundtrip(self, tmp_path) -> None:
        directory = build_directory(tmp_path, {"bank.csv": bank_csv_builder()})
        adapter = FinancialSourceAdapter(directory)
        record = adapter.discover()[0]
        loaded = adapter.load_record(record.source_key)
        assert loaded.source_key == record.source_key
        assert loaded.content_hash == record.content_hash
        assert loaded.payload == record.payload
        assert loaded.metadata == record.metadata

    def test_load_record_unknown_key_raises(self, tmp_path) -> None:
        directory = build_directory(tmp_path, {"bank.csv": bank_csv_builder()})
        adapter = FinancialSourceAdapter(directory)
        with pytest.raises(Exception) as excinfo:
            adapter.load_record("financial/bank/2099/nope")
        assert "No such" in str(excinfo.value)

    def test_determinism_across_repeated_canonicalization(self) -> None:
        payload = bank_csv_builder().encode()
        first = canonicalize_csv(payload)
        second = canonicalize_csv(payload)
        assert first.source_key == second.source_key
        assert first.content_hash == second.content_hash
        assert first.canonical_text == second.canonical_text
