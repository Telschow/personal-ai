# Data Model

This document describes the normalized job object and related data models used in the Job Agent system.

## Core Entities

### Job
The central entity representing a job posting, with fields tracking its lifecycle from discovery through application.

#### Schema (SQLite)
| Field | Type | Description |
| :--- | :--- | :--- |
| id | TEXT | Unique identifier (source-native or generated) |
| title | TEXT | Job title |
| company | TEXT | Employer name |
| url | TEXT | Original job posting URL |
| apply_url | TEXT | Direct application URL (if different from url) |
| source | TEXT | Source name (e.g., "greenhouse", "linkedin") |
| source_type | TEXT | Source kind (api, rss_json, structured_page, etc.) |
| canonical_url | TEXT | Normalized URL for deduplication |
| location | TEXT | Raw location string from source |
| country | TEXT | ISO country code |
| normalized_location | TEXT | Standardized location (city, region, country) |
| remote_mode | TEXT | Remote work mode (remote, hybrid, onsite, unknown) |
| employment_type | TEXT | Employment type (full-time, part-time, contract, etc.) |
| date_posted | TEXT | ISO timestamp when job was posted |
| description | TEXT | Full job description |
| salary_min | REAL | Minimum salary in original currency |
| salary_max | REAL | Maximum salary in original currency |
| salary_currency | TEXT | Original currency code (EUR, USD, etc.) |
| salary_min_eur | REAL | Minimum salary converted to EUR |
| salary_max_eur | REAL | Maximum salary converted to EUR |
| salary_converted | BOOLEAN | Whether salary conversion was successful |
| canonical_key | TEXT | Deduplication key (company-title-location hash) |
| status | TEXT | Lifecycle status (active, stale, closed, duplicate) |
| run_id | TEXT | Identifier of the discovery run that first saw this job |
| discovered_at | TEXT | ISO timestamp when job was first discovered |
| last_seen | TEXT | ISO timestamp when job was last seen in a scan |
| last_checked | TEXT | ISO timestamp when job was last checked for updates |
| missing_scans | INTEGER | Number of consecutive scans where job was missing |
| closed_at | TEXT | ISO timestamp when job was marked closed |
| user_status | TEXT | User's status for this job (NEW, SAVED, REJECTED, APPLIED) |
| user_status_updated_at | TEXT | ISO timestamp when user status was last updated |
| raw_json | TEXT | Original raw JSON from source (if applicable) |

### Evaluation
Scores and assessments for a job, keyed by job_id.

#### Schema
| Field | Type | Description |
| :--- | :--- | :--- |
| job_id | TEXT | Foreign key to jobs.id |
| total | REAL | Overall score (0-100) |
| decision | TEXT | Recommendation (strong, review, reject) |
| reasons_json | TEXT | JSON array of reasons for the score |
| gaps_json | TEXT | JSON array of identified gaps |
| evaluated_at | TEXT | ISO timestamp of evaluation |
| llm_explanation | TEXT | Optional LLM-generated explanation |
| confidence | REAL | Confidence in the assessment (0-1) |
| scoring_version | TEXT | Version of the scoring policy used |
| profile_version | TEXT | Version of the career profile used |

### CareerFit
Deterministic career fit assessment for a job, keyed by job_id.

#### Schema
| Field | Type | Description |
| :--- | :--- | :--- |
| job_id | TEXT | Foreign key to jobs.id |
| current_fit | REAL | Current match score (0-1) |
| career_upside | REAL | Growth potential score (0-1) |
| evidence_coverage | REAL | Fraction of required capabilities backed by evidence (0-1) |
| breakdown_json | TEXT | JSON breakdown of score components |
| strengths_json | TEXT | JSON array of identified strengths |
| gaps_json | TEXT | JSON array of identified gaps |
| transferable_json | TEXT | JSON array of transferable skills |
| positioning_json | TEXT | JSON object with positioning guidance |
| risks_json | TEXT | JSON array of identified risks |
| evidence_refs_json | TEXT | JSON array of evidence IDs used |
| knowledge_sources_json | TEXT | JSON array of knowledge sources consulted |
| narrative_json | TEXT | Optional LLM-generated narrative |
| llm_used | INTEGER | Whether LLM was used for narrative (0/1) |
| created_at | TEXT | ISO timestamp of assessment creation |

### Feedback
Human feedback on job relevance and suitability.

