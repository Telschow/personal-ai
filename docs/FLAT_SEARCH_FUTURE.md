# Second Vertical — Architecture Notes

This project is a personal knowledge and agent system. The job-search vertical
is the first implementation of a reusable shape: **multi-source discovery →
typed normalization → deduplication → deterministic scoring → persistence →
human-in-the-loop review → document generation**.

A second vertical (for example housing search, price tracking, or grant
applications) is the same shape with different domain models. The notes below
record that mapping. They deliberately contain no search target, budget, city,
or household policy: those are operator decisions made in local, gitignored
configuration, never in the repository.

Reference implementation consulted for the pattern:
<https://github.com/lukasthekid/flatscraper>

## What Transfers Directly

| Concern | Job vertical today | Second vertical |
|---------|--------------------|-----------------|
| Source catalog | `sources_catalog.yaml` | Catalog of the vertical's sources |
| Provider adapters | `providers.py` | One adapter per provider |
| Search fallback | `query_plan.py` | Vertical's query planner |
| Normalization | `normalizer.py` | Vertical's typed record |
| Canonicalization | `canonical.py` | Same pattern: content identity + canonical URL |
| Scoring | `scoring.py` + `career/fit.py` | Vertical's scoring policy |
| Persistence | SQLite | Same tables, vertical-specific columns |
| Review UI | Dashboard + list + detail | Same |
| Documents | CV/cover letter artifacts | Vertical's document set |
| Notifications | Dashboard badge | Same, plus optional webhook |

## What Does Not Transfer

- **The scoring dimensions.** Price/commute/size is not role/skill/seniority.
  The vertical defines its own dimensions and weights.
- **The scoring policy must be configurable, not hardcoded.** The job vertical
  originally embedded a default compensation floor and a default preferred city
  in source. That was a design error: it made every operator inherit someone
  else's policy, and it meant a fresh checkout ranked postings against personal
  preferences nobody in the repo had chosen. Every dimension and every bound now
  comes from configuration and defaults to neutral.
- **The domain vocabulary.** Provider names and sector terms are vertical
  specific and belong in config and catalogs, not in module-level constants.

## Reusable Design Rules

1. **Never auto-submit.** The agent prepares; a human sends. This holds for any
   vertical, and is enforced by validation (`auto_submit` / `auto_publish` are
   hard-blocked).
2. **All state local.** SQLite on a data volume. Nothing personal in git or in a
   container image.
3. **Sensitive documents never leave the sandbox.** Generated locally, reviewed
   before sending.
4. **Deterministic scoring.** Reproducible, explainable, versioned. An LLM may
   assist with unstructured extraction, never with the score itself.
5. **Evidence and provenance on every decision.** Any claim a document or
   ranking rests on must be traceable to a source the system actually read.
6. **Providers fail in isolation.** One bad source degrades that source, not the
   run.

## Second Vertical Provider Shape

Whatever the vertical, the adapter contract is the same:

| Field | Meaning |
|-------|---------|
| Provider kind | API / RSS / HTML / direct company page |
| Auth | None / API key / OAuth2 |
| Enabled | Operator toggle, per source |
| Priority | Rotation order |
| Rate-limit class | Governs pacing |
| Failure isolation | Per-source error capture, never fatal |

Local municipal or official sources (public housing allocations, government
benefit registers) are a normal provider kind: official, unauthenticated, and
lower volume but higher value than aggregators.

## Vertical-Neutral Data Model

```sql
-- Records, like jobs
records:
  id, title, subject, city, region, lat, lon,
  amount_min, amount_max, amount_currency, amount_period,
  size, category, attributes_json,
  source, source_type, canonical_url, canonical_key,
  status (active/stale/closed/duplicate), discovered_at, last_seen

-- Evaluations, like job evaluations
record_evaluations:
  record_id, total_score, decision, reasons, gaps,
  scoring_version, evaluated_at

-- Operator status, like job user_status
record_user_status:
  record_id, user_status (NEW/SAVED/REJECTED/APPLIED/…), updated_at

-- Applications / claims, like job applications
record_applications:
  record_id, stage, notes, applied_at, next_action_at,
  documents_json, follow_up_at

-- Documents, like career_documents / user_artifacts
record_documents:
  id, record_id, type, status (draft/generated/approved/sent),
  path, generated_at, approved_at
```

Everything a vertical needs beyond these columns goes in `attributes_json` or in
vertical-specific tables. The shared tables stay stable so that UI, evidence,
and audit code are written once.

## Vertical-Neutral Automation Boundary

```
READ (provider APIs, search)
  ↓
NORMALIZE + DEDUP + SCORE
  ↓
FILTER (configured preferences)
  ↓
PRESENT (dashboard: new matches, saved, applied)
  ↓
HUMAN REVIEW (save / reject / proceed)
  ↓
GENERATE DOCUMENTS (tailored from master documents)
  ↓
HUMAN APPROVAL (review output)
  ↓
MANUAL SUBMIT (email / portal / post)
  ↓
TRACK (submitted → pending → outcome → closed)
```

## Future UI (shared shape)

- **Dashboard**: new matches, saved, in progress, closed
- **Map view**: geographic pins color-coded by status
- **Detail view**: photos/facts, score breakdown, criteria, document panel
- **Document center**: master documents → tailored set per record → review →
  download
- **Calendar**: scheduled actions with reminders
- **Notifications**: new high-score matches (toast + optional webhook)

## Privacy Considerations

- All data local (SQLite on a data volume)
- No personal documents in git or in the Docker image
- Sensitive supporting documents never leave the sandbox
- Documents generated locally and reviewed before sending
- No external APIs in the core loop; provider APIs carry public listing data only

## Not in This Slice

A second vertical is a separate phase. The reusable shape above is what makes
it cheap later; no second-vertical code ships with the first.
