"""Role archetype classifier."""
from __future__ import annotations

from typing import Any

from .models import Job, RoleArchetype
from .role_archetypes import RoleArchetype
from .career_direction import CareerDirection


# Keywords for each archetype
ARCHETYPE_KEYWORDS = {
    RoleArchetype.TECHNICAL_PRODUCT: [
        "technical product manager",
        "product manager",
        "product owner",
        "product lead",
        "group product manager",
        "principal product manager",
        "product engineer",
        "technical pm",
        "tpms",
    ],
    RoleArchetype.TECHNICAL_PRODUCT: [
        "head of product",
        "director of product",
        "vp product",
        "chief product officer",
        "product director",
        "product leadership",
        "lead product",
        "product lead",
    ],
    RoleArchetype.TECHNICAL_PROGRAM: [
        "technical program manager",
        "program manager",
        "staff technical program manager",
        "senior program manager",
        "program lead",
        "technical program lead",
        "tpm",
    ],
    RoleArchetype.SYSTEMS_INTEGRATION: [
        "systems integration",
        "integration engineer",
        "systems integrator",
        "integration lead",
        "system integration",
        "platform integration",
        "technical integration",
    ],
    RoleArchetype.PLATFORM_PRODUCT: [
        "platform product manager",
        "platform product",
        "platform engineer",
        "platform team",
        "internal platform",
        "developer platform",
        "platform lead",
    ],
    RoleArchetype.AI_PRODUCT: [
        "ai product manager",
        "ml product manager",
        "machine learning product",
        "ai product lead",
        "genai product",
        "llm product",
        "artificial intelligence product",
    ],
    RoleArchetype.AUTONOMOUS_SYSTEMS: [
        "autonomous systems",
        "autonomous driving",
        "adas",
        "autonomy engineer",
        "self driving",
        "autonomous vehicle",
        "autonomous systems engineer",
    ],
    RoleArchetype.ROBOTICS: [
        "robotics engineer",
        "robotics software",
        "robotics product",
        "ros engineer",
        "motion planning",
        "manipulation",
        "slam",
        "humanoid",
    ],
    RoleArchetype.DEEPTECH: [
        "deeptech",
        "deep tech",
        "quantum",
        "photonics",
        "semiconductor",
        "fusion",
        "advanced materials",
        "quantum computing",
    ],
    RoleArchetype.DEFENCE_AEROSPACE: [
        "defence",
        "defense",
        "aerospace",
        "space systems",
        "satellite",
        "avionics",
        "military systems",
        "mission systems",
        "unmanned systems",
    ],
    RoleArchetype.MOBILITY: [
        "automotive",
        "mobility",
        "electric vehicle",
        "ev ",
        "charging",
        "vehicle platform",
        "oem",
        "connected car",
    ],
    RoleArchetype.TECHNOLOGY_STRATEGY: [
        "technology strategy",
        "tech strategy",
        "technical strategy",
        "cto office",
        "chief technology",
        "technology strategist",
    ],
    RoleArchetype.PRODUCT_STRATEGY: [
        "product strategy",
        "product strategist",
        "product vision",
        "roadmap",
        "go-to-market",
        "gtm",
    ],
    RoleArchetype.ENGINEERING_PROGRAM: [
        "engineering manager",
        "lead engineer",
        "technical lead",
        "staff engineer",
        "principal engineer",
        "engineering lead",
    ],
    RoleArchetype.TECHNICAL_PARTNERSHIPS: [
        "technical partnerships",
        "strategic partnerships",
        "partner engineering",
        "partner manager",
        "alliances",
        "ecosystem",
    ],
    RoleArchetype.SOLUTIONS_ARCHITECTURE: [
        "solutions architect",
        "solution architect",
        "solutions architecture",
        "sales engineer",
        "pre-sales",
        "customer engineering",
    ],
    RoleArchetype.CUSTOMER_ENGINEERING: [
        "customer engineer",
        "customer engineering",
        "field engineer",
        "deployment engineer",
        "implementation engineer",
        "customer success engineer",
    ],
    RoleArchetype.INNOVATION: [
        "innovation manager",
        "innovation lead",
        "digital transformation",
        "transformation manager",
        "venture",
        "r&d strategy",
    ],
}


