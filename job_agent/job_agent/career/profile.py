"""Career profile: structured, provenance-typed view of the user's trajectory.

``derive_career_profile`` is deterministic and conservative: explicit fields
in the profile file's ``career`` block are user-stated (verified provenance),
everything inferred from the experience list is marked ``inferred`` and is
never treated as a user-confirmed claim.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

ROLE_FAMILIES: tuple[str, ...] = (
    "product_management",
    "product_leadership",
    "technical_leadership",
    "program_leadership",
    "engineering",
    "ml_engineering",
    "consulting",
    "research",
)

# Adjacent families share skill space; used for "adjacent" match scoring.
_ADJACENT: dict[str, frozenset[str]] = {
    "product_management": frozenset({"product_leadership", "program_leadership", "technical_leadership"}),
    "product_leadership": frozenset({"product_management", "program_leadership", "technical_leadership"}),
    "technical_leadership": frozenset({"engineering", "ml_engineering", "program_leadership", "product_management"}),
    "program_leadership": frozenset({"product_management", "product_leadership", "technical_leadership"}),
    "engineering": frozenset({"ml_engineering", "technical_leadership"}),
    "ml_engineering": frozenset({"engineering", "technical_leadership"}),
    "consulting": frozenset({"product_management", "program_leadership"}),
    "research": frozenset({"ml_engineering", "engineering"}),
}

TRACKS: tuple[str, ...] = ("product-led", "technical-led", "program-led")

DEFAULT_INDUSTRY_DOMAINS: tuple[str, ...] = (
    "automotive",
    "mobility",
    "autonomous driving",
    "industrial",
)


class CareerProfile(BaseModel):
    """Structured career profile with per-field provenance.

    ``inferred_fields`` records which fields were auto-derived rather than
    user-stated; those fields never back a VERIFIED claim.
    """

    name: str = ""
    current_role_family: str = "product_management"
    target_role_families: list[str] = Field(default_factory=lambda: ["product_management", "product_leadership"])
    target_track: str = "product-led"
    target_seniority: int = 4
    leadership_direction: str = "product"
    technical_depth: int = 6
    product_depth: int = 7
    systems_depth: int = 7
    ai_exposure: int = 8
    agentic_ai_exposure: int = 5
    program_scope: int = 6
    organizational_scope: int = 4
    industry_domains: list[str] = Field(default_factory=lambda: list(DEFAULT_INDUSTRY_DOMAINS))
    languages: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    willingness_to_relocate: bool = True
    family_compatibility_important: bool = True
    inferred_fields: frozenset[str] = Field(default_factory=frozenset)

    @field_validator("target_role_families", "industry_domains", mode="after")
    @classmethod
    def _drop_blank(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(v for v in value if v))


def _infer_role_family(title: str) -> str:
    t = title.casefold()
    if any(k in t for k in ("head of", "director of", "vp of", "chief")):
        if any(k in t for k in ("engineering", "technology", "cto", "tech")):
            return "technical_leadership"
        if any(k in t for k in ("product", "digital")):
            return "product_leadership"
        return "technical_leadership"
    if any(k in t for k in ("product owner", "product manager", "product lead")):
        return "product_management"
    if any(k in t for k in ("engineering manager", "tech lead", "technical lead")):
        return "technical_leadership"
    if "program manager" in t or "program director" in t:
        return "program_leadership"
    if any(k in t for k in ("machine learning", "ml engineer", "data scientist")):
        return "ml_engineering"
    if any(k in t for k in ("software", "system engineer", "engineer")):
        return "engineering"
    return "product_management"


def _infer_leadership_direction(title: str) -> str:
    t = title.casefold()
    if any(k in t for k in ("engineering", "software", "tech", "cto")):
        return "technical"
    if any(k in t for k in ("program", "project", "coordination")):
        return "program"
    return "product"


def _latest_title(profile_yaml: dict[str, Any]) -> str:
    experiences = profile_yaml.get("experience", []) or []
    if not experiences:
        return ""
    return str(experiences[0].get("title", "")).strip()


class _Infer:
    """Collects which career fields were derived rather than user-stated."""

    def __init__(self) -> None:
        self.fields: set[str] = set()


def _explicit_or(
    explicit: dict[str, Any],
    inferred: _Infer,
    key: str,
    default: Any,
    derive: Any | None = None,
) -> Any:
    if key in explicit:
        return explicit[key]
    inferred.fields.add(key)
    return default if derive is None else derive


def derive_career_profile(profile_yaml: dict[str, Any]) -> CareerProfile:
    """Deterministically build the :class:`CareerProfile` from ``profile.yaml``.

    Explicit ``career`` block fields (verified) win over derived defaults;
    inferred fields are recorded in ``inferred_fields`` and never promoted to
    VERIFIED claims.
    """
    explicit: dict[str, Any] = dict(profile_yaml.get("career", {}) or {})
    inferred = _Infer()

    name = str(profile_yaml.get("name", "")).strip() or str(explicit.get("name", ""))
    languages = [str(x) for x in (profile_yaml.get("languages") or [])]
    education = [
        f"{e.get('degree', '')} @ {e.get('school', '')}".strip()
        for e in (profile_yaml.get("education") or [])
        if isinstance(e, dict)
    ]

    current_title = _latest_title(profile_yaml)
    current_family = _infer_role_family(current_title)

    target_families = _explicit_or(
        explicit,
        inferred,
        "target_role_families",
        [current_family, "product_leadership"],
        derive=[current_family, "product_leadership"],
    )
    if isinstance(target_families, str):
        target_families = [target_families]
    target_families = list(dict.fromkeys(target_families))
    if current_family not in target_families:
        target_families.insert(0, current_family)

    constraints = profile_yaml.get("constraints", {}) or {}
    career = CareerProfile(
        name=name,
        languages=languages,
        education=education,
        current_role_family=_explicit_or(explicit, inferred, "current_role_family", current_family),
        target_role_families=target_families,
        target_track=_explicit_or(explicit, inferred, "target_track", "product-led"),
        target_seniority=int(_explicit_or(explicit, inferred, "target_seniority", 4)),
        leadership_direction=_explicit_or(
            explicit,
            inferred,
            "leadership_direction",
            _infer_leadership_direction(current_title),
        ),
        technical_depth=int(_explicit_or(explicit, inferred, "technical_depth", 6)),
        product_depth=int(_explicit_or(explicit, inferred, "product_depth", 7)),
        systems_depth=int(_explicit_or(explicit, inferred, "systems_depth", 7)),
        ai_exposure=int(_explicit_or(explicit, inferred, "ai_exposure", 8)),
        agentic_ai_exposure=int(_explicit_or(explicit, inferred, "agentic_ai_exposure", 5)),
        program_scope=int(_explicit_or(explicit, inferred, "program_scope", 6)),
        organizational_scope=int(_explicit_or(explicit, inferred, "organizational_scope", 4)),
        industry_domains=list(_explicit_or(explicit, inferred, "industry_domains", list(DEFAULT_INDUSTRY_DOMAINS))),
        willingness_to_relocate=bool(
            _explicit_or(
                explicit,
                inferred,
                "willingness_to_relocate",
                bool(constraints.get("willing_to_relocate", True)),
            )
        ),
        family_compatibility_important=bool(
            _explicit_or(
                explicit,
                inferred,
                "family_compatibility_important",
                bool(constraints.get("family_compatibility_important", True)),
            )
        ),
        inferred_fields=frozenset(inferred.fields),
    )
    return career
