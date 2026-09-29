# Agent Architecture

## Core Agent
Synchronous tool-calling loop against local Ollama
max_tool_rounds=8
PolicyEngine gates tool execution
Observer pattern for logging
Result bounding MAX_TOOL_RESULT_CHARS=12000
Think disabled

## Policy Engine
AgentTool permissions: AGENT, TOOL, MEMORY_READ, MEMORY_WRITE, WORKOUT_READ, PERSONAL_CONTEXT, PEOPLE_READ, CHUNK_READ, EXTRACTION_READ, EVENT_READ, CONTROL_PLANE_READ/WRITE
Decision: ALLOWED / DENIED / APPROVAL_REQUIRED
Tool existence checked separately

## Model Router
Capabilities: SIMPLE, RESEARCH, REASONING, VISION, CODING, VERIFICATION
Maps request to Ollama model
Local-only constraint

## Orchestrator
Planner -> Executor -> Verifier workflow
Event sourcing for audit
Approval gating
Plan resumption

## Executor
Scoped approval context
PolicyEngine integration
ModelRouter selection
Work dispatch

## Verifier
Deterministic verification of task outputs

## Approvals
Human-in-the-loop for approval-required tools/tasks
State tracking pending/approved/rejected

## Deterministic vs Probabilistic
Deterministic: policy, orchestrator, memory reconciliation, scoring, retrieval planning
Probabilistic: LLM reasoning, structured extraction, vision extraction
