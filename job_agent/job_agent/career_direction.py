from enum import StrEnum


class CareerDirection(StrEnum):
    DIRECT_MATCH = "direct_match"
    ADJACENT_MATCH = "adjacent_match"
    STRETCH_MATCH = "stretch_match"
    CAREER_PIVOT = "career_pivot"
    UNKNOWN = "unknown"
