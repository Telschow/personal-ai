"""Career profile + fit intelligence (Slice 2).

Public surface used by the CLI and tests:

- ``derive_career_profile`` / ``CareerProfile`` — structured career profile.
- ``build_profile_evidence`` / ``CareerEvidence`` / ``VerificationLevel`` —
  attributable, provenance-typed claims.
- ``extract_job_attributes`` / ``StructuredJobAttributes`` — semantic view
  over a stored job.
- ``build_retrieval_plan`` / ``collect_evidence`` / ``compact_evidence`` —
  bounded evidence retrieval.
- ``build_knowledge`` — the knowledge-provider seam (default: offline).
- ``analyze_fit`` / ``FitAssessment`` — deterministic fit intelligence.
- ``OllamaJsonClient`` / ``FitNarrative`` — optional, opt-in narrative.
- ``ingest_document`` / ``DocumentSection`` / ``CareerDocument`` — bounded
  CV/document ingestion (txt, md, docx).
- ``reconcile_document`` / ``ReconcileResult`` — evidence reconciliation
  (exact / new / conflict).
- ``map_requirements`` / ``CoverageLevel`` — requirement→capability mapping.
- ``build_positioning_plan`` / ``PositioningPlan`` — evidence-referenced
  positioning.
- ``validate_claim`` / ``CVArtifact`` — anti-fabrication claim validation and
  the (proposal-only) artifact model.
- ``UserArtifact`` / ``UserArtifactType`` / ``UserArtifactStatus`` —
  user-uploaded career artifacts (CV, cover letter) with approval lifecycle.
- ``generate_cv`` / ``render_cv_human`` — evidence-grounded full CV generation.
- ``build_evidence_manifest`` / ``render_manifest_human`` — claim provenance.
"""

from .achievements import Achievement, derive_achievements
from .artifacts import (
    ArtifactStatus,
    Bullet,
    CVArtifact,
    EvidenceManifest,
    EvidenceProvenance,
)
from .cv_generation import (
    CareerMoveType,
    CVSection,
    GeneratedCV,
    generate_cv,
    render_cv_human,
)
from .documents import CareerDocument, DocumentSection, document_id_for, ingest_document
from .evidence import (
    CATEGORY_KEYS,
    CareerEvidence,
    CareerEvidenceType,
    GapSupportLevel,
    CareerMoveClassification,
    VerificationLevel,
    build_profile_evidence,
    classify_categories,
    evidence_id,
    level_index,
    rank_evidence,
)
from .evidence_manifest import build_evidence_manifest, render_manifest_human
from .fit import (
    FitAssessment,
    FitScore,
    FitWeights,
    analyze_fit,
)
from .knowledge import (
    CareerKnowledge,
    CareerKnowledgeUnavailable,
    NullCareerKnowledge,
    PersonalAiCareerKnowledge,
    build_knowledge,
)
from .linkedin_optimization import (
    LinkedInOptimizationResult,
    LinkedInOptimizer,
    LinkedInRecommendation,
    import_linkedin_profile,
    optimize_linkedin,
)
from .llm import (
    FitNarrative,
    OllamaJsonClient,
    parse_narrative,
)
from .linkedin_optimization import (
    LinkedInOptimizationResult,
    LinkedInOptimizer,
    LinkedInRecommendation,
    import_linkedin_profile,
    optimize_linkedin,
)
from .mapping import CoverageLevel, RequirementMap, map_requirements
from .positioning_plan import PositioningPlan, build_positioning_plan
from .profile import ROLE_FAMILIES, CareerProfile, derive_career_profile
from .reconcile import (
    ReconcileResult,
    ReconciliationConflict,
    candidate_evidence_from_document,
    reconcile_document,
)
from .requirements import StructuredJobAttributes, extract_job_attributes
from .retrieval import (
    build_retrieval_plan,
    collect_evidence,
    compact_evidence,
    concept_coverage,
)
from .user_artifacts import (
    ALLOWED_MIME_TYPES,
    MAX_ARTIFACT_SIZE,
    ArtifactValidationError,
    UserArtifact,
    UserArtifactSource,
    UserArtifactStatus,
    UserArtifactType,
    artifact_id_for,
    artifact_storage_path,
    content_hash,
    resolve_artifact_root,
    safe_filename,
    validate_artifact_file,
)
from .validation import ClaimValidation, validate_claim

__all__ = [
    "Achievement",
    "ALLOWED_MIME_TYPES",
    "ArtifactStatus",
    "ArtifactValidationError",
    "Bullet",
    "CATEGORY_KEYS",
    "CVArtifact",
    "CVSection",
    "CareerDocument",
    "CareerEvidence",
    "CareerEvidenceType",
    "CareerKnowledge",
    "CareerKnowledgeUnavailable",
    "CareerMoveType",
    "CareerProfile",
    "ClaimValidation",
    "CoverageLevel",
    "EvidenceManifest",
    "EvidenceProvenance",
    "FitAssessment",
    "FitNarrative",
    "FitScore",
    "FitWeights",
    "GeneratedCV",
    "LinkedInOptimizationResult",
    "LinkedInOptimizer",
    "LinkedInRecommendation",
    "MAX_ARTIFACT_SIZE",
    "NullCareerKnowledge",
    "OllamaJsonClient",
    "PersonalAiCareerKnowledge",
    "PositioningPlan",
    "ROLE_FAMILIES",
    "ReconcileResult",
    "ReconciliationConflict",
    "RequirementMap",
    "StructuredJobAttributes",
    "VerificationLevel",
    "UserArtifact",
    "UserArtifactSource",
    "UserArtifactStatus",
    "UserArtifactType",
    "analyze_fit",
    "artifact_id_for",
    "artifact_storage_path",
    "build_evidence_manifest",
    "build_knowledge",
    "build_positioning_plan",
    "build_profile_evidence",
    "build_retrieval_plan",
    "candidate_evidence_from_document",
    "classify_categories",
    "collect_evidence",
    "compact_evidence",
    "concept_coverage",
    "content_hash",
    "derive_achievements",
    "derive_career_profile",
    "document_id_for",
    "evidence_id",
    "extract_job_attributes",
    "generate_cv",
    "import_linkedin_profile",
    "ingest_document",
    "level_index",
    "map_requirements",
    "optimize_linkedin",
    "parse_narrative",
    "rank_evidence",
    "reconcile_document",
    "render_cv_human",
    "render_manifest_human",
    "resolve_artifact_root",
    "safe_filename",
    "validate_artifact_file",
    "validate_claim",
]