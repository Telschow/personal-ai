"""End-to-end tests for financial CSV ingestion through the generic pipeline.

Financial records flow through the existing generic ``DocumentIngestor``:
canonicalized searchable payload -> text extraction -> classification ->
chunks -> FTS5 index. No model calls, embedding provider, or vector store are
involved, mirroring the email source path.
"""

from personal_ai.ingestion import DocumentIngestor
from personal_ai.orchestration import ingest_source
from personal_ai.retrieval import SearchDocumentsRequest, search_documents
from personal_ai.sources.financial import FinancialSourceAdapter
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)

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


def _render(columns, rows, delimiter):
    lines = [delimiter.join(columns)]
    lines.extend(delimiter.join(row) for row in rows)
    return "\n".join(lines) + "\n"


def _built_bank_csv():
    return (
        'Girokonto;"DE89370400440532013000"\r\n'
        "Zeitraum:;2024\r\n"
        "Kontostand vom 31.12.2024:;1234,56\r\n" + _render(BANK_COLUMNS, BANK_ROWS, ";")
    )


def _built_bank_csv_with(rows):
    return (
        'Girokonto;"DE89370400440532013000"\r\n'
        "Zeitraum:;2024\r\n"
        "Kontostand vom 31.12.2024:;1234,56\r\n" + _render(BANK_COLUMNS, rows, ";")
    )


def make_corpus(tmp_path):
    directory = tmp_path / "finance"
    directory.mkdir()
    (directory / "Umsatzliste_2024.csv").write_text(_built_bank_csv(), encoding="utf-8")
    (directory / "Revolut.csv").write_text(
        _render(CARD_COLUMNS, CARD_ROWS, ","), encoding="utf-8"
    )
    (directory / "Traderepublic.csv").write_text(
        _render(BROKERAGE_COLUMNS, BROKERAGE_ROWS, ","), encoding="utf-8"
    )
    (directory / "Degiro.csv").write_text(
        _render(PORTFOLIO_COLUMNS, PORTFOLIO_ROWS, ","), encoding="utf-8"
    )
    (directory / "Traderepublic_Statement.pdf").write_bytes(b"%PDF-1.4 placeholder")
    (directory / "notes.md").write_text("not finance", encoding="utf-8")
    return directory


def make_ingestor(connection):
    return DocumentIngestor(
        DocumentStore(connection),
        ExtractionStore(connection),
        None,
        ChunkStore(connection),
        EmbeddingStore(connection),
    )


class TestFinancialIngestionE2E:
    def test_full_pipeline_ingests_financial_csvs(self, tmp_path) -> None:
        directory = make_corpus(tmp_path)
        connection = connect_database(":memory:")
        try:
            ingestor = make_ingestor(connection)
            summary = ingest_source(FinancialSourceAdapter(directory), ingestor)
            assert summary.source_type == "financial"
            assert summary.documents == 4
            assert summary.chunk_count > 0
            assert set(summary.kind_counts) == {"text_heavy", "mixed"}

            documents = DocumentStore(connection).list_documents()
            assert len(documents) == 4
            assert all(doc.source_type == "financial" for doc in documents)
            kinds = {doc.metadata["financial_kind"] for doc in documents}
            assert kinds == {"bank", "card", "investment_transaction", "portfolio"}
            assert all(
                doc.metadata["canonicalization_version"] == "v1" for doc in documents
            )

            rows = connection.execute(
                "SELECT COUNT(*) FROM structured_extractions"
            ).fetchone()[0]
            assert rows == 0
        finally:
            connection.close()

    def test_search_finds_safe_fields_but_never_sensitive_tokens(
        self, tmp_path
    ) -> None:
        directory = make_corpus(tmp_path)
        connection = connect_database(":memory:")
        try:
            ingest_source(FinancialSourceAdapter(directory), make_ingestor(connection))
            chunk_store = ChunkStore(connection)

            assert search_documents(
                chunk_store, SearchDocumentsRequest(query="Supermarkt", limit=10)
            )
            assert search_documents(
                chunk_store, SearchDocumentsRequest(query="Coffee", limit=10)
            )
            assert search_documents(
                chunk_store, SearchDocumentsRequest(query="XXXX", limit=10)
            )

            sensitive = [
                "DE02120300000000202051",
                "DE98ZZZ09999999999",
                "REF-KND-2024",
                "DE00000000000000000000",
                "REF-2022-0001",
                "ABC-REF1234567890",
            ]
            for token in sensitive:
                hits = search_documents(
                    chunk_store, SearchDocumentsRequest(query=token, limit=10)
                )
                assert hits == (), token

            assert (
                search_documents(
                    chunk_store, SearchDocumentsRequest(query="HACKER", limit=10)
                )
                == ()
            )
        finally:
            connection.close()

    def test_embedded_ref_reference_in_purpose_not_searchable(self, tmp_path) -> None:
        directory = make_corpus(tmp_path)
        rows = [list(row) for row in BANK_ROWS]
        rows[0][5] = "Miete Januar ABC-REF1234567890"
        (directory / "Umsatzliste_2024.csv").write_text(
            _built_bank_csv_with(rows), encoding="utf-8"
        )
        connection = connect_database(":memory:")
        try:
            ingest_source(FinancialSourceAdapter(directory), make_ingestor(connection))
            chunk_store = ChunkStore(connection)
            assert search_documents(
                chunk_store,
                SearchDocumentsRequest(query="Miete", limit=10),
            )
            assert (
                search_documents(
                    chunk_store,
                    SearchDocumentsRequest(query="ABC-REF1234567890", limit=10),
                )
                == ()
            )
        finally:
            connection.close()

    def test_second_ingest_is_idempotent(self, tmp_path) -> None:
        directory = make_corpus(tmp_path)
        connection = connect_database(":memory:")
        try:
            adapter = FinancialSourceAdapter(directory)
            ingestor = make_ingestor(connection)
            first = ingest_source(adapter, ingestor)
            chunk_store = ChunkStore(connection)
            before_count = chunk_store.count()

            second = ingest_source(adapter, ingestor)
            assert second.documents == first.documents == 4
            assert second.chunk_count == first.chunk_count
            assert chunk_store.count() == before_count
            assert len(DocumentStore(connection).list_documents()) == 4
        finally:
            connection.close()

    def test_rename_does_not_create_duplicate_document(self, tmp_path) -> None:
        directory = make_corpus(tmp_path)
        connection = connect_database(":memory:")
        try:
            adapter = FinancialSourceAdapter(directory)
            ingestor = make_ingestor(connection)
            ingest_source(adapter, ingestor)
            document_store = DocumentStore(connection)
            ids_before = {doc.id for doc in document_store.list_documents()}

            (directory / "Umsatzliste_2024.csv").rename(
                directory / "Renamed_Bank_Export.csv"
            )
            ingest_source(FinancialSourceAdapter(directory), ingestor)
            ids_after = {doc.id for doc in document_store.list_documents()}
            assert ids_before == ids_after
            renamed = {
                doc.id
                for doc in document_store.list_documents()
                if doc.metadata["financial_kind"] == "bank"
            }
            assert len(renamed) == 1
            assert len(DocumentStore(connection).list_documents()) == 4
        finally:
            connection.close()

    def test_discovery_is_deterministic_across_runs(self, tmp_path) -> None:
        directory = make_corpus(tmp_path)
        adapter = FinancialSourceAdapter(directory)
        first = [r.source_key for r in adapter.discover()]
        second = [r.source_key for r in adapter.discover()]
        assert first == second
        assert len(first) == 4
        assert all(key.startswith("financial/") for key in first)
