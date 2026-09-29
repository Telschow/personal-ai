# Reproducibility

## Environment
Python 3.14+
uv package manager
Ollama local instance
OS: Linux/macOS

## Dependencies
Pinned in pyproject.toml and uv.lock
Model versions documented in config

## Test Commands
uv run pytest
uv run pytest tests/test_privacy_regression.py -q
uv run ruff check .
uv run ruff format --check src tests

## Smoke Test
uv run pytest tests/test_smoke.py -q

## Data
Synthetic fixtures only under tests/fixtures/synthetic/
No real personal data required

## Determinism
Unit tests deterministic
Integration tests use synthetic data
Agent tests with mock LLM where possible

## Outputs
Test outputs written to local tmp, never committed
