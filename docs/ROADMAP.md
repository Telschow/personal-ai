# Roadmap

## Phase 0: Architecture Baseline
**Goal**: Document the current state of the system.

- [x] Create architecture documentation
- [x] Map integration points between Personal AI and Job Agent
- [x] Identify data flows and privacy boundaries

## Phase 1: Discovery Reliability (Q4 2026)
**Goal**: Improve the reliability and coverage of job discovery.

- [ ] Expand provider coverage (Workable, Company Radar enhancements)
- [ ] Improve search-engine discovery with better rate limiting
- [ ] Add fallback mechanisms for failed sources
- [ ] Add monitoring and alerting for source health

## Phase 2: Ranking + Human Feedback Calibration (Q1 2027)
**Goal**: Create a feedback loop to improve ranking quality.

- [x] Implement feedback collection and storage
- [x] Build calibration reports to measure precision@K
- [x] Analyze false positive/negative rates
- [x] Generate daily intelligence reports with delta tracking
- [x] Build interactive feedback review commands

## Phase 3: Private Career Evidence Integration (Q2 2027)
**Goal**: Deepen the integration with Personal AI for career insights.

- [ ] Implement read-only retrieval of career-related memories and documents
- [ ] Add evidence-based scoring dimensions to the fit assessment
- [ ] Create evidence-linked career narratives
- [ ] Add support for retrieving user skills, projects, and accomplishments from Personal AI

## Phase 4: CV Optimization (Q3 2027)
**Goal**: Generate tailored CVs grounded in user evidence.

- [x] Parse and extract evidence from user CV documents
- [x] Map job requirements to user capabilities using evidence
- [x] Generate proposal-only tailored CVs with evidence references
- [x] Implement anti-fabrication validation (no invented claims)
- [x] Add optional semantic refinement and LLM polishing layers

## Phase 5: LinkedIn Optimization (Q4 2027)
**Goal**: Optimize LinkedIn profiles using the same evidence-based approach.

- [ ] Analyze user's LinkedIn profile against target role requirements
- [ ] Generate evidence-based headline, about section, and experience entries
- [ ] Maintain distinction between current profile and optimization suggestions
- [ ] Never invent experience or qualifications

## Phase 6: Local GUI (Q1 2028)
**Goal**: Enhance the existing dashboard with comprehensive career intelligence features.

- [ ] Add job filtering by role archetype, career direction, and location
- [ ] Show score decomposition and evidence for each job
- [ ] Implement feedback controls directly in the interface
- [ ] Add career views (Best Overall, Munich, AI/Autonomous, etc.)
- [ ] Include application tracking views (Interested, Applied, Interview, etc.)
- [ ] Add CV/LinkedIn optimization actions

## Phase 7: Unified Private Career Intelligence Platform (Q2 2028)
**Goal**: Unify Personal AI and Job Agent into a single coherent system.

- [ ] Consolidate data models and storage layers
- [ ] Share common configuration and authentication systems
- [ ] Create unified APIs for both personal knowledge and career intelligence
- [ ] Maintain clear separation between general knowledge and career-specific data