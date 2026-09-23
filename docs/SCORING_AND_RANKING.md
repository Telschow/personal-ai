# Scoring and Ranking Architecture

This document describes the scoring and ranking architecture used in the Job Agent system.

## Overview

Job Agent uses a deterministic policy scoring system to evaluate job suitability against a user's career profile. The system is designed to be transparent, auditable, and free from hidden biases or black-box decision making.

## Scoring Philosophy

### Deterministic First
The core principle is that scoring must be deterministic:
- Same inputs always produce same outputs
- No hidden randomness or stochastic elements
- Fully auditable and explainable
- No hidden weights or parameters
- LLM can only explain, never decide or influence scores

### Two-Axis Assessment
Rather than reducing career fit to a single score, the system evaluates along two orthogonal axes:
1. **Current Fit**: How well the job matches the user's current profile and requirements
2. **Career Upside**: How much the job advances the user's long-term career trajectory

This prevents inflating current match scores for stretch roles that are genuinely attractive for growth reasons.

### Evidence-Modulated, Not Arithmetic
Evidence from the user's profile or documents affects confidence and credibility, but never changes the raw arithmetic score:
- Evidence coverage affects the career upside calculation
- Evidence quality affects confidence and risk notes
- Missing evidence creates gaps, but doesn't subtract from the score
- Evidence never gets added to or subtracted from the deterministic score

## Scoring Dimensions

The overall score is composed of nine weighted dimensions, each normalized to a 0-1 scale:

### 1. Role Match (25%)
Measures how well the job's role family matches the user's target role families.
- **Calculation**: Jaccard similarity between job role families and target role families
- **Range**: 0.0 (no overlap) to 1.0 (complete overlap)
- **Examples**: 
  - Job: "Product Manager", Target: ["Product Management"] → 1.0
  - Job: "Software Engineer", Target: ["Product Management"] → 0.0
  - Job: "Technical Product Manager", Target: ["Product Management"] → 0.5 (partial overlap)

### 2. AI Relevance (20%)
Measures the job's relevance to artificial intelligence and autonomous systems.
- **Calculation**: Based on keyword matching in title, description, and company
- **Range**: 0.0 (no AI relevance) to 1.0 (high AI relevance)
- **Keywords**: artificial intelligence, AI, machine learning, autonomous systems, robotics, deep tech
- **Examples**:
  - Job: "AI Research Scientist" → 1.0
  - Job: "Software Engineer (Web)" → 0.1
  - Job: "Machine Learning Engineer" → 1.0

### 3. Compensation Match (15%)
Measures how well the job's compensation matches the user's salary requirements.
- **Calculation**: Piecewise linear function based on EUR salary range
- **Range**: 0.0 (below minimum acceptable) to 1.0 (at or above target)
- **Parameters**: 
  - Minimum acceptable: €120,000 (hard floor, scores 0 below this)
  - Target: €150,000 (ideal target, scores 1.0 at this point)
  - Above target: Continues to increase score but with diminishing returns
- **Examples**:
  - Job: €100,000 salary → 0.0 (below minimum)
  - Job: €120,000 salary → 0.0 (at minimum)
  - Job: €135,000 salary → 0.5 (midway between min and target)
  - Job: €150,000 salary → 1.0 (at target)
  - Job: €200,000 salary → >1.0 (above target, continued increase)

### 4. Location Match (10%)
Measures how well the job's location matches the user's location preferences.
- **Calculation**: Based on location matching and remote work preferences
- **Range**: 0.0 (completely unsuitable location) to 1.0 (ideal location)
- **Factors**: 
  - Geographic match (Munich, remote EU, etc.)
  - Remote work compatibility (remote, hybrid, onsite)
  - Willingness to relocate (from user profile)
- **Examples**:
  - Job: Munich, onsite, willing to relocate → 1.0
  - Job: New York, onsite, not willing to relocate → 0.0
  - Job: Remote, any location preference → 1.0
  - Job: Berlin, hybrid, willing to relocate → 0.8

### 5. Leadership Match (20%)
Measures the job's leadership requirements and opportunities.
- **Calculation**: Based on leadership scope, people management, and leadership keywords
- **Range**: 0.0 (no leadership) to 1.0 (high leadership responsibility)
- **Keywords**: leadership, management, lead, head, director, manager, supervisor
- **Examples**:
  - Job: "Individual Contributor" → 0.0
  - Job: "Team Lead" → 0.5
  - Job: "Engineering Manager" → 0.8
  - Job: "VP of Engineering" → 1.0

