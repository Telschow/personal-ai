# CLAUDE.md

## Project
personal-ai: local-first personal knowledge and agent platform. SQLite (FTS5 + optional
embeddings), hybrid RRF retrieval with provenance, governed memory, bounded tool-calling agent
loop, policy engine with human approval, OpenAI-compatible API, MCP server.

## Commands
- `uv sync --locked --dev`
- `uv run pytest -q`
- `uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`
- `uv run python scripts/mypy_ratchet.py`
- Offline demo: `uv run python -m personal_ai.demo`

## Invariants
- Synthetic data only in the repository; `tests/test_privacy_regression.py` must pass.
- No real names, e-mails, salaries, employers, private corpus statistics in any tracked file.
- No action executes without policy approval; demo test enforces the approval boundary.
- Supported Python: 3.12+. Do not use 3.14-only syntax.
- No file names differing only by case.

## Writing rules
- No em dashes. One README section per topic. No new markdown unless a reviewer will read it.
- Every quality claim needs a reproducible command and a number.

## Workflow
- Small PRs, tests, green CI. Ask before force-push, history rewrite, visibility changes.