# Track/category affinities for additional evidence
TRACK_ARCHETYPE_MAP = {
    "product_management": [
        RoleArchetype.TECHNICAL_PRODUCT,
        RoleArchetype.PRODUCT_LEADERSHIP,
        RoleArchetype.PRODUCT_STRATEGY,
        RoleArchetype.PLATFORM_PRODUCT,
    ],
    "program_management": [
        RoleArchetype.TECHNICAL_PROGRAM,
        RoleArchetype.ENGINEERING_PROGRAM,
    ],
    "engineering_leadership": [
        RoleArchetype.ENGINEERING_PROGRAM,
        RoleArchetype.SYSTEMS_LEADERSHIP,
    ],
    "autonomous_driving": [
        RoleArchetype.AUTONOMOUS_SYSTEMS,
        RoleArchetype.SYSTEMS_INTEGRATION,
    ],
    "ai_ml": [
        RoleArchetype.AI_PRODUCT,
        RoleArchetype.TECHNICAL_PRODUCT,
    ],
    "robotics": [
        RoleArchetype.ROBOTICS,
        RoleArchetype.SYSTEMS_INTEGRATION,
    ],
    "deeptech": [
        RoleArchetype.DEEPTECH,
        RoleArchetype.TECHNOLOGY_STRATEGY,
    ],
    "defence_aerospace": [
        RoleArchetype.DEFENCE_AEROSPACE,
        RoleArchetype.SYSTEMS_INTEGRATION,
    ],
    "mobility": [
        RoleArchetype.MOBILITY,
        RoleArchetype.PLATFORM_PRODUCT,
    ],
    "energy_cleantech": [
        RoleArchetype.DEEPTECH,
        RoleArchetype.TECHNOLOGY_STRATEGY,
    ],
    "data_platform": [
        RoleArchetype.PLATFORM_PRODUCT,
        RoleArchetype.TECHNICAL_PRODUCT,
    ],
    "innovation_strategy": [
        RoleArchetype.INNOVATION,
        RoleArchetype.TECHNOLOGY_STRATEGY,
        RoleArchetype.PRODUCT_STRATEGY,
    ],
}


# Career direction mapping
CAREER_DIRECTION_MAP = {
    RoleArchetype.TECHNICAL_PRODUCT: CareerDirection.DIRECT_MATCH,
    RoleArchetype.PRODUCT_LEADERSHIP: CareerDirection.DIRECT_MATCH,
    RoleArchetype.TECHNICAL_PROGRAM: CareerDirection.DIRECT_MATCH,
    RoleArchetype.SYSTEMS_INTEGRATION: CareerDirection.ADJACENT_MATCH,
    RoleArchetype.PLATFORM_PRODUCT: CareerDirection.ADJACENT_MATCH,
    RoleArchetype.AI_PRODUCT: CareerDirection.DIRECT_MATCH,
    RoleArchetype.AUTONOMOUS_SYSTEMS: CareerDirection.DIRECT_MATCH,
    RoleArchetype.ROBOTICS: CareerDirection.ADJACENT_MATCH,
    RoleArchetype.DEEPTECH: CareerDirection.STRETCH_MATCH,
    RoleArchetype.DEFENCE_AEROSPACE: CareerDirection.STRETCH_MATCH,
    RoleArchetype.MOBILITY: CareerDirection.ADJACENT_MATCH,
    RoleArchetype.TECHNOLOGY_STRATEGY: CareerDirection.CAREER_PIVOT,
    RoleArchetype.PRODUCT_STRATEGY: CareerDirection.CAREER_PIVOT,
    RoleArchetype.ENGINEERING_PROGRAM: CareerDirection.ADJACENT_MATCH,
    RoleArchetype.TECHNICAL_PARTNERSHIPS: CareerDirection.CAREER_PIVOT,
    RoleArchetype.SOLUTIONS_ARCHITECTURE: CareerDirection.ADJACENT_MATCH,
    RoleArchetype.CUSTOMER_ENGINEERING: CareerDirection.ADJACENT_MATCH,
    RoleArchetype.INNOVATION: CareerDirection.CAREER_PIVOT,
}


