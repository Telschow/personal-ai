"""Measured retrieval evaluation over the committed synthetic question set.

Compares the three production ``ChunkIndex`` backends (keyword BM25, semantic
cosine, hybrid RRF) on the fixtures in ``tests/fixtures/synthetic/eval/`` and
scores them with recall@5 and MRR@5. Everything is offline and deterministic:
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
    uv run python scripts/eval_retrieval.py --latency  # p50/p95, not checked

``--check`` regenerates the tables, fails if they differ from the block in
``README.md`` between the ``retrieval-eval`` markers, and fails if any backend
falls below ``tests/fixtures/synthetic/eval/retrieval_thresholds.json``.
Latency depends on the machine, so it is never part of the checked block.
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


@dataclass(frozen=True, slots=True)
class Fixture:
    chunks: tuple[tuple[str, str, str], ...]  # (chunk id, document id, text)
    questions: tuple[Question, ...]


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
    return Fixture(chunks=chunks, questions=questions)


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
    category_of = {question.id: question.category for question in fixture.questions}
    overall: dict[str, Scores] = {}
    by_category: dict[str, dict[str, float]] = {}
    for backend in BACKENDS:
        own = [result for result in results if result.backend == backend]
        overall[backend] = Scores(
            recall=_mean([result.recall for result in own]),
            mrr=_mean([result.reciprocal_rank for result in own]),
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
            "vectoriser, not a neural embedding model."
        ),
        "",
        f"| Backend | recall@{K} | MRR@{K} |",
        "|---------|-----------:|--------:|",
    ]
    for backend in BACKENDS:
        scores = report.overall[backend]
        lines.append(f"| {backend} | {scores.recall:.3f} | {scores.mrr:.3f} |")
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


def extract_block(readme_text: str) -> str | None:
    start = readme_text.find(BEGIN_MARKER)
    end = readme_text.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        return None
    return readme_text[start + len(BEGIN_MARKER) : end].strip("\n")


def replace_block(readme_text: str, block: str) -> str:
    start = readme_text.find(BEGIN_MARKER)
    end = readme_text.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        msg = f"README is missing the {BEGIN_MARKER} / {END_MARKER} markers"
        raise ValueError(msg)
    return (
        readme_text[: start + len(BEGIN_MARKER)]
        + "\n"
        + block
        + "\n"
        + readme_text[end:]
    )


def threshold_failures(report: Report, thresholds: Mapping[str, object]) -> list[str]:
    """Return one message per backend metric below its committed floor."""
    failures = []
    floors = {
        "recall_at_5": {b: s.recall for b, s in report.overall.items()},
        "mrr_at_5": {b: s.mrr for b, s in report.overall.items()},
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="compare with README")
    mode.add_argument("--write", action="store_true", help="refresh README block")
    mode.add_argument("--latency", action="store_true", help="print p50/p95")
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--readme", type=Path, default=README)
    args = parser.parse_args(argv)

    fixture = load_fixture(args.data_dir)

    if args.latency:
        latency = measure_latency(fixture, args.repeats)
        print(
            f"{len(fixture.questions)} queries x {args.repeats} repeats, "
            f"limit={K}, embedder={EMBEDDING_MODEL}, no model server"
        )
        print(
            f"python {platform.python_version()} on {platform.system()} "
            f"{platform.machine()}, cpu: {_cpu_name()}"
        )
        print("backend    p50_ms  p95_ms")
        for backend in BACKENDS:
            p50, p95 = latency[backend]
            print(f"{backend:<9} {p50:>7.3f} {p95:>7.3f}")
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
    current = extract_block(args.readme.read_text("utf-8"))
    if current is None:
        problems.append(f"README is missing the {BEGIN_MARKER} markers")
    elif current.strip() != block.strip():
        problems.append(
            "README table differs from the measured table; run "
            "`uv run python scripts/eval_retrieval.py --write`"
        )
    thresholds = json.loads(
        (args.data_dir / "retrieval_thresholds.json").read_text("utf-8")
    )
    problems.extend(threshold_failures(report, thresholds))
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("ok: README table matches and all thresholds hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