### 6. Purpose Match (5%)
Measures alignment with the user's purpose and values.
- **Calculation**: Based on purpose-related keywords and company mission alignment
- **Range**: 0.0 (misaligned purpose) to 1.0 (strong purpose alignment)
- **Keywords**: mission, purpose, vision, values, impact, meaning
- **Sources**: Job description, company mission, industry sector
- **Examples**:
  - Job: "Tobacco Sales Representative" → 0.0 (if user values health)
  - Job: "Renewable Energy Engineer" → 0.8 (if user values sustainability)
  - Job: "Non-Profit Program Director" → 1.0 (if user values social impact)

### 7. Work-Life Balance (5%)
Measures the job's impact on work-life balance.
- **Calculation**: Based on work-life balance keywords, hours expectations, and flexibility
- **Range**: 0.0 (poor work-life balance) to 1.0 (excellent work-life balance)
- **Keywords**: flexible, remote, work-life balance, wellness, time off, vacation
- **Negative Indicators**: overtime, on-call, weekend work, travel requirements
- **Examples**:
  - Job: "Investment Banking Analyst" (80+ hr weeks) → 0.0
  - Job: "Software Engineer" (flexible hours, remote ok) → 0.8
  - Job: "Product Manager" (core hours, generous PTO) → 1.0

## Score Calculation Process

### Step 1: Dimension Scoring
Each dimension is scored independently on a 0-1 scale:
```
role_score = calculate_role_match(job, profile)
ai_score = calculate_ai_relevance(job, profile)
comp_score = calculate_compensation_match(job, profile)
loc_score = calculate_location_match(job, profile)
lead_score = calculate_leadership_match(job, profile)
purp_score = calculate_purpose_match(job, profile)
wlb_score = calculate_work_life_balance(job, profile)
```

### Step 2: Weight Application
Each dimension score is multiplied by its configured weight:
```
weighted_role = role_score × role_weight
weighted_ai = ai_score × ai_weight
weighted_comp = comp_score × comp_weight
weighted_loc = loc_score × loc_weight
weighted_lead = lead_score × lead_weight
weighted_purp = purp_score × purp_weight
weighted_wlb = wlb_score × wlb_weight
```

### Step 3: Summation
The weighted scores are summed to produce the raw total score:
```
raw_total = weighted_role + weighted_ai + weighted_comp + 
            weighted_loc + weighted_lead + weighted_purp + weighted_wlb
```

### Step 4: Normalization
The raw total is converted to a 0-100 scale for storage and reporting:
```
stored_total = raw_total × 100
```

### Step 5: Decision Logic
Based on the stored total score, a decision is made:
- **Strong (75-100)**: Highly recommended, proceed with application
- **Review (50-74)**: Worth considering, examine more closely
- **Reject (0-49)**: Not recommended, do not pursue

## Career Fit Assessment (Two-Axis)

Beyond the basic scoring, Job Agent performs a more nuanced career fit assessment:

### Current Fit Calculation
Measures how well the job matches the user's current profile:
```
current_fit = (role_score × 0.30) +
              (skill_score × 0.25) +
              (exp_score × 0.20) +
              (lead_score × 0.15) +
              (domain_score × 0.10)
```
Where:
- role_score: Role family match (0-1)
- skill_score: Skills match (0-1)
- exp_score: Experience level match (0-1)
- lead_score: Leadership match (0-1)
- domain_score: Industry/domain match (0-1)

### Career Upside Calculation
Measures how much the job advances the user's long-term trajectory:
```
career_upside = trajectory_progress × (0.5 + 0.5 × evidence_coverage)
```
Where:
- trajectory_progress: How much the job advances the user's career path (0-1)
- evidence_coverage: Fraction of required capabilities backed by evidence (0-1)

The formula ensures that upside is gated by evidence credibility - unbacked potential doesn't count as real upside.

### Evidence Coverage
Measures what fraction of required capabilities are backed by user evidence:
```
evidence_coverage = (number of capabilities with evidence) / (total number of required capabilities)
```
Where capabilities are drawn from the job's structured attributes and mapped to the user's evidence base.

## Score Decomposition and Transparency

The system provides full transparency into how scores are calculated:

### Score Breakdown
For any job, users can see:
- Raw dimension scores (0-1 scale)
- Weighted dimension scores (contribution to total)
- Percentage contribution of each dimension to the total
- Raw total score (0-100 scale)
- Final decision (strong/review/reject)