# Role family mapping
ROLE_FAMILY_MAP = {
    RoleArchetype.TECHNICAL_PRODUCT: "Product Management",
    RoleArchetype.PRODUCT_LEADERSHIP: "Product Leadership",
    RoleArchetype.TECHNICAL_PROGRAM: "Program Management",
    RoleArchetype.SYSTEMS_INTEGRATION: "Systems Integration",
    RoleArchetype.PLATFORM_PRODUCT: "Platform Product",
    RoleArchetype.AI_PRODUCT: "AI Product",
    RoleArchetype.AUTONOMOUS_SYSTEMS: "Autonomous Systems",
    RoleArchetype.ROBOTICS: "Robotics",
    RoleArchetype.DEEPTECH: "Deep Tech",
    RoleArchetype.DEFENCE_AEROSPACE: "Defence & Aerospace",
    RoleArchetype.MOBILITY: "Mobility",
    RoleArchetype.TECHNOLOGY_STRATEGY: "Technology Strategy",
    RoleArchetype.PRODUCT_STRATEGY: "Product Strategy",
    RoleArchetype.ENGINEERING_PROGRAM: "Engineering Leadership",
    RoleArchetype.TECHNICAL_PARTNERSHIPS: "Technical Partnerships",
    RoleArchetype.SOLUTIONS_ARCHITECTURE: "Solutions Architecture",
    RoleArchetype.CUSTOMER_ENGINEERING: "Customer Engineering",
    RoleArchetype.INNOVATION: "Innovation",
}


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().strip().split())


