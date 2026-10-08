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
        f"{script.BEGIN_MARKER}\n| stale table |\n{script.END_MARKER}\n", "utf-8"
    )
    assert script.main(["--check", "--readme", str(readme)]) == 1
    assert "differs" in capsys.readouterr().err


def test_check_fails_without_markers(script, tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("no markers here\n", "utf-8")
    assert script.main(["--check", "--readme", str(readme)]) == 1


def test_write_then_check_round_trips(script, tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(f"{script.BEGIN_MARKER}\n{script.END_MARKER}\n", "utf-8")
    assert script.main(["--write", "--readme", str(readme)]) == 0
    assert script.main(["--check", "--readme", str(readme)]) == 0


def test_check_fails_when_a_threshold_is_raised_above_the_measurement(
    script, tmp_path: Path, capsys
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    for name in ("retrieval_corpus.json", "retrieval_questions.json"):
        (data / name).write_text((DATA_DIR / name).read_text("utf-8"), "utf-8")
    thresholds = json.loads((DATA_DIR / "retrieval_thresholds.json").read_text("utf-8"))
    thresholds["recall_at_5"]["hybrid"] = 0.99
    (data / "retrieval_thresholds.json").write_text(json.dumps(thresholds), "utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(f"{script.BEGIN_MARKER}\n{script.END_MARKER}\n", "utf-8")
    script.main(["--write", "--readme", str(readme), "--data-dir", str(data)])
    assert (
        script.main(["--check", "--readme", str(readme), "--data-dir", str(data)]) == 1
    )
    assert "hybrid recall_at_5" in capsys.readouterr().err


def test_percentile_uses_nearest_rank(script) -> None:
    values = [float(n) for n in range(1, 101)]
    assert script.percentile(values, 0.50) == 50.0
    assert script.percentile(values, 0.95) == 95.0
