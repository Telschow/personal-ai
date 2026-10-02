# Feedback Calibration

This document describes the feedback calibration system in the Job Agent, which enables measuring and improving the quality of job recommendations through human feedback.

## Overview

The feedback calibration system collects human judgments on job relevance and uses them to measure the effectiveness of the ranking system. It does not automatically adjust scoring weights but provides diagnostics for manual review and improvement.

## Feedback Collection

### Methods
Users can provide feedback through:
1. **CLI Commands**: `job-agent feedback add <job_id> <label> [--note "reason"]`
2. **GUI Interface**: Feedback controls in the job detail modal
3. **Bulk Operations**: Import/export of feedback data (planned)

### Feedback Labels
The system uses a standardized set of feedback labels:

#### Positive Labels
- `strong_interest`: User has strong interest in the job
- `interested`: User is interested in the job

#### Neutral Labels
- `maybe`: User is uncertain about interest
- `duplicate`: Job is a duplicate of another already seen

#### Negative Labels
- `not_interested`: User is not interested in the job
- `wrong_role`: Job role is not suitable for the user
- `wrong_seniority`: Job seniority level is not suitable
- `wrong_location`: Job location is not suitable for the user
- `wrong_compensation`: Job compensation is not suitable
- `wrong_domain`: Job domain/industry is not suitable
- `irrelevant`: Job is irrelevant to user's career goals

### Validation
All feedback labels are validated against the `VALID_LABELS` set before storage to prevent invalid entries.

## Storage Schema

Feedback is stored in an append-only table to preserve historical accuracy:

```
feedback table:
- id: INTEGER PRIMARY KEY (auto-increment)
- job_id: TEXT NOT NULL (foreign key to jobs.id)
- label: TEXT NOT NULL (must be in VALID_LABELS)
- note: TEXT (optional free-form note)
- created_at: TEXT NOT NULL (ISO timestamp)
- run_id: TEXT (optional identifier of discovery run when feedback was given)

Indexes:
- idx_feedback_job (job_id) - for job-specific queries
- idx_feedback_label (label) - for label-based queries
- idx_feedback_created (created_at) - for time-based queries
```

### Key Characteristics
- **Append-Only**: Feedback records are never updated or deleted
- **Immutable**: Historical feedback remains unchanged
- **Run Attribution**: Optional run_id enables per-run calibration analysis
- **Job Linking**: Foreign key ensures referential integrity with jobs table
- **Indexed**: Efficient querying by job, label, and time

## Feedback Persistence Layer

The persistence layer ensures:
1. **Immutability**: Feedback records are never overwritten or modified
2. **Validation**: All labels are validated against VALID_LABELS before storage
3. **Run Attribution**: Feedback can be attributed to specific discovery runs
4. **Historical Integrity**: Complete historical record of all feedback given
5. **Efficient Retrieval**: Indexed access for common query patterns

### Core Functions
- `add_feedback()`: Add a new feedback record (append-only)
- `get_feedback_for_job()`: Get all feedback for a specific job
- `get_latest_feedback_for_job()`: Get the most recent feedback for a job
- `get_feedback_summary()`: Get aggregate statistics across all feedback
- `get_labeled_job_ids()`: Get all job IDs that have received feedback
- `has_feedback()`: Check if a job has any feedback

## Calibration Diagnostics

The system provides several diagnostic measures to assess ranking quality:

### Precision@K
Measures what percentage of the top-K ranked jobs receive positive feedback.

**Formula**: Precision@K = (Number of jobs in top-K with positive feedback) / K

**Positive Feedback Labels**: `strong_interest`, `interested`

**Examples**:
- Precision@5 = 0.60 means 3 out of top 5 jobs received positive feedback
- Precision@10 = 0.40 means 4 out of top 10 jobs received positive feedback
- Precision@20 = 0.30 means 6 out of top 20 jobs received positive feedback

### False Positive Rate
Measures what percentage of the top-K ranked jobs receive negative feedback.

**Formula**: FPR@K = (Number of jobs in top-K with negative feedback) / K

**Negative Feedback Labels**: `not_interested`, `wrong_role`, `wrong_seniority`, `wrong_location`, `wrong_compensation`, `wrong_domain`, `irrelevant`

**Examples**:
- FPR@10 = 0.20 means 2 out of top 10 jobs received negative feedback
- FPR@20 = 0.15 means 3 out of top 20 jobs received negative feedback

### False Negative Rate
Measures what percentage of jobs with positive feedback were ranked outside the top-K.