def classify_role(job: Job, track: str | None = None) -> tuple[set[RoleArchetype], str, float, str]:
    """Classify a job into role archetypes based on title, description, and track.

    Returns a tuple of:
    - set of matching archetypes (multi-label)
    - role family
    - classification confidence
    - classification reason
    """
    archetypes: set[RoleArchetype] = set()
    reason_parts: list[str] = []
    confidence = 0.0

    title = _normalize_text(job.title or "")
    description = _normalize_text(job.description or "")
    combined = f"{title} {description}"

    # 1. Keyword matching on title + description
    for archetype, keywords in ARCHETYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                archetypes.add(archetype)
                reason_parts.append(f"title/description contains '{kw}'")
                break
    
    # 2. Check for specific archetypes that might be missed
    if "technical product" in combined:
        archetypes.add(RoleArchetype.TECHNICAL_PRODUCT)
        reason_parts.append("technical product detected")
    if "technical program" in combined:
        archetypes.add(RoleArchetype.TECHNICAL_PROGRAM)
        reason_parts.append("technical program detected")
    if "ai product" in combined:
        archetypes.add(RoleArchetype.AI_PRODUCT)
        reason_parts.append("AI product detected")
    if "autonomous" in combined:
        archetypes.add(RoleArchetype.AUTONOMOUS_SYSTEMS)
        reason_parts.append("autonomous systems detected")
    if "robotics" in combined:
        archetypes.add(RoleArchetype.ROBOTICS)
        reason_parts.append("robotics detected")
    if "quantum" in combined:
        archetypes.add(RoleArchetype.DEEPTECH)
        reason_parts.append("quantum/deeptech detected")
    if "solutions architect" in combined:
        archetypes.add(RoleArchetype.SOLUTIONS_ARCHITECTURE)
        reason_parts.append("solutions architecture detected")
    
    # 3. Check for AI Engineer specifically
    if "ai engineer" in combined:
        archetypes.add(RoleArchetype.AI_PRODUCT)
        reason_parts.append("AI engineer detected")

    # 2. Track-based affinity
    if track and track in TRACK_ARCHETYPE_MAP:
        track_archetypes = TRACK_ARCHETYPE_MAP[track]
        archetypes.update(track_archetypes)
        reason_parts.append(f"track '{track}' suggests {len(track_archetypes)} archetypes")

    # 3. Fallback: if no archetypes found, mark as UNKNOWN
    if not archetypes:
        archetypes.add(RoleArchetype.UNKNOWN)
        reason_parts.append("no matching keywords or track affinity")
    
    # 4. Check for AI Engineer specifically
    if "ai engineer" in combined:
        archetypes.add(RoleArchetype.AI_PRODUCT)
        reason_parts.append("AI engineer detected")
        
    # 5. Check for Product Manager specifically
    if "product manager" in combined:
        archetypes.add(RoleArchetype.TECHNICAL_PRODUCT)
        reason_parts.append("product manager detected")
        
    # 6. Check for Program Manager specifically
    if "program manager" in combined:
        archetypes.add(RoleArchetype.TECHNICAL_PROGRAM)
        reason_parts.append("program manager detected")
        
    # 7. Check for Solutions Architect specifically
    if "solutions architect" in combined:
        archetypes.add(RoleArchetype.SOLUTIONS_ARCHITECTURE)
        reason_parts.append("solutions architect detected")
        
    # 8. Check for AI Engineer specifically
    if "ai engineer" in combined:
        archetypes.add(RoleArchetype.AI_PRODUCT)
        reason_parts.append("AI engineer detected")
        
    # 9. Check for Product Manager specifically
    if "product manager" in combined:
        archetypes.add(RoleArchetype.TECHNICAL_PRODUCT)
        reason_parts.append("product manager detected")
        
    # 10. Check for Program Manager specifically
    if "program manager" in combined:
        archetypes.add(RoleArchetype.TECHNICAL_PROGRAM)
        reason_parts.append("program manager detected")
        
    # 11. Check for Technical Product Manager specifically
    if "technical product manager" in combined:
        archetypes.add(RoleArchetype.TECHNICAL_PRODUCT)
        reason_parts.append("technical product manager detected")
        
    # 12. Check for Technical Program Manager specifically
    if "technical program manager" in combined:
        archetypes.add(RoleArchetype.TECHNICAL_PROGRAM)
        reason_parts.append("technical program manager detected")
        
    # 13. Check for AI Product Manager specifically
    if "ai product manager" in combined:
        archetypes.add(RoleArchetype.AI_PRODUCT)
        reason_parts.append("ai product manager detected")

    # Determine confidence based on evidence quality
    confidence = 0.5  # default
    
    # Check for exact title matches (highest confidence)
    title_lower = title.lower()
    if "technical product manager" in title_lower:
        confidence = 0.95
    elif "ai product manager" in title_lower:
        confidence = 0.95
    elif "product manager" in title_lower:
        confidence = 0.9
    elif "program manager" in title_lower:
        confidence = 0.9
    elif "robotics engineer" in title_lower:
        confidence = 0.9
    elif "quantum engineer" in title_lower:
        confidence = 0.9
    elif "global solutions architect" in title_lower:
        confidence = 0.9
    
    # Check for strong description matches
    elif "technical product manager" in combined:
        confidence = 0.9
    elif "ai product manager" in combined or "ai engineer" in combined:
        confidence = 0.9
    elif "autonomous systems" in combined:
        confidence = 0.9
    elif "senior technical product manager" in combined:
        confidence = 0.95
    elif "senior ai product manager" in combined:
        confidence = 0.95
    elif "senior program manager" in combined:
        confidence = 0.9
    elif "senior robotics engineer" in combined:
        confidence = 0.9
    elif "senior quantum engineer" in combined:
        confidence = 0.9
    elif "senior solutions architect" in combined:
        confidence = 0.9
    
    # Check for good matches
    elif "product manager" in combined or "program manager" in combined:
        confidence = 0.8
    elif "technical product" in combined:
        confidence = 0.8
    elif "technical program" in combined:
        confidence = 0.8
    elif "ai product" in combined:
        confidence = 0.8
    elif "autonomous systems" in combined:
        confidence = 0.8
    elif "senior" in title.lower() and "technical product" in title.lower():
        confidence = 0.9
    elif "senior" in title.lower() and "technical program" in title.lower():
        confidence = 0.9
    elif "senior" in title.lower() and "ai product" in title.lower():
        confidence = 0.9
    elif "senior" in title.lower() and "autonomous systems" in title.lower():
        confidence = 0.9
    elif "senior" in title.lower() and "product" in title.lower():
        confidence = 0.8
    elif "senior" in title.lower() and "technical" in title.lower():
        confidence = 0.8
    elif "senior" in title.lower() and "product manager" in title.lower():
        confidence = 0.9
    elif "senior" in title.lower() and "program manager" in title.lower():
        confidence = 0.9
    
    # Adjust for multiple archetypes (but don't penalize too much for clear primary matches)
    archetype_count = len([a for a in archetypes if a != RoleArchetype.UNKNOWN])
    if archetype_count > 1:
        # If we have a high confidence primary match, don't reduce too much
        if confidence >= 0.9:
            confidence = max(confidence, 0.8)
        elif confidence >= 0.8:
            confidence = max(confidence, 0.7)
        else:
            confidence = min(confidence, 0.7)
    elif len(archetypes) == 1 and RoleArchetype.UNKNOWN in archetypes:
        confidence = 0.5

    # Determine role family
    role_family = "Unknown"
    if archetypes and RoleArchetype.UNKNOWN not in archetypes:
        # Use the first archetype's family as the primary family
        primary_archetype = next(iter(archetypes))
        role_family = ROLE_FAMILY_MAP.get(primary_archetype, "Unknown")
        
        # Special case for technical product manager
        if RoleArchetype.TECHNICAL_PRODUCT in archetypes:
            role_family = "Product Management"
        elif RoleArchetype.TECHNICAL_PROGRAM in archetypes:
            role_family = "Program Management"
        elif RoleArchetype.AI_PRODUCT in archetypes:
            role_family = "AI Product"
        elif RoleArchetype.AUTONOMOUS_SYSTEMS in archetypes:
            role_family = "Autonomous Systems"
        elif RoleArchetype.ROBOTICS in archetypes:
            role_family = "Robotics"
        elif RoleArchetype.DEEPTECH in archetypes:
            role_family = "Deep Tech"
        elif RoleArchetype.SOLUTIONS_ARCHITECTURE in archetypes:
            role_family = "Solutions Architecture"
        elif "product" in title.lower():
            role_family = "Product Management"
        elif "ai" in title.lower() and "engineer" in title.lower():
            role_family = "AI Product"

    # Determine career direction
    career_direction = CareerDirection.UNKNOWN
    if archetypes and RoleArchetype.UNKNOWN not in archetypes:
        # Use the first archetype's career direction as the primary direction
        primary_archetype = next(iter(archetypes))
        career_direction = CAREER_DIRECTION_MAP.get(primary_archetype, CareerDirection.UNKNOWN)
        
        # Special case for technical product manager
        if RoleArchetype.TECHNICAL_PRODUCT in archetypes:
            career_direction = CareerDirection.DIRECT_MATCH
        elif RoleArchetype.TECHNICAL_PROGRAM in archetypes:
            career_direction = CareerDirection.DIRECT_MATCH
        elif RoleArchetype.AI_PRODUCT in archetypes:
            career_direction = CareerDirection.DIRECT_MATCH
        elif RoleArchetype.AUTONOMOUS_SYSTEMS in archetypes:
            career_direction = CareerDirection.DIRECT_MATCH
        elif RoleArchetype.ROBOTICS in archetypes:
            career_direction = CareerDirection.ADJACENT_MATCH
        elif RoleArchetype.DEEPTECH in archetypes:
            career_direction = CareerDirection.STRETCH_MATCH
        elif RoleArchetype.SOLUTIONS_ARCHITECTURE in archetypes:
            career_direction = CareerDirection.ADJACENT_MATCH

    return archetypes, role_family, confidence, "; ".join(reason_parts), career_direction
