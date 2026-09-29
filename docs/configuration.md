# Configuration

## Environment Variables

Copy `.env.example` to `.env` and fill local values.

```bash
cp .env.example .env
```

## Required

* `OLLAMA_BASE_URL=http://localhost:11434`
* `PERSONAL_AI_DATA_DIR=data/private`

## Optional

* `PERSONAL_AI_CHAT_MODEL=qwen3.5:9b`
* `PERSONAL_AI_EMBEDDING_MODEL`
* `PERSONAL_AI_VISION_MODEL`

## Never commit

* `.env`
* credentials
* tokens
* API keys

## Defaults

All defaults are documented in `.env.example`.

## Configuration files

* `job_agent/config.yaml`
* `src/personal_ai/config.py`

## Example

```bash
PERSONAL_AI_DATA_DIR=/path/to/private
echo "PERSONAL_AI_DATA_DIR=$PERSONAL_AI_DATA_DIR" >> .env
```