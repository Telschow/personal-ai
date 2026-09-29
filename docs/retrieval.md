# Retrieval

## Overview

Retrieval provides fast, deterministic search across stored personal data.

## Modes

* Keyword: SQLite FTS5 BM25 search (default, always works)
* Semantic: Vector cosine similarity search (requires embedding model)
* Hybrid: RRF fusion of keyword and semantic search

## Features

* Multi-store search: documents, extractions, conversations, memories
* Deterministic ranking and fusion
* Provenance tracking with source IDs
* Scope-based filtering for memories
* Context budgeting for LLM consumption

## CLI

```bash
uv run personal-ai search --query "example topic"
```

## Hybrid Search

Configure `PERSONAL_AI_RETRIEVAL_MODE=hybrid` in `.env` to enable hybrid retrieval. Requires embeddings to be pre-computed via `personal-ai embeddings backfill`.

## Safety

Retrieval operations are read-only and never mutate stored data. Length limits prevent context overflow.