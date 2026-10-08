"""Measured retrieval evaluation over the committed synthetic question set.

Compares the three production ``ChunkIndex`` backends (keyword BM25, semantic
cosine, hybrid RRF) on the fixtures in ``tests/fixtures/synthetic/eval/`` and
scores them with recall@5, MRR@5 and evidence support@5. Everything is offline and deterministic:
an in-memory SQLite database, no network, no Ollama, no wall-clock reads in the
quality numbers.

The semantic backend needs vectors, and this repository's tests must not
depend on a model server. It therefore uses ``HashedNgramEmbedder`` below, a
fixed hashed word and character-trigram vectoriser. It is NOT a neural
embedding model: it sees surface form, not meaning, so the semantic and
hybrid numbers here say little about what a real embedding model would score
on paraphrases.

Usage:

    uv run python scripts/eval_retrieval.py            # print the tables
    uv run python scripts/eval_retrieval.py --write    # refresh the README block
    uv run python scripts/eval_retrieval.py --check    # CI gate, exit 1 on drift
    uv run python scripts/eval_retrieval.py --latency  # print p50/p95
    uv run python scripts/eval_retrieval.py --write-latency  # refresh latency block

Evidence support@5 asks: do the five chunks a backend would cite for a question
together contain every fact the answer needs (``facts`` in the question file)?
It measures whether a grounded answer is possible from the cited evidence. It
does NOT score the faithfulness of generated text, which needs a model. The
scorer itself is checked against ``retrieval_citation_labels.json``.

``--check`` regenerates the tables, fails if they differ from the block in
``README.md`` between the ``retrieval-eval`` markers, fails if any backend
falls below ``tests/fixtures/synthetic/eval/retrieval_thresholds.json``, and
fails if the scorer disagrees with a labelled citation. Latency depends on the
machine, so ``--write-latency`` fills its own README block, which ``--check``
only requires to exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from personal_ai.documents import Document, DocumentChunk, Embedding
from personal_ai.documents.models import compute_content_hash
from personal_ai.hybrid_index import HybridChunkIndex
from personal_ai.retrieval_evaluation import (
    ChunkSearcher,
    EvaluationCase,
    EvaluationResult,
    evaluate_case,
)
from personal_ai.semantic_index import SemanticChunkIndex
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    connect_database,
)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "tests" / "fixtures" / "synthetic" / "eval"
README = ROOT / "README.md"

K = 5
EMBEDDING_MODEL = "hashed-ngram-256"
EMBEDDING_DIMENSIONS = 256
BACKENDS = ("keyword", "semantic", "hybrid")
CATEGORIES = ("keyword", "identifier", "question", "paraphrase", "multi")

BEGIN_MARKER = "<!-- retrieval-eval:begin -->"
END_MARKER = "<!-- retrieval-eval:end -->"
LATENCY_BEGIN_MARKER = "<!-- retrieval-latency:begin -->"
LATENCY_END_MARKER = "<!-- retrieval-latency:end -->"
FIXED_TIMESTAMP = "2024-01-01T00:00:00+00:00"

_WORD = re.compile(r"[a-z0-9]+")


class HashedNgramEmbedder:
    """Deterministic offline vectoriser: hashed words plus character trigrams.

    Features are hashed with BLAKE2b (never Python's salted ``hash``), counted
    into ``EMBEDDING_DIMENSIONS`` buckets and damped with ``log1p``. Same text,
    same vector, on every machine.
    """

    model = EMBEDDING_MODEL

    def embed(self, text: str) -> Embedding:
        words = _WORD.findall(text.lower())
        features = list(words)
        for word in words:
            padded = f"#{word}#"
            features.extend(padded[i : i + 3] for i in range(len(padded) - 2))
        counts = [0.0] * EMBEDDING_DIMENSIONS
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            counts[int.from_bytes(digest, "big") % EMBEDDING_DIMENSIONS] += 1.0
        return Embedding(
            model=self.model, vector=tuple(math.log1p(value) for value in counts)
        )


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    category: str
    query: str
    relevant: frozenset[str]
    facts: tuple[str, ...]  # lowercase substrings the cited evidence must contain


@dataclass(frozen=True, slots=True)
class Fixture:
    chunks: tuple[tuple[str, str, str], ...]  # (chunk id, document id, text)
    questions: tuple[Question, ...]

    def text_of(self, chunk_id: str) -> str:
        return next(text for cid, _, text in self.chunks if cid == chunk_id)


def evidence_supports(
    fixture: Fixture, question: Question, cited: Sequence[str]
) -> bool:
    """True iff every required fact occurs in at least one cited chunk."""
    texts = [fixture.text_of(chunk_id).lower() for chunk_id in cited]
    return all(any(fact in text for text in texts) for fact in question.facts)


def load_fixture(data_dir: Path = DATA_DIR) -> Fixture:
    """Load the corpus and question set and check the labels are consistent."""
    corpus = json.loads((data_dir / "retrieval_corpus.json").read_text("utf-8"))
    raw_questions = json.loads(
        (data_dir / "retrieval_questions.json").read_text("utf-8")
    )
    chunks = tuple(
        (f"{document['id']}-c{number}", document["id"], text)
        for document in corpus["documents"]
        for number, text in enumerate(document["chunks"], start=1)
    )
    known = {chunk_id for chunk_id, _, _ in chunks}
    if len(known) != len(chunks):
        msg = "duplicate chunk ids in retrieval_corpus.json"
        raise ValueError(msg)
    questions = tuple(
        Question(
            id=item["id"],
            category=item["category"],
            query=item["query"],
            relevant=frozenset(item["relevant"]),
            facts=tuple(item["facts"]),
        )
        for item in raw_questions["questions"]
    )
    for question in questions:
        if question.category not in CATEGORIES:
            msg = f"{question.id}: unknown category {question.category!r}"
            raise ValueError(msg)
        missing = question.relevant - known
        if missing or not question.relevant:
            msg = f"{question.id}: relevant ids missing or unknown: {sorted(missing)}"
            raise ValueError(msg)
    if len({question.id for question in questions}) != len(questions):
        msg = "duplicate question ids in retrieval_questions.json"
        raise ValueError(msg)
    fixture = Fixture(chunks=chunks, questions=questions)
    for question in questions:
        if not question.facts or any(f != f.lower() for f in question.facts):
            msg = f"{question.id}: facts must be non-empty and lowercase"
            raise ValueError(msg)
        if not evidence_supports(fixture, question, sorted(question.relevant)):
            msg = f"{question.id}: the labelled chunks do not contain every fact"
            raise ValueError(msg)
    return fixture


def label_mismatches(fixture: Fixture, labels_path: Path) -> list[str]:
    """Return one message per labelled citation the scorer judges differently."""
    labels = json.loads(labels_path.read_text("utf-8"))["labels"]
    by_id = {question.id: question for question in fixture.questions}
    mismatches = []
    for label in labels:
        question = by_id[label["question"]]
        verdict = evidence_supports(fixture, question, label["cited"])
        if verdict != label["supported"]:
            mismatches.append(
                f"{label['question']} cited {label['cited']}: scorer says "
                f"{verdict}, label says {label['supported']}"
            )
    return mismatches


def build_backends(fixture: Fixture) -> tuple[Mapping[str, ChunkSearcher], object]:
    """Seed an in-memory database and return the three backends plus the connection."""
    connection = connect_database(":memory:")
    documents = DocumentStore(connection)
    chunks = ChunkStore(connection)
    embeddings = EmbeddingStore(connection)
    embedder = HashedNgramEmbedder()
    for document_id in sorted({document_id for _, document_id, _ in fixture.chunks}):
        documents.add(
            Document(
                id=document_id,
                source="synthetic/retrieval_corpus.json",
                source_type="eval",
                content_hash=compute_content_hash(b"synthetic"),
                created_at=FIXED_TIMESTAMP,
                modified_at=FIXED_TIMESTAMP,
                metadata={},
            )
        )
    for chunk_id, document_id, text in fixture.chunks:
        chunks.add(DocumentChunk(id=chunk_id, document_id=document_id, text=text))
        embeddings.add(embedder.embed(text), chunk_id)
    semantic = SemanticChunkIndex(connection, embedder)
    backends: dict[str, ChunkSearcher] = {
        "keyword": chunks,
        "semantic": semantic,
        "hybrid": HybridChunkIndex(chunks, SemanticChunkIndex(connection, embedder)),
    }
    return backends, connection


@dataclass(frozen=True, slots=True)
class Scores:
    recall: float
    mrr: float
    support: float


@dataclass(frozen=True, slots=True)
class Report:
    question_count: int
    chunk_count: int
    overall: dict[str, Scores]
    by_category: dict[str, dict[str, float]]  # backend -> category -> recall@K
    category_counts: dict[str, int]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def score(fixture: Fixture, results: Sequence[EvaluationResult]) -> Report:
    question_of = {question.id: question for question in fixture.questions}
    category_of = {question.id: question.category for question in fixture.questions}
    overall: dict[str, Scores] = {}
    by_category: dict[str, dict[str, float]] = {}
    for backend in BACKENDS:
        own = [result for result in results if result.backend == backend]
        overall[backend] = Scores(
            recall=_mean([result.recall for result in own]),
            mrr=_mean([result.reciprocal_rank for result in own]),
            support=_mean(
                [
                    float(
                        evidence_supports(
                            fixture,
                            question_of[result.case.name],
                            result.returned_chunk_ids,
                        )
                    )
                    for result in own
                ]
            ),
        )
        grouped: dict[str, list[float]] = defaultdict(list)
        for result in own:
            grouped[category_of[result.case.name]].append(result.recall)
        by_category[backend] = {
            category: _mean(grouped[category]) for category in CATEGORIES
        }
    counts = {
        category: sum(
            1 for question in fixture.questions if question.category == category
        )
        for category in CATEGORIES
    }
    return Report(
        question_count=len(fixture.questions),
        chunk_count=len(fixture.chunks),
        overall=overall,
        by_category=by_category,
        category_counts=counts,
    )


def run_evaluation(fixture: Fixture) -> Report:
    backends, connection = build_backends(fixture)
    try:
        results: list[EvaluationResult] = []
        for question in fixture.questions:
            case = EvaluationCase(
                name=question.id,
                query=question.query,
                relevant_chunk_ids=question.relevant,
            )
            results.extend(evaluate_case(case, backends, k=K))
    finally:
        connection.close()  # type: ignore[attr-defined]
    return score(fixture, results)


def render_block(report: Report) -> str:
    """Render the deterministic Markdown block that lives in the README."""
    lines = [
        (
            f"Measured on {report.question_count} synthetic questions over "
            f"{report.chunk_count} chunks, k={K}. The semantic backend uses "
            f"`{EMBEDDING_MODEL}`, an offline hashed word and character n-gram "
            "vectoriser, not a neural embedding model. Evidence support@5 is the "
            "share of questions whose five cited chunks contain every fact the "
            "answer needs; it does not score generated text."
        ),
        "",
        f"| Backend | recall@{K} | MRR@{K} | evidence support@{K} |",
        "|---------|-----------:|--------:|---------------------:|",
    ]
    for backend in BACKENDS:
        scores = report.overall[backend]
        lines.append(
            f"| {backend} | {scores.recall:.3f} | {scores.mrr:.3f} "
            f"| {scores.support:.3f} |"
        )
    header = " | ".join(
        f"{category} ({report.category_counts[category]})" for category in CATEGORIES
    )
    lines += [
        "",
        f"recall@{K} by question category (question count in brackets):",
        "",
        f"| Backend | {header} |",
        "|---------|" + "|".join("---:" for _ in CATEGORIES) + "|",
    ]
    for backend in BACKENDS:
        cells = " | ".join(
            f"{report.by_category[backend][category]:.3f}" for category in CATEGORIES
        )
        lines.append(f"| {backend} | {cells} |")
    return "\n".join(lines)


def _span(
    text: str, begin: str = BEGIN_MARKER, end: str = END_MARKER
) -> tuple[int, int] | None:
    start = text.find(begin)
    stop = text.find(end)
    if start == -1 or stop == -1 or stop < start:
        return None
    return start + len(begin), stop


def extract_block(
    readme_text: str, begin: str = BEGIN_MARKER, end: str = END_MARKER
) -> str | None:
    span = _span(readme_text, begin, end)
    return None if span is None else readme_text[span[0] : span[1]].strip("\n")


def replace_block(
    readme_text: str,
    block: str,
    begin: str = BEGIN_MARKER,
    end: str = END_MARKER,
) -> str:
    span = _span(readme_text, begin, end)
    if span is None:
        msg = f"README is missing the {begin} / {end} markers"
        raise ValueError(msg)
    return readme_text[: span[0]] + "\n" + block + "\n" + readme_text[span[1] :]


def threshold_failures(report: Report, thresholds: Mapping[str, object]) -> list[str]:
    """Return one message per backend metric below its committed floor."""
    failures = []
    floors = {
        "recall_at_5": {b: s.recall for b, s in report.overall.items()},
        "mrr_at_5": {b: s.mrr for b, s in report.overall.items()},
        "support_at_5": {b: s.support for b, s in report.overall.items()},
    }
    for metric, measured in floors.items():
        minimums = thresholds[metric]
        assert isinstance(minimums, dict)
        for backend in BACKENDS:
            if measured[backend] < minimums[backend]:
                failures.append(
                    f"{backend} {metric} {measured[backend]:.3f} "
                    f"< threshold {minimums[backend]:.3f}"
                )
    return failures


def percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile of an ascending sequence."""
    index = max(0, math.ceil(fraction * len(sorted_values)) - 1)
    return sorted_values[index]


def measure_latency(fixture: Fixture, repeats: int) -> dict[str, tuple[float, float]]:
    """Return ``(p50_ms, p95_ms)`` of one ``search`` call per backend."""
    backends, connection = build_backends(fixture)
    try:
        samples: dict[str, list[float]] = {backend: [] for backend in BACKENDS}
        for _ in range(repeats):
            for question in fixture.questions:
                for backend in BACKENDS:
                    started = time.perf_counter()
                    backends[backend].search(question.query, limit=K)
                    samples[backend].append((time.perf_counter() - started) * 1000)
    finally:
        connection.close()  # type: ignore[attr-defined]
    return {
        backend: (
            percentile(sorted(values), 0.50),
            percentile(sorted(values), 0.95),
        )
        for backend, values in samples.items()
    }


def _cpu_name() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text("utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def render_latency_block(
    fixture: Fixture, latency: Mapping[str, tuple[float, float]], repeats: int
) -> str:
    """Render the machine-dependent latency block for the README."""
    lines = [
        (
            f"Latency of one `search` call (limit={K}) over "
            f"{len(fixture.questions)} queries x {repeats} repeats, in-memory "
            f"SQLite, embedder `{EMBEDDING_MODEL}`, no model server. Measured on "
            f"Python {platform.python_version()}, {platform.system()} "
            f"{platform.machine()}, {_cpu_name()}. These numbers depend on the "
            "machine: regenerate them with `--write-latency`; `--check` does not "
            "compare them. A real embedding model adds its own query-embedding "
            "time, which is not measured here."
        ),
        "",
        "| Backend | p50 ms | p95 ms |",
        "|---------|-------:|-------:|",
    ]
    for backend in BACKENDS:
        p50, p95 = latency[backend]
        lines.append(f"| {backend} | {p50:.3f} | {p95:.3f} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="compare with README")
    mode.add_argument("--write", action="store_true", help="refresh README block")
    mode.add_argument("--latency", action="store_true", help="print p50/p95")
    mode.add_argument(
        "--write-latency", action="store_true", help="refresh README latency block"
    )
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--readme", type=Path, default=README)
    args = parser.parse_args(argv)

    fixture = load_fixture(args.data_dir)

    if args.latency or args.write_latency:
        latency = measure_latency(fixture, args.repeats)
        latency_block = render_latency_block(fixture, latency, args.repeats)
        if args.write_latency:
            args.readme.write_text(
                replace_block(
                    args.readme.read_text("utf-8"),
                    latency_block,
                    LATENCY_BEGIN_MARKER,
                    LATENCY_END_MARKER,
                ),
                "utf-8",
            )
            print(f"updated {args.readme}")
        else:
            print(latency_block)
        return 0

    report = run_evaluation(fixture)
    block = render_block(report)

    if args.write:
        args.readme.write_text(
            replace_block(args.readme.read_text("utf-8"), block), "utf-8"
        )
        print(f"updated {args.readme}")
        return 0

    if not args.check:
        print(block)
        return 0

    problems = []
    readme_text = args.readme.read_text("utf-8")
    current = extract_block(readme_text)
    if current is None:
        problems.append(f"README is missing the {BEGIN_MARKER} markers")
    elif current.strip() != block.strip():
        problems.append(
            "README table differs from the measured table; run "
            "`uv run python scripts/eval_retrieval.py --write`"
        )
    if extract_block(readme_text, LATENCY_BEGIN_MARKER, LATENCY_END_MARKER) is None:
        problems.append(f"README is missing the {LATENCY_BEGIN_MARKER} markers")
    thresholds = json.loads(
        (args.data_dir / "retrieval_thresholds.json").read_text("utf-8")
    )
    problems.extend(threshold_failures(report, thresholds))
    problems.extend(
        label_mismatches(fixture, args.data_dir / "retrieval_citation_labels.json")
    )
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("ok: README table matches, thresholds hold, scorer agrees with labels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
