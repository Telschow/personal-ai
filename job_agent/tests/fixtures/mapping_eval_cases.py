"""Labeled eval cases for the deterministic requirement→evidence mapping layer.

Each case drives ``extract_job_attributes`` on ``job_description``, isolates
the single ``capability`` from the resulting concept set, and compares
``map_requirements`` coverage to ``expected_coverage``.

Categories:
    DIRECT       – concept term in evidence claim, verified/documented level
    STRONG_TRANSFER – adjacent category evidence with concept hint
    PARTIAL_TRANSFER – adjacent category evidence, weaker level
    WEAK_TRANSFER – adjacent category evidence WITHOUT concept hint (backend
                   or similar); expected GAP after the Phase 3.6 fix
    GAP – no evidence at all
    UNKNOWN – sparse profile (no name/experience in profile base)
    CONFLICTING – negative evidence ("no experience with X")
"""

from __future__ import annotations

from job_agent.career.eval import MappingEvalCase

# fmt: off
MAPPING_EVAL_CASES: list[MappingEvalCase] = [
    # ────────────────────────────────────────────────────────────────────────────
    # DIRECT (concept term present in evidence claim)
    # ────────────────────────────────────────────────────────────────────────────
    MappingEvalCase(
        id="d01", job_description="autonomous driving, ADAS experience required",
        capability="autonomous_driving",
        evidence=(("Built autonomous valet parking at Nimbus Motors", "verified",
                    ("ai", "domain"), ("autonomous", "adas")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d02", job_description="product strategy and roadmap ownership",
        capability="product_strategy",
        evidence=(("Owned product roadmap from 0→1", "documented",
                    ("product",), ("roadmap", "strategy")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d03", job_description="stakeholder management, cross-functional leadership",
        capability="stakeholder_management",
        evidence=(("Led cross-functional stakeholder workshops", "documented",
                    ("communication", "leadership"), ("stakeholder",)),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d04", job_description="machine learning model development",
        capability="ai_systems",
        evidence=(("Trained computer vision neural networks in PyTorch", "verified",
                    ("ai",), ("neural", "computer vision")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d05", job_description="agile program management, scrum master",
        capability="program_management",
        evidence=(("Managed release planning and agile ceremonies", "documented",
                    ("program",), ("agile", "release planning")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d06", job_description="backend engineering, REST APIs, Kubernetes",
        capability="backend_engineering",
        evidence=(("Built REST APIs deployed to Kubernetes", "documented",
                    ("technical",), ("rest api", "kubernetes")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d07", job_description="data engineering, ETL pipelines",
        capability="data_engineering",
        evidence=(("Built ETL pipelines for financial reporting", "verified",
                    ("technical",), ("etl", "pipeline")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d08", job_description="embedded C++ for ADAS sensors",
        capability="embedded_engineering",
        evidence=(("Developed firmware for automotive sensors", "verified",
                    ("technical",), ("firmware", "sensor", "embedded")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d09", job_description="functional safety, ISO 26262 compliance",
        capability="safety_critical",
        evidence=(("Led safety case for ISO 26262 ASIL-D", "verified",
                    ("systems",), ("functional safety", "iso 26262")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d10", job_description="AI systems, LLM-based agents",
        capability="ai_systems",
        evidence=(("Built agentic AI framework with LLM tooling", "documented",
                    ("ai",), ("llm", "agents")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d11", job_description="team leadership, manage engineers",
        capability="team_leadership",
        evidence=(("Led a team of 12 engineers", "verified",
                    ("leadership",), ("team lead", "manage engineers")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d12", job_description="GDPR and regulatory compliance",
        capability="regulation",
        evidence=(("Ensured GDPR compliance for payment data", "verified",
                    ("systems",), ("gdpr", "compliance")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d13", job_description="go-to-market strategy, sales",
        capability="gtm",
        evidence=(("Drove go-to-market strategy for SaaS product", "verified",
                    ("product",), ("go-to-market strategy",)),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d14", job_description="user research, usability testing",
        capability="user_research",
        evidence=(("Conducted user interviews and usability studies", "documented",
                    ("product", "communication"), ("user research", "usability")),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d15", job_description="systems engineering, requirements engineering",
        capability="systems_engineering",
        evidence=(("Defined requirements engineering for ADAS platform", "verified",
                    ("systems",), ("requirements engineering",)),),
        category="DIRECT", expected_coverage="STRONG",
    ),
    MappingEvalCase(
        id="d16", job_description="machine learning and autonomous driving",
        capability="ai_systems",
        evidence=(("Built production ML pipeline for autonomous driving", "inferred",
                    ("ai",), ("machine learning",)),),
        category="DIRECT", expected_coverage="PARTIAL",
    ),
    # ────────────────────────────────────────────────────────────────────────────
    # STRONG_TRANSFER — adjacent category + concept hint present in claim
    # ────────────────────────────────────────────────────────────────────────────
    MappingEvalCase(
        id="st01", job_description="team leadership, manage people",
        capability="team_leadership",
        evidence=(("Led cross-functional stakeholder workshops", "documented",
                    ("communication", "leadership"), ("lead",)),),
        category="STRONG_TRANSFER", expected_coverage="TRANSFERABLE",
    ),
    MappingEvalCase(
        id="st02", job_description="product strategy and vision",
        capability="product_strategy",
        evidence=(("Presented quarterly product reviews to leadership",
                    "documented", ("product", "communication"), ()),),
        category="STRONG_TRANSFER", expected_coverage="TRANSFERABLE",
    ),
    MappingEvalCase(
        id="st03", job_description="safety-critical avionics software",
        capability="safety_critical",
        evidence=(("Designed validation suite for medical device firmware",
                    "verified", ("systems", "engineering"), ("validation", "firmware")),),
        category="STRONG_TRANSFER", expected_coverage="TRANSFERABLE",
    ),
    MappingEvalCase(
        id="st04", job_description="autonomous driving, sensor fusion",
        capability="autonomous_driving",
        evidence=(("Built simulation tooling for vehicle dynamics studies",
                    "documented", ("ai", "domain"), ()),),
        category="STRONG_TRANSFER", expected_coverage="TRANSFERABLE",
    ),
    # ────────────────────────────────────────────────────────────────────────────
    # PARTIAL_TRANSFER — adjacent category, weaker evidence level
    # ────────────────────────────────────────────────────────────────────────────
    MappingEvalCase(
        id="pt01", job_description="team leadership, leadership of engineers",
        capability="team_leadership",
        evidence=(("Provided guidance to peers in design reviews",
                    "inferred", ("communication",), ()),),
        category="PARTIAL_TRANSFER", expected_coverage="TRANSFERABLE",
    ),
    MappingEvalCase(
        id="pt02", job_description="program management, agile ceremonies",
        capability="program_management",
        evidence=(("Helped document project status updates", "inferred",
                    ("program",), ()),),
        category="PARTIAL_TRANSFER", expected_coverage="TRANSFERABLE",
    ),
    MappingEvalCase(
        id="pt03", job_description="systems engineering, platform architecture",
        capability="systems_engineering",
        evidence=(("Shadowed platform bring-up activities", "candidate",
                    ("systems",), ()),),
        category="PARTIAL_TRANSFER", expected_coverage="TRANSFERABLE",
    ),
    # ────────────────────────────────────────────────────────────────────────────
    # WEAK_TRANSFER — adjacent category evidence but no concept term hint
    # (backend/data/embedded engineering requires hint; without it → GAP)
    # ────────────────────────────────────────────────────────────────────────────
    MappingEvalCase(
        id="wt01", job_description="backend engineering, microservices, REST",
        capability="backend_engineering",
        evidence=(("Worked as Development Engineer - Product Design",
                    "verified", ("engineering", "technical"), ()),),
        category="WEAK_TRANSFER", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="wt02", job_description="data engineering, ETL, Spark",
        capability="data_engineering",
        evidence=(("Worked as Specialist Engineer - Autonomous Driving",
                    "verified", ("technical", "ai"), ()),),
        category="WEAK_TRANSFER", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="wt03", job_description="embedded firmware, C++ real-time",
        capability="embedded_engineering",
        evidence=(("Led cross-functional automotive systems project",
                    "verified", ("systems", "engineering"), ()),),
        category="WEAK_TRANSFER", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="wt04", job_description="backend engineering, cloud infrastructure",
        capability="backend_engineering",
        evidence=(("Managed software development teams to deliver product",
                    "verified", ("engineering", "technical"), ()),),
        category="WEAK_TRANSFER", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="wt05", job_description="data engineering, SQL, data pipelines",
        capability="data_engineering",
        evidence=(("Implemented real-time ADAS sensor calibration routines",
                    "verified", ("technical", "ai"), ()),),
        category="WEAK_TRANSFER", expected_coverage="GAP",
    ),
    # ────────────────────────────────────────────────────────────────────────────
    # GAP — no evidence at all
    # ────────────────────────────────────────────────────────────────────────────
    MappingEvalCase(
        id="g01", job_description="backend engineering, APIs, cloud",
        capability="backend_engineering",
        evidence=(()),
        category="GAP", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="g02", job_description="data engineering, Spark, ETL",
        capability="data_engineering",
        evidence=(()),
        category="GAP", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="g03", job_description="autonomous driving, ADAS",
        capability="autonomous_driving",
        evidence=(()),
        category="GAP", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="g04", job_description="AI systems, machine learning",
        capability="ai_systems",
        evidence=(()),
        category="GAP", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="g05", job_description="product strategy, go-to-market",
        capability="product_strategy",
        evidence=(()),
        category="GAP", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="g06", job_description="embedded firmware, C++",
        capability="embedded_engineering",
        evidence=(()),
        category="GAP", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="g07", job_description="GDPR compliance, regulatory",
        capability="regulation",
        evidence=(()),
        category="GAP", expected_coverage="GAP",
    ),
    # ────────────────────────────────────────────────────────────────────────────
    # UNKNOWN — sparse profile (no name/experience); un-evidenced → UNKNOWN
    # ────────────────────────────────────────────────────────────────────────────
    MappingEvalCase(
        id="u01", job_description="autonomous driving experience required",
        capability="autonomous_driving",
        evidence=(()),
        category="UNKNOWN", expected_coverage="UNKNOWN", sparse_profile=True,
    ),
    MappingEvalCase(
        id="u02", job_description="AI systems and machine learning",
        capability="ai_systems",
        evidence=(()),
        category="UNKNOWN", expected_coverage="UNKNOWN", sparse_profile=True,
    ),
    MappingEvalCase(
        id="u03", job_description="backend engineering, APIs",
        capability="backend_engineering",
        evidence=(()),
        category="UNKNOWN", expected_coverage="UNKNOWN", sparse_profile=True,
    ),
    MappingEvalCase(
        id="u04", job_description="team leadership",
        capability="team_leadership",
        evidence=(()),
        category="UNKNOWN", expected_coverage="UNKNOWN", sparse_profile=True,
    ),
    # ────────────────────────────────────────────────────────────────────────────
    # CONFLICTING — negative evidence → GAP with negative_evidence=True
    # ────────────────────────────────────────────────────────────────────────────
    MappingEvalCase(
        id="cx01", job_description="data engineering, ETL pipelines",
        capability="data_engineering",
        evidence=(("No experience with data engineering at all",
                    "documented", ("technical",), ("data engineering",)),),
        category="CONFLICTING", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="cx02", job_description="backend engineering, microservices",
        capability="backend_engineering",
        evidence=(("No knowledge of backend development or APIs",
                    "documented", ("technical",), ("backend",)),),
        category="CONFLICTING", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="cx03", job_description="AI systems, machine learning",
        capability="ai_systems",
        evidence=(("Not familiar with neural networks or machine learning",
                    "documented", ("ai",), ("machine learning",)),),
        category="CONFLICTING", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="cx04", job_description="stakeholder management",
        capability="stakeholder_management",
        evidence=(("Never worked with senior stakeholders or executive alignment",
                    "documented", ("communication",), ("stakeholder",)),),
        category="CONFLICTING", expected_coverage="GAP",
    ),
    MappingEvalCase(
        id="cx05", job_description="embedded firmware, C++, real-time",
        capability="embedded_engineering",
        evidence=(("Lack of embedded systems or firmware background",
                    "documented", ("technical",), ("embedded",)),),
        category="CONFLICTING", expected_coverage="GAP",
    ),
]
# fmt: on
