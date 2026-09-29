# Ingestion

## Overview

Ingestion is explicit and deterministic. Data sources must be specified explicitly.

## Supported Sources

* Filesystem: explicit directory paths
* Email: MBOX files
* Chrome history: explicit JSON files
* Financial: explicit CSV/PDF files
* ChatGPT: explicit JSON exports
* Workouts: explicit data files

## Safety

* No recursive directory scanning
* No home directory scanning
* No Downloads scanning
* No .git scanning
* No .ssh scanning
* No credential directory scanning

## CLI

```bash
uv run personal-ai ingest --source filesystem tests/fixtures/synthetic/
```

## Explicit Paths

Always specify explicit paths. Never use wildcards that could accidentally scan private directories.

## Example

```bash
uv run personal-ai ingest --source filesystem /path/to/synthetic/data
```