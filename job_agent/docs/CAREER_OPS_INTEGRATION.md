# Career-Ops Integration Reference

This document maps Career-Ops concepts to our implementation decisions.

| Idea | Career-Ops Implementation | Our Implementation | Decision | Reason |
|------|---------------------------|-------------------|----------|--------|
| Provider-based discovery | JavaScript/TypeScript adapters per source (Greenhouse, Lever, Workday, etc.) | Python native providers (RemoteOK, Remotive) + ATS API adapters + search fallback | **ADAPT** | Native providers preferred; ATS adapters already implemented; search fallback for remaining sources |
| Structured job evaluation | Scoring algorithm with weighted criteria | Deterministic ScoringPolicy (salary, location, purpose, leadership, WLB) + career fit (role_family, AI relevance, leadership, technical_depth, product_scope, seniority, domain) | **KEEP** | Our deterministic pipeline is more rigorous; LLM narrative is consultative only |
| Tailored CV generation | Markdown/JSON templates with LLM assistance | Structured evidence base + requirement→capability→evidence mapping + LLM proposal (fail-closed) + validation layer | **ADAPT** | Evidence-first approach prevents fabrication; append-only versioning; entity-level anti-fabrication checks |
| Application lifecycle | Kanban board with stages | Typed stage machine: NOT_APPLIED → APPLIED → RESPONDED → INTERVIEW → OFFER → HIRED; terminal REJECTED/WITHDRAWN | **KEEP** | Same concept, typed transitions with validation |
| Interview planning | Question bank, STAR stories | Interview story bank structure defined (achievement, context, challenge, action, result, reflection, skills, evidence_refs) | **DEFER** | Structure ready; seeding from verified evidence is future work |
| Company research | Automated research agents | Bounded company intel workflow (business context, AI strategy, challenges, competitors, positioning) | **DEFER** | Only for high-fit candidates; infrastructure exists |
| Recruiter/contact workflows | Message templates, outreach tracking | Draft generation only (recruiter message, hiring manager message, follow-up, application email) | **ADAPT** | Human approval required; never auto-send |
| Follow-up workflows | Reminders, sequences | Application state includes follow_up_at, interview_date, interview_stage, notes | **KEEP** | Minimal viable tracking |
| Outcome tracking | Analytics dashboard | Dashboard with stage counts, discovery run history, provider health | **KEEP** | Operational in UI |
| Funnel analytics | Conversion rates by source/stage | DiscoveryYieldReport (queried-vs-yielded, 5-level dedup, provider health) + application stage counts | **KEEP** | More detailed on discovery side |
| Provenance / source-of-truth | Markdown front-matter + Git | SQLite with content-hash identities, job_sources table, career_documents, career_evidence with verification levels | **KEEP** | Stronger: content-hash deduplication, evidence trust ladder, reconciliation |
| Human-in-the-loop | Explicit approval steps | Every CV artifact status: DRAFT → VALIDATED/REQUIRES_REVIEW → APPROVED/REJECTED (human); application transitions require explicit action | **KEEP** | Hard boundary: never auto-submit |
| ATS-oriented PDF generation | React-PDF / typst templates | Not yet implemented; deterministic artifact model ready for PDF backend | **DEFER** | Artifact model is source of truth; PDF rendering is separate concern |
| Job legitimacy / ghost-job checks | Heuristics on posting age, company signals | Freshness tracking (stale/closed lifecycle), salary floor enforcement, source reputation via provider health | **ADAPT** | Deterministic signals only; no speculative heuristics |
| Compensation analysis | Market data integration | Salary normalization to EUR; floor €120k / target €150k; unknown = reviewable | **KEEP** | Local-first; no external salary APIs |
| Career profile | YAML/Markdown files | Structured CareerProfile with per-field provenance (verified vs inferred); profile.yaml + career block | **KEEP** | Provenance-typed; explicit fields win over inference |
| Evidence reconciliation | Manual review | Automated reconciliation (exact match / new / conflict) with checkpoint records; conflicts never auto-resolved | **ADAPT** | More systematic; idempotent document ingestion |

## Key Architectural Boundaries Adopted

1. **LLM never decides** — deterministic pipeline is authoritative; LLM only explains or proposes
2. **Evidence trust ladder** — VERIFIED (profile) > DOCUMENTED (personal_ai) > USER_CONFIRMED > CANDIDATE > INFERRED > UNKNOWN
3. **Provenance on everything** — job_sources, career_documents, career_evidence, career_artifacts all carry source metadata
4. **Append-only artifacts** — career_artifacts versioned per job; never mutate history
5. **No auto-submit** — auto_submit/auto_publish hard-blocked in config validator
6. **Local-first** — all data in SQLite; no cloud dependencies for core loop

## Rejected from Career-Ops

- JavaScript/Node.js runtime — we use Python throughout
- Markdown/YAML file-based storage — we use SQLite with migrations
- Autonomous browser automation for applications — explicitly deferred (v0.8)
- Generalized agent framework — we use explicit tools + policy engine
- External salary APIs — local normalization only