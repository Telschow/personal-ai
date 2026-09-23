# STAGED LARGE CRAWL DESIGN

## Overview

Following the principle from Section 18: "Run in Stages", we will execute the large crawl in three stages:

### Stage A: Exploration (10-20% of budget)
- Goal: Measure yield/query, parse rate, date-valid rate, fit distribution
- Configuration: Limit to 25 queries total, 5 per track, 2 per source
- Duration: ~15-20 minutes

### Stage B: Expansion (60% of budget) 
- Goal: Allocate resources based on Stage A measurements
- Configuration: Dynamic limits based on Stage A yield/query metrics
- Duration: ~60-90 minutes

### Stage C: Saturation (30% of budget)
- Goal: Fill gaps and maximize unique yield
- Configuration: Focus on high-yield sources/query families from Stages A-B
- Duration: ~30-45 minutes

Total target: >1,000 unique persisted jobs

## Stage A Configuration

```yaml
# stage_a_config.yaml overrides
career:
  discovery:
    max_queries_total: 25
    max_queries_per_track: 5
    max_queries_per_source: 2
    max_sources_per_track: 4
  pacing:
    interval_by_class:
      low: 1.0
      medium: 2.0  
      high: 4.0
    backoff_base_s: 2.0
    backoff_max_s: 30.0
    max_consecutive_failures: 3
```

Expected Stage A Outcomes:
- 200-400 candidate URLs discovered
- 80-150 jobs parsed and normalized
- 60-100 unique jobs persisted
- Measurement of: jobs/query, parse rate, recent job %, fit score distribution

## Stage B Configuration (Data-Driven)

Based on Stage A measurements, allocate budget to:
1. Highest yielding sources (2x allocation)
2. Highest yielding query families (2x allocation)  
3. Under-explored geographic regions (1.5x)
4. Maintain exploratory allocation (1x) for new discoveries

Example if RemoteOK shows 5 jobs/query and Greenhouse shows 3 jobs/query:
- Increase RemoteOK allocation by 60%
- Increase Greenhouse allocation by 20%
- Maintain baseline for exploratory sources

## Stage C Configuration (Optimization)

Focus on:
1. Query families with highest high-fit yield
2. Geographic regions with unexplored potential
3. Sources with good yield but low recent job % (to find older high-value roles)
4. Long-tail specialized combinations

## Monitoring & Adaptation

After each stage, measure:
1. **Discovery Efficiency**: jobs_persisted / queries_executed
2. **Recent Job Ratio**: jobs_within_30_days / jobs_persisted  
3. **High-Fit Ratio**: jobs_with_fit_>=0.60 / jobs_persited
4. **Source Health**: success rate, latency, quota efficiency
5. **Duplicate Rate**: duplicates / candidates_found

Adaptation Rules:
- If jobs/query < 1.5: investigate source parsing issues
- If recent job ratio < 0.25: expand geographic/recent query terms  
- If high-fit ratio < 0.15: review query relevance or fit model strictness
- If duplicate rate > 0.4: improve deduplication or reduce repetitive queries

## Resource Protection

Maintain throughout all stages:
1. Per-source rate limiting (per DiscoveryPacing config)
2. Per-query max retries = 0 (per config - retry storms unacceptable)
3. Source pausing after max_consecutive_failures = 3
4. Global query timeout inherited from provider config
5. Memory usage monitoring (no unbounded accumulation)

## Success Criteria for Stage Progression

**A→B**: Complete when:
- Minimum 20 queries executed
- At least 3 sources sampled
- Preliminary yield metrics available

**B→C**: Complete when:
- 60% of planned Stage B queries executed
- Diminishing returns observed (<20% increase in unique jobs per 10 queries)
- Geographic/track coverage balanced

## Final Validation

Post-crawl verification:
1. Deduplication efficacy check (canonical_url uniqueness)
2. Date filtering accuracy (manual spot-check of 20 recent jobs)
3. Fit score validation (manual review of 10 high-fit vs 10 low-fit)
4. GUI functionality test (sorting, filtering, pagination with >1000 jobs)