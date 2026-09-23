# Architecture Overview

## 1. Documentation Map

| Document | Purpose |
| :--- | :--- |
| `docs/ARCHITECTURE.md` | High-level system architecture and component layering. |
| `docs/PERSONAL_AI_JOB_AGENT_INTEGRATION.md` | Integration and data flow between the two systems. |
| `docs/ROADMAP.md` | Roadmap and implementation phases. |

## 2. Current Architecture

The system consists of two primary components: **Personal AI** (a local-first knowledge/agent system) and **Job Agent** (a career-search and assessment agent).

### Personal AI
A standalone local AI system that ingests personal data (files, emails, browsing history) into a structured knowledge base, providing a question-answering interface via a local LLM.

### Job Agent
A job discovery agent that crawls job sources, normalizes them, scores them against a career profile, and tracks the user's application workflow.

## 3. Personal-AI ↔ Job-Agent Integration

The systems are currently integrated through:
- **Read-only SQLite access**: Job Agent accesses the Personal AI database in read-only mode to retrieve evidence-based career data (the `PersonalAiCareerKnowledge` adapter).
- **No bi-directional synchronization**: Job Agent does not modify Personal AI's state.

## 4. Target Architecture

The long-term goal is to unify these systems into a single platform where the Job Agent is a specialized application of the Personal AI infrastructure, sharing a common data model and intelligence layer.

## 5. CV Optimization Roadmap

The next phase should focus on the evidence-grounded CV generation:

1. **Evidence Layer**: Map CV claims to evidence retrieved from Personal AI.
2. **Gap Analysis**: Use the evidence map to identify areas for CV improvement.
3. **Tailoring**: Generate job-specific CV drafts based on the user's documented achievements.
4. **Validation**: Ensure all claims are supported by verified evidence.

## 6. LinkedIn Optimization Roadmap

LinkedIn optimization will leverage the same evidence-grounded approach as CV tailoring, ensuring professional branding matches the user's verified experiences.

## 7. GUI

The system currently has a local web dashboard served via FastAPI (served from `src/personal_ai/static/`). This provides the base interface for both systems.

## 8. Top Architecture Risks

- **Performance**: High latency in local LLM reasoning can impact real-time interactions.
- **Evidence Quality**: The system's quality is entirely dependent on the quality of the ingested Personal AI evidence.
- **Coupling**: The read-only database integration is tightly bound to the schema of the Personal AI system.

## 9. Next Three Implementation Slices

1. **Private Career Evidence Layer**: Expand the evidence model to support cross-system retrieval.
2. **Evidence-Grounded CV Generation**: Implement the end-to-end evidence mapping and tailored CV generation pipeline.
3. **Local GUI Expansion**: Enhance the existing dashboard to include the career and application tracking features.