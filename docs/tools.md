# Tools

## Overview

Tools provide the agent with safe, gated access to system functionality.

## Tool Definition

Each tool declares:
* Name
* Description
* Parameters (JSON Schema)
* Handler function

## Registration

Tools are registered at startup in src/personal_ai/tools/registry.py.
Duplicate registration prevented; tool names validated.

## Policy Gating

PolicyEngine evaluates:
* AgentTool permission combination
* Decision: ALLOWED / DENIED / APPROVAL_REQUIRED
* Tool existence checked separately

## Default Tools

From src/personal_ai/tools/defaults.py:
* personal_context: overview of available personal data
* list_directory: list files and directories in workspace
* get_document: fetch document metadata + bounded chunk window
* get_memory: fetch memory by id with content-free provenance
* query_events: answer structural temporal-event queries
* search_workouts: search workout activity by movement name
* search_people / get_person: search/fetch people by identity
* search_documents: search document chunks (keyword index)
* search_knowledge: unified search (chunks + extractions + conversations)
* propose_memory: propose a new memory (requires approval)

## Safety

* Result bounding: MAX_TOOL_RESULT_CHARS = 12000 truncation
* Handler execution with sanitized arguments for logging
* Error handling: tool failures return error strings
* No dynamic tool registration
* Argument schema validation before handler execution
* Read-only critical connectors (Job Agent bridge read-only)

## Extending

To add a new tool:
1. Create handler function in src/personal_ai/tools/
2. Register in tool registry with name, description, parameters
3. Define required permissions
4. Add tests

## CLI

Tools are available via agent loop: `uv run personal-ai agent`

## Architecture

See AGENT_ARCHITECTURE.md for PolicyEngine, ModelRouter, Agent details.