**Formula**: FNR@K = (Number of jobs with positive feedback ranked outside top-K) / (Total number of jobs with positive feedback)

**Examples**:
- FNR@10 = 0.25 means 25% of jobs with positive feedback were ranked outside the top 10
- FNR@20 = 0.10 means 10% of jobs with positive feedback were ranked outside the top 20

### Score-Feedback Correlation
Analyzes the relationship between scores and feedback labels:

**Analysis**:
- Average score for each feedback label
- Score distribution for each feedback label
- Correlation coefficient between scores and binary feedback (positive/negative)
- Calibration curves showing predicted vs actual positive rates

## Feedback Report

The system generates a feedback calibration report that includes:

### 1. Feedback Infrastructure
- Schema description
- Valid labels enumeration
- Command interface documentation
- Implementation status

### 2. Feedback Persistence
- Immutability guarantees
- Run attribution capability
- Validation mechanisms
- Historical integrity

### 3. Precision@K and False Positive/Negative Diagnostics
- Precision@K measurements
- False positive rate measurements
- False negative rate measurements
- Score-feedback correlation analysis

### 4. Calibration Review Queue
- Interactive review mechanism
- Pending jobs identification
- Score breakdown display
- Quick labeling interface
- Optional note capture

### 5. Score Explanation
- Per-dimension score breakdown
- Weight application visibility
- Final score calculation
- Confidence metrics

### 6-8: Classification Handling
- Unknown vs poor match treatment
- Hard constraints vs soft preferences distinction
- Career pivot special handling (DIRECT_MATCH vs ADJACENT_MATCH vs NO_MATCH)

### 9-15: Ranking Stability
- Stable top-five analysis (tracking top jobs across runs)
- Diversification assurance (variety across role families in top-N)
- Feedback-based diagnostics (correlating feedback with score components)
- False positive/negative identification (high-score negative feedback, low-score positive feedback)
- Precision@K reporting (multiple K values)

### 16-18: Config-Driven Calibration
- Feedback label to score adjustment mapping (conceptual)
- Precision threshold configurability
- Manual weight adjustment process (no automatic adjustment)

### 19-22: Daily Intelligence
- Daily report with delta tracking (new jobs, changed scores, feedback impact)
- Weekly career report aggregating trends
- Scheduling options (cron, manual invocation)

### 23-27: Validation
- Regression tests for feedback persistence
- Unknown vs poor match handling tests
- Career pivot classification tests
- Score decomposition accuracy tests

## Review Queue Mechanism

The interactive feedback review system works as follows:

1. **Identification**: Find jobs without feedback (complement of labeled jobs)
2. **Presentation**: Show jobs one at a time with:
   - Job factual information (title, company, location)
   - Current score breakdown (if available)
   - Existing feedback (if any)
3. **Input**: Accept single-character labels or commands:
   - `s`: strong_interest
   - `i`: interested
   - `m`: maybe
   - `n`: not_interested
   - `r`: wrong_role
   - `y`: wrong_seniority
   - `l`: wrong_location
   - `c`: wrong_compensation
   - `d`: wrong_domain
   - `x`: duplicate
   - `q`: quit review
4. **Recording**: Store feedback with timestamp and optional note
5. **Continuation**: Move to next job or allow navigation

## Score Explanation in Feedback Context

When reviewing jobs during feedback collection, the system shows:

### Current Score Breakdown
For each job, displays:
- Individual dimension scores (role, AI, compensation, etc.)
- Weighted contributions to total score
- Final score and decision
- This helps users understand why the system ranked a job as it did

### Feedback-Job Relationship
Users can see:
- Whether their feedback aligns with the system's assessment
- Patterns in their feedback (e.g., consistently disliking certain location types)
- Opportunities to improve the system through better configuration or weights

## Daily Intelligence Report

The system generates a daily intelligence report that includes:

### Corpus Statistics
- Total jobs in the system
- New jobs added since last report
- Jobs with feedback
- Total feedback records

### Precision Metrics
- Precision@5, Precision@10, Precision@20
- Trends over time

### False Positive/Negative Analysis
- False positive rate in top-10
- False negative rate
- Examples of each (when present)

### Feedback Distribution
- Breakdown by feedback label
- Trends over time
- Emerging patterns

### Score-Feedback Correlation
- Average scores by feedback label
- Score distributions
- Calibration insights

### Career Direction Trends
- Counts of DIRECT_MATCH, ADJACENT_MATCH, NO_MATCH jobs
- Trends over time

