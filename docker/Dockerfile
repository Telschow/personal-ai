# syntax=docker/dockerfile:1
#
# Production image for the Personal AI gateway.
#
# The image runs the existing module entrypoint `python -m personal_ai.server`
# on a Python >= 3.14 runtime built from pyproject.toml + uv.lock. No console
# script is expected (the project defines none). Runtime state (SQLite
# database and the agent workspace) is never baked in here — it arrives as
# mounted volumes (/data), see docker/docker-compose.yml inside this repo.
#
# Build (from the repository root):
#   docker build -t personal-ai:latest -f docker/Dockerfile .

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS base

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first: uv.lock is the single source of truth; the project is
# installed non-editable so the image ships a real installed copy.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# --no-dev keeps the image free of test/lint tooling (tests/ is also excluded
# from the build context via .dockerignore).
RUN uv sync --frozen --no-dev --no-editable

# Job-Agent: the local job discovery/application package (Phase 4/40+). It is
# installed *editable* so its runtime data (sources_catalog.yaml, profile/,
# templates/) stays at /app/job_agent and resolves through PROJECT_ROOT.
# Providers need the `discovery` extra (DDGS) for the search-fallback track.
COPY job_agent ./job_agent
RUN uv pip install --python /app/.venv/bin/python \
        --editable "/app/job_agent[discovery]"

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Listen on all interfaces so Open WebUI can reach the gateway over the shared
# Docker network. All other configuration arrives through the environment:
#   PERSONAL_AI_WORKSPACE   (agent workspace sandbox, must be a directory)
#   PERSONAL_AI_DATABASE    (SQLite runtime database)
#   JOB_AGENT_DB            (Job-Agent SQLite database, defaults under /data)
#   OLLAMA_BASE_URL         (e.g. http://ollama:11434)
#   PERSONAL_AI_CHAT_MODEL, PERSONAL_AI_API_TOKEN, ...
CMD ["python", "-m", "personal_ai.server", "--host", "0.0.0.0", "--port", "8000"]