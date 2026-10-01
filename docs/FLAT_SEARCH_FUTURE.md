# Flat Search — Future Architecture

Reference: https://github.com/lukasthekid/flatscraper

## Flatscraper Ideas Worth Borrowing

| Idea | Flatscraper Implementation | Our Adaptation |
|------|---------------------------|----------------|
| Provider-based discovery | Multiple real estate APIs + search fallback | Same pattern: native APIs (ImmobilienScout24, ImmoWelt, WG-Gesucht) + search fallback |
| Structured normalization | Parse listings into typed models | Same: Flat model with canonical_key, location normalization, price normalization |
| Deduplication | Content-hash + URL-based dedup | Same: content-hash identity + canonical_url |
| Scoring/filtering | User preferences → ranked results | Same: deterministic scoring policy + user preferences |
| Notification/alerting | Telegram/email alerts for new matches | Future: local notification (dashboard badge, optional webhook) |
| Human-in-the-loop | User reviews before contact | Same: SAVED/REJECTED/APPLIED status; never auto-contact |
| Application documents | Auto-generate application PDFs | Same pattern: master documents → tailored application → PDF export |
| Multi-user | Designed for couples/households | Future: shared profile for two household members |

## Architecture Mapping

| Component | Job-Agent Equivalent | Flat Search Equivalent |
|-----------|---------------------|------------------------|
| Source catalog | `sources_catalog.yaml` (42 sources) | Flat catalog (ImmobilienScout24, ImmoWelt, WG-Gesucht, eBay Kleinanzeigen, etc.) |
| Provider adapters | `providers.py` (RemoteOK, Remotive) | Flat providers (IS24 API, ImmoWelt API, WG-Gesucht RSS) |
| Normalization | `normalizer.py` (Job model) | `flat_normalizer.py` (Flat model) |
| Canonicalization | `canonical.py` (canonical_key) | Same pattern |
| Scoring | `scoring.py` + `career/fit.py` | `flat_scoring.py` (price, location, size, rooms, commute) |
| Discovery | `discovery.py` + `query_plan.py` | `flat_discovery.py` (area-based queries, price tiers) |
| Persistence | SQLite (jobs, evaluations, applications) | SQLite (flats, evaluations, applications) |
| UI | Dashboard + job list + detail | Dashboard + flat list + detail |
| Documents | CV/cover letter artifacts | Mieterselbstauskunft, Schufa, income proof, rental history |

## Required Providers (Munich)

| Provider | Type | Auth | Notes |
|----------|------|------|-------|
| ImmobilienScout24 (IS24) | API | OAuth2 / API key | Primary; structured data; rate-limited |
| ImmoWelt | API | API key | Secondary; good coverage |
| WG-Gesucht | RSS/HTML | None | WG-focused; also complete apartments |
| eBay Kleinanzeigen | HTML/search | None | High volume; no API; search fallback |
| Munich.de / Stadt München | Official | None | Social housing (Wohnberechtigungsschein) |
| Vonovia / LEG / large landlords | Direct | None | Company career pages pattern |

## Munich-Specific Constraints

- **Complete apartment only** — not WG (Wohngemeinschaft)
- **2-person household** — minimum 2 rooms, ~60m²+
- **Munich city + nearby** — MVV zones 1-3 (Munich, Dachau, Freising, Ebersberg, Starnberg)
- **Budget** — warm rent €1800-2500/month (market 2024/2025)
- **Application documents** — Mieterselbstauskunft, Schufa, last 3 salary slips, employer confirmation, rental history, optionally Mietschuldenfreiheitsbescheinigung
- **WBS (Wohnberechtigungsschein)** — check eligibility; separate track
- **Competition** — high; speed matters (apply within hours of listing)
- **Besichtigungstermine** — scheduling coordination for two people

## Documents Required

| Document | Source | Frequency |
|----------|--------|-----------|
| Mieterselbstauskunft | Template (fill once, reuse) | Per application |
| Schufa-Auskunft | Schufa (online) | Every ~3 months |
| Gehaltsnachweise (3 months) | Employer | Per application |
| Arbeitgeberbestätigung | Employer | Per application |
| Mietschuldenfreiheitsbescheinigung | Previous landlord | Per application |
| Einkommensteuerbescheid | Finanzamt | Optional |
| Personalausweis copy | User | Per application |

## Automation Boundary

```
READ (provider APIs, search)
  ↓
NORMALIZE + DEDUP + SCORE
  ↓
FILTER (user preferences: price, size, location, rooms, WBS)
  ↓
PRESENT (dashboard: new matches, saved, applied)
  ↓
HUMAN REVIEW (save / reject / apply)
  ↓
GENERATE APPLICATION DOCUMENTS (tailored Mieterselbstauskunft + attachments)
  ↓
HUMAN APPROVAL (review PDF)
  ↓
MANUAL SUBMIT (email / portal / post)
  ↓
TRACK (applied → viewing → offer → signed → moved)
```

**Never auto-submit.** Human decides which flats to apply to and sends the application.

## Future Data Model

```sql
-- Flats (like jobs)
flats:
  id, title, address, city, district, lat, lon, price_cold, price_warm,
  size_m2, rooms, floor, has_balcony, has_parking, wbs_required,
  available_from, source, source_type, canonical_url, canonical_key,
  status (active/stale/closed/duplicate), discovered_at, last_seen

-- Evaluations (like job evaluations)
flat_evaluations:
  flat_id, total_score, decision, reasons, gaps, scoring_version, evaluated_at

-- User status (like job user_status)
flat_user_status:
  flat_id, user_status (NEW/SAVED/REJECTED/APPLIED/VIEWING/OFFER/SIGNED), updated_at

-- Applications (like job applications)
flat_applications:
  flat_id, stage, notes, applied_at, viewing_at, offer_at, signed_at,
  documents_json (list of generated document paths), follow_up_at

-- Documents (like career_documents / user_artifacts)
flat_documents:
  id, flat_id, type (self_declaration/schufa/salary/employer/rental_history/id),
  status (draft/generated/approved/sent), path, generated_at, approved_at
```

## Future UI

- **Dashboard**: New matches (cards with photo, price, size, location, score), saved, applied, viewing, offers
- **Map view**: Munich map with pins color-coded by status
- **Flat detail**: Photos, facts, score breakdown, commute time (MVV), application panel
- **Document center**: Master documents → generate tailored set per flat → review → download
- **Calendar**: Besichtigungstermine with reminders
- **Notifications**: New high-score flats (toast + optional webhook)

## Privacy Considerations

- All data local (SQLite on /data volume)
- No personal documents in git / Docker image
- Schufa/salary docs never leave sandbox
- Application documents generated locally, user reviews before sending
- No external APIs for core loop (provider APIs are public listing data only)

## Not Implementing in This Slice

This is a **future project**. The current slice is Career Operating System MVP.
Flat search will be a separate phase after Career MVP is stable and in daily use.