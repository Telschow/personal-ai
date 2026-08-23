"""Tests for the NotebookLM article source adapter."""

from pathlib import Path

import pytest

from personal_ai.documents.classifier import DocumentKind, classify_document
from personal_ai.documents.extractor import extract_text
from personal_ai.sources.base import SourceAdapter
from personal_ai.sources.notebooklm import (
    EmptyArticleError,
    NotebookLMParseError,
    NotebookLMSourceAdapter,
    PathOutsideExportError,
    SourceNotFoundError,
    UnsupportedArticleError,
    build_article_record,
    compose_article_text,
)

DEFAULT_KEY = "Local AI/Sources/hardware.html"


def record_for(html: bytes, source_key: str = DEFAULT_KEY, **overrides: object):
    return build_article_record(source_key, html, **overrides)


def text_of(html: bytes, **overrides: object) -> str:
    record = record_for(html, **overrides)
    assert record.payload is not None
    return record.payload.decode("utf-8")


def write_article(
    root: Path,
    relative: str,
    payload: bytes,
    *,
    title: str | None = None,
    content_type: str | None = None,
) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    if title is not None or content_type is not None:
        nested: dict[str, object] = {}
        if content_type is not None:
            nested["originalSourceContentType"] = content_type
            nested["sourceAddedTimestamp"] = "2026-07-25T16:35:57.729358Z"
        meta = {"title": title, "metadata": nested}
        target.with_name(target.stem + " metadata.json").write_text(
            str(meta).replace("'", '"'), encoding="utf-8"
        )
    return target


BASIC = (
    b"<h3>Choosing Hardware</h3>"
    b"<p>A mid-range GPU now runs 70B models usefully.</p>"
    b"<p>The question is which tier fits your budget.</p>"
)


class TestComposition:
    def test_basic_article_structure_is_preserved(self) -> None:
        assert text_of(BASIC) == (
            "Choosing Hardware\n\n"
            "A mid-range GPU now runs 70B models usefully.\n\n"
            "The question is which tier fits your budget."
        )

    def test_headings_are_standalone_blocks_with_inline_markup(self) -> None:
        html = b"<h1><strong>CAPPA</strong></h1><h3>Conference</h3><p>Welcome.</p>"
        assert text_of(html) == "CAPPA\n\nConference\n\nWelcome."

    def test_unordered_lists_become_bullet_lines(self) -> None:
        html = b"<ul><li>RTX 4090</li><li>Apple Silicon</li></ul>"
        assert text_of(html) == "- RTX 4090\n- Apple Silicon"

    def test_ordered_lists_are_numbered(self) -> None:
        html = b"<ol><li>Measure power</li><li>Pick memory</li></ol>"
        assert text_of(html) == "1. Measure power\n2. Pick memory"

    def test_link_text_is_kept_and_targets_are_dropped(self) -> None:
        html = (
            b"<p>See <a href='https://ollama.com/'>Ollama</a> for details "
            b"and <a href='https://icon.example'></a> icons.</p>"
        )
        text = text_of(html)
        assert text == "See Ollama for details and icons."
        assert "http" not in text

    def test_embedded_images_never_leak_into_text(self) -> None:
        blob = b"A" * 4000
        record = record_for(
            b"<p>Diagram below.</p><img alt=image src='data:image/jpeg;base64,"
            + blob
            + b"' />"
        )
        assert record.payload is not None
        assert record.payload.decode("utf-8") == "Diagram below."
        assert record.metadata["image_count"] == 1

    def test_preformatted_content_keeps_internal_whitespace(self) -> None:
        html = b"<pre>ollama run qwen3.5:9b\n  --verbose  true</pre>"
        record = record_for(html)
        assert record.payload is not None
        assert record.payload.decode("utf-8") == (
            "ollama run qwen3.5:9b\n  --verbose  true"
        )

    def test_tables_render_as_pipe_separated_rows(self) -> None:
        html = (
            b"<table><thead><tr><th><strong>Tool</strong></th>"
            b"<th>Best for</th></tr></thead><tbody>"
            b"<tr><td><a href='https://x.example'>Plaud</a></td>"
            b"<td>Privacy-first users</td></tr></tbody></table>"
        )
        assert text_of(html) == "Tool | Best for\nPlaud | Privacy-first users"

    def test_script_style_noscript_are_removed(self) -> None:
        html = (
            b"<script>var tracking = 1;</script>"
            b"<style>.share { display: none }</style>"
            b"<noscript>Enable JS</noscript>"
            b"<p>Real content only.</p>"
        )
        assert text_of(html) == "Real content only."

    def test_chat_style_paragraphs_merge_into_flow_prose(self) -> None:
        html = (
            b"<p><strong>User</strong> : Describe the picture\n"
            b"<strong>Gemini</strong> : Here is a sketch of it."
            b"http://googleusercontent.com/image_generation_content/0</p>"
        )
        text = text_of(html)
        assert text == (
            "User : Describe the picture Gemini : Here is a sketch of it."
            "http://googleusercontent.com/image_generation_content/0"
        )

    def test_real_corpus_boilerplate_shape_normalizes_cleanly(self) -> None:
        html = (
            b"<p>Picking the Right Hardware | Pinggy Blog\n"
            b"<a href='https://pinggy.io/'>Pinggy Technical Blog</a> "
            b"<a href='https://pinggy.io/contact_us/'></a>\nTechnology</p>\n"
            b"<h3>Picking the Right Hardware to Run LLMs Locally in 2026</h3>\n"
            b"<p>June 5, 2026 16 min read\n"
            b"*  <a href='https://pinggy.io/tags/ollama/'>Ollama</a>\n"
            b"*  <a href='https://twitter.com/share?url=x'></a>\n"
            b"Running an LLM locally used to mean settling.</p>\n"
            b"<h4>Summary</h4>\n<p><strong>By model size:</strong></p>"
        )
        text = text_of(html)
        lines = text.split("\n")
        assert "Picking the Right Hardware to Run LLMs Locally in 2026" in lines, lines
        assert "Summary" in lines
        assert "http" not in text
        assert "<a" not in text and "</p>" not in text


