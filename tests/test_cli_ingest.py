"""Tests for the CLI ingestion mode."""

import json
import sqlite3
from pathlib import Path
from typing import Self

import pymupdf
import pytest

from personal_ai import cli
from personal_ai.documents import StructuredExtraction
from personal_ai.documents.conversations import Conversation
from personal_ai.retrieval import SearchDocumentsRequest, search_documents
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    connect_database,
)

LONG_ANSWER = (
    "Practice guitar in short daily sessions. Warm up with scales, then "
    "work on chord transitions slowly with a metronome. Record yourself "
    "weekly and compare against earlier recordings to hear progress. "
    "Focus on clean fretting before speed, and always end with something "
    "fun to keep motivation high."
)


@pytest.fixture()
def gemini_export(tmp_path: Path) -> Path:
    export = tmp_path / "gemini-export"
    export.mkdir()
    conversation = {
        "id": "conv-1",
        "title": "Guitar learning",
        "messages": [
            {"role": "user", "content": "How do I practice guitar?"},
            {"role": "assistant", "content": LONG_ANSWER},
        ],
        "createdAt": "2026-01-15T10:00:00Z",
        "lastMessageAt": "2026-01-15T10:05:00Z",
        "messageCount": 2,
    }
    (export / "conversation.json").write_text(json.dumps(conversation))
    return export


class FakeOllamaClient:
    """Context-manager double that can only serve chat-shaped use.

    It deliberately exposes no embedding method: if ingestion ever tried
    to contact an embedding provider, the test would fail loudly.
    """

    def __init__(self, model: str) -> None:
        self.model = model

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class FakeStructuredExtractor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract(self, extraction) -> StructuredExtraction:  # type: ignore[no-untyped-def]
        self.calls.append(extraction.document_id)
        return StructuredExtraction(document_id=extraction.document_id)


class FakeVisionExtractor:
    """Records rendered page images and returns a searchable marker text."""

    PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

    def __init__(self) -> None:
        self.model = "fake-vision-model"
        self.prompt_version = "v1"
        self.calls: list[bytes] = []

    def extract(self, image: bytes) -> str:
        self.calls.append(image)
        return "Vision marker alpha for searchable page content. " * 20


class ExplodingVisionExtractor:
    """Raises if constructed: email ingestion must stay vision-free."""

    def __init__(self, client: object, prompt_version: str) -> None:
        raise AssertionError("email ingestion must not construct a vision extractor")


@pytest.fixture()
def faked_model(monkeypatch: pytest.MonkeyPatch) -> FakeStructuredExtractor:
    extractor = FakeStructuredExtractor()
    monkeypatch.setattr(cli, "OllamaClient", FakeOllamaClient)
    monkeypatch.setattr(cli, "OllamaStructuredExtractor", lambda client: extractor)
    return extractor


def run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["personal-ai", *argv])
    cli.main()


def table_counts(database: Path) -> tuple[int, int, int]:
    connection = connect_database(database)
    try:
        documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = connection.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[
            0
        ]
        embeddings = connection.execute(
            "SELECT COUNT(*) FROM chunk_embeddings"
        ).fetchone()[0]
    finally:
        connection.close()
    return int(documents), int(chunks), int(embeddings)


def conversation_counts(database: Path) -> tuple[int, int, int]:
    connection = connect_database(database)
    try:
        conversations = connection.execute(
            "SELECT COUNT(*) FROM conversations"
        ).fetchone()[0]
        messages = connection.execute(
            "SELECT COUNT(*) FROM conversation_messages"
        ).fetchone()[0]
        attachments = connection.execute(
            "SELECT COUNT(*) FROM conversation_attachments"
        ).fetchone()[0]
    finally:
        connection.close()
    return int(conversations), int(messages), int(attachments)


def ingest_argv(gemini_export: Path, database: Path) -> list[str]:
    return [
        "--ingest",
        "gemini",
        str(gemini_export),
        "--database",
        str(database),
    ]


def test_ingest_creates_database_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    database = tmp_path / "knowledge.db"

    run_cli(monkeypatch, *ingest_argv(gemini_export, database))

    assert database.is_file()
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "source_type: gemini",
        "conversations: 1",
        "messages: 2",
        "md_files_skipped: 0",
        "aggregate_files_skipped: 0",
    ]
    assert conversation_counts(database) == (1, 2, 0)
    assert len(faked_model.calls) == 0


