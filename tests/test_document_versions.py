"""Superseded document versions are retired on re-ingest.

Regression: the document id includes the content hash and earlier versions
were never removed, so an edited file kept answering with its old text: both
versions stayed in search, with their chunks, embeddings and extraction.

Retirement is scoped to the ingested folder, so the same relative path under
two folders is two items, and a document still current in another folder is
never deleted.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Self

import pytest

from personal_ai import cli
from personal_ai.documents import Embedding
from personal_ai.documents.structured import StructuredExtraction
from personal_ai.ingestion import DocumentIngestor
from personal_ai.orchestration import ingest_source
from personal_ai.sources.filesystem import FilesystemSourceAdapter
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    VisionStore,
    connect_database,
)

OLD = "Bluebird migration notes for the garden survey. " * 20
NEW = "Kestrel hunting notes for the garden survey. " * 20


class _Extractor:
    def extract(self, extraction) -> StructuredExtraction:  # type: ignore[no-untyped-def]
        return StructuredExtraction(document_id=extraction.document_id)


class _Corpus:
    def __init__(self) -> None:
        self.connection = connect_database(":memory:")
        self.documents = DocumentStore(self.connection)
        self.extractions = ExtractionStore(self.connection)
        self.chunks = ChunkStore(self.connection)
        self.embeddings = EmbeddingStore(self.connection)
        self.vision = VisionStore(self.connection)
        self.ingestor = DocumentIngestor(
            self.documents,
            self.extractions,
            _Extractor(),  # type: ignore[arg-type]
            self.chunks,
            self.embeddings,
            vision_store=self.vision,
        )

    def ingest(self, folder: Path) -> int:
        return ingest_source(FilesystemSourceAdapter(folder), self.ingestor).retired

    def hits(self, term: str) -> set[str]:
        return {hit.document_id for hit in self.chunks.search(term, limit=50)}

    def count(self, table: str) -> int:
        return int(
            self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        )


@pytest.fixture()
def corpus() -> _Corpus:
    c = _Corpus()
    yield c
    c.connection.close()


def test_edited_file_retires_its_previous_version(
    corpus: _Corpus, tmp_path: Path
) -> None:
    folder = tmp_path / "notes"
    folder.mkdir()
    note = folder / "note.txt"
    note.write_text(OLD)
    assert corpus.ingest(folder) == 0
    (old_id,) = corpus.hits("Bluebird")
    old_chunks = corpus.chunks.list_for_document(old_id)
    corpus.embeddings.add(Embedding(model="m", vector=(1.0, 0.0)), old_chunks[0].id)
    corpus.vision.save(old_id, 1, "page text", "vision-model", "v1")

    note.write_text(NEW)
    assert corpus.ingest(folder) == 1

    assert corpus.hits("Bluebird") == set()
    (new_id,) = corpus.hits("Kestrel")
    assert new_id != old_id
    assert corpus.documents.get(old_id) is None
    assert corpus.chunks.list_for_document(old_id) == ()
    assert corpus.embeddings.get(old_chunks[0].id) is None
    assert corpus.extractions.get(old_id) is None
    assert corpus.vision.get(old_id, 1) is None
    assert corpus.count("documents") == 1

    # Unchanged re-ingest stays idempotent; reverting the edit works too.
    assert corpus.ingest(folder) == 0
    note.write_text(OLD)
    assert corpus.ingest(folder) == 1
    assert corpus.hits("Kestrel") == set() and len(corpus.hits("Bluebird")) == 1


def test_same_relative_path_in_two_folders_is_two_items(
    corpus: _Corpus, tmp_path: Path
) -> None:
    home, work = tmp_path / "home", tmp_path / "work"
    for folder, text in ((home, OLD), (work, NEW)):
        folder.mkdir()
        (folder / "README.txt").write_text(text)
        corpus.ingest(folder)
    corpus.ingest(home)
    corpus.ingest(work)
    assert len(corpus.hits("Bluebird")) == 1 and len(corpus.hits("Kestrel")) == 1


def test_shared_document_survives_while_another_folder_still_has_it(
    corpus: _Corpus, tmp_path: Path
) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    for folder in (a, b):
        folder.mkdir()
        (folder / "note.txt").write_text(OLD)
        corpus.ingest(folder)
    (shared_id,) = corpus.hits("Bluebird")

    (a / "note.txt").write_text(NEW)
    assert corpus.ingest(a) == 0  # folder b still has the old text
    assert corpus.hits("Bluebird") == {shared_id}

    (b / "note.txt").write_text(NEW)
    assert corpus.ingest(b) == 1
    assert corpus.hits("Bluebird") == set()


def test_sources_without_a_version_scope_never_retire(corpus: _Corpus) -> None:
    from personal_ai.documents.models import compute_content_hash
    from personal_ai.sources.models import SourceRecord

    def record(text: str) -> SourceRecord:
        payload = text.encode()
        return SourceRecord(
            source_type="email",
            source_key="<same-id@example.test>",
            content_hash=compute_content_hash(payload),
            created_at="2026-01-01T00:00:00+00:00",
            modified_at="2026-01-01T00:00:00+00:00",
            payload=payload,
            metadata={"mime_type": "text/plain"},
        )

    corpus.ingestor.ingest(record(OLD))
    assert corpus.ingestor.ingest(record(NEW)).retired == ()
    assert corpus.count("documents") == 2


def test_cli_ingest_reports_retired_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Client:
        def __init__(self, model: str, base_url: str | None = None) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(cli, "OllamaClient", _Client)
    monkeypatch.setattr(cli, "OllamaStructuredExtractor", lambda client: _Extractor())
    folder = tmp_path / "notes"
    folder.mkdir()
    database = tmp_path / "knowledge.db"
    argv = ["personal-ai", "--ingest", "file", str(folder), "--database", str(database)]

    (folder / "note.txt").write_text(OLD)
    monkeypatch.setattr("sys.argv", argv)
    cli.main()
    assert "retired_versions" not in capsys.readouterr().out
    (folder / "note.txt").write_text(NEW)
    cli.main()
    assert "retired_versions: 1" in capsys.readouterr().out

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    finally:
        connection.close()
