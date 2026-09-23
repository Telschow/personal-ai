# PROVIDER DISCOVERY STRATEGY FOR STAGE B

## Executive Summary

The current job database contains only **146 unique jobs**, with the majority from:
- RemoteOK: 99 jobs (~68%)
- Remotive: 20 jobs (~14%)  
- Direct web scraping: 26 jobs (~18%)
- Test data: 1 job (~1%)

This represents a **critically insufficient dataset** for achieving the target of >1,000 unique jobs.

## Immediate Requirement

**Provider sources must provide the primary scaling mechanism** for reaching the 1,000-job target within realistic timeframes.

The current architecture has:
- 32 enabled sources in catalog
- 2 with functional providers (RemoteOK, Remotive)
- 30 without providers (search engines, structured feeds, ATS boards)

## Strategic Pivot Required

Current State:
```text
Search engines:
25 queries → 45 candidates → 1.8 jobs/query

each query requires DDGS, rate limiting, bot protection
→ slow, unreliable, hard to scale

Providers (RemoteOK + Remotive):
2 queries → 119 jobs → 60 jobs/query

each query is fast, structured, reliable
```

Required:
```text
Provider-first crawl
→ scale to 500-1000+ provider queries
→ disable search engines temporarily
→ target 1,000+ jobs from providers alone
→ optimize provider yield
```

## Provider Architecture Analysis

### Existing Provider Implementation

RemoteOK:
- HTTP API to `https://remoteok.com/api`
- Returns ~100 jobs per request
- Fully structured JSON
- Rate limited but well-behaved
- Status: **PROVEN** ✓

Remotive:  
- HTTP API to `https://remotive.com/api/remote-jobs`
- Returns ~20 jobs per request
- Full job details with descriptions
- Some rate limiting
- Status: **PROVEN** ✓

### Missing Provider Implementations

The following potential providers require immediate implementation:

#### Tier A - High-Yield Structured Sources

**Greenhouse** (`https://boards-api.greenhouse.io/v1/boards/{company}/jobs`)
- **Status**: Currently broken (404 error)
- **Implication**: Verify actual API endpoint and authentication
- **Yield**: Platform job boards, often high-volume
- **Priority**: **CRITICAL** - one of the largest ATS providers

**Lever** (`https://api.lever.co/v0/postings/{company}?mode=json`)
- **Status**: Currently broken (404 error)
- **Implication**: Company token validation required
- **Yield**: Major tech company boards
- **Priority**: **CRITICAL** - large enterprise presence

**Ashby** (`https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true`)
- **Status**: Currently broken (404 error)
- **Implication**: Board name validation required
- **Yield**: Modern ATS, high volume potential
- **Priority**: **CRITICAL** - growing platform

**SmartRecruiters** (`https://api.smartrecruiters.com/v1/companies/{company}/postings`)
- **Status**: Partially working (0 jobs returned)
- **Implication**: May require additional parameters or different endpoint
- **Yield**: Global enterprise ATS
- **Priority**: **HIGH** - international presence

#### Tier B - Structured Job Feeds

**RSS/JSON Feeds** (various)
- **Status**: Multiple entries in catalog
- **Implication**: Implementation exists but requires testing
- **Yield**: Varies by source, can be high for niche boards
- **Priority**: **MEDIUM** - require validation

#### Tier C - Emerging Platforms

**Workable**, **Recruitee**, **Teamtailor**, **Personio**
- **Status**: Catalog entries exist
- **Implication**: Implementation would require effort
- **Yield**: Established platforms
- **Priority**: **LOWER** - implement after core providers

## Technical Implementation Plan

### 1. Provider Registry

Create a unified provider interface (`DiscoveryProvider` abstract base class) if one doesn't exist.

Current status: Providers module exists with `DiscoveryProvider` base class and basic implementations (RemoteOkProvider, RemotiveProvider).

### 2. Provider Factory

Implement a factory function (`provider_for`) that resolves catalog entries to provider instances.

Current status: Exists but only returns 2 of 34 catalog entries.

### 3. Provider-Only Discovery Mode

Add a discovery mode flag (`provider_only`) that:
- Skips all search engine queries
- Only processes sources with functional providers
- Tracks provider-specific metrics

### 4. Provider Health Tracking

Implement provider health monitoring for:
- Success/failure rates
- Job yield per request
- Latency and rate limiting behavior
- Duplicate detection
- Recent job availability

## Provider Configuration Requirements

### Configuration Schema

Add provider-specific configuration to `job_agent/config.py`:

