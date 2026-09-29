# Contributing to Personal AI

We welcome contributions. Please follow these guidelines.

## Setup

```bash
git clone https://github.com/Telschow/personal-ai
cd personal-ai
uv sync
```

## Tests

Run the test suite:

```bash
uv run pytest
```

Run specific tests:

```bash
uv run pytest tests/test_privacy_regression.py
```

## Lint and Format

```bash
uv run ruff check .
uv run ruff format .
```

## Type Checking

```bash
uv run mypy src
```

## Adding Connectors

1. Create source adapter in src/personal_ai/sources/
2. Follow safety guidelines: path validation, error isolation, no code execution
3. Add tests under tests/sources/
4. Ensure adapter works with synthetic test data

## Adding Tools

1. Create handler in src/personal_ai/tools/
2. Register in tool registry with name, description, parameters, permissions
3. Add tests
4. Follow safety: result bounding, argument validation, error handling

## Adding Models

1. Configure via environment variables
2. Ensure model works with local Ollama
3. Update documentation if new capability needed

## Privacy Requirements

* Never commit real personal data
* Use synthetic fixtures only (example.invalid domains, synthetic names)
* Do not include credentials, tokens, API keys in commits
* Respect .gitignore: data/, Financial_data/, previous_project_and_raw_data/
* Logging must not include private data

## Documentation

Update relevant docs in docs/ when changing behavior.

## Code Style

Follow existing style in the repository. Ruff enforces formatting.

## Commit Messages

Use conventional commits: feat, fix, docs, test, chore, refactor, perf, ci, style.

## Questions

Open an issue or start a discussion.