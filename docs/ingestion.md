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

* Only the folder you name is read, including its subfolders; symlinks are not followed and
  files that resolve outside the folder are skipped
* No home directory scanning
* No Downloads scanning
* No .git scanning
* No .ssh scanning
* No credential directory scanning

## CLI

```bash
uv run personal-ai --ingest file tests/fixtures/synthetic/ --database knowledge.db
```

## Changed Files

Re-ingesting a folder after a file or note changed stores the new version and retires the
previous one: its document row, chunks, full-text rows, embeddings, structured extraction and
vision pages are deleted, and the summary prints `retired_versions: N`. Versions are tracked per
ingested folder (`file`, `keep` and `notebooklm` sources), so the same relative path in two
folders is two items, and a version still current in another folder is kept. E-mail and
financial records are immutable items and are never retired. A file deleted from the folder
is not removed from the database yet.

## Explicit Paths

Always specify explicit paths. Never use wildcards that could accidentally scan private directories.

## Example

```bash
uv run personal-ai --ingest file /path/to/synthetic/data --database knowledge.db
```