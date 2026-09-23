"""LinkedIn profile import and optimization."""

from __future__ import annotations

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_agent.models import Job


@dataclass
class LinkedInProfile:
    """Canonical LinkedIn profile representation."""
    
    headline: str
    about: str
    current_role: str
    experience: list[dict[str, Any]]
    education: list[dict[str, Any]]
    skills: list[str]
    certifications: list[str]
    projects: list[dict[str, Any]]
    languages: list[str]
    location: str
    custom_sections: dict[str, Any]
    profile_url: str
    last_updated: str
    source: str


class LinkedInImporter:
    """Import LinkedIn profile from user-provided content."""

    def __init__(self, input_path: str):
        self.input_path = Path(input_path)

    def import_profile(self) -> LinkedInProfile:
        """Import LinkedIn profile from supported format."""
        if not self.input_path.exists():
            raise FileNotFoundError(f"LinkedIn profile not found at {self.input_path}")
        
        if self.input_path.suffix == ".yaml":
            return self._import_yaml()
        elif self.input_path.suffix == ".md":
            return self._import_markdown()
        else:
            raise ValueError(f"Unsupported LinkedIn profile format: {self.input_path.suffix}")

    def _import_yaml(self) -> LinkedInProfile:
        """Import LinkedIn profile from YAML file."""
        with open(self.input_path, "r") as f:
            data = yaml.safe_load(f)
        
        return LinkedInProfile(
            headline=data.get("headline", ""),
            about=data.get("about", ""),
            current_role=data.get("current_role", ""),
            experience=data.get("experience", []),
            education=data.get("education", []),
            skills=data.get("skills", []),
            certifications=data.get("certifications", []),
            projects=data.get("projects", []),
            languages=data.get("languages", []),
            location=data.get("location", ""),
            custom_sections=data.get("custom_sections", {}),
            profile_url=data.get("profile_url", ""),
            last_updated=data.get("last_updated", ""),
            source="yaml_import",
        )

    def _import_markdown(self) -> LinkedInProfile:
        """Import LinkedIn profile from Markdown file."""
        with open(self.input_path, "r") as f:
            content = f.read()
        
        # Simplified markdown parsing
        sections = {
            "headline": "",
            "about": "",
            "current_role": "",
            "experience": [],
            "education": [],
            "skills": [],
            "certifications": [],
            "projects": [],
            "languages": [],
            "location": "",
            "custom_sections": {},
            "profile_url": "",
            "last_updated": "",
        }
        
        current_section = None
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# ") and line[2:].lower() in sections:
                current_section = line[2:].lower()
            elif current_section and line:
                if current_section in ["skills", "certifications", "languages"]:
                    sections[current_section].append(line)
                elif current_section in ["experience", "education", "projects"]:
                    if line.startswith("- ") or line.startswith("* ") or line.startswith("+ ") or line.startswith("• ") or line.startswith("· ") or line.startswith("o ") or line.startswith("•") or line.startswith("·") or line.startswith("o"):
                        sections[current_section].append({"item": line[2:]})
                elif current_section in sections:
                    sections[current_section] = line
        
        return LinkedInProfile(
            headline=sections["headline"],
            about=sections["about"],
            current_role=sections["current_role"],
            experience=sections["experience"],
            education=sections["education"],
            skills=sections["skills"],
            certifications=sections["certifications"],
            projects=sections["projects"],
            languages=sections["languages"],
            location=sections["location"],
            custom_sections=sections["custom_sections"],
            profile_url=sections["profile_url"],
            last_updated=sections["last_updated"],
            source="markdown_import",
        )