class TestRecordPolicy:
    def test_empty_articles_are_rejected(self) -> None:
        with pytest.raises(EmptyArticleError):
            record_for(b"<p></p><hr />")

    def test_invalid_utf8_raises_a_parse_error(self) -> None:
        with pytest.raises(NotebookLMParseError):
            compose_article_text(b"<p>\xff\xfe</p>")

    def test_provenance_metadata_is_preserved(self) -> None:
        record = record_for(
            BASIC,
            title="Picking the Right Hardware | Pinggy Blog",
            source_content_type="SOURCE_CONTENT_TYPE_URL",
            added_at="2026-07-25T16:35:57.729358Z",
        )
        assert record.source_type == "notebooklm"
        assert record.metadata == {
            "filename": "hardware.html",
            "mime_type": "text/html",
            "title": "Picking the Right Hardware | Pinggy Blog",
            "notebook": "Local AI",
            "image_count": 0,
            "source_content_type": "SOURCE_CONTENT_TYPE_URL",
        }
        assert record.created_at == "2026-07-25T16:35:57.729358+00:00"
        assert record.modified_at == record.created_at

    def test_title_falls_back_to_filename_stem(self) -> None:
        record = record_for(BASIC)
        assert record.metadata["title"] == "hardware"
        assert record.created_at == ""


class TestIdentityStability:
    def test_serialization_variance_keeps_identity(self) -> None:
        compact = (
            b"<h4>Summary</h4><ul><li><strong>7B models</strong>: small</li>"
            b"<li>13B: medium</li></ul><p>End of summary.</p>"
        )
        reformatted = (
            b"<h4>\n  Summary\n</h4>\n<ul>\n  <li><strong>7B models"
            b"</strong>: small</li>\n  <li>13B: medium</li>\n</ul>"
            b"\n<p>\n  End of summary.\n</p>"
        )
        first = record_for(compact)
        second = record_for(reformatted)
        assert first.content_hash == second.content_hash
        assert first.payload == second.payload
        assert first == second

    def test_repeated_builds_are_fully_deterministic(self) -> None:
        assert record_for(BASIC) == record_for(BASIC)

    def test_long_articles_classify_as_text_heavy_downstream(self) -> None:
        paragraph = (
            b"<p>Running large language models locally changed what private "
            b"document work looks like for individuals who care about data "
            b"ownership and offline availability of their own tools.</p>"
        )
        record = record_for(paragraph * 3)
        classification = classify_document(extract_text(record))
        assert classification.kind is DocumentKind.TEXT_HEAVY


