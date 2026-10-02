# Personal AI Career Search System

## Overview

A fully operational personal career-search assistant that discovers jobs, classifies them, scores them against your career profile, and generates actionable recommendations.

## Architecture

### Core Components

1. **Discovery Pipeline**
   - Company radar discovery
   - Greenhouse pagination
   - Ashby pagination
   - SmartRecruiters integration
   - Direct source discovery
   - Run isolation with unique run IDs

2. **Normalization & Classification**
   - Job normalization
   - Role archetype classification
   - Location classification
   - Career direction classification
   - Compensation tracking

3. **Career Intelligence**
   - Career profile from `profile/profile.yaml`
   - Deterministic fit analysis
   - Separate fit dimensions (role, location, compensation, etc.)
   - Explainable scoring

4. **CV & Portfolio**
   - CV ingestion from PDF
   - CV tailoring engine
   - Portfolio project recommendations
   - Evidence validation

5. **LinkedIn Integration**
   - User-provided profile import (manual only)
   - Profile optimization recommendations
   - Manual job import with deduplication
   - LinkedIn search pack generation

6. **Application Tracking**
   - Status tracking
   - Next action management
   - Dossier generation

## Quick Start

### 1. Configuration

```bash
# Configuration
config.yaml
profile/profile.yaml  # Career profile
company_radar.yaml    # Company radar config
```

### 2. Run Discovery

```bash
cd job_agent
uv run python -m job_agent.cli scan --no-global-search
```

### 3. Generate Report

```bash
cd job_agent
uv run python generate_sample_report.py
```

### 4. View Output

```bash
ls output/reports/
```

## Workflow

### Daily Workflow

1. **Run crawl**
   ```bash
   uv run python -m job_agent.cli scan --no-global-search
   ```

2. **Review new jobs**
   ```bash
   cat output/reports/career_report_*.md
   ```

3. **Review shortlists**
   - Top 20 overall
   - Opportunities in your preferred city
   - Remote and international

4. **Select for application**
   ```bash
   uv run python -c "from job_agent.career.applications import *; ..."
   ```

5. **Generate tailored CV**
   - CV variant automatically generated for each job

6. **Review portfolio recommendations**
   - Project suggestions for career gaps

7. **Apply manually**
   - Use generated application package

8. **Track outcome**
   - Update application status

9. **Refresh profile periodically**
   - Update profile/profile.yaml

### Weekly Workflow

1. Run full crawl
2. Review LinkedIn optimization recommendations
3. Review application pipeline
4. Update next actions
5. Review portfolio project progress

## Data Model

### Jobs Table

Key fields:
- `run_id` - Discovery run isolation
- `role_family`, `role_archetypes` - Role classification
- `location_city`, `location_country`, `location_scope`, `location_score` - Location classification
- `salary_min`, `salary_max`, `compensation_status` - Compensation tracking
- `discovery_source`, `company_radar_id`, `provider_native_id` - Provenance

### Applications Table

- `job_id` - Linked to jobs
- `status` - Application status
- `cv_variant` - Tailored CV reference
- `next_action` - Pending actions
- `next_action_date` - Due date

## LinkedIn Safety

**This system does NOT:**
- Scrape LinkedIn
- Automate login
- Use browser automation
- Access cookies/sessions
- Send automated messages
- Auto-apply

**This system DOES:**
- Import user-provided LinkedIn profile content
- Generate optimization recommendations
- Support manual job import
- Generate LinkedIn search packs
- Recommend profile improvements

## Features

### Career Fit Analysis

Separate dimensions:
- Career fit score
- Role fit
- Seniority fit
- Domain fit
- Skill fit
- Leadership fit
- Location fit
- Compensation fit
- Company fit

Each dimension is independently scored and explainable.

### CV Tailoring

- Factual, non-invented content
- Evidence mapping to master CV
- Keyword alignment with job requirements
- Gap identification
- Rationale for changes

### Portfolio Projects

- Map to specific career gaps
- Target specific job clusters
- Demonstrate relevant skills
- Estimated effort
- GitHub structure recommendations

### Application Tracking

Statuses:
- discovered
- reviewed
- shortlisted
- applied
- interview
- rejected
- offer
- withdrawn
- closed

## Testing

```bash
cd job_agent
uv run pytest -q
```

Test suites:
- Role classification
- Location classification
- Integration
- Run isolation
- Company radar
- Pagination
- Direct source safety

## Output Structure

```
output/
├── reports/
│   ├── career_report_<run_id>.md
│   └── comprehensive_career_report.md
├── runs/
│   └── run_<run_id>.json
├── jobs/
├── cv/
├── linkedin/
├── projects/
└── applications/
```

## Migration

Migrations are applied automatically:
- `migrations.py` - Versioned schema changes
- `user_version` tracks migration state
- No manual intervention required

## Configuration

### profile/profile.yaml

```yaml
name: Alice Example
location: Berlin, Germany
languages: [...]
education: [...]
experience: [...]
skills: [...]
values: [...]
constraints:
  # Compensation expectations are personal policy. Omit the keys to disable
  # the floor; supply both to enable it.
  minimum_salary_eur: 0
  preferred_salary_eur: 0
  willing_to_relocate: true
  family_compatibility_important: true
```

## Career Profile

The career profile is derived from `profile/profile.yaml`:
- Current role family
- Target role families
- Target seniority
- Leadership direction
- Industry domains
- Skills

## Limitations

- No LinkedIn scraping or automation
- No auto-apply
- CV requires manual verification
- LLM used sparingly for high-value reasoning
- Local model required for some features

## Future Enhancements

- Delta reporting (new jobs since last run)
- Automated LinkedIn search alerts (manual setup)
- Integration with job alert emails
- More sophisticated portfolio project recommendations
- Enhanced CV parsing from PDF

## License

Personal use only.
