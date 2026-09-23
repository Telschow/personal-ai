"""GitHub/portfolio project recommendation engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ProjectRecommendation:
    """Recommended portfolio project."""
    
    project_name: str
    career_target: str
    problem: str
    why_this_project: str
    target_skills_demonstrated: list[str]
    architecture: dict[str, Any]
    tech_stack: list[str]
    repo_structure: list[str]
    milestones: list[str]
    mvp_features: list[str]
    stretch_features: list[str]
    readme_outline: list[str]
    demo: str
    test_strategy: str
    metrics: list[str]
    cv_bullet_potential: str
    linkedin_post_potential: str
    estimated_effort: str


class ProjectRecommendationEngine:
    """Generate portfolio project recommendations based on career gaps."""

    def __init__(self, career_profile: dict[str, Any], master_cv: dict[str, Any]):
        self.career_profile = career_profile
        self.master_cv = master_cv

    def recommend_projects(self, job_keywords: list[str], job_role: str) -> list[ProjectRecommendation]:
        """Generate project recommendations for a specific role."""
        
        # Based on career profile and job requirements
        target_roles = self.career_profile.get('target_role_families', [])
        
        if 'product_management' in target_roles and 'autonomous' in job_keywords:
            return [
                self._recommend_autonomous_product_project(),
                self._recommend_ml_product_project(),
            ]
        elif 'technical_leadership' in target_roles:
            return [
                self._recommend_systems_integration_project(),
            ]
        
        return [self._recommend_autonomous_product_project()]

    def _recommend_autonomous_product_project(self) -> ProjectRecommendation:
        """Recommend autonomous systems product project."""
        return ProjectRecommendation(
            project_name="Autonomous Systems Product Simulator",
            career_target="Technical Product Manager, Autonomous Driving",
            problem="Product managers in autonomous systems need better tools to simulate and communicate complex system behaviors to stakeholders",
            why_this_project="Demonstrates product thinking in autonomous systems, systems integration, and stakeholder communication",
            target_skills_demonstrated=[
                "Product requirement specification for complex systems",
                "Systems architecture understanding",
                "Stakeholder communication",
                "Roadmapping for hardware/software integration",
                "Risk management in safety-critical systems"
            ],
            architecture={
                "type": "Web application with simulation backend",
                "components": [
                    "Frontend: React-based dashboard",
                    "Backend: FastAPI simulation engine",
                    "Simulation: Python models for autonomous decision making",
                    "Data storage: SQLite for scenario management"
                ]
            },
            tech_stack=["Python", "FastAPI", "React", "TypeScript", "SQLite", "Plotly", "Docker"],
            repo_structure=[
                "README.md",
                "frontend/",
                "backend/",
                "simulations/",
                "docs/",
                "tests/"
            ],
            milestones=[
                "MVP: Basic scenario editor and playback",
                "V1: Real-time simulation with metrics",
                "V2: Stakeholder dashboard and reporting",
                "V3: Advanced scenarios and edge cases"
            ],
            mvp_features=[
                "Create/edit autonomous driving scenarios",
                "Simulate basic decision making",
                "Visualize vehicle paths and sensor data",
                "Export scenario reports"
            ],
            stretch_features=[
                "Monte Carlo simulation for edge cases",
                "Multi-vehicle coordination simulation",
                "Safety validation metrics",
                "Integration with real-world data sources"
            ],
            readme_outline=[
                "# Autonomous Systems Product Simulator",
                "## Problem",
                "## Solution",
                "## Demo",
                "## Architecture",
                "## Features",
                "## Tech Stack",
                "## Performance",
                "## Future Work"
            ],
            demo="Interactive web demo showing scenario simulation and decision analysis",
            test_strategy="Unit tests for simulation engine, integration tests for API, scenario-based testing",
            metrics=[
                "Scenario execution time",
                "Simulation accuracy",
                "User adoption rate",
                "Stakeholder engagement"
            ],
            cv_bullet_potential="Led development of autonomous systems simulation tool used by product team to evaluate safety scenarios",
            linkedin_post_potential="Built an open-source simulator for autonomous driving scenarios to help product teams communicate complex systems",
            estimated_effort="4-6 weeks part-time"
        )

    def _recommend_ml_product_project(self) -> ProjectRecommendation:
        """Recommend ML product project."""
        return ProjectRecommendation(
            project_name="ML Product Feature Management Dashboard",
            career_target="Product Manager, AI/ML",
            problem="ML product teams need better visibility into model performance, drift, and feature impact",
            why_this_project="Demonstrates ML product management, understanding of model lifecycle, and data-driven decision making",
            target_skills_demonstrated=[
                "ML product management",
                "Model monitoring and observability",
                "Data pipeline understanding",
                "Feature store concepts",
                "A/B testing for ML features"
            ],
            architecture={
                "type": "Full-stack application",
                "components": [
                    "Frontend: Dashboard for feature tracking",
                    "Backend: API for metrics aggregation",
                    "Data layer: PostgreSQL for metrics storage",
                    "Monitoring: Integration with MLflow"
                ]
            },
            tech_stack=["Python", "FastAPI", "React", "PostgreSQL", "MLflow", "Docker", "Grafana"],
            repo_structure=[
                "README.md",
                "frontend/",
                "backend/",
                "analytics/",
                "tests/"
            ],
            milestones=[
                "MVP: Basic metrics dashboard",
                "V1: Model drift detection",
                "V2: Feature impact analysis",
                "V3: Automated alerts"
            ],
            mvp_features=[
                "Track model performance metrics",
                "Visualize feature distribution",
                "Alert on drift detection",
                "Basic A/B test results"
            ],
            stretch_features=[
                "Automated retraining triggers",
                "Feature importance analysis",
                "Cost tracking for ML inference",
                "Integration with CI/CD"
            ],
            readme_outline=[
                "# ML Product Feature Management",
                "## Problem",
                "## Solution",
                "## Features",
                "## Architecture",
                "## Demo"
            ],
            demo="Dashboard showing model performance over time with drift alerts",
            test_strategy="Unit tests, integration tests, mock data validation",
            metrics=[
                "Time to detect drift",
                "Feature usage tracking",
                "Dashboard load time",
                "Alert accuracy"
            ],
            cv_bullet_potential="Designed and built ML model monitoring dashboard adopted by data science team",
            linkedin_post_potential="Open-sourced ML product management tool for model monitoring and feature tracking",
            estimated_effort="3-5 weeks part-time"
        )

    def _recommend_systems_integration_project(self) -> ProjectRecommendation:
        """Recommend systems integration project."""
        return ProjectRecommendation(
            project_name="Automotive Systems Integration Simulator",
            career_target="Technical Leader, Systems Engineering",
            problem="Systems engineers need tools to validate integration between software and hardware components",
            why_this_project="Demonstrates systems thinking, integration expertise, and technical leadership",
            target_skills_demonstrated=[
                "Systems architecture",
                "Hardware/software integration",
                "Interface specification",
                "Validation and verification",
                "Technical documentation"
            ],
            architecture={
                "type": "Simulation and validation platform",
                "components": [
                    "Core simulator engine",
                    "Component interface definitions",
                    "Validation test suite",
                    "Reporting dashboard"
                ]
            },
            tech_stack=["Python", "C++", "Docker", "ROS", "SQLite"],
            repo_structure=[
                "README.md",
                "src/",
                "tests/",
                "docs/",
                "examples/"
            ],
            milestones=[
                "MVP: Basic component interface simulator",
                "V1: Multi-component integration",
                "V2: Validation test suite",
                "V3: Reporting and analytics"
            ],
            mvp_features=[
                "Define component interfaces",
                "Simulate component interactions",
                "Basic validation checks",
                "Export validation reports"
            ],
            stretch_features=[
                "Real-time simulation",
                "Hardware-in-the-loop testing",
                "Automated regression testing",
                "Integration with CI/CD"
            ],
            readme_outline=[
                "# Automotive Systems Integration Simulator",
                "## Overview",
                "## Features",
                "## Architecture",
                "## Usage"
            ],
            demo="Video showing component integration validation",
            test_strategy="System tests, integration tests, validation scenarios",
            metrics=[
                "Test coverage",
                "Simulation accuracy",
                "Integration success rate",
                "Validation time reduction"
            ],
            cv_bullet_potential="Built systems integration simulator reducing validation time by 40%",
            linkedin_post_potential="Open-sourced automotive systems integration tool for validation and verification",
            estimated_effort="6-8 weeks part-time"
        )
