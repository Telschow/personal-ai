# Limitations

## Local LLM Quality

* Depends on model choice and hardware capabilities
* Smaller models may have reduced reasoning ability
* Quantization may affect output quality

## Vector Search Optional

* Semantic search requires embedding model and pre-computed embeddings
* Without embedding model, falls back to keyword search only
* Embedding generation is a separate backfill step

## Explicit Ingestion

* No automatic monitoring of personal directories
* Users must explicitly specify source paths for ingestion
* No scheduled ingestion by default

## Memory Management

* No automatic expiration or archiving of old memories
* Memories accumulate unless manually purged
* Physical purge option removes record and content

## Job Agent Integration

* Read-only SQLite bridge only
* Job Agent cannot modify Personal AI state
* Career assessments depend on Personal AI data freshness

## Synthetic Data Requirement

* Public repository contains no real personal data
* All examples and tests use synthetic data
* Real personal data must be provided locally by user

## Platform Assumptions

* Primarily developed and tested on Linux
* Docker deployment uses host.docker.internal for Ollama
* Windows/macOS support may require adjustments

## Performance

* Large personal corpora may affect retrieval latency
* Embedding storage grows with document count
* Memory table grows with number of memories

## Security Model

* Local trusted zone assumes user machine is secure
* No protection against malware on host system
* External model providers (if used) receive data per configuration