def test_second_invocation_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    database = tmp_path / "knowledge.db"
    arguments = ingest_argv(gemini_export, database)

    run_cli(monkeypatch, *arguments)
    first_output = capsys.readouterr().out
    assert first_output.splitlines()[-2:] == [
        "md_files_skipped: 0",
        "aggregate_files_skipped: 0",
    ]

    run_cli(monkeypatch, *arguments)
    second_output = capsys.readouterr().out

    assert second_output.splitlines()[:3] == [
        "source_type: gemini",
        "conversations: 0",  # nothing new stored on a re-run
        "messages: 0",
    ]
    assert len(faked_model.calls) == 0
    assert conversation_counts(database) == (1, 2, 0)


def test_ingested_database_is_immediately_searchable(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    faked_model: FakeStructuredExtractor,
) -> None:
    database = tmp_path / "knowledge.db"

    run_cli(monkeypatch, *ingest_argv(gemini_export, database))

    connection = connect_database(database)
    try:
        hits = ConversationStore(connection).search("metronome")
    finally:
        connection.close()

    assert len(hits) == 1
    assert hits[0].conversation_id
    assert "metronome" in hits[0].content_text


def test_ingest_file_source_discovers_and_chunks_text_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("Meeting notes about the project. " * 30)
    database = tmp_path / "knowledge.db"

    run_cli(
        monkeypatch, "--ingest", "file", str(workspace), "--database", str(database)
    )

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "source_type: file"
    documents, chunks, embeddings = table_counts(database)
    assert (documents, chunks, embeddings) == (1, 1, 0)
    assert len(faked_model.calls) == 1


def test_ingest_file_discovers_nested_supported_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    workspace = tmp_path / "workspace"
    subdir = workspace / "inbox"
    subdir.mkdir(parents=True)
    (workspace / "top.md").write_text("Top level goals for the quarter. " * 30)
    (subdir / "notes.txt").write_text("Nested meeting notes about the project. " * 30)
    database = tmp_path / "knowledge.db"

    run_cli(
        monkeypatch, "--ingest", "file", str(workspace), "--database", str(database)
    )

    documents, chunks, embeddings = table_counts(database)
    assert (documents, chunks, embeddings) == (2, 2, 0)
    assert len(faked_model.calls) == 2


def _write_single_page_pdf(path: Path, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    lines = [text[i : i + 80] for i in range(0, len(text), 80)]
    page.insert_text((36, 36), "\n".join(lines))
    path.write_bytes(doc.tobytes())
    doc.close()


def _write_image_only_pdf(path: Path) -> None:
    """A PDF whose only page is a raster image: IMAGE_HEAVY."""
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), 0)
    pixmap.clear_with(255)
    page.insert_image(pymupdf.Rect(10, 10, 190, 190), stream=pixmap.tobytes("png"))
    path.write_bytes(doc.tobytes())
    doc.close()


def test_ingest_pdf_file_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_single_page_pdf(
        workspace / "notes.pdf", "Page one notes on the project. " * 20
    )
    database = tmp_path / "knowledge.db"

    run_cli(
        monkeypatch, "--ingest", "file", str(workspace), "--database", str(database)
    )

    documents, chunks, embeddings = table_counts(database)
    assert (documents, chunks, embeddings) == (1, 1, 0)
    assert len(faked_model.calls) == 1


def test_ingest_mixed_pdf_is_chunked_without_model_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mixed_path = workspace / "mixed.pdf"
    doc = pymupdf.open()
    text_page = doc.new_page(width=595, height=842)
    text = "Notes about the mixed document. " * 20
    lines = [text[i : i + 80] for i in range(0, len(text), 80)]
    text_page.insert_text((36, 36), "\n".join(lines))
    image_page = doc.new_page(width=200, height=200)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), 0)
    pixmap.clear_with(255)
    image_page.insert_image(
        pymupdf.Rect(10, 10, 190, 190), stream=pixmap.tobytes("png")
    )
    mixed_path.write_bytes(doc.tobytes())
    doc.close()
    database = tmp_path / "knowledge.db"

    run_cli(
        monkeypatch, "--ingest", "file", str(workspace), "--database", str(database)
    )

    # Mixed documents are chunked but never routed to a model.
    documents, chunks, embeddings = table_counts(database)
    assert (documents, chunks, embeddings) == (1, 1, 0)
    assert len(faked_model.calls) == 0


