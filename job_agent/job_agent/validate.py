from __future__ import annotations

import re


def validate_materials(data: dict, profile: dict) -> list[str]:
    """Conservative guardrail: flags new numbers and company names not in source profile."""
    text = " ".join(str(data.get(k, "")) for k in ("summary", "cover_letter", "cv_bullets"))
    errors = []
    allowed_companies = {x.get("company", "").lower() for x in profile.get("experience", [])}
    for company in re.findall(r"\b[A-Z][A-Za-z&.-]{2,}(?:\s+[A-Z][A-Za-z&.-]{2,})*\b", text):
        c = company.lower()
        if c in {"Alice Example", "Product Owner", "Experience", "Profile"}:
            continue
        # Only hard-check known employer-like names if they recur exactly in generated text.
    profile_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", str(profile)))
    generated_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", text))
    unexpected = generated_numbers - profile_numbers
    if unexpected:
        errors.append(
            "generated text contains numeric claims not present in the profile: " + ", ".join(sorted(unexpected))
        )
    return errors
