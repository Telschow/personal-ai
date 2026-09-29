# ADR-001: Data Directory Separation

## Context
Personal data must stay local and outside source control.

## Decision
Use data/private/, data/local/, data/runtime/ for runtime data.
.gitignore covers data/, Financial_data/, previous_project_and_raw_data/
.env.example provided, .env never committed.

## Consequences
Tests must use synthetic fixtures.
Ingestion must be explicit.
