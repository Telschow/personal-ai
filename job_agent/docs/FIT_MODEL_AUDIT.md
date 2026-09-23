# FIT MODEL AUDIT

## Overview

The job discovery and evaluation agent employs a two-tier scoring architecture:
1. **Deterministic Rule-Based Scoring (`scoring.py`)**: Computes an authoritative 0–100 score (`evaluations.total`) based on weighted components (similarity, AI relevance, compensation, location, leadership, purpose, work-life balance) and handles hard exclusions/rejections.
2. **Advanced Career Fit Intelligence (`career/fit.py`)**: Computes granular multi-dimensional scores (`current_fit`, `career_upside`, `evidence_coverage`) mapped against verified personal evidence and profile metadata.

---

## Current Dimensions & Weights

### Deterministic Scoring (`ScoringPolicy`)

| Dimension | Weight | Range | Source | Type | Effect on Final Score |
| :--- | :---: | :---: | :--- | :--- | :--- |
| **Similarity** (Role + Skill) | 0.25 | 0.0 – 1.0 | Job Title & Description vs. Target Roles & Profile Skills | Deterministic | Primary alignment driver; penalizes mismatched titles. |
| **AI Relevance** | 0.20 | 0.3 – 1.0 | Job Title & Description keyword matching | Deterministic | Rewards AI/autonomous systems/robotics focus. |
| **Leadership** | 0.20 | 0.3 – 1.0 | Title & Description management indicators | Deterministic | Rewards Product Owner / Lead / Manager scope. |
| **Compensation** | 0.15 | 0.0 – 1.0 | Published salary range (normalized to EUR) | Deterministic | Hard rejects if below floor (€120k); rewards if ≥ target (€150k). |
| **Location** | 0.10 | 0.0 – 1.0 | Job Location vs. Munich preference & remote modes | Deterministic | Strongest weight for Munich/Germany/Remote Europe. |
| **Purpose / Industry** | 0.05 | 0.45 – 1.0 | Preferred industries (AI, Robotics, DeepTech, Mobility, etc.) | Deterministic | Minor boost for mission alignment. |
| **Work-Life Balance (WLB)** | 0.05 | 0.55 – 0.85 | Flexibility/Remote mentions | Deterministic | Minor boost for hybrid/flexible terms. |

*Note: Hard exclusions (negative keywords or compensation below floor) clamp total score to ≤ 30.0 and trigger a `reject` decision.*

---

### Granular Career Fit Intelligence (`FitWeights`)

| Dimension | Weight | Range | Source | Type | Effect on Assessment |
| :--- | :---: | :---: | :--- | :--- | :--- |
| **Role Family** | 0.22 | 0.0 – 1.0 | Structured Role Family vs. Target Role Families | Deterministic | Core capability match. |
| **AI Relevance** | 0.18 | 0.0 – 1.0 | AI exposure vs. Career Profile | Deterministic | Domain depth alignment. |
| **Leadership** | 0.18 | 0.0 – 1.0 | Leadership scope vs. Profile goals | Deterministic | Leadership responsibility match. |
| **Technical Depth** | 0.14 | 0.0 – 1.0 | Required technical depth vs. Profile | Deterministic | Engineering capability match. |
| **Product Scope** | 0.10 | 0.0 – 1.0 | Product scope requirements vs. Profile | Deterministic | Product responsibility match. |
| **Seniority** | 0.08 | 0.0 – 1.0 | Seniority level vs. Target Seniority | Deterministic | Career tier alignment. |
| **Domain** | 0.10 | 0.0 – 1.0 | Industry domain vs. Profile domains | Deterministic | Sector alignment. |

---

## Identified Audit Findings

1. **Overlapping AI weighting**: AI relevance is factored into both deterministic scoring and career fit weights, which can slightly double-count AI-heavy roles (though appropriate given career goals).
2. **Compensation Unavailability**: Many European and remote job postings do not publish salary ranges. The current model assigns a neutral score (0.55) rather than rejecting them, which correctly preserves search recall but creates uncertainty.
3. **Location Flexibility**: The model heavily favors Munich and DACH, but remote Europe roles receive appropriate partial weighting via location tiers.
4. **Role Family Expansion**: Transferable skill logic successfully connects automotive systems, ADAS, and autonomous driving to AI product management and technical product management via adjacency tables.