### Example Score Decomposition
For a job with the following scores:
- Role Match: 0.8 × 0.25 = 0.20 (20% of total)
- AI Relevance: 0.9 × 0.20 = 0.18 (18% of total)
- Compensation: 0.6 × 0.15 = 0.09 (9% of total)
- Location: 1.0 × 0.10 = 0.10 (10% of total)
- Leadership: 0.7 × 0.20 = 0.14 (14% of total)
- Purpose: 0.5 × 0.05 = 0.025 (2.5% of total)
- WLB: 0.8 × 0.05 = 0.04 (4% of total)

Raw Total: 0.775 → Stored Total: 77.5 → Decision: Strong

## Evidence Integration

While evidence doesn't change the arithmetic score, it affects the assessment in several ways:

### Evidence Coverage in Career Upside
As shown in the career upside formula, evidence coverage directly scales the upside calculation:
- Low evidence coverage (0.0) → career_upside = trajectory_progress × 0.5
- High evidence coverage (1.0) → career_upside = trajectory_progress × 1.0

### Confidence and Risk Notes
Evidence affects the confidence in the assessment and generates risk notes:
- High evidence across multiple dimensions → high confidence, low risk
- Mixed evidence → medium confidence, mixed risk notes
- Low evidence → low confidence, high risk notes
- Specific gaps in evidence → specific risk notes about missing evidence

### Strengths and Gaps Identification
The system identifies:
- **Strengths**: Areas where the user has strong evidence exceeding job requirements
- **Gaps**: Areas where the job requires capabilities the user lacks evidence for
- **Transferable Skills**: Areas where the user has related evidence that could apply
- **Positioning Guidance**: How to frame the user's background for this role

## Hard Constraints vs Soft Preferences

The system distinguishes between hard constraints (must-haves) and soft preferences (nice-to-haves):

### Hard Constraints (Score = 0 if Violated)
These are absolute requirements that, if not met, result in automatic rejection:
- **Salary Floor**: Below minimum acceptable salary (e.g., €120k)
- **Mandatory Requirements**: Non-negotiable job requirements (e.g., specific certification)
- **Location Veto**: Absolute location requirements (e.g., must be in Munich)
- **Work Authorization**: Legal right to work in the job's location
- **Security Clearance**: Required security clearance level

### Soft Preferences (Adjust Score)
These are preferences that adjust the score but don't cause automatic rejection:
- **Salary Target**: Preferred salary range (affects compensation match score)
- **Location Preference**: Preferred geographic areas (affects location match score)
- **Work Style Preference**: Remote/hybrid/onsite preference (affects location match score)
- **Industry Preference**: Preferred industries (affects domain match score)
- **Company Size Preference**: Preferred company sizes (affects company match score)
- **Role Preference**: Preferred role types (affects role match score)

### Implementation
Hard constraints are checked first - if any are violated, the job gets a score of 0 and is marked as a hard fail.
Soft preferences are incorporated into the normal scoring dimensions.

## Munich Preference Implementation

The user's preference for Munich is implemented as part of the location match dimension:

### Location Match Calculation
```
base_location_score = geographic_match_score
remote_modifier = remote_work_compatibility_score
relocation_modifier = willingness_to_relocate_score

location_score = base_location_score × remote_modifier × relocation_modifier
```

Where:
- **geographic_match_score**: 
  - Munich: 1.0
  - Remote EU: 0.8
  - Other EU: 0.6
  - International: 0.4
  - Non-EU: 0.2
- **remote_work_compatibility_score**:
  - Fully remote: 1.0
  - Hybrid: 0.7
  - Onsite only: 0.3
- **willingness_to_relocate_score**:
  - Willing to relocate anywhere: 1.0
  - Willing to relocate within EU: 0.8
  - Not willing to relocate: 0.3 (applies to non-current-location jobs)

This creates a preference gradient where Munich jobs score highest, followed by remote EU jobs, etc.

## Compensation Logic

The compensation scoring uses a piecewise linear function with a hard floor:

### Compensation Match Calculation
```
if salary_min_eur < minimum_eur:
    comp_score = 0.0
elif salary_max_eur <= target_eur:
    comp_score = (salary_max_eur - minimum_eur) / (target_eur - minimum_eur)
else:
    # Above target - continued increase with diminishing returns
    excess_ratio = (salary_max_eur - target_eur) / (target_eur - minimum_eur)
    comp_score = 1.0 + (0.5 * (1 - math.exp(-excess_ratio)))
```