def test_ingest_file_workspace_rerun_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("Meeting notes about the project. " * 30)
    database = tmp_path / "knowledge.db"
    arguments = ["--ingest", "file", str(workspace), "--database", str(database)]

    run_cli(monkeypatch, *arguments)
    first_output = capsys.readouterr().out
    first_calls = len(faked_model.calls)

    run_cli(monkeypatch, *arguments)

    assert capsys.readouterr().out == first_output
    assert len(faked_model.calls) == first_calls
    assert table_counts(database) == (1, 1, 0)


def test_unknown_source_exits_without_creating_database(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
) -> None:
    database = tmp_path / "should-not-exist.db"

    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--ingest",
            "gmail",
            str(gemini_export),
            "--database",
            str(database),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code != 0
    assert "Unknown source type" in str(exc_info.value)
    assert not database.exists()


def test_missing_source_path_exits_without_creating_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-export"
    database = tmp_path / "should-not-exist.db"

    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--ingest",
            "gemini",
            str(missing),
            "--database",
            str(database),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code != 0
    assert "not a directory" in str(exc_info.value)
    assert not database.exists()


def test_existing_database_is_reused_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    faked_model: FakeStructuredExtractor,
) -> None:
    database = tmp_path / "knowledge.db"
    seed_connection = connect_database(database)
    seed_store = ConversationStore(seed_connection)
    seed_store.save_conversation(
        Conversation(
            id="conv-seed",
            title="Seeded conversation",
            source_type="gemini",
            created_at="2025-06-01T00:00:00+00:00",
            modified_at="2025-06-01T00:00:00+00:00",
            metadata={},
        )
    )
    seed_connection.close()

    run_cli(monkeypatch, *ingest_argv(gemini_export, database))

    connection = connect_database(database)
    try:
        ids = {
            str(row[0])
            for row in connection.execute("SELECT id FROM conversations").fetchall()
        }
    finally:
        connection.close()

    # The seeded conversation survived and exactly one gemini conversation joined.
    assert "conv-seed" in ids
    assert len(ids) == 2


def test_connection_is_released_after_ingestion(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    faked_model: FakeStructuredExtractor,
) -> None:
    database = tmp_path / "knowledge.db"
    real_connect = cli.connect_database
    opened: list[sqlite3.Connection] = []

    def tracking_connect(path: object) -> sqlite3.Connection:
        connection = real_connect(path)  # type: ignore[arg-type]
        opened.append(connection)
        return connection

    monkeypatch.setattr(cli, "connect_database", tracking_connect)

    run_cli(monkeypatch, *ingest_argv(gemini_export, database))

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_search_and_ingest_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--search",
            "guitar",
            "--ingest",
            "gemini",
            str(gemini_export),
            "--database",
            str(tmp_path / "k.db"),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args()

    assert exc_info.value.code == 2