### Location Distribution
- Geographic distribution of jobs
- Trends over time

### Personalized Recommendations
Based on feedback patterns:
- Suggested adjustments to weights or thresholds
- Identified systematic biases or miscalibrations
- Recommended areas for investigation

## Implementation Status

### Completed
- [x] Feedback CLI commands implemented and integrated
- [x] Feedback persistence layer complete with migration v16
- [x] Feedback review queue with interactive UI
- [x] Feedback summary statistics

### In Progress
- [ ] Precision@K and false positive/negative diagnostics
- [ ] Score decomposition in reports
- [ ] Daily intelligence report
- [ ] Regression tests

### Planned
- [ ] Interactive feedback review enhancements
- [ ] Feedback-driven weight adjustment guidance
- [ ] Advanced correlation analysis
- [ ] Automated report scheduling

## Usage Examples

### Adding Feedback
```bash
# Add strong interest feedback
job-agent feedback add abc123 strong_interest --note "Great match for my skills"

# Add negative feedback
job-agent feedback add def456 wrong_location --note "Too far from my preferred city"

# Add neutral feedback
job-agent feedback add ghi789 maybe --note "Interesting but needs more details"
```

### Reviewing Feedback
```bash
# Review up to 10 jobs without feedback
job-agent feedback review --limit 10

# Review all pending jobs (default limit is 5)
job-agent feedback review
```

### Checking Feedback Status
```bash
# Show summary statistics
job-agent feedback summary

# Show pending jobs count
job-agent feedback pending
```

## Design Principles

### Immutability
Feedback represents historical facts and must never be altered to preserve accuracy and auditability.

### Validation
All feedback must be validated against the known set of labels to prevent data corruption.

### Attribution
Feedback should be attributable to specific jobs and optionally to specific discovery runs for per-run analysis.

### Accessibility
Feedback collection should be easy and lightweight to encourage user participation.

### Privacy
Feedback data is strictly personal and never shared without explicit user consent.

### Utility
Feedback should be actionable and provide clear insights for system improvement.

## Limitations

### No Automatic Weight Adjustment
The system does NOT automatically adjust scoring weights based on feedback to:
- Preserve auditability and reproducibility
- Prevent unintended consequences from automatic adjustments
- Require explicit human review before changing core scoring logic
- Avoid feedback loops or instability

### Limited to Explicit Feedback
The system only measures feedback that users explicitly provide:
- Does not infer feedback from implicit behaviors (clicks, dwell time, etc.)
- Requires active user participation to generate useful signals
- May suffer from selection bias (users more likely to give extreme feedback)

### Temporal Lag
Feedback reflects historical user preferences:
- May not capture rapidly changing preferences or circumstances
- Best suited for measuring stable preferences and systematic biases
- Less useful for detecting sudden shifts in user interests

### Sample Size Dependence
Diagnostic reliability depends on sufficient feedback volume:
- Low feedback volume → high variance in measurements
- Recommendations require sufficient statistical significance
- Early stages may have noisy or misleading metrics

## Future Enhancements

### Planned Improvements
- [ ] Automatic report generation and distribution
- [ ] Enhanced visualization of feedback trends
- [ ] Feedback-driven experimental weight testing (A/B testing framework)
- [ ] Integration with application outcomes for end-to-end measurement
- [ ] Advanced statistical analysis (confidence intervals, significance testing)
- [ ] Custom feedback label support (with validation)
- [ ] Anonymous aggregation for community-level insights (opt-in)

### Research Directions
- [ ] Optimal feedback collection strategies (when, how often, how many)
- [ ] Bias detection and correction methodologies
- [ ] Long-term preference drift measurement and modeling
- [ ] Cross-validation of feedback with other outcome measures
- [ ] Active learning approaches for efficient preference elicitation

## Summary

The feedback calibration system provides:
- **Structured feedback collection** with validated labels
- **Immutable, append-only storage** preserving historical accuracy
- **Run attribution** for per-run calibration analysis
- **Comprehensive diagnostics** including Precision@K, false positive/negative rates, and score-feedback correlations
- **Interactive review mechanism** for efficient feedback collection
- **Score transparency** to help users understand system reasoning
- **Daily intelligence reporting** with trends and personalized recommendations
- **Design for manual improvement** rather than automatic adjustment
- **Privacy-preserving and local-first** implementation
- **Extensible foundation** for future enhancements

The system enables users to understand how well the job recommendations match their preferences and identify areas for improvement through informed configuration adjustments, while preserving the auditability and reproducibility of the core scoring system.