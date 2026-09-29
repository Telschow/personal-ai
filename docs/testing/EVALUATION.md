# Evaluation Strategy

## RAG Evaluation
Synthetic corpus under tests/fixtures/synthetic/eval/
Metrics: retrieval relevance, recall, precision, ranking, citation provenance, context construction

## Memory Evaluation
Test creation, retrieval, update, contradiction, stale info, deletion, provenance, source attribution
Ensure memory does not absorb unrelated context

## Agent Evaluation
Test correct tool selection, invalid selection, tool failures, malformed output, loops, timeouts, retries, hallucinated tools, prompt injection resistance

## Reproducibility
Python 3.14+, uv, Ollama local
Document model versions, dataset versions
Single command: uv run pytest
Smoke test: uv run pytest tests/test_smoke.py

## Benchmarking
Measure latency, retrieval time, embedding time, model inference, memory use, storage size, throughput
Report only synthetic benchmarks