class LinkedInOptimizer:
    """Optimize LinkedIn profile based on career goals and job market."""

    def __init__(self, profile: LinkedInProfile, career_profile: dict[str, Any], job_corpus: list[Job]):
        self.profile = profile
        self.career_profile = career_profile
        self.job_corpus = job_corpus

    def optimize(self) -> dict[str, Any]:
        """Generate LinkedIn optimization recommendations."""
        recommendations = {
            "headline": self._optimize_headline(),
            "about": self._optimize_about(),
            "experience": self._optimize_experience(),
            "skills": self._optimize_skills(),
            "projects": self._optimize_projects(),
            "positioning": self._optimize_positioning(),
            "market_alignment": self._optimize_market_alignment(),
        }
        return recommendations

    def _optimize_headline(self) -> dict[str, Any]:
        """Generate headline recommendations."""
        # Generate 3 headline options
        return {
            "options": [
                f"{self.career_profile['current_role_family']} with {self.career_profile['technical_depth']}/10 technical depth",
                f"{self.career_profile['current_role_family']} leading {self.career_profile['product_depth']}/10 product scope",
                f"{self.career_profile['current_role_family']} with {self.career_profile['ai_exposure']}/10 AI exposure",
            ],
            "rationale": "Headlines should reflect current role and key capabilities",
        }

    def _optimize_about(self) -> dict[str, Any]:
        """Generate about section recommendations."""
        # Generate about section
        return {
            "primary": f"Results-driven {self.career_profile['current_role_family']} with expertise in {self.career_profile['industry_domains'][0]} and {self.career_profile['industry_domains'][1]}",
            "alternative": f"Experienced {self.career_profile['current_role_family']} with focus on {self.career_profile['target_role_families'][0]}",
            "rationale": "About section should summarize career focus and key domains",
        }

    def _optimize_experience(self) -> dict[str, Any]:
        """Generate experience recommendations."""
        # Analyze current experience
        recommendations = []
        for exp in self.profile.experience:
            if "facts" in exp:
                recommendations.append({
                    "company": exp.get("company", ""),
                    "title": exp.get("title", ""),
                    "recommendations": [
                        "Add specific metrics where possible",
                        "Highlight transferable skills",
                        "Use action verbs",
                    ],
                })
        
        return {
            "recommendations": recommendations,
            "rationale": "Experience should demonstrate impact and relevant skills",
        }

    def _optimize_skills(self) -> dict[str, Any]:
        """Generate skills recommendations."""
        # Analyze current skills
        skill_status = {
            "retain": [],
            "prioritize": [],
            "add_if_demonstrated": [],
            "develop": [],
        }
        
        # This would be more sophisticated with actual job corpus analysis
        for skill in self.profile.skills:
            if skill.lower() in ["product management", "product strategy", "roadmapping"]:
                skill_status["retain"].append(skill)
            elif skill.lower() in ["autonomous systems", "adas", "automotive"]:
                skill_status["prioritize"].append(skill)
            else:
                skill_status["add_if_demonstrated"].append(skill)
        
        return {
            "status": skill_status,
            "rationale": "Skills should reflect demonstrated capabilities and target career goals",
        }

    def _optimize_projects(self) -> dict[str, Any]:
        """Generate projects recommendations."""
        # Recommend projects based on career goals
        return {
            "recommendations": [
                "Add a project demonstrating product management in autonomous systems",
                "Include a project showing leadership in complex programs",
                "Highlight a project demonstrating AI/ML integration",
            ],
            "rationale": "Projects should demonstrate relevant capabilities and career potential",
        }

    def _optimize_positioning(self) -> dict[str, Any]:
        """Generate positioning recommendations."""
        # Analyze current positioning
        return {
            "recommendations": [
                f"Emphasize {self.career_profile['current_role_family']} expertise",
                f"Highlight {self.career_profile['industry_domains'][0]} experience",
                "Show career progression",
            ],
            "rationale": "Positioning should reflect career goals and market opportunities",
        }

    def _optimize_market_alignment(self) -> dict[str, Any]:
        """Generate market alignment recommendations."""
        # Analyze job corpus for market trends
        market_terms = self._extract_market_terms()
        
        return {
            "market_terms": market_terms,
            "alignment": self._check_alignment(market_terms),
            "recommendations": [
                "Add relevant market terms to About section",
                "Highlight projects that demonstrate market expertise",
                "Update skills to include current market terms",
            ],
            "rationale": "Profile should reflect current market trends and opportunities",
        }

    def _extract_market_terms(self) -> list[str]:
        """Extract market terms from job corpus."""
        # Simplified term extraction
        terms = set()
        for job in self.job_corpus:
            if job.description:
                words = job.description.lower().split()
                for word in words:
                    if len(word) > 4 and word.isalpha():
                        terms.add(word)
        
        # Return top 20 terms
        return sorted(terms)[:20]

    def _check_alignment(self, market_terms: list[str]) -> dict[str, bool]:
        """Check alignment with market terms."""
        alignment = {
            "headline": any(term in self.profile.headline.lower() for term in market_terms),
            "about": any(term in self.profile.about.lower() for term in market_terms),
            "skills": any(term in skill.lower() for term in market_terms for skill in self.profile.skills),
            "projects": any(term in str(project).lower() for term in market_terms for project in self.profile.projects),
        }
        return alignment


def generate_linkedin_search_pack(job_corpus: list[Job], career_profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate LinkedIn search pack based on job corpus and career profile."""
    
    # Extract common terms from top jobs
    common_terms = set()
    for job in job_corpus[:20]:  # Top 20 jobs
        if job.description:
            words = job.description.lower().split()
            for word in words:
                if len(word) > 4 and word.isalpha():
                    common_terms.add(word)
    
    # Generate searches around common terms and career profile
    searches = [
        {
            "intent": "Find Munich opportunities",
            "query": f"{career_profile['current_role_family']} AND Munich",
            "location": "Munich, Germany",
            "experience": "Mid-Senior level",
            "companies": "",
            "why": "Target Munich opportunities for current role family",
        },
        {
            "intent": "Find career acceleration opportunities",
            "query": f"{career_profile['target_role_families'][0]} AND Germany",
            "location": "Germany",
            "experience": "Senior level",
            "companies": "",
            "why": "Find opportunities to accelerate career to target role family",
        },
        {
            "intent": "Find AI/autonomous systems opportunities",
            "query": f"{career_profile['current_role_family']} AND (AI OR autonomous OR robotics)",
            "location": "Germany",
            "experience": "Mid-Senior level",
            "companies": "",
            "why": "Find opportunities in AI/autonomous systems",
        },
        {
            "intent": "Find remote opportunities",
            "query": f"{career_profile['current_role_family']} AND remote",
            "location": "Germany",
            "experience": "Mid-Senior level",
            "companies": "",
            "why": "Find remote opportunities for current role family",
        },
    ]
    
    return searches
