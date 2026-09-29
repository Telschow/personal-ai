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

## CLI

Run agent interactively: `uv run personal-ai agent`

## Architecture

See AGENT_ARCHITECTURE.md for detailed component breakdown.