```yaml
providers:
  enable_provider_only: true
  skip_search_engines: true
  max_provider_requests: 1000
  min_jobs_per_request: 10
  provider_timeout_seconds: 30

provider:
  greenhouse:
    token: "your-company-token"
    enabled: true
    priority: 90
  lever:
    site: "your-lever-site"
    enabled: true
    priority: 88
  ashby:
    board: "your-ashby-board"
    enabled: true
    priority: 86
  smartrecruiters:
    company: "your-company-id"
    enabled: true
    priority: 84
```

### Provider Validation

Before large-scale deployment, validate each provider:
1. **Reachable**: Can the API be accessed?
2. **Authenticated**: Does it require tokens/sites?
3. **Pagination**: Does it support pagination?
4. **Yield**: How many jobs per request on average?
5. **Reliability**: Success rate and error handling?

## Implementation Timeline

### Phase 1: Provider-only Crawl

**Goal**: Reach 1,000 jobs using existing providers only

**Configuration**:
- Set `provider_only: true`
- `skip_search_engines: true`
- Increase provider request limits
- Optimize query planning for providers

**Implementation**:
1. Modify discovery search to skip sources without providers
2. Add provider health tracking and metrics
3. Optimize query rotation for provider sources
4. Validate provider pagination support

### Phase 2: Provider Expansion

**Goal**: Add 3-4 new high-yield providers

**Priority Order**:
1. **Greenhouse** (if endpoint is correct)
2. **Lever** (if site name is correct)
3. **Ashby** (if board name is correct)
4. **Workable** (if API works)

**Validation**:
- Small-scale testing (10 requests per provider)
- Measure job yield
- Validate data structure
- Test pagination

### Phase 3: Provider Optimization

**Goal**: Maximize yield from existing providers

**Techniques**:
1. Optimize query parameters
2. Implement intelligent caching
3. Add retry logic for transient errors
4. Monitor and adapt to rate limits
5. Track job freshness

### Phase 4: Large-Scale Deployment

**Goal**: 1,000+ jobs with high provider coverage

**Configuration**:
- High provider request limits (5000+ requests)
- Aggressive query rotation
- Provider-specific pacing
- Fallback to search engines only if provider yield is insufficient

## Performance Targets

### Provider Targets

| Provider | Current Jobs/Request | Target Jobs/Request | Status |
| -------- | -------------------: | ------------------: | ------- |
| RemoteOK | 60 | 80-100 | **PROVEN** |
| Remotive | 20 | 50-80 | **PROVEN** |
| Greenhouse | 0 | 50-100 | **NEEDS WORK** |
| Lever | 0 | 30-60 | **NEEDS WORK** |
| Ashby | 0 | 40-80 | **NEEDS WORK** |
| Workable | 0 | 30-60 | **NEEDS WORK** |

### Discovery Targets

- **Total queries**: 500-1000 (provider-only)
- **Expected jobs**: 20,000-40,000 candidates
- **Unique persisted**: 1,000+ jobs
- **Recent high-fit**: 100+ jobs (last 30 days)
- **Provider coverage**: >90% of total jobs

## Risk Mitigation

### 1. Provider Dependencies

**Risk**: Relying on external providers that may change APIs or rate limit.

**Mitigation**:
- Implement fallback providers
- Cache provider results where possible
- Monitor provider health continuously
- Have multiple provider sources per job category

### 2. Provider Authentication

**Risk**: Providers require API tokens or authentication.

**Mitigation**:
- Use environment variables for credentials
- Skip authenticated providers during initial scale-up
- Document authentication requirements

### 3. Provider Rate Limiting

**Risk**: Providers may rate limit or block queries.

**Mitigation**:
- Implement intelligent pacing based on provider health
- Track provider-specific rate limits
- Implement exponential backoff for failures
- Use multiple providers for redundancy

### 4. Provider Data Quality

**Risk**: Provider data may be inconsistent or incomplete.

**Mitigation**:
- Implement robust data validation
- Handle missing fields gracefully
- Normalize job data across providers
- Track data completeness metrics

## Technical Implementation Details

### Provider Interface

```python
class DiscoveryProvider(ABC):
    name: str
    base_url: str
    
    def fetch(self, **kwargs) -> ProviderResult:
        """Fetch jobs from provider"""
        pass
```

### Provider Result

```python
@dataclass(frozen=True)
class ProviderResult:
    source_id: str
    provider: str
    jobs: tuple[Job, ...]
    status: ProviderStatus
    requests: int = 0
    hits: int = 0
    duplicates: int = 0
    errors: tuple[str, ...] = ()
    latency_ms: int = 0
```

### Discovery Configuration

