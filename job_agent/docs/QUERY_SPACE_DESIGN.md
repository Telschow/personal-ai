# QUERY SPACE DESIGN FOR LARGE-SCALE CRAWL

## Query Matrix Framework

To achieve >1,000 unique persisted postings with high career fit concentration, we will construct a systematic query matrix combining:

ROLE FAMILY × DOMAIN × SENIORITY × GEOGRAPHY × SPECIALIZATION

### Core Role Families (12 families)

1. Technical Product Manager / Product Owner
2. Technical Program Manager / Program Manager  
3. AI Product Manager / AI Product Lead
4. Autonomous Systems Product Manager
5. Robotics Product Manager
6. DeepTech Product Manager
7. Industrial AI Product Manager
8. Defense/Aerospace Product Manager
9. Navigation/Localization Product Manager
10. Technical Leadership (Director/Head/VP)
11. Systems Integration Lead
12. Innovation & Technology Strategy

### Core Domains (10 domains)

1. AI / Machine Learning
2. Autonomous Driving / ADAS
3. Robotics / Autonomy
4. Deep Tech / Quantum Computing
5. Industrial AI / Manufacturing AI
6. Aerospace / Defense Technology
7. Mobility / EV / Transportation
8. Navigation / Localization / GNSS
9. Edge AI / Embedded Systems
10. Dual Use / Civilian-Military Tech

### Seniority Levels (5 tiers)

1. Individual Contributor (IC) / Specialist
2. Senior / Lead
3. Staff / Principal
4. Manager / Director
5. Head / VP / Executive

### Geographic Focus (4 tiers)

1. Configured preferred city (highest priority; **absent unless the operator
   sets one**, in which case discovery starts at the national tier)
2. Country (from config or source locality)
3. Regional remote scope (timezone compatible)
4. International (relocation-friendly roles)

### Specialization Areas (8 areas)

1. Product Strategy & Roadmapping
2. Cross-functional Leadership
3. Requirements Engineering
4. Stakeholder Management
5. Go-to-Market / Commercialization
6. Systems Architecture
7. Safety & Validation (ISO 26262, SOTIF)
8. Data & Analytics (for AI systems)

## Query Generation Strategy

### Primary Approach: Targeted Combinations

Generate queries from meaningful combinations rather than Cartesian product explosion:

**Example Query Patterns:**
- `"Technical Product Manager" "Autonomous Driving" <preferred city>`
- `"AI Product Lead" "Machine Learning" <remote scope>`
- `"Program Manager" "Robotics" Germany`
- `"Head of AI" "Computer Vision" <configured sector>`

### Geographic Scoping Rules

1. **Regional sources**: query with the configured preferred city + country only
2. **Global sources**: full rotation (preferred city → country → remote scope → international)
3. **Remote-first sources**: prioritize remote-scope and international terms
4. **No preferred city configured**: the city tier is dropped from the rotation
   entirely rather than being filled with a default

### Source-Specific Term Limits

1. **ATS platforms** (Greenhouse/Lever/Ashby): 2-3 search terms max (platform handles filtering)
2. **Search engines**: 4-5 terms for precision
3. **Specialized boards**: 1-2 domain terms + role

## Estimated Query Budget Allocation

Based on current config (max_queries_total=120, max_per_track=24, max_per_source=6, max_sources_per_track=8):

### Track Distribution (12 active tracks × avg 8 sources × 3 rounds = 288 queries → capped at 120)

Actual distribution after applying caps:
- High-priority tracks (Product Management, Program Management, Autonomous Driving, AI/ML): 35% of budget (~42 queries)
- Medium tracks (Robotics, DeepTech, Industrial AI, Defense/Aero): 30% (~36 queries)
- Lower tracks (Navigation, Technical Leadership, Systems Integration, Innovation): 20% (~24 queries)
- Geographic expansion buffer: 15% (~18 queries)

### Query Yield Optimization

Focus on high-yield combinations:
1. **Role + Domain + Munich** (highest signal)
2. **Role + Domain + Remote Europe** (broad reach)
3. **Role + Seniority + Domain** (experience level targeting)
4. **Role + Specialization** (capability discovery)

## Deduplication Strategy

1. **Primary**: Canonical URL matching
2. **Secondary**: Provider-specific job ID (RemoteOK/Remotive)
3. **Fallback**: Normalized company + title + location hash

## Geographic Query Terms Rotation

For each source, location terms rotate in priority order:
1. Munich
2. München  
3. Germany
4. Deutschland
5. Remote Europe
6. Berlin/Hamburg/etc (for Germany-focused sources)

## Implementation Notes

1. Query plan uses round-robin rotation across (track, source) pairs
2. Each round assigns one location term per track/source combination
3. German sources are locality-scoped to Munich/Germany terms only
4. Munich appears first in location rotation for maximum relevance
5. Budget caps prevent any single source/track from dominating