# Open WebUI integration (Personal AI as an OpenAI-compatible backend)

Phase 30 turns the accepted CLI POC into a small local HTTP service that Open
WebUI can sit on top of. It does **not** replace the Agent/retrieval layer and
it does **not** talk to Ollama directly for personal questions.

```
Open WebUI                 (UI layer)
    |
    | OpenAI-compatible connection
    v
Personal AI HTTP API       (this project, POST /v1/chat/completions)
    |
    v
existing Agent  (+ ToolRegistry)
    |
    +---- search_knowledge
    +---- query_events
    +---- activity_summary
    |
    v
personal corpus  +  local Ollama (qwen3.5:9b)
```

> **Why not point Open WebUI straight at Ollama?**
> A direct `Open WebUI -> Ollama` path would bypass the personal-data Agent and
> its ToolRegistry — the layer that actually grounds answers in your stored
> history. For personal questions you want the Agent in the middle. A direct
> Ollama connection is at most a fallback for generic, non-personal chat.

---

## 1. Start the Personal AI API

Pre-warm the model first so the first request is not a cold-load wait:

```bash
curl http://127.0.0.1:11434/api/chat -d '{"model":"qwen3.5:9b","messages":[{"role":"user","content":"hi"}]}'
```

Then start the API (localhost only by default):

```bash
uv run python -m personal_ai.server \
  --workspace /path/to/workspace \
  --database /path/to/corpus.db
```

Defaults:

* Host `127.0.0.1`, port `8000`.
* Model `qwen3.5:9b`, from `PERSONAL_AI_CHAT_MODEL` unless overridden.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `PERSONAL_AI_CHAT_MODEL` | `qwen3.5:9b` | Chat model used for every request. The incoming `model` field is ignored (this is a personal-AI endpoint, not a generic model proxy). |
| `PERSONAL_AI_API_HOST` | `127.0.0.1` | Bind address. Keep localhost unless you explicitly want LAN access (then set a token). |
| `PERSONAL_AI_API_PORT` | `8000` | Bind port. |
| `PERSONAL_AI_API_TOKEN` | *(none)* | Optional bearer token. Required for every request when set. |
| `PERSONAL_AI_WORKSPACE` | current dir | Workspace sandbox (also `--workspace`). |
| `PERSONAL_AI_DATABASE` | *(none)* | Path to the personal corpus SQLite DB (also `--database`). |

CLI flags (`--workspace`, `--database`, `--host`, `--port`, `--token`) take
precedence over environment variables.

### Optional token

For localhost development the token is optional, so a bare
`Open WebUI -> http://127.0.0.1:8000/v1` works immediately. To lock the
service down, export a token and it becomes mandatory:

```bash
export PERSONAL_AI_API_TOKEN='some-secret'
curl -H 'Authorization: Bearer some-secret' \
     -H 'Content-Type: application/json' \
     -d '{"messages":[{"role":"user","content":"What do I know about BCG?"}]}' \
     http://127.0.0.1:8000/v1/chat/completions
```

Behaviour: missing token → `401`; wrong token → `401`; correct token → `200`.
Any non-`Bearer <exact> ` header is rejected.

---

## 2. Configure Open WebUI (local install)

In Open WebUI → **Settings → Connections → OpenAI API / Add connection**:

* **Provider type / API**: `OpenAI` (OpenAI-compatible)
* **Base URL**: `http://127.0.0.1:8000/v1`
* **API key**: the `PERSONAL_AI_API_TOKEN` value **if** one is configured, else
  any non-empty placeholder (e.g. `personal-ai`)
* **Model**: `qwen3.5:9b` (listed by `GET /v1/models`)

Open WebUI talks to:

```
Open WebUI
    -> http://127.0.0.1:8000/v1   (Personal AI API)
    -> GET    /v1/models
    -> POST   /v1/chat/completions
```

The API currently supports **non-streaming** responses (Phase 30). If Open
WebUI requests `stream=true` the API returns `400` with a clear message;
streaming is a Phase 33 follow-up.

---

## 3. Configure Open WebUI when Open WebUI runs in Docker

> **Important:** inside an Open WebUI container, `127.0.0.1` refers to the
> **container itself**, not the host that runs the Personal AI API.

* Personal AI API on the host fixes: `uv run python -m personal_ai.server \
  --database /path/to/corpus.db` binds to `127.0.0.1` on the host.

Options to connect the container to the host service:

### Option A — run the API on the Docker network (recommended for `docker run`)

1. Start the API and let it join the bridge network used by Open WebUI.

   For a default bridge (`docker run --network bridge`), use the host's
   gateway IP. On Linux that is usually `172.17.0.1`:

   ```bash
   PERSONAL_AI_API_HOST=0.0.0.0 uv run python -m personal_ai.server \
     --database /path/to/corpus.db            # or bind 172.17.0.1
   ```

2. In Open WebUI set **Base URL** to `http://172.17.0.1:8000/v1`.

   (On macOS/Windows with Docker Desktop, `host.docker.internal` resolves to
   the host instead, so use `http://host.docker.internal:8000/v1`.)

### Option B — create a shared user-defined bridge network

```bash
docker network create pai-net
# run Open WebUI on pai-net, and run the API with:
#   docker run --network pai-net ... -p 8000:8000 personal-ai
# then Open WebUI Base URL = http://personal-ai:8000/v1
```

In all container cases the Personal AI API must bind to an address the
container can reach — usually `0.0.0.0` or the specific gateway. **Only do this
when you want container networking.** For a purely local Open WebUI install the
**default `127.0.0.1` binding needs no change.**

> **LAN access:** If you expose the API beyond the loopback interface (bind to
> `0.0.0.0` / a LAN IP), **always set `PERSONAL_AI_API_TOKEN`** and pass it to
> Open WebUI.

---

## 4. API reference

### `GET /v1/models`

Lists the single served model for Open WebUI discovery.

```json
{"object":"list","data":[{"id":"qwen3.5:9b","object":"model","created":0,"owned_by":"personal-ai"}]}
```

### `POST /v1/chat/completions`

OpenAI-compatible request (non-streaming). Full `system` / `user` /
`assistant` history is passed straight to the Agent, so multi-turn grounding is
preserved. No extra grounding system prompt is injected.

```json
{
  "model": "qwen3.5:9b",
  "messages": [
    {"role": "system", "content": "Optional system context."},
    {"role": "user", "content": "What do I know about BCG?"}
  ]
}
```

Response:

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1787948472,
  "model": "qwen3.5:9b",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "Based on your history..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
  "system_fingerprint": "personal-ai"
}
```

Internal tool calls are **not** returned as assistant text; you only ever see
the Agent's final answer.

### Errors

| Case | Status |
| --- | --- |
| malformed JSON body | `400` |
| `messages` missing / empty / no `user` message | `400` |
| unsupported role / non-string content | `400` |
| `stream: true` | `400` (not supported in Phase 30) |
| missing or wrong API token | `401` |
| model service unreachable | `502` |
| Agent error / empty answer / max rounds | `500` |

Errors use an OpenAI-style `{"error":{"message","type","param","code"}}` shape.
Details are logged server-side without dumping personal corpus content.

---

## 5. Smoke test

```bash
# model discovery
curl http://127.0.0.1:8000/v1/models

# a real grounded personal question
curl -H 'Content-Type: application/json' \
     -d '{"messages":[{"role":"user","content":"What do I know about BCG?"}]}' \
     http://127.0.0.1:8000/v1/chat/completions
```

The first request after startup can be slow while `qwen3.5:9b` warms up.
Subsequent warm requests run at the normal 13–65 s lookup latency.