### Parameters
- **minimum_eur**: €120,000 (hard floor - scores 0 below this)
- **target_eur**: €150,000 (ideal target - scores 1.0 at this point)
- **Above target behavior**: Continues to increase score but with diminishing returns
  - €180,000 → ~1.2
  - €240,000 → ~1.4
  - €300,000 → ~1.5

### Special Cases
- **Undisclosed Salary**: Treated as unknown_salary_reviewable (configurable)
  - If true: Score based on other dimensions, flag for review
  - If false: Score 0.0 (treated as below minimum)
- **Salary Range Width**: Wider ranges increase confidence in the score
- **Salary Source**: Different weights for different salary sources (advertised vs. negotiated vs. etc.)

## Score Thresholds and Calibration

The system uses configurable thresholds to determine decisions:

### Decision Thresholds
- **Strong Threshold**: Minimum score for "strong" recommendation (default: 75)
- **Review Threshold**: Minimum score for "review" recommendation (default: 50)
- **Reject Threshold**: Scores below this are "reject" (implicitly: 0-49)

These thresholds are configurable in the system configuration to adjust sensitivity.

### Calibration Through Feedback
While the scoring weights are fixed (not automatically adjusted based on feedback), the system uses feedback to:
1. **Measure Precision@K**: What percentage of top-K ranked jobs receive positive feedback?
2. **Analyze False Positives**: What percentage of top-ranked jobs receive negative feedback?
3. **Analyze False Negatives**: What percentage of positively reviewed jobs were ranked outside top-K?
4. **Generate Reports**: Daily intelligence reports showing trends and recommendations
5. **Inform Manual Adjustments**: Human reviewers can adjust weights based on observed performance

### Example Feedback-Driven Insights
If feedback shows:
- High false positive rate in top-10 → Consider increasing weight on dimensions that separate good from bad fits
- Low precision@20 → Consider increasing weight on more discriminative dimensions
- Systematic under-scoring of AI roles → Consider increasing weight on AI relevance dimension
- Location miscalibration → Consider adjusting location match parameters

The system does NOT automatically adjust weights based on feedback - this requires explicit human review and configuration change to prevent unintended consequences and maintain auditability.

## Implementation Details

### Score Calculation Flow
```
Job Attributes Extraction
        ↓
Career Profile Derivation
        ↓
Dimension Scoring (7 dimensions)
        ↓
Weight Application (configurable weights)
        ↓
Summation (raw total score)
        ↓
Normalization (0-100 scale)
        ↓
Decision Logic (strong/review/reject thresholds)
        ↓
Persistence to evaluations table
```

### Career Fit Assessment Flow
```
Structured Job Attributes Extraction
        ↓
Career Profile Derivation
        ↓
Optional Parent Retrieval (Personal AI)
        ↓
Evidence Collection & Compaction
        ↓
Current Fit Calculation (weighted match)
        ↓
Career Upside Calculation (trajectory × evidence)
        ↓
Strengths/Gaps/Transferables Identification
        ↓
Positioning & Risks Analysis
        ↓
Optional LLM Narrative (schema-validated, fail-closed)
        ↓
Persistence to career_fit table
```

### Anti-Fabrication and Validation
All scoring and assessment includes validation to prevent fabrication:
- Input validation (job attributes within expected ranges)
- Range checking (scores within 0-1 or 0-100)
- Cross-validation (dimensions sum correctly)
- Evidence tracking (claims trace back to source data)
- LLM output validation (schema compliance, evidence ID restriction)
- Length limiting (prevent runaway generation)

## Configuration

The scoring system is configured through the `ranking` section in config.yaml:

```yaml
ranking:
  similarity_weight: 0.25      # Role match
  ai_relevance_weight: 0.20    # AI relevance
  compensation_weight: 0.15    # Compensation match
  location_weight: 0.10        # Location match
  leadership_weight: 0.20      # Leadership match
  purpose_weight: 0.05         # Purpose match
  wlb_weight: 0.05             # Work-life balance
```

### Constraints
- All weights must be non-negative
- Weights must sum exactly to 1.0
- Changes require explicit configuration update
- No automatic weight adjustment based on feedback (requires manual review)

## Validation and Testing

### Unit Tests
- Individual dimension scoring functions tested with fixtures
- Weight application and summation tested
- Score normalization and decision logic tested
- Career fit assessment calculations tested
- Hard constraint detection tested
- Soft preference adjustment tested
- Edge cases and boundary conditions tested

### Integration Tests
- End-to-end scoring pipelines tested with sample jobs
- Score decomposition accuracy verified
- Decision threshold boundaries tested
- Feedback integration verified
- Report generation validated

