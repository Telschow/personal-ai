# Rezme Integration Reference

This document maps Rezme concepts to our implementation decisions.

| Idea | Rezme Implementation | Our Implementation | Decision | Reason |
|------|---------------------|-------------------|----------|--------|
| CV/resume generation | AI-powered resume builder with templates | Structured evidence base + deterministic requirement→evidence mapping + LLM proposal (fail-closed) | **ADAPT** | Evidence-first prevents fabrication; our model is more rigorous |
| Job matching | Semantic matching between resume and JD | Deterministic fit (current_fit 0..1) + career_upside (growth potential) with evidence attribution | **KEEP** | Our match is explainable, provenance-tagged, and deterministic |
| Career workflow | End-to-end application management | Discovery → normalize → score → fit → tailor → apply (manual) → track | **KEEP** | Same flow; we add native providers and provenance |
| User interaction | Web app with chat-like interface | Local Docker UI (dashboard, job list, detail modal, discovery, application panel) | **KEEP** | Local-first, no cloud; our UI is operational |
| Adaptive documents | Resume tailored per job | CVArtifact: headline, summary, bullets with evidence_ids; append-only versioning per job | **ADAPT** | Our artifacts are proposals, not final documents; validation layer enforces evidence backing |
| Structured career data | Profile + experience + skills | CareerProfile (verified/inferred fields) + career_evidence (trust ladder) + career_documents (ingested CVs) | **KEEP** | More granular provenance; reconciliation tracks conflicts |
| Automation boundaries | Unclear from docs | Hard boundary: READ → ANALYZE → DRAFT → USER APPROVAL → EXECUTE (manual) | **ADAPT** | Explicit boundary documented; never READ → AI DECIDES → SUBMIT |
| AI-assisted writing | LLM generates resume content | LLM proposal only; every bullet must carry evidence_id; validation drops unsupported claims; fail-closed | **ADAPT** | Stronger guardrails; entity-level anti-fabrication (employers, titles, dates, scope, team-size) |
| Job discovery | Not a focus (assumes jobs found elsewhere) | Catalog-driven multi-track discovery with native providers (RemoteOK, Remotive) + ATS APIs + search fallback | **KEEP** | Discovery is a core differentiator for us |
| Application tracking | Basic status tracking | Full lifecycle state machine with timestamps, stages, notes, follow-ups, interview details | **ADAPT** | More complete lifecycle model |
| Interview preparation | Question generation | Story bank structure defined (achievement, context, challenge, action, result, reflection, skills, evidence_refs) | **DEFER** | Structure ready; seeding from verified evidence is future work |
| Cover letters | AI-generated | Not yet implemented; artifact model supports cover_letter type | **DEFER** | Tailored CV is priority; cover letter follows same pattern |

## Key Differences

**Rezme appears to be:**
- Cloud/SaaS oriented (based on typical AI resume builders)
- Resume-first (upload resume, match jobs)
- LLM-heavy for content generation

**Our approach:**
- Local-first (Docker + SQLite + Ollama)
- Discovery-first (find jobs, then match)
- Deterministic-first with LLM as consultative layer
- Evidence-provenance as architectural invariant
- Human-in-the-loop for all decisions

## Adopted from Rezme

- Concept of adaptive/tailored documents per job
- Structured career data model (profile, experience, skills)
- Job matching as core value proposition
- Human review of generated content

## Rejected from Rezme

- Cloud dependency
- LLM as primary content authority
- Resume-first workflow (we are discovery-first)
- SaaS architecture