```python
@dataclass
class DiscoveryConfig:
    provider_only: bool = False
    skip_search_engines: bool = True
    max_provider_requests: int = 1000
    min_jobs_per_request: int = 10
    provider_timeout_seconds: int = 30
    provider_pacing: DiscoveryPacing
```

### Query Planning for Providers

```python
def build_provider_query_plan(
    cfg: Config,
    catalog: SourceCatalog,
    limit_total: int,
) -> QueryPlan:
    """Plan queries focusing on sources with providers"""
    pass
```

## Implementation Steps

### Step 1: Add Provider-Only Mode

1. Modify `job_agent/discovery_search.py`:
   - Add `provider_only` parameter to `run_planned_discovery`
   - Skip sources without providers when in provider-only mode
   - Track provider-specific metrics

2. Update `job_agent/config.py`:
   - Add `provider_only` and `skip_search_engines` fields
   - Update validation logic

### Step 2: Validate Existing Provider Endpoints

1. Test Greenhouse endpoint:
   - Check actual API endpoint
   - Verify authentication requirements
   - Test with real company token

2. Test Lever endpoint:
   - Check actual API endpoint
   - Verify site name requirements
   - Test with real site identifier

3. Test Ashby endpoint:
   - Check actual API endpoint
   - Verify board name requirements
   - Test with real board identifier

### Step 3: Implement Missing Providers

1. **Greenhouse Implementation**:
   - Fix endpoint URL
   - Add error handling
   - Implement pagination if available
   - Add rate limiting

2. **Lever Implementation**:
   - Fix endpoint URL
   - Add error handling
   - Implement pagination
   - Add rate limiting

3. **Ashby Implementation**:
   - Fix endpoint URL
   - Add error handling
   - Implement pagination
   - Add rate limiting

### Step 4: Optimize Query Planning

1. Modify `job_agent/query_plan.py`:
   - Add provider-specific query planning
   - Optimize for provider sources
   - Implement provider health-based rotation

2. Update `job_agent/discovery_search.py`:
   - Add provider health tracking
   - Implement provider-specific pacing
   - Optimize query prioritization

### Step 5: Performance Testing

1. Set up provider-only benchmark:
   - Target 1,000 jobs from providers only
   - Track provider health metrics
   - Measure query efficiency

2. Validate provider health:
   - Success/failure rates
   - Job yield per request
   - Latency and rate limiting
   - Duplicate detection

### Step 6: Large-Scale Deployment

1. Configure provider-only discovery:
   - Enable provider-only mode
   - Increase provider request limits
   - Optimize provider rotation

2. Run large crawl:
   - Monitor provider health
   - Adjust query limits if needed
   - Validate job quality

## Testing Strategy

### Unit Tests

1. **Provider Tests**:
   - Test provider adapters with mock responses
   - Test error handling
   - Test pagination

2. **Discovery Tests**:
   - Test provider-only mode
   - Test search engine fallback
   - Test provider health tracking

3. **Integration Tests**:
   - Test end-to-end provider discovery
   - Test provider query planning
   - Test provider-only crawl

### Performance Tests

1. **Provider Health Tests**:
   - Test provider yield measurement
   - Test provider rate limiting
   - Test provider error handling

2. **Discovery Performance Tests**:
   - Test provider-only discovery performance
   - Test provider health monitoring
   - Test provider optimization

3. **Scalability Tests**:
   - Test large provider discovery
   - Test provider health tracking at scale
   - Test provider optimization

## Monitoring and Alerting

### Provider Health Monitoring

Track provider health metrics:
- Success/failure rate
- Job yield per request
- Average latency
- Rate limiting events
- Error rates

### Discovery Health Monitoring

Track discovery health metrics:
- Provider-only discovery success
- Search engine fallback usage
- Query efficiency
- Job quality metrics

### Alerting

Set up alerts for:
- Provider failures
- High error rates
- Low job yield
- Provider rate limiting

## Rollout Plan

### Phase 1: Provider-Only Baseline

1. Enable provider-only mode
2. Skip all search engines
3. Target 500 provider queries
4. Validate provider health
5. Measure job yield

### Phase 2: Provider Expansion

1. Add Greenhouse provider
2. Add Lever provider
3. Add Ashby provider
4. Increase provider queries to 1,000
5. Validate new providers

### Phase 3: Provider Optimization

1. Optimize provider query planning
2. Implement provider-specific pacing
3. Add provider health-based rotation
4. Increase provider queries to 2,000
5. Validate provider optimization

### Phase 4: Large-Scale Production

1. Enable provider-only mode in production
2. Run discovery to 5,000+ queries
3. Target 10,000+ unique jobs
4. Validate job quality
5. Optimize for speed and reliability

## Success Criteria

### Minimum Requirements