### Property-Based Tests
- Score monotonicity (better inputs never produce worse scores)
- Dimension independence (changing one dimension doesn't unexpectedly affect others)
- Translation invariance (adding constant to all inputs doesn't change relative rankings)
- Identity preservation (identical jobs produce identical scores)
- Symmetry preservation (swapping symmetric inputs produces symmetric outputs)

## Performance Characteristics

### Deterministic Operations (Fast)
- Dimension scoring: microseconds to milliseconds per job
- Weight application and summation: microseconds per job
- Score normalization: microseconds per job
- Decision logic: microseconds per job
- Career fit assessment: milliseconds to seconds per job (depends on evidence retrieval)

### Variable Operations
- Evidence retrieval: depends on Personal AI database size and query complexity
- LLM narrative generation: depends on model speed and prompt complexity (seconds to tens of seconds)
- Report generation: depends on number of jobs and complexity of analysis

### Primary Bottlenecks
1. **Evidence Retrieval**: Querying Personal AI database for career-relevant evidence
2. **LLM Narrative**: Optional LLM generation for career fit explanations
3. **Report Generation**: Analysis of large numbers of jobs for trends and insights

## Extensibility

### Adding New Dimensions
To add a new scoring dimension:
1. Implement the dimension scoring function (returns 0-1 scale)
2. Add the dimension to the weighting configuration
3. Update the decision logic if needed (unlikely)
4. Add unit tests for the new dimension
5. Update score decomposition and reporting
6. Ensure the dimension weight is included in the validation that weights sum to 1.0

### Changing Weights
To adjust dimension weights:
1. Update the values in the ranking configuration section
2. Ensure the new weights still sum to exactly 1.0
3. Update any documentation referencing the old weights
4. No code changes needed - the system reads weights from configuration

### Changing Thresholds
To adjust decision thresholds:
1. Update the strong_throttle and review_throttle values in configuration
2. Ensure strong_throttle > review_throttle ≥ 0
3. Update any documentation referencing the old thresholds
4. No code changes needed - the system reads thresholds from configuration

### Adding New Hard Constraints
To add a new hard constraint:
1. Implement the constraint checking function (returns boolean)
2. Add the constraint to the hard constraint checking logic
3. Add unit tests for the new constraint
4. Update documentation to reflect the new constraint

## Comparison to Alternative Approaches

### vs. Machine Learning Ranking
**Advantages of Deterministic Approach:**
- Full transparency and auditability
- No hidden biases or black-box behavior
- Explainable decisions
- No training data required
- No risk of overfitting or data leakage
- Consistent behavior across deployments
- Easy to debug and validate

**Disadvantages:**
- May miss complex nonlinear relationships
- Requires manual feature engineering
- Less adaptive to changing preferences without manual updates
- May not capture subtle interactions between dimensions

### vs. Pure Evidence-Based Scoring
**Advantages of Hybrid Approach:**
- Combines structured assessment with evidence credibility
- Prevents evidence-poor but potentially good matches from being unfairly penalized
- Allows for trajectory-based assessment (career upside)
- Maintains decidability and explainability
- Evidence modulates confidence without changing core assessment

**Disadvantages:**
- More complex than pure evidence-based or pure structural approaches
- Requires maintaining both structural and evidence systems
- Potential for disagreement between structural score and evidence assessment

### vs. Rule-Based Systems
**Advantages of Weighted Approach:**
- More nuanced than binary pass/fail rules
- Allows for trade-offs between dimensions
- Continuous scoring enables ranking and selection
- Configurable weights enable tuning without code changes
- More expressive than simple rule sets

**Disadvantages:**
- Requires careful weight tuning to reflect true preferences
- Risk of weight drift over time without recalibration
- More complex to validate than simple rule sets
- May produce counterintuitive results if weights are poorly chosen

## Summary

The Job Agent scoring architecture provides:
- **Deterministic, auditable scoring** with no hidden randomness
- **Transparent, explainable decisions** through full score decomposition
- **Two-axis assessment** (current fit vs career upside) preventing score inflation
- **Evidence-modulated assessment** where evidence affects confidence but not raw scores
- **Configurable weights and thresholds** enabling tuning without code changes
- **Hard constraint handling** for absolute requirements
- **Soft preference modeling** for nuanced trade-offs
- **Full provenance tracking** for auditability and reproducibility
- **Anti-fabrication guarantees** ensuring no invented claims influence scores
- **Extensible design** allowing for new dimensions and constraints
- **Validation and testing** ensuring correctness and reliability