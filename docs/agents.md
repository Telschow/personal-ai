# Agents

## Overview

The agent is a synchronous tool-calling loop against a local LLM.

## Core Components

* Agent: tool-calling loop with max rounds (default 8)
* PolicyEngine: gates tool execution (ALLOWED/DENIED/APPROVAL_REQUIRED)
* ModelRouter: routes capability requests to configured models
* Observer: operational logging

## Tools

Tools are registered in the tool registry with explicit JSON schemas. Examples:
* personal_context: read-only overview of personal data
* list_directory: list files in workspace
* get_document: fetch document metadata + bounded chunk window
* get_memory: fetch memory by id with content-free provenance
* query_events: structural temporal-event queries
* search_workouts: search workout activity
* search_people / get_person: search/fetch people
* search_documents: search document chunks
* search_knowledge: unified search
* propose_memory: propose a new memory (requires approval)

## Permissions

Permissions include: AGENT, TOOL, MEMORY_READ, MEMORY_WRITE, WORKOUT_READ, PERSONAL_CONTEXT, PEOPLE_READ, CHUNK_READ, EXTRACTION_READ, EVENT_READ, CONTROL_PLANE_READ/WRITE.

## Safety

* PolicyEngine enforces permissions in code, not model obedience
* Result bounding: MAX_TOOL_RESULT_CHARS truncation
* Think disabled: `"think": false` to prevent reasoning token leakage
* Evidence ID allow-list: LLM can only reference evidence IDs provided in context
* Fail-closed design: validation failures fall back to deterministic output

## Model router

Maps a capability (SIMPLE, RESEARCH, REASONING, VISION, CODING, VERIFICATION) to a configured Ollama model. Local only.

## Orchestration

Planner, executor and verifier. Every state change is an event, tasks that need approval stop at `needs_approval`, and a plan can be resumed. Verification of task outputs is deterministic. Executors run with a scoped approval context and go through the `PolicyEngine`.

## Tool definition and registration

Each tool declares a name, a description, a JSON Schema for its parameters and a handler. Tools are registered at startup in `src/personal_ai/tools/registry.py`. Duplicate registration is rejected, names are validated, and there is no dynamic registration. Arguments are schema-validated before the handler runs, and tool failures return error strings to the agent. Arguments are sanitized before logging.

## Adding a tool

1. Write the handler in `src/personal_ai/tools/`.
2. Register it with a name, description and parameters.
3. Declare the permissions it requires.
4. Add tests.

## Deterministic and probabilistic

Deterministic: policy, orchestrator, memory reconciliation, retrieval planning. Probabilistic: LLM reasoning, structured extraction, vision extraction.

## CLI

Run the agent: `uv run python -m personal_ai.cli --database data/personal-ai.db "your question"`.