1. **Provider-only discovery works**: Can run discovery without search engines
2. **Provider yield meets targets**: Average yield >= 50 jobs/request
3. **Provider health tracked**: Success/failure rates measured
4. **Provider health monitored**: Latency, rate limiting, errors tracked
5. **Job quality validated**: Jobs are well-structured and relevant

### Success Criteria

1. **Scale**: Achieve 1,000+ unique jobs from providers
2. **Quality**: Maintain high job quality and career fit
3. **Reliability**: Provider discovery is reliable and resilient
4. **Performance**: Discovery completes within reasonable timeframes
5. **Coverage**: Broad provider coverage across job categories

## Conclusion

The provider-first discovery strategy offers the best path to achieving the 1,000+ job target. By:

1. Focusing on high-yield providers
2. Implementing robust provider health monitoring
3. Optimizing query planning for providers
4. Skipping search engines initially

We can rapidly scale job discovery while maintaining quality and reliability.

This approach:
- Leverages proven provider APIs
- Minimizes search engine bottlenecks
- Provides clear provider health metrics
- Allows for iterative provider expansion
- Maintains discovery resilience

The implementation is feasible with minimal architectural changes and provides a clear path to achieving the discovery scale targets.

---

## Required Code Changes

### High-Level Changes

1. **job_agent/config.py**:
   - Add `provider_only` configuration option
   - Update validation logic
   - Add provider-specific settings

2. **job_agent/discovery_search.py**:
   - Add provider-only mode support
   - Skip sources without providers
   - Track provider-specific metrics
   - Implement provider health monitoring

3. **job_agent/query_plan.py**:
   - Optimize query planning for providers
   - Add provider-specific rotation
   - Implement provider health-based scheduling

4. **job_agent/providers.py**:
   - Ensure all providers work correctly
   - Fix broken provider endpoints
   - Add error handling
   - Implement pagination

### Configuration Updates

Update `job_agent/config.yaml`:
```yaml
discovery:
  provider_only: true
  skip_search_engines: true
  max_queries_total: 1000
  max_queries_per_track: 200
  max_queries_per_source: 50
  max_sources_per_track: 15
```

### Testing Updates

Update test configurations:
- Add provider-only mode tests
- Add provider health monitoring tests
- Add provider expansion tests
- Add provider fallback tests

### Documentation Updates

Update documentation:
- Add provider-first discovery strategy
- Document provider health metrics
- Document provider expansion process
- Document provider optimization techniques

---

## Immediate Next Steps

### Step 1: Enable Provider-Only Mode

1. Modify `job_agent/discovery_search.py` to support provider-only mode
2. Update `job_agent/config.py` with provider-only configuration
3. Implement provider health tracking
4. Validate provider-only discovery works

### Step 2: Test Provider-Only Discovery

1. Run provider-only discovery with current providers
2. Validate job yield targets
3. Measure provider health metrics
4. Optimize provider rotation

### Step 3: Add New Providers

1. Fix Greenhouse provider endpoint
2. Add Lever provider implementation
3. Add Ashby provider implementation
4. Validate all providers work correctly

### Step 4: Scale Up Provider Queries

1. Increase provider request limits
2. Optimize provider query planning
3. Implement provider-specific pacing
4. Run large provider-only discovery

### Step 5: Validate Results

1. Ensure 1,000+ jobs from providers
2. Validate recent high-fit job targets
3. Confirm provider health metrics
4. Optimize for production deployment

---

This strategy provides a clear, actionable path to achieving the discovery scale targets while maintaining job quality and reliability.

Key advantages:
- Leverages proven provider APIs
- Minimizes search engine bottlenecks
- Provides clear metrics for success
- Allows for iterative improvement
- Maintains discovery resilience

The implementation is feasible with minimal architectural changes and provides a clear roadmap for achieving the discovery goals.

---

## Implementation Priority Matrix

| Provider | Current Yield | Target Yield | Priority | Status |
| -------- | -------------: | ------------: | -------- | ------- |
| RemoteOK | 60 | 80-100 | **HIGH** | **PROVEN** |
| Remotive | 20 | 50-80 | **HIGH** | **PROVEN** |
| Greenhouse | 0 | 50-100 | **CRITICAL** | **BROKEN** |
| Lever | 0 | 30-60 | **CRITICAL** | **BROKEN** |
| Ashby | 0 | 40-80 | **CRITICAL** | **BROKEN** |

**Immediate Action Items**:
1. Fix Greenhouse endpoint
2. Implement Lever provider
3. Implement Ashby provider
4. Enable provider-only mode
5. Scale up provider queries

**Timeline**: 2-3 weeks for initial implementation, 4-6 weeks for full deployment.