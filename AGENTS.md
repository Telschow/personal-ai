# Agent Skills (OpenCode): Personal AI

This project uses skills installed under `.opencode/skills/`.

## Core Rules

- If a task matches a skill, invoke it with the `skill` tool before acting.
- Skills are located in `.opencode/skills/<skill-name>/SKILL.md`.
- Follow the skill workflow strictly; do not partially apply it.
- Never skip required steps such as spec, plan, or test when a skill demands them.

## Intent → Skill Mapping

Map the user's intent to the matching skill automatically:

- **Feature / new functionality** → `spec-driven-development`, then `incremental-implementation` and `test-driven-development`
- **Planning / breakdown** → `planning-and-task-breakdown`
- **Bug / failure / unexpected behavior** → `debugging-and-error-recovery`
- **Code review** → `code-review-and-quality`
- **Refactoring / simplification** → `code-simplification` (if installed) or `incremental-implementation`
- **API or interface design** → `api-and-interface-design` (if installed) or `spec-driven-development`
- **UI work** → `frontend-ui-engineering` (if installed) or `spec-driven-development`
- **Documentation / ADRs** → `documentation-and-adrs`
- **Security hardening** → `security-and-hardening`
- **Performance optimization** → `performance-optimization`
- **Release / shipping** → `shipping-and-launch`
- **Git workflow / versioning** → `git-workflow-and-versioning`
- **Source-driven development** → `source-driven-development`

## Project-Specific Context

This is **Personal AI**: a local-first personal knowledge and agent system.

**Architecture:**
```
CLI → Agent → ToolRegistry → FilesystemTool → sandboxed workspace
```

**Tech stack:** Python 3.12+, FastAPI, SQLite, Ollama, Pydantic, Uvicorn

**Quality gates:**
```bash
uv run pytest
uv run ruff check .
uv run ruff format --check src tests
```

**Constraints:**
- Local-first: prefer local services/models (Ollama)
- Provider independence: Ollama client stays at infrastructure boundary
- Testability: business logic testable without network/Ollama/filesystem outside tmp
- Security: filesystem workspace sandbox is hard boundary
- Explicit tools only: no eval/exec/shell/dynamic imports
- Data privacy: avoid logging personal content
- Small components, avoid premature complexity

## Execution Model

For every request:

1. Determine if any skill applies (even a small chance).
2. Load the skill with `skill({ name: "<skill-name>" })`.
3. Follow the skill workflow exactly.
4. Only proceed to implementation once required steps are complete.

## Current Phase

See `docs/roadmap.md`.
