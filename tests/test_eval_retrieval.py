"""Tests for scripts/eval_retrieval.py and its synthetic question set."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "eval_retrieval.py"
DATA_DIR = ROOT / "tests" / "fixtures" / "synthetic" / "eval"

# Words too common to count as shared content between a query and a chunk.
_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "also",
        "because",
        "before",
        "being",
        "from",
        "have",
        "into",
        "more",
        "only",
        "over",
        "some",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "what",
        "when",
        "where",
        "which",
        "while",
        "will",
        "with",
        "would",
        "your",
    }
)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("eval_retrieval", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their own module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load_script()


@pytest.fixture(scope="module")
def fixture(script: ModuleType):
    return script.load_fixture()


def _empty_readme(script: ModuleType) -> str:
    return (
        f"{script.BEGIN_MARKER}\n{script.END_MARKER}\n"
        f"{script.LATENCY_BEGIN_MARKER}\n{script.LATENCY_END_MARKER}\n"
    )


def _copy_data(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    for path in DATA_DIR.glob("retrieval_*.json"):
        (data / path.name).write_text(path.read_text("utf-8"), "utf-8")
    return data


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _chunk_text(fixture, chunk_id: str) -> str:
    return next(text for cid, _, text in fixture.chunks if cid == chunk_id)


def test_fixture_meets_roadmap_size(fixture) -> None:
    assert 50 <= len(fixture.questions) <= 100


def test_fixtures_are_marked_synthetic() -> None:
    for name in ("retrieval_corpus.json", "retrieval_questions.json"):
        data = json.loads((DATA_DIR / name).read_text("utf-8"))
        assert data["synthetic"] == "SYNTHETIC-DEMO-DATA"


def test_every_category_is_used(script, fixture) -> None:
    used = {question.category for question in fixture.questions}
    assert used == set(script.CATEGORIES)


def test_keyword_questions_only_use_terms_from_their_chunk(fixture) -> None:
    for question in fixture.questions:
        if question.category not in {"keyword", "identifier"}:
            continue
        for chunk_id in question.relevant:
            missing = _words(question.query) - _words(_chunk_text(fixture, chunk_id))
            assert not missing, f"{question.id}: {sorted(missing)} not in {chunk_id}"


def test_paraphrase_questions_share_no_content_word_with_their_chunk(fixture) -> None:
    for question in fixture.questions:
        if question.category != "paraphrase":
            continue
        query_words = {
            word
            for word in _words(question.query)
            if len(word) > 3 and word not in _STOPWORDS
        }
        for chunk_id in question.relevant:
            shared = query_words & _words(_chunk_text(fixture, chunk_id))
            assert not shared, f"{question.id} shares {sorted(shared)} with {chunk_id}"


def test_load_fixture_rejects_unknown_relevant_chunk(script, tmp_path: Path) -> None:
    (tmp_path / "retrieval_corpus.json").write_text(
        json.dumps({"documents": [{"id": "d1", "chunks": ["alpha beta"]}]}), "utf-8"
    )
    (tmp_path / "retrieval_questions.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "id": "q1",
                        "category": "keyword",
                        "query": "alpha",
                        "relevant": ["d1-c9"],
                        "facts": ["alpha"],
                    }
                ]
            }
        ),
        "utf-8",
    )
    with pytest.raises(ValueError, match="q1"):
        script.load_fixture(tmp_path)


def test_embedder_is_deterministic(script) -> None:
    embedder = script.HashedNgramEmbedder()
    first = embedder.embed("Nightly backup of the ledger database")
    second = embedder.embed("Nightly backup of the ledger database")
    assert first == second
    assert first.dimensions == script.EMBEDDING_DIMENSIONS
    assert first.model == script.EMBEDDING_MODEL


def test_evaluation_is_deterministic(script, fixture) -> None:
    assert script.run_evaluation(fixture) == script.run_evaluation(fixture)


def test_keyword_backend_finds_every_exact_term_question(script, fixture) -> None:
    report = script.run_evaluation(fixture)
    assert report.by_category["keyword"]["keyword"] == 1.0
    assert report.by_category["keyword"]["identifier"] == 1.0


def test_readme_table_and_thresholds_hold(script) -> None:
    assert script.main(["--check"]) == 0


def test_check_fails_when_readme_table_drifts(script, tmp_path: Path, capsys) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        f"{script.BEGIN_MARKER}\n| stale table |\n{script.END_MARKER}\n"
        f"{script.LATENCY_BEGIN_MARKER}\n{script.LATENCY_END_MARKER}\n",
        "utf-8",
    )
    assert script.main(["--check", "--readme", str(readme)]) == 1
    assert "differs" in capsys.readouterr().err


def test_check_fails_without_markers(script, tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("no markers here\n", "utf-8")
    assert script.main(["--check", "--readme", str(readme)]) == 1


def test_write_then_check_round_trips(script, tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(_empty_readme(script), "utf-8")
    assert script.main(["--write", "--readme", str(readme)]) == 0
    assert script.main(["--check", "--readme", str(readme)]) == 0


def test_check_fails_when_a_threshold_is_raised_above_the_measurement(
    script, tmp_path: Path, capsys
) -> None:
    data = _copy_data(tmp_path)
    thresholds = json.loads((data / "retrieval_thresholds.json").read_text("utf-8"))
    thresholds["recall_at_5"]["hybrid"] = 0.99
    (data / "retrieval_thresholds.json").write_text(json.dumps(thresholds), "utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(_empty_readme(script), "utf-8")
    script.main(["--write", "--readme", str(readme), "--data-dir", str(data)])
    assert (
        script.main(["--check", "--readme", str(readme), "--data-dir", str(data)]) == 1
    )
    assert "hybrid recall_at_5" in capsys.readouterr().err


def test_every_question_has_facts_stated_by_its_labelled_chunks(
    script, fixture
) -> None:
    for question in fixture.questions:
        assert question.facts
        assert script.evidence_supports(fixture, question, sorted(question.relevant))


def test_load_fixture_rejects_a_fact_missing_from_the_labelled_chunk(
    script, tmp_path: Path
) -> None:
    data = _copy_data(tmp_path)
    questions = json.loads((data / "retrieval_questions.json").read_text("utf-8"))
    questions["questions"][0]["facts"] = ["not in the chunk"]
    (data / "retrieval_questions.json").write_text(json.dumps(questions), "utf-8")
    with pytest.raises(ValueError, match="q01"):
        script.load_fixture(data)


def test_evidence_support_needs_every_fact_in_the_cited_chunks(script, fixture) -> None:
    multi = next(q for q in fixture.questions if q.id == "q57")
    assert script.evidence_supports(fixture, multi, sorted(multi.relevant))
    assert not script.evidence_supports(fixture, multi, ["d01-c1", "d01-c2"])
    assert not script.evidence_supports(fixture, multi, [])


def test_scorer_agrees_with_every_labelled_citation(script, fixture) -> None:
    labels = DATA_DIR / "retrieval_citation_labels.json"
    assert script.label_mismatches(fixture, labels) == []
    data = json.loads(labels.read_text("utf-8"))
    assert {label["supported"] for label in data["labels"]} == {True, False}


def test_check_fails_when_the_scorer_disagrees_with_a_label(
    script, tmp_path: Path, capsys
) -> None:
    data = _copy_data(tmp_path)
    labels = json.loads((data / "retrieval_citation_labels.json").read_text("utf-8"))
    labels["labels"][0]["supported"] = not labels["labels"][0]["supported"]
    (data / "retrieval_citation_labels.json").write_text(json.dumps(labels), "utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(_empty_readme(script), "utf-8")
    script.main(["--write", "--readme", str(readme), "--data-dir", str(data)])
    assert (
        script.main(["--check", "--readme", str(readme), "--data-dir", str(data)]) == 1
    )
    assert "scorer says" in capsys.readouterr().err


def test_write_latency_fills_only_the_latency_block(script, tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(_empty_readme(script), "utf-8")
    code = script.main(["--write-latency", "--repeats", "1", "--readme", str(readme)])
    text = readme.read_text("utf-8")
    assert code == 0
    latency = script.extract_block(
        text, script.LATENCY_BEGIN_MARKER, script.LATENCY_END_MARKER
    )
    assert latency is not None and "| hybrid |" in latency
    assert script.extract_block(text) == ""


def test_check_requires_the_latency_markers(script, tmp_path: Path, capsys) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(f"{script.BEGIN_MARKER}\n{script.END_MARKER}\n", "utf-8")
    script.main(["--write", "--readme", str(readme)])
    assert script.main(["--check", "--readme", str(readme)]) == 1
    assert script.LATENCY_BEGIN_MARKER in capsys.readouterr().err


def test_percentile_uses_nearest_rank(script) -> None:
    values = [float(n) for n in range(1, 101)]
    assert script.percentile(values, 0.50) == 50.0
    assert script.percentile(values, 0.95) == 95.0


# --- Real embedding model run (stubbed Ollama) ---------------------------------


def _stub_ollama(script: ModuleType, status: int = 200):
    import httpx

    embedder = script.HashedNgramEmbedder()

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text="model not found")
        payload = json.loads(request.content)
        vector = list(embedder.embed(payload["input"]).vector)
        return httpx.Response(
            200, json={"model": payload["model"], "embeddings": [vector]}
        )

    return httpx.MockTransport(handler)


def test_ollama_run_reports_the_model_and_is_not_a_ci_claim(script, fixture) -> None:
    code, text = script.run_ollama(
        fixture,
        "stub-model",
        "http://ollama.invalid",
        1,
        transport=_stub_ollama(script),
    )
    assert code == 0
    assert "`stub-model`" in text
    assert "not checked by CI" in text
    assert "| hybrid |" in text
    assert "query-embedding request" in text


def test_ollama_run_with_a_stub_matches_the_offline_semantic_scores(
    script, fixture
) -> None:
    code, text = script.run_ollama(
        fixture,
        "stub-model",
        "http://ollama.invalid",
        1,
        transport=_stub_ollama(script),
    )
    offline = script.render_block(script.run_evaluation(fixture))
    semantic_row = next(
        line for line in offline.splitlines() if line.startswith("| semantic |")
    )
    assert code == 0 and semantic_row in text


def test_ollama_run_reports_an_unavailable_model(script, fixture) -> None:
    code, text = script.run_ollama(
        fixture,
        "missing-model",
        "http://ollama.invalid",
        1,
        transport=_stub_ollama(script, status=404),
    )
    assert code == 2
    assert "not ready" in text and "missing-model" in text


def test_ollama_mode_cannot_be_combined_with_check(script) -> None:
    with pytest.raises(SystemExit):
        script.main(["--ollama-model", "x", "--check"])