#### Schema
| Field | Type | Description |
| :--- | :--- | :--- |
| id | INTEGER | Primary key |
| job_id | TEXT | Foreign key to jobs.id |
| label | TEXT | Feedback label (see VALID_LABELS) |
| note | TEXT | Optional free-form note |
| created_at | TEXT | ISO timestamp when feedback was added |
| run_id | TEXT | Optional identifier of discovery run when feedback was given |

#### Valid Labels
| Label | Type | Description |
| :--- | :--- | :--- |
| strong_interest | Positive | User has strong interest in the job |
| interested | Positive | User is interested in the job |
| maybe | Neutral | User is uncertain about interest |
| not_interested | Negative | User is not interested in the job |
| wrong_role | Negative | Job role is not suitable |
| wrong_seniority | Negative | Job seniority level is not suitable |
| wrong_location | Negative | Job location is not suitable |
| wrong_compensation | Negative | Job compensation is not suitable |
| wrong_domain | Negative | Job domain/industry is not suitable |
| duplicate | Neutral | Job is a duplicate of another |
| irrelevant | Negative | Job is irrelevant to user's career goals |

### CareerDocument
Ingested CV or career document.

#### Schema
| Field | Type | Description |
| :--- | :--- | :--- |
| document_id | TEXT | Primary key (content hash) |
| filename | TEXT | Original filename |
| source_path | TEXT | Path where document was stored |
| mime_type | TEXT | MIME type of document |
| content_hash | TEXT | SHA256 hash of document content |
| size_bytes | INTEGER | Document size in bytes |
| sections_json | TEXT | JSON representation of document sections |
| ingested_at | TEXT | ISO timestamp when document was ingested |

### CareerEvidence
Evidence extracted from user profile or documents.

#### Schema
| Field | Type | Description |
| :--- | :--- | :--- |
| evidence_id | TEXT | Primary key (content hash) |
| claim | TEXT | The factual claim being made |
| level | TEXT | Verification level (verified, documented, inferred, candidate) |
| source | TEXT | Source of evidence (profile, document name, etc.) |
| source_type | TEXT | Source type category |
| categories_json | JSON | Categories the evidence belongs to |
| keywords_json | JSON | Keywords associated with the evidence |
| confidence | REAL | Confidence in the evidence (0-1) |
| normalized_fact | TEXT | Normalized version of the claim |
| authority | TEXT | Authority or source of the claim |
| created_at | TEXT | ISO timestamp when evidence was created |

### CareerArtifact
Tailored career materials (CV, cover letter) for a specific job.

#### Schema
| Field | Type | Description |
| :--- | :--- | :--- |
| artifact_id | TEXT | Primary key |
| job_id | TEXT | Foreign key to jobs.id |
| version | INTEGER | Version number (append-only) |
| base_document_id | TEXT | Foreign key to career_documents.document_id |
| status | TEXT | Status (DRAFT, VALIDATED, REQUIRES_REVIEW, APPROVED, REJECTED) |
| profile_version | TEXT | Version of career profile used |
| sections_json | JSON | Sections of the artifact |
| mapping_json | JSON | Requirement→capability→evidence mapping |
| positioning_json | JSON | Positioning plan |
| validation_json | JSON | Claim validation results |
| created_at | TEXT | ISO timestamp when artifact was created |
| llm_used | INTEGER | Whether LLM was used (0/1) |
| schema_version | TEXT | Version of the schema used |

### CareerArtifactEvidence
Links between artifacts and the evidence they reference.

#### Schema
| Field | Type | Description |
| :--- | :--- | :--- |
| artifact_id | TEXT | Foreign key to career_artifacts.artifact_id |
| evidence_id | TEXT | Foreign key to career_evidence.evidence_id |

## Data Flow and Transformation

### From Raw Source to Stored Job
1. **Raw Data**: Source adapter fetches raw job data from provider/API
2. **Normalization**: `normalizer.normalize_job()` converts to canonical form:
   - Salary converted to EUR using fixed FX table
   - Location normalized (city, country, remote/hybrid/onsite)
   - Raw JSON stored for provenance
   - Canonical key generated for deduplication
3. **Persistence**: `db.upsert_job()` stores or updates the job record
4. **Deduplication**: Jobs with same canonical_key are folded into single entry
5. **Lifecycle Tracking**: Jobs tracked across scans (active → stale → closed)

### From Job to Evaluation
1. **Scoring**: `scoring.score()` computes deterministic score based on:
   - Job attributes vs. career profile
   - Salary, location, leadership, purpose, WLB dimensions
   - Configurable weights summing to 1
2. **Persistence**: `db.record_evaluation()` stores the score
3. **Decision Logic**: Score thresholds determine strong/review/reject

