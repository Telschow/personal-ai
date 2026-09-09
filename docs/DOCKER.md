# Dockerized Personal AI gateway (legacy Phase 45)

This repo ships a production compose setup that runs the existing
`personal_ai` HTTP gateway (`python -m personal_ai.server`) as a Docker
service that Open WebUI talks to on **`http://personal-ai:8000`** — a pure
Docker-internal name, no host port, no `host.docker.internal`, no Tailscale
address and no host firewall holes.

```
Open WebUI (UI)  ──────  Open WebUI project network: open-webui_default
                              │  http://personal-ai:8000/v1
                              ▼
                    personal-ai  (this repo, Docker Compose)
                              │  http://ollama:11434
                              ▼
                    ollama  (Ollama project network: ollama_default)
```

## Why these two networks?

The gateway joins two **external** Docker networks that already exist on the
host:

- `open-webui_default` — so the `open-webui` container can reach it;
- `ollama_default` — so it can reach the `ollama` container's HTTP API.

This keeps every hop on container bridge networks. The host firewall's
`FORWARD` policy is DROP, which blocks Docker→`host:8000`, so the old
`host.docker.internal:8000` workaround cannot work here; shared networks are
the clean replacement. The gateway publishes **no host ports** at all — port
8000 is private to those networks (you do not want your personal agent exposed
on the LAN, let alone Tailscale).

`~/open-webui/docker-compose.yml` (the Open WebUI project) is left
untouched: Open WebUI discovers the gateway simply because the gateway joins
Open WebUI's network. Its `OLLAMA_BASE_URL: http://ollama:11434` and
`WEBUI_URL: ...` settings are preserved unmodified.

## Build & run

From the repository root:

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

- builds `personal-ai:latest` from `docker/Dockerfile` (Python ≥ 3.14 via uv,
  `uv lock`-pinned, non-editable install of the existing module entrypoint),
- starts the `personal-ai` container (`restart: unless-stopped`),
- mounts `./data` as `/data` and `./data/workspace` as the agent workspace,
- configures `PERSONAL_AI_WORKSPACE`, `PERSONAL_AI_DATABASE`,
  `OLLAMA_BASE_URL=http://ollama:11434` and an overridable
  `PERSONAL_AI_CHAT_MODEL` (default `qwen3.5:9b`).

Environment overrides work as with any compose file, e.g.:

```bash
PERSONAL_AI_CHAT_MODEL=qwen3.5:14b \
  docker compose -f docker/docker-compose.yml up -d --build
```

## Verify

Healthcheck (compose): `http://127.0.0.1:8000/v1/models` inside the container.

```bash
docker exec personal-ai \  # from anywhere
  python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/v1/models', timeout=5).read().decode())"

# the exact Open WebUI hop — run from inside the Open WebUI container:
docker exec open-webui curl -s http://personal-ai:8000/v1/models
```

Expected models payload (served id is always `personal-ai`):

```json
{"object":"list","data":[{"id":"personal-ai","object":"model","created":0,"owned_by":"personal-ai"}]}
```

Then point Open WebUI at the gateway:

1. Open WebUI → **Settings → Connections → OpenAI**.
2. API base URL: `http://personal-ai:8000/v1` (Open WebUI's own container must
   be on Open WebUI's network for this name to resolve).
3. API key: blank (unless `PERSONAL_AI_API_TOKEN` is set in the compose env).
4. Model id: `personal-ai`. Turn on a streaming client for SSE chat.

## Managing the gateway

```bash
docker compose -f docker/docker-compose.yml down            # stop
docker compose -f docker/docker-compose.yml up -d           # start (no rebuild)
docker compose -f docker/docker-compose.yml logs -f         # logs
docker compose -f docker/docker-compose.yml ps              # status + health
docker compose -f docker/docker-compose.yml down -v         # stop + drop the
                                                            # *empty* named
                                                            # networks (do not
                                                            # use on shared nets)
```

## Persistence & security notes

- The SQLite database and agent workspace live in `./data` on the host and
  survive container restarts/rebuilds. Re-ingestion is idempotent (content
  hashes), so rebuilding the image never duplicates documents.
- `Email_Outlook/`, `Financial_data/`, `Workouts/`, `raw_data.zip`,
  `previous_project_and_raw_data/`, `knowledge.db`, `tests/` and all Python
  build artifacts are excluded from the build context via `.dockerignore` —
  private personal data can never enter the image or a published registry.
- No broker, no vector DB, no cloud API is added; the gateway is the existing
  local-first service, containerized.