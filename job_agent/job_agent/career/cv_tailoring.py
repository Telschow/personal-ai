"""CV tailoring engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from job_agent.models import Job
from job_agent.career.profile import CareerProfile


@dataclass
class CVVariant:
    """Tailored CV variant for a specific job."""
    
    job_id: str
    job_title: str
    company: str
    key_message: str
    selected_experience: list[dict[str, Any]]
    skills_emphasis: list[str]
    summary: str
    bullet_reordering: list[str]
    keyword_alignment: list[str]
    gaps: list[str]
    rationale: str


class CVTailoringEngine:
    """Generate tailored CV variants based on job requirements."""

    def __init__(self, career_profile: CareerProfile, master_cv: dict[str, Any]):
        self.career_profile = career_profile
        self.master_cv = master_cv

    def tailor_for_job(self, job: Job) -> CVVariant:
        """Generate CV variant for a specific job."""
        
        # Extract job requirements
        job_keywords = self._extract_keywords(job)
        
        # Select relevant experience
        selected_experience = self._select_experience(job_keywords)
        
        # Identify skills to emphasize
        skills_emphasis = self._emphasize_skills(job_keywords)
        
        # Identify gaps
        gaps = self._identify_gaps(job_keywords)
        
        # Generate key message
        key_message = self._generate_key_message(job, job_keywords)
        
        # Generate summary
        summary = self._generate_summary(job, job_keywords)
        
        return CVVariant(
            job_id=job.id,
            job_title=job.title,
            company=job.company,
            key_message=key_message,
            selected_experience=selected_experience,
            skills_emphasis=skills_emphasis,
            summary=summary,
            bullet_reordering=self._reorder_bullets(job_keywords),
            keyword_alignment=job_keywords,
            gaps=gaps,
            rationale=self._generate_rationale(job, gaps),
        )

    def _extract_keywords(self, job: Job) -> list[str]:
        """Extract keywords from job description and title."""
        # Simplified keyword extraction
        title_keywords = job.title.lower().split()
        desc_keywords = job.description.lower().split() if job.description else []
        
        # Combine and filter common words
        common_words = {'the', 'and', 'or', 'for', 'with', 'a', 'an', 'in', 'on', 'to', 'of', 'is', 'are', 'was', 'were'}
        keywords = []
        for word in title_keywords + desc_keywords[:100]:  # First 100 words of description
            if len(word) > 3 and word not in common_words:
                keywords.append(word)
        
        return list(dict.fromkeys(keywords))[:20]  # Unique top 20

    def _select_experience(self, keywords: list[str]) -> list[dict[str, Any]]:
        """Select most relevant experience based on keywords."""
        experience = self.master_cv.get('experience', [])
        selected = []
        
        for exp in experience:
            exp_text = (exp.get('title', '') + ' ' + exp.get('facts', '')).lower()
            relevance_score = sum(1 for kw in keywords if kw in exp_text)
            
            if relevance_score > 0:
                selected.append({
                    'company': exp.get('company'),
                    'title': exp.get('title'),
                    'dates': exp.get('dates'),
                    'relevance_score': relevance_score,
                    'facts': exp.get('facts', []),
                })
        
        # Sort by relevance
        selected.sort(key=lambda x: x['relevance_score'], reverse=True)
        return selected[:5]  # Top 5

    def _emphasize_skills(self, keywords: list[str]) -> list[str]:
        """Identify skills to emphasize."""
        skills = self.master_cv.get('skills', [])
        emphasized = []
        
        for skill in skills:
            skill_lower = skill.lower()
            if any(kw in skill_lower for kw in keywords):
                emphasized.append(skill)
        
        return emphasized

    def _identify_gaps(self, keywords: list[str]) -> list[str]:
        """Identify skill gaps."""
        # Simplified gap identification
        master_skills = [s.lower() for s in self.master_cv.get('skills', [])]
        gaps = []
        
        for kw in keywords:
            if kw not in master_skills and len(kw) > 4:
                gaps.append(kw)
        
        return gaps[:5]

    def _generate_key_message(self, job: Job, keywords: list[str]) -> str:
        """Generate key message for CV tailoring."""
        role_family = job.role_family or "role"
        return f"Experienced {role_family} professional seeking to leverage autonomous systems and product management expertise"

    def _generate_summary(self, job: Job, keywords: list[str]) -> str:
        """Generate tailored summary."""
        return f"Results-driven professional with expertise in {job.role_family or 'product management'} and autonomous driving systems"

    def _reorder_bullets(self, keywords: list[str]) -> list[str]:
        """Reorder bullet points to highlight relevant achievements."""
        return [f"Achievement related to {kw}" for kw in keywords[:5]]

    def _generate_rationale(self, job: Job, gaps: list[str]) -> str:
        """Generate rationale for CV tailoring."""
        return f"CV tailored to {job.title} role at {job.company}. Emphasized autonomous driving and product management experience. Identified gaps: {', '.join(gaps)}"


def create_cv_variants_for_jobs(
    jobs: list[Job],
    career_profile: CareerProfile,
    master_cv: dict[str, Any],
    top_n: int = 10
) -> list[CVVariant]:
    """Create CV variants for top N jobs."""
    engine = CVTailoringEngine(career_profile, master_cv)
    
    # Score jobs
    scored = []
    for job in jobs:
        score = job.role_classification_confidence * 0.7 + job.location_score * 0.3
        scored.append((job, score))
    
    scored.sort(key=lambda x: x[1], reverse=True)
    
    # Generate variants for top jobs
    variants = []
    for job, _ in scored[:top_n]:
        variant = engine.tailor_for_job(job)
        variants.append(variant)
    
    return variants