### From Job to CareerFit
1. **Evidence Collection**: `career.retrieval` builds query plan based on job requirements
2. **Parent Retrieval**: Optional read-only access to Personal AI memory/corpus
3. **Evidence Compaction**: Results ranked by trust and compacted to character budget
4. **Fit Calculation**: `career.fit.analyze_fit()` computes current_fit and career_upside
5. **Persistence**: `db.save_career_fit()` stores the assessment
6. **LLM Narrative**: Optional schema-validated LLM explanation (fail-closed to None)

### From Job to CareerArtifact
1. **Document Evidence**: `career.documents` extracts sections from user CV
2. **Evidence Extraction**: `career.reconcile` creates DOCUMENTED evidence from CV sections
3. **Requirement Mapping**: `career.mapping` creates requirement→capability→evidence floor
4. **Positioning Plan**: `career.positioning_plan` generates structured positioning guidance
5. **Assembly**: `career.tailoring` combines mapping, positioning, and validation
6. **LLM Polish**: Optional LLM refinement (fail-closed to deterministic)
7. **Validation**: `career.validation` checks for fabricated claims
8. **Persistence**: `db.save_career_artifact()` stores as append-only version
9. **Evidence Links**: `db.save_career_artifact_evidence()` links artifact to evidence

## Field Provenance and Mutability

Each field in the Job model has a specific provenance and mutability characteristic:

### Source Facts (Never Modified After Ingestion)
- id, title, company, url, apply_url, source, source_type, canonical_url, 
  location, country, date_posted, description, salary_min, salary_max, salary_currency, raw_json

### Deterministic Transformations (Updated on Re-normalization)
- normalized_location, remote_mode, employment_type, salary_min_eur, salary_max_eur, 
  salary_converted, canonical_key, status, run_id, discovered_at, last_seen, 
  last_checked, missing_scans, closed_at

### Derived Classification Data (Updated on Re-analysis)
- All fields in evaluations table
- All fields in career_fit table

### Human Feedback (Append-Only)
- All fields in feedback table

### Application Outcome (Updated by User)
- user_status, user_status_updated_at (in jobs table)
- All fields in applications table

## Privacy and Security Boundaries

### Private Data (Never Leaves Local Machine)
- All Personal AI memories and documents
- User CVs and career documents
- Extracted evidence and claims
- Tailored CV/cover letter artifacts
- Feedback and application decisions
- Local LLM inputs and outputs (when using local Ollama)

### Public Data (May Be Shared with Discretion)
- Job titles, companies, locations (generalized)
- Score distributions and analytics (anonymized)
- General career trends (without personal identifiers)

### Network Boundaries
- **Personal AI**: Entirely local (Ollama on localhost, SQLite files)
- **Job Agent Discovery**: Outbound HTTP to job providers and search engines
- **Job Agent Scoring**: Entirely local (deterministic policy + optional local LLM)
- **Job Agent Persistence**: Local SQLite database
- **Integration**: Local read-only SQLite connection (Personal AI → Job Agent)

## Performance Characteristics

### Deterministic Operations (Fast, Predictable)
- Job normalization
- Deduplication and lifecycle tracking
- Deterministic scoring and fit assessment
- Evidence extraction and mapping
- Anti-fabrication validation
- Artifact assembly and persistence

### Variable Operations (Depend on External Factors)
- Job discovery (network-dependent, variable latency)
- Search-engine discovery (rate-limited, variable results)
- LLM reasoning (model-dependent, variable latency and quality)
- Provider APIs (variable response times and availability)

### Bottlenecks
1. **LLM Reasoning**: Can take tens of seconds per call on local hardware
2. **Job Discovery**: Network calls to external providers can be slow or fail
3. **Search Engines**: Rate limiting and variable result quality
4. **Evidence Retrieval**: Depends on size and complexity of Personal AI database

## Validation and Testing

### Schema Validation
- All pydantic models enforce type safety and constraints
- SQLite schema migrations are versioned and tested
- Foreign key constraints are enforced where appropriate

### Business Logic Validation
- Feedback labels validated against VALID_LABELS set
- Score thresholds validated to be in 0-100 range
- Weights validated to sum to 1.0
- Evidence levels validated against VerificationLevel enum
- Artifact statuses validated against ArtifactStatus enum

### Anti-Fabrication Guards
- Numeric claims must have supporting evidence
- Entity claims (employer, title, dates) must have supporting evidence
- Generated material never becomes authoritative evidence without human approval
- LLM outputs are strictly schema-validated and evidence-id restricted