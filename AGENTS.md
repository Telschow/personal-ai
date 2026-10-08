# Agent rules: Personal AI

Rules for any coding agent working in this repository. `CLAUDE.md` holds the commands and invariants; this file holds the design constraints.

**Architecture:**
```
CLI -> Agent -> ToolRegistry -> FilesystemTool -> sandboxed workspace
```

**Tech stack:** Python 3.12+, FastAPI, SQLite, Ollama, Pydantic, Uvicorn

**Quality gates:**
```bash
uv run pytest
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run python scripts/mypy_ratchet.py
```

**Constraints:**
- Local-first: prefer local services and models (Ollama).
- Provider independence: the Ollama client stays at the infrastructure boundary.
- Testability: business logic is testable without network, Ollama or a filesystem outside a temp directory.
- Security: the filesystem workspace sandbox is a hard boundary.
- Explicit tools only: no eval, exec, shell or dynamic imports.
- Data privacy: avoid logging personal content; synthetic data only in the repository.
- Small components, avoid premature complexity.

The plan and exit tests are in `docs/roadmap.md`.