class TestNotebookLMSourceAdapter:
    @pytest.fixture()
    def export_dir(self, tmp_path: Path) -> Path:
        root = tmp_path / "Takeout" / "NotebookLM"
        root.mkdir(parents=True)
        write_article(
            root,
            "Local AI Deployment/Sources/a_hardware.html",
            BASIC,
            title="Hardware Guide",
            content_type="SOURCE_CONTENT_TYPE_URL",
        )
        write_article(root, "Growth/Sources/b_therapy.html", BASIC)
        write_article(
            root,
            "Local AI Deployment/Chat History/Chat Session - abc.html",
            b"<p>chat log</p>",
        )
        write_article(
            root, "Local AI Deployment/Artifacts/briefing.html", b"<p>artifact</p>"
        )
        (root / "Growth/Sources/readme.txt").write_text("not an article")
        (root / ".git").mkdir()
        (root / ".git/hidden.html").write_bytes(b"<p>x</p>")
        return root

    def test_discovers_only_source_articles_in_deterministic_order(
        self, export_dir: Path
    ) -> None:
        discovered = NotebookLMSourceAdapter(export_dir).discover()
        assert [record.source_key for record in discovered] == [
            "Growth/Sources/b_therapy.html",
            "Local AI Deployment/Sources/a_hardware.html",
        ]

    def test_discovered_records_carry_export_provenance(self, export_dir: Path) -> None:
        first = NotebookLMSourceAdapter(export_dir).discover()[1]
        assert first.metadata["title"] == "Hardware Guide"
        assert first.metadata["source_content_type"] == "SOURCE_CONTENT_TYPE_URL"
        assert first.metadata["notebook"] == "Local AI Deployment"

    def test_load_record_matches_direct_build(self, export_dir: Path) -> None:
        adapter = NotebookLMSourceAdapter(export_dir)
        loaded = adapter.load_record("Local AI Deployment/Sources/a_hardware.html")
        direct = build_article_record(
            "Local AI Deployment/Sources/a_hardware.html",
            BASIC,
            title="Hardware Guide",
            source_content_type="SOURCE_CONTENT_TYPE_URL",
            added_at="2026-07-25T16:35:57.729358+00:00",
        )
        assert loaded == direct

    @pytest.mark.parametrize(
        ("source_key", "expected"),
        [
            ("../outside.html", PathOutsideExportError),
            ("Missing Notebook/Sources/gone.html", SourceNotFoundError),
            (
                "Local AI Deployment/Chat History/Chat Session - abc.html",
                UnsupportedArticleError,
            ),
            ("Local AI Deployment/Artifacts/briefing.html", UnsupportedArticleError),
            ("Growth/Sources/readme.txt", UnsupportedArticleError),
        ],
    )
    def test_load_record_rejects_bad_targets(
        self, export_dir: Path, source_key: str, expected: type[Exception]
    ) -> None:
        with pytest.raises(expected):
            NotebookLMSourceAdapter(export_dir).load_record(source_key)

    def test_corrupt_articles_fail_loudly_during_discovery(
        self, export_dir: Path
    ) -> None:
        write_article(export_dir, "Growth/Sources/z_corrupt.html", b"<p>\xff\xfe")
        with pytest.raises(NotebookLMParseError):
            NotebookLMSourceAdapter(export_dir).discover()

    def test_adapter_satisfies_source_protocol(self, export_dir: Path) -> None:
        assert isinstance(NotebookLMSourceAdapter(export_dir), SourceAdapter)
