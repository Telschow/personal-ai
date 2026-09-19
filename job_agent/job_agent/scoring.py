"""Deterministic rule-based fit scoring.

This is the authoritative signal for hard rejections and the numeric ranking.
The LLM (later slices) may *explain* fit, but may never decide it. All inputs
are derived from typed config and profile; the output includes a breakdown so
historical decisions remain explainable after algorithm changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz.fuzz import token_set_ratio

from .location import LocationWeights, location_fit
from .models import Job, Score
from .salary import SalaryInfo, salary_exceeds_floor, salary_reaches_target

AI_WEIGHT_TERMS: dict[str, float] = {
    "autonomous driving": 1.0,
    "autonomous": 0.9,
    "ai ": 0.8,
    "artificial intelligence": 0.8,
    "machine learning": 0.9,
    "deep learning": 0.9,
    "llm": 0.9,
    "agentic": 1.0,
    "robotics": 0.7,
    "computer vision": 0.8,
}


def normalize_text(t: str) -> str:
    return re.sub(r"\s{2,}", " ", (t or "").casefold())


def _tokens(t: str) -> list[str]:
    """Split normalized text into tokens on non-alphanumeric runs.

    ``international`` stays a single token, so a negative keyword like
    ``intern`` never matches inside it.
    """
    return re.split(r"[^a-z0-9]+", normalize_text(t))


def contains_negative_keyword(text: str, keyword: str) -> bool:
    """Whole-token, contiguous presence test for a negative keyword.

    Matches the exact token sequence (``intern`` == ``[intern]``) rather
    than a raw substring, so ``international product manager`` is not a
    hard exclusion even though it contains the letters ``intern``.
    """
    tokens = _tokens(text)
    keyword_tokens = _tokens(keyword)
    if not keyword_tokens or any(not tok for tok in keyword_tokens):
        return False
    width = len(keyword_tokens)
    return any(tokens[i : i + width] == keyword_tokens for i in range(len(tokens) - width + 1))


def title_like(job: Job, roles: list[str]) -> float:
    if not job.title:
        return 0.0
    title = normalize_text(job.title)
    sims = [token_set_ratio(title, r) for r in roles]
    return max(sims or [0]) / 100


def skill_similarity(job: Job, skills: list[str]) -> float:
    text = normalize_text(job.title + " " + job.description)
    hits = sum(1 for s in skills if normalize_text(s) in text)
    return min(1.0, hits / max(1, len(skills)) * 3)


def ai_relevance(job: Job) -> float:
    text = normalize_text(job.title + " " + job.description + " " + job.location)
    score = 0.0
    for term, w in AI_WEIGHT_TERMS.items():
        if term.casefold() in text:
            score = max(score, w)
    # Title-level AI indication not covered by the phrase table.
    if "ai" in normalize_text(job.title) and score < 0.5:
        score = max(score, 0.5)
    return score if score else 0.3  # baseline: everything carries some AI flavor in 2020s


def leadership_score(job: Job) -> float:
    text = normalize_text(job.title + " " + job.description)
    if any(
        k in text
        for k in (
            "lead ",
            "leadership",
            "team lead",
            "group product",
            "head of",
            "director",
            "vp of",
            "chief",
            "product owner",
            "program manager",
            "staff ",
            "principal",
        )
    ):
        return 1.0
    if any(k in text for k in ("manager", "management", "leadership")):
        return 0.8
    return 0.3


def purpose_score(job: Job, industries_preferred: list[str]) -> float:
    text = normalize_text(job.title + " " + job.description + " " + job.location)
    purpose_terms = [
        "ai",
        "artificial intelligence",
        "robotics",
        "deep tech",
        "autonomous",
        "innovation",
        "energy",
        "aerospace",
        "defence",
        "defense",
        "climate",
        "medical technology",
        "electric",
        "quantum",
        "space",
    ]
    if any(k in text for k in purpose_terms):
        return 1.0
    for industry in industries_preferred:
        if industry.casefold() in text:
            return 1.0
    return 0.45


def wlb_score(job: Job) -> float:
    text = normalize_text(job.title + " " + job.description)
    if any(
        k in text
        for k in (
            "work-life",
            "work life",
            "flexible",
            "flexibility",
            "family friendly",
            "parental",
            "remote",
            "hybrid",
        )
    ):
        return 0.85
    return 0.55


@dataclass
class ScoringPolicy:
    """Frozen snapshot of the scoring configuration (for version pinning)."""

    salary_min: float
    salary_target: float
    similarity_weight: float
    ai_weight: float
    compensation_weight: float
    location_weight: float
    leadership_weight: float
    purpose_weight: float
    wlb_weight: float
    industries: tuple[str, ...]
    negative_keywords: tuple[str, ...]
    target_roles: tuple[str, ...]
    location_weights: LocationWeights = field(default_factory=LocationWeights)

    @property
    def version(self) -> str:
        import hashlib

        blob = "|".join(
            [
                f"{getattr(self, k)}"
                for k in (
                    "salary_min",
                    "salary_target",
                    "similarity_weight",
                    "ai_weight",
                    "compensation_weight",
                    "location_weight",
                    "leadership_weight",
                    "purpose_weight",
                    "wlb_weight",
                )
            ]
        )
        blob += "|loc=" + "|".join(f"{k}:{v}" for k, v in self.location_weights.model_dump().items())
        blob += "|" + "|".join(sorted(self.industries))
        blob += "|" + "|".join(sorted(self.negative_keywords))
        blob += "|" + "|".join(sorted(self.target_roles))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def scoring_policy_from_config(policy: dict) -> ScoringPolicy:
    """Build a ScoringPolicy from the nested config mapping (``jobs.salary``,
    ``ranking``, ``search``, ``career.location.weights``)."""
    salary = policy.get("jobs", {}).get("salary", {})
    ranking = policy.get("ranking", {})
    search = policy.get("search", {})
    career = policy.get("career", {})
    location_cfg = career.get("location", {})
    industries = policy.get("industries_preferred") or [
        "AI",
        "DeepTech",
        "Robotics",
        "Mobility",
        "Industrial Technology",
        "Aerospace",
        "Defence",
        "Energy",
    ]
    location_weights = LocationWeights.model_validate(location_cfg.get("weights", {}) or {})
    return ScoringPolicy(
        salary_min=float(salary.get("minimum_eur", 120_000)),
        salary_target=float(salary.get("target_eur", 150_000)),
        similarity_weight=float(ranking.get("similarity_weight", 0.25)),
        ai_weight=float(ranking.get("ai_relevance_weight", 0.20)),
        compensation_weight=float(ranking.get("compensation_weight", 0.15)),
        location_weight=float(ranking.get("location_weight", 0.10)),
        leadership_weight=float(ranking.get("leadership_weight", 0.20)),
        purpose_weight=float(ranking.get("purpose_weight", 0.05)),
        wlb_weight=float(ranking.get("wlb_weight", 0.05)),
        industries=tuple(x for x in industries),
        negative_keywords=tuple(search.get("keywords_negative", [])),
        target_roles=tuple(search.get("target_roles", [])),
        location_weights=location_weights,
    )


def score(job: Job, profile: dict, policy: ScoringPolicy) -> Score:
    """Compute the deterministic fit score for a job.

    ``profile`` supplies skills and values (e.g. leadership importance);
    ``policy`` is a frozen :class:`ScoringPolicy`. Decision thresholds:
    reject < 50 (or hard fail), strong >= 75, else review.
    """
    text = normalize_text(job.title + " " + job.description[:2000] + " " + job.location + " " + job.company)
    pos = policy.negative_keywords
    hard: list[str] = []
    reasons: list[str] = []
    gaps: list[str] = []

    for neg in pos:
        if contains_negative_keyword(text, neg):
            hard.append(f"negative keyword: {neg}")

    role_sim = title_like(job, list(policy.target_roles))
    skill_sim = skill_similarity(job, profile.get("skills", []))
    similarity = 0.65 * role_sim + 0.35 * skill_sim
    ai = ai_relevance(job)

    willing = bool(profile.get("willing_to_relocate", True))
    loc_score, loc_parse = location_fit(
        job.location,
        policy.location_weights,
        remote_mode=job.remote_mode,
        willing_to_relocate=willing,
    )

    salary = SalaryInfo(
        min_eur=job.salary_min_eur,
        max_eur=job.salary_max_eur,
        currency=job.salary_currency,
        converted=job.salary_converted,
    )
    comp_score, comp_uncertain = _salary_score(
        salary,
        policy.salary_min,
        policy.salary_target,
    )
    if salary.min_eur is None and salary.max_eur is None:
        gaps.append(f"no published compensation (target €{policy.salary_target:,.0f}+)")
    elif comp_score == 0.0:
        hard.append(f"compensation below floor €{policy.salary_min:,.0f}")
    elif comp_uncertain:
        gaps.append("compensation needs FX conversion — treat as approximate")
    if job.salary_converted:
        gaps.append("compensation converted from foreign currency")

    lead = leadership_score(job)
    purpose = purpose_score(job, list(policy.industries))
    wlb = wlb_score(job)

    total = 100 * (
        policy.similarity_weight * similarity
        + policy.ai_weight * ai
        + policy.compensation_weight * comp_score
        + policy.location_weight * loc_score
        + policy.leadership_weight * lead
        + policy.purpose_weight * purpose
        + policy.wlb_weight * wlb
    )
    total = min(100.0, max(0.0, total))
    if hard:
        total = min(total, 30.0)

    if profile.get("values", {}).get("leadership") and profile["values"]["leadership"] >= 8:
        leadership_needed = True
    else:
        leadership_needed = bool(profile.get("leadership_required", True))
    if leadership_needed and lead < 0.5:
        gaps.append("leadership responsibility not evident")

    if similarity >= 0.65:
        reasons.append("strong role/profile alignment")
    if ai >= 0.7:
        reasons.append("strong AI relevance")
    elif ai >= 0.5:
        reasons.append("AI-relevant scope")
    if lead >= 0.8:
        reasons.append("leadership responsibility appears relevant")
    if salary_reaches_target(salary, policy.salary_target):
        reasons.append(f"compensation ≥ target €{policy.salary_target:,.0f}")
    elif salary_exceeds_floor(salary, policy.salary_min):
        reasons.append(f"compensation ≥ floor €{policy.salary_min:,.0f}")
    if loc_parse.region == "international":
        gaps.append("requires relocation/remote flexibility")

    hard_reason = "; ".join(hard)
    if hard_reason:
        reasons.append(f"hard exclusion: {hard_reason}")

    decision: str
    if hard or total < 50:
        decision = "reject"
    elif total >= 75:
        decision = "strong"
    else:
        decision = "review"

    confidence = _confidence(total, hard, salary)
    breakdown = {
        "similarity": round(similarity, 3),
        "ai_relevance": round(ai, 3),
        "compensation": round(comp_score, 3),
        "location": round(loc_score, 3),
        "location_tier": loc_parse.tier.value,
        "leadership": round(lead, 3),
        "purpose": round(purpose, 3),
        "wlb": round(wlb, 3),
    }

    return Score(
        total=round(total, 1),
        decision=decision,  # type: ignore[arg-type]
        reasons=reasons,
        gaps=gaps,
        hard_fail=bool(hard),
        confidence=confidence,
        breakdown=breakdown,
    )


def _salary_score(salary: SalaryInfo, minimum: float, target: float) -> tuple[float, bool]:
    """Score published compensation.

    Returns (score, uncertain) where uncertain means a conversion was applied
    or the value came from a range with possible non-EUR meaning. Unknown
    salary is reviewable (0.55) — never auto-rejected.
    """
    if salary.min_eur is None and salary.max_eur is None:
        return 0.55, False
    # Use the *lower bound* if present else max.
    value = salary.min_eur if salary.min_eur is not None else salary.max_eur
    assert value is not None
    if value < minimum:
        return 0.0, True
    if value >= target:
        return 1.0, True
    # Linearly scale between floor and target.
    scaled = (value - minimum) / (target - minimum)
    return max(0.0, min(1.0, scaled)), True


def _confidence(total: float, hard: list[str], salary: SalaryInfo) -> float:
    if hard:
        return 1.0  # a hard rejection is certain
    # Confidence rises with evidence: known compensation + published details.
    c = 0.4 + 0.4 * (total / 100)
    if salary.min_eur is not None:
        c += 0.1
    if not salary.converted:
        c += 0.1
    return round(min(1.0, c), 3)
