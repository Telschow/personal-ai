# Installation

## Requirements

* Python 3.14+
* uv package manager
* Ollama (local model server)

## Steps

```bash
git clone https://github.com/Telschow/personal-ai
cd personal-ai
uv sync
```

## Ollama

Install Ollama and pull a model:

```bash
ollama pull qwen3.5:9b
```

## Verify

```bash
uv run personal-ai --help
```