"""Location classifier."""
from __future__ import annotations

from typing import Any

from .models import Job


# Location scope categories
LOCATION_SCOPES = {
    "munich": "Munich",
    "munich_region": "Munich Region",
    "germany": "Germany",
    "remote_germany": "Remote Germany",
    "eu": "EU",
    "remote_eu": "Remote EU",
    "international": "International",
    "unknown": "Unknown",
}


# Location score weights
LOCATION_SCORE_WEIGHTS = {
    "munich": 1.0,
    "munich_region": 0.8,
    "germany": 0.6,
    "remote_germany": 0.5,
    "eu": 0.4,
    "remote_eu": 0.3,
    "international": 0.2,
    "unknown": 0.0,
}


# Location keywords
LOCATION_KEYWORDS = {
    "munich": [
        "munich",
        "münchen",
        "munich, germany",
        "münchen, germany",
        "munich, de",
        "münchen, de",
    ],
    "munich_region": [
        "munich region",
        "munich area",
        "munich metropolitan",
        "munich and surrounding",
        "munich and nearby",
    ],
    "germany": [
        "germany",
        "deutschland",
        "germany, europe",
        "deutschland, europa",
        "germany, eu",
        "deutschland, eu",
    ],
    "remote_germany": [
        "remote germany",
        "remote - germany",
        "remote germany",
        "remote - germany",
        "remote germany",
        "remote - germany",
    ],
    "eu": [
        "europe",
        "europa",
        "eu",
        "european union",
        "europäische union",
    ],
    "remote_eu": [
        "remote europe",
        "remote - europe",
        "remote eu",
        "remote - eu",
    ],
    "international": [
        "worldwide",
        "global",
        "international",
        "multiple locations",
        "multiple sites",
    ],
}


# Remote keywords
REMOTE_KEYWORDS = [
    "remote",
    "remote work",
    "work from home",
    "home office",
    "fully remote",
    "100% remote",
    "remote-first",
    "remote position",
]


# Hybrid keywords
HYBRID_KEYWORDS = [
    "hybrid",
    "hybrid / on-site",
    "hybrid/on-site",
    "mix aus",
    "hybrid remote",
    "remote hybrid",
]


# On-site keywords
ONSITE_KEYWORDS = [
    "on-site",
    "onsite",
    "on site",
    "präsenz",
    "vor ort",
    "in-office",
    "in office",
]


# Munich city keywords
MUNICH_CITY_KEYWORDS = [
    "munich",
    "münchen",
]


# Munich region keywords
MUNICH_REGION_KEYWORDS = [
    "munich region",
    "munich area",
    "munich metropolitan",
    "munich and surrounding",
    "munich and nearby",
]


# Germany keywords
GERMANY_KEYWORDS = [
    "germany",
    "deutschland",
    "germany, europe",
    "deutschland, europa",
    "germany, eu",
    "deutschland, eu",
]


# Remote Germany keywords
REMOTE_GERMANY_KEYWORDS = [
    "remote germany",
    "remote - germany",
    "remote germany",
    "remote - germany",
    "remote germany",
    "remote - germany",
]


# EU keywords
EU_KEYWORDS = [
    "europe",
    "europa",
    "eu",
    "european union",
    "europäische union",
]


# Remote EU keywords
REMOTE_EU_KEYWORDS = [
    "remote europe",
    "remote - europe",
    "remote eu",
    "remote - eu",
]


# International keywords
INTERNATIONAL_KEYWORDS = [
    "worldwide",
    "global",
    "international",
    "multiple locations",
    "multiple sites",
]


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().strip().split())