def test_ingest_requires_database(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["personal-ai", "--ingest", "gemini", str(gemini_export)]
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args()

    assert exc_info.value.code == 2


def test_agent_mode_unchanged_by_ingest_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("sys.argv", ["personal-ai", "--workspace", str(tmp_path)])

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args()

    # Missing prompt in agent mode keeps its original required behavior.
    assert exc_info.value.code == 2


def write_mbox(root: Path, mbox_dir_name: str, messages: list[object]) -> None:
    """Write MIME messages into an mbox file under a ``*.mbox`` directory."""
    import mailbox as mailbox_mod

    mbox_dir = root / mbox_dir_name
    mbox_dir.mkdir(parents=True, exist_ok=True)
    mbox = mailbox_mod.mbox(str(mbox_dir / "mbox"))
    for msg in messages:
        mbox.add(msg)
    mbox.close()


def make_text_email(
    *,
    subject: str,
    body: str,
    message_id: str | None,
    sender: str = "sender@example.test",
) -> object:
    from email.mime.text import MIMEText

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["To"] = "me@example.test"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 01 Jan 2026 12:00:00 +0000"
    if message_id is not None:
        msg["Message-ID"] = message_id
    return msg


def make_html_email(
    *,
    subject: str,
    html: str,
    message_id: str,
    sender: str = "sender@example.test",
) -> object:
    from email.mime.text import MIMEText

    msg = MIMEText(html, "html", "utf-8")
    msg["From"] = sender
    msg["To"] = "me@example.test"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 01 Jan 2026 12:00:00 +0000"
    msg["Message-ID"] = message_id
    return msg


@pytest.fixture()
def email_export(tmp_path: Path) -> Path:
    export = tmp_path / "takeout" / "Email"
    export.mkdir(parents=True)

    # Four distinct messages; message C is copied into two mailboxes, so
    # six records collapse into four documents.
    celestial = make_text_email(
        subject="Hobbies",
        body="celestial navigation is my favorite hobby. " * 12,
        message_id="<a@example.test>",
    )
    climbing = make_text_email(
        subject="Weekend plans",
        body="rock climbing routes near the alpine valley. " * 12,
        message_id="<b@example.test>",
    )
    missing_id = make_text_email(
        subject="No identifier",
        body="missing identifier fallback demo body. " * 12,
        message_id=None,
    )
    sourdough = make_html_email(
        subject="Kitchen notes",
        html=(
            "<p>First paragraph about cooking sourdough bread daily.</p>"
            "<p>Second paragraph about travel to the mountains.</p>"
        )
        * 6,
        message_id="<d@example.test>",
    )
    write_mbox(export, "INBOX.mbox", [celestial, missing_id])
    write_mbox(export, "Labels.mbox", [climbing, sourdough, missing_id])
    return export


class ExplodingOllamaClient:
    """Raises if constructed: email ingestion must stay model-free."""

    def __init__(self, model: str) -> None:
        raise AssertionError("email ingestion must not invoke structured extraction")


def email_ingest_argv(email_export: Path, database: Path) -> list[str]:
    return [
        "--ingest",
        "email",
        str(email_export),
        "--database",
        str(database),
    ]


def content_counts(database: Path) -> tuple[int, int, int]:
    connection = connect_database(database)
    try:
        documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = connection.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[
            0
        ]
        extractions = connection.execute(
            "SELECT COUNT(*) FROM structured_extractions"
        ).fetchone()[0]
    finally:
        connection.close()
    return int(documents), int(chunks), int(extractions)


def test_ingest_email_never_invokes_structured_extraction(
    monkeypatch: pytest.MonkeyPatch,
    email_export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "knowledge.db"
    monkeypatch.setattr(cli, "OllamaClient", ExplodingOllamaClient)
    # Keep the synchronous ingestion path intact (no fake extractor needed).
    monkeypatch.setattr(cli, "OllamaStructuredExtractor", lambda client: None)

    run_cli(monkeypatch, *email_ingest_argv(email_export, database))

    lines = capsys.readouterr().out.splitlines()
    # Five records (message C appears in two mailboxes) collapse into four
    # distinct durable documents and chunks.
    assert lines[0] == "source_type: email"
    assert "documents: 5" in lines
    assert any(line.strip() == "text_heavy: 5" for line in lines)
    assert "chunks: 5" in lines
    assert content_counts(database) == (4, 4, 0)


def test_ingested_email_database_is_immediately_searchable(
    monkeypatch: pytest.MonkeyPatch,
    email_export: Path,
    tmp_path: Path,
) -> None:
    database = tmp_path / "knowledge.db"
    monkeypatch.setattr(cli, "OllamaClient", ExplodingOllamaClient)
    monkeypatch.setattr(cli, "OllamaStructuredExtractor", lambda client: None)

    run_cli(monkeypatch, *email_ingest_argv(email_export, database))

    connection = connect_database(database)
    try:
        navigation = search_documents(
            ChunkStore(connection),
            SearchDocumentsRequest(query="celestial navigation"),
        )
        travel = search_documents(
            ChunkStore(connection),
            SearchDocumentsRequest(query="sourdough travel"),
        )
    finally:
        connection.close()

    assert len(navigation) == 1
    assert "celestial navigation" in navigation[0].text
    assert len(travel) == 1
    assert "sourdough bread" in travel[0].text
    assert "mountains" in travel[0].text


def test_ingested_email_rerun_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    email_export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "knowledge.db"
    monkeypatch.setattr(cli, "OllamaClient", ExplodingOllamaClient)
    monkeypatch.setattr(cli, "OllamaStructuredExtractor", lambda client: None)
    arguments = email_ingest_argv(email_export, database)

    run_cli(monkeypatch, *arguments)
    first_output = capsys.readouterr().out

    run_cli(monkeypatch, *arguments)

    assert capsys.readouterr().out == first_output
    assert content_counts(database) == (4, 4, 0)


def test_ingest_email_deduplicates_identical_missing_id_copies(
    monkeypatch: pytest.MonkeyPatch,
    email_export: Path,
    tmp_path: Path,
) -> None:
    database = tmp_path / "knowledge.db"
    monkeypatch.setattr(cli, "OllamaClient", ExplodingOllamaClient)
    monkeypatch.setattr(cli, "OllamaStructuredExtractor", lambda client: None)

    run_cli(monkeypatch, *email_ingest_argv(email_export, database))

    connection = connect_database(database)
    try:
        rows = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE source LIKE 'noid/%'"
        ).fetchone()[0]
    finally:
        connection.close()

    # The duplicate missing-ID message collapsed into a single document.
    assert int(rows) == 1


def _enable_vision_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> FakeVisionExtractor:
    vision = FakeVisionExtractor()
    monkeypatch.setenv("PERSONAL_AI_VISION_MODEL", "fake-vision-model")
    monkeypatch.setattr(
        cli, "OllamaVisionExtractor", lambda client, prompt_version: vision
    )
    return vision


def test_ingest_image_heavy_pdf_runs_vision_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_image_only_pdf(workspace / "scan.pdf")
    database = tmp_path / "knowledge.db"
    vision = _enable_vision_cli(monkeypatch)

    run_cli(
        monkeypatch, "--ingest", "file", str(workspace), "--database", str(database)
    )

    lines = capsys.readouterr().out.splitlines()
    assert any(line.strip() == "image_heavy: 1" for line in lines)
    documents, chunks, embeddings = table_counts(database)
    assert (documents, chunks, embeddings) == (1, 1, 0)
    assert len(vision.calls) == 1
    assert vision.calls[0][:8] == b"\x89PNG\r\n\x1a\n"
    # The substantial vision text is also routed through the structured
    # extractor; the persisted extraction carries the vision provenance.
    assert len(faked_model.calls) == 1
    connection = connect_database(database)
    try:
        extraction = connection.execute(
            "SELECT metadata FROM structured_extractions"
        ).fetchone()[0]
    finally:
        connection.close()
    assert json.loads(extraction)["extraction_source"] == "vision"


def test_ingest_image_heavy_becomes_searchable_after_vision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    faked_model: FakeStructuredExtractor,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_image_only_pdf(workspace / "scan.pdf")
    database = tmp_path / "knowledge.db"
    _enable_vision_cli(monkeypatch)

    run_cli(
        monkeypatch, "--ingest", "file", str(workspace), "--database", str(database)
    )

    connection = connect_database(database)
    try:
        hits = search_documents(
            ChunkStore(connection),
            SearchDocumentsRequest(query="vision marker alpha"),
        )
    finally:
        connection.close()

    assert len(hits) == 1
    assert "Vision marker alpha" in hits[0].text


def test_ingest_image_heavy_vision_rerun_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_image_only_pdf(workspace / "scan.pdf")
    database = tmp_path / "knowledge.db"
    arguments = ["--ingest", "file", str(workspace), "--database", str(database)]
    vision = _enable_vision_cli(monkeypatch)

    run_cli(monkeypatch, *arguments)
    first_output = capsys.readouterr().out
    first_calls = len(vision.calls)

    run_cli(monkeypatch, *arguments)

    assert capsys.readouterr().out == first_output
    assert len(vision.calls) == first_calls
    assert table_counts(database) == (1, 1, 0)


def test_ingest_image_heavy_without_vision_stays_unchunked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_image_only_pdf(workspace / "scan.pdf")
    database = tmp_path / "knowledge.db"

    run_cli(
        monkeypatch, "--ingest", "file", str(workspace), "--database", str(database)
    )

    lines = capsys.readouterr().out.splitlines()
    assert any(line.strip() == "image_heavy: 1" for line in lines)
    assert any(line.strip() == "chunks: 0" for line in lines)


def test_email_ingest_never_constructs_vision_extractor(
    monkeypatch: pytest.MonkeyPatch,
    email_export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "knowledge.db"
    monkeypatch.setenv("PERSONAL_AI_VISION_MODEL", "fake-vision-model")
    monkeypatch.setattr(cli, "OllamaClient", ExplodingOllamaClient)
    monkeypatch.setattr(cli, "OllamaStructuredExtractor", lambda client: None)
    monkeypatch.setattr(cli, "OllamaVisionExtractor", ExplodingVisionExtractor)

    run_cli(monkeypatch, *email_ingest_argv(email_export, database))

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "source_type: email"
    assert content_counts(database) == (4, 4, 0)