def classify_location(job: Job) -> tuple[str, str, float, str]:
    """Classify a job's location based on the location string and description.

    Returns a tuple of:
    - location city
    - location country
    - location score
    - location reason
    """
    location = _normalize_text(job.location or "")
    description = _normalize_text(job.description or "")
    combined = f"{location} {description}"

    # Initialize variables
    location_city = "Unknown"
    location_country = "Unknown"
    location_scope = "unknown"
    location_score = 0.0
    reason_parts = []

    # Check for Munich
    if any(kw in combined for kw in MUNICH_CITY_KEYWORDS):
        location_city = "Munich"
        location_country = "Germany"
        location_scope = "munich"
        location_score = LOCATION_SCORE_WEIGHTS["munich"]
        reason_parts.append("Munich city detected")

    # Check for Munich region
    elif any(kw in combined for kw in MUNICH_REGION_KEYWORDS):
        location_city = "Munich"
        location_country = "Germany"
        location_scope = "munich"
        location_score = LOCATION_SCORE_WEIGHTS["munich"]
        reason_parts.append("Munich region detected")

    # Check for Germany
    elif any(kw in combined for kw in GERMANY_KEYWORDS):
        location_country = "Germany"
        location_scope = "germany"
        location_score = LOCATION_SCORE_WEIGHTS["germany"]
        reason_parts.append("Germany detected")

    # Check for Remote Germany
    elif any(kw in combined for kw in REMOTE_GERMANY_KEYWORDS):
        location_country = "Germany"
        location_scope = "remote_germany"
        location_score = LOCATION_SCORE_WEIGHTS["remote_germany"]
        reason_parts.append("Remote Germany detected")

    # Check for EU
    elif any(kw in combined for kw in EU_KEYWORDS):
        location_scope = "eu"
        location_score = LOCATION_SCORE_WEIGHTS["eu"]
        reason_parts.append("EU detected")

    # Check for Remote EU
    elif any(kw in combined for kw in REMOTE_EU_KEYWORDS):
        location_scope = "remote_eu"
        location_score = LOCATION_SCORE_WEIGHTS["remote_eu"]
        reason_parts.append("Remote EU detected")

    # Check for International
    elif any(kw in combined for kw in INTERNATIONAL_KEYWORDS):
        location_scope = "international"
        location_score = LOCATION_SCORE_WEIGHTS["international"]
        reason_parts.append("International detected")

    # If no location found, check for remote/hybrid/onsite
    if location_scope == "unknown":
        if any(kw in combined for kw in REMOTE_KEYWORDS):
            location_scope = "remote_eu"
            location_score = LOCATION_SCORE_WEIGHTS["remote_eu"]
            reason_parts.append("Remote detected")
        elif any(kw in combined for kw in HYBRID_KEYWORDS):
            location_scope = "munich_region"
            location_score = LOCATION_SCORE_WEIGHTS["munich_region"]
            reason_parts.append("Hybrid detected")
        elif any(kw in combined for kw in ONSITE_KEYWORDS):
            location_scope = "munich"
            location_score = LOCATION_SCORE_WEIGHTS["munich"]
            reason_parts.append("On-site detected")

    # If still unknown, check for Munich in the location string
    if location_scope == "unknown" and any(kw in location for kw in MUNICH_CITY_KEYWORDS):
        location_city = "Munich"
        location_country = "Germany"
        location_scope = "munich"
        location_score = LOCATION_SCORE_WEIGHTS["munich"]
        reason_parts.append("Munich detected in location string")

    # If still unknown, check for Munich region in the location string
    elif location_scope == "unknown" and any(kw in location for kw in MUNICH_REGION_KEYWORDS):
        location_city = "Munich"
        location_country = "Germany"
        location_scope = "munich_region"
        location_score = LOCATION_SCORE_WEIGHTS["munich_region"]
        reason_parts.append("Munich region detected in location string")

    # If still unknown, check for Germany in the location string
    elif location_scope == "unknown" and any(kw in location for kw in GERMANY_KEYWORDS):
        location_country = "Germany"
        location_scope = "germany"
        location_score = LOCATION_SCORE_WEIGHTS["germany"]
        reason_parts.append("Germany detected in location string")

    # If still unknown, check for Remote Germany in the location string
    elif location_scope == "unknown" and any(kw in location for kw in REMOTE_GERMANY_KEYWORDS):
        location_country = "Germany"
        location_scope = "remote_germany"
        location_score = LOCATION_SCORE_WEIGHTS["remote_germany"]
        reason_parts.append("Remote Germany detected in location string")

    # If still unknown, check for EU in the location string
    elif location_scope == "unknown" and any(kw in location for kw in EU_KEYWORDS):
        location_scope = "eu"
        location_score = LOCATION_SCORE_WEIGHTS["eu"]
        reason_parts.append("EU detected in location string")

    # If still unknown, check for Remote EU in the location string
    elif location_scope == "unknown" and any(kw in location for kw in REMOTE_EU_KEYWORDS):
        location_scope = "remote_eu"
        location_score = LOCATION_SCORE_WEIGHTS["remote_eu"]
        reason_parts.append("Remote EU detected in location string")

    # If still unknown, check for International in the location string
    elif location_scope == "unknown" and any(kw in location for kw in INTERNATIONAL_KEYWORDS):
        location_scope = "international"
        location_score = LOCATION_SCORE_WEIGHTS["international"]
        reason_parts.append("International detected in location string")

    # If still unknown, set to unknown
    if location_scope == "unknown":
        reason_parts.append("No location information found")

    return location_city, location_country, location_scope, location_score, "; ".join(reason_parts)
