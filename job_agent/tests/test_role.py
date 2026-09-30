"""Tests for role classification."""

from __future__ import annotations

from job_agent.models import Job
from job_agent.role_archetypes import RoleArchetype
from job_agent.role_classifier import classify_role


def test_technical_product_manager():
    job = Job(
        id="test1",
        title="Technical Product Manager",
        description="Responsible for product strategy and roadmap",
        company="Example Inc",
        url="https://example.com/jobs/1",
        source="test",
        source_type="test",
    )
    archetypes, family, confidence, reason, direction = classify_role(job)
    assert RoleArchetype.TECHNICAL_PRODUCT in archetypes
    assert family == "Product Management"
    assert confidence >= 0.9
    assert "technical product manager" in reason


def test_technical_program_manager():
    job = Job(
        id="test2",
        title="Technical Program Manager",
        description="Managing cross-functional programs",
        company="Example Inc",
        url="https://example.com/jobs/2",
        source="test",
        source_type="test",
    )
    archetypes, family, confidence, reason, direction = classify_role(job)
    assert RoleArchetype.TECHNICAL_PROGRAM in archetypes
    assert family == "Program Management"
    assert confidence >= 0.9
    assert "technical program manager" in reason


def test_ai_product_manager():
    job = Job(
        id="test3",
        title="AI Product Manager",
        description="Leading AI product development",
        company="Example Inc",
        url="https://example.com/jobs/3",
        source="test",
        source_type="test",
    )
    archetypes, family, confidence, reason, direction = classify_role(job)
    assert RoleArchetype.AI_PRODUCT in archetypes
    assert family == "Product Management"
    assert confidence >= 0.9
    assert "ai product manager" in reason


def test_autonomous_systems():
    job = Job(
        id="test4",
        title="Autonomous Systems Engineer",
        description="Working on autonomous driving technology",
        company="Example Inc",
        url="https://example.com/jobs/4",
        source="test",
        source_type="test",
    )
    archetypes, family, confidence, reason, direction = classify_role(job)
    assert RoleArchetype.AUTONOMOUS_SYSTEMS in archetypes
    assert family == "Autonomous Systems"
    assert confidence >= 0.9
    assert "autonomous systems" in reason


def test_robotics():
    job = Job(
        id="test5",
        title="Robotics Engineer",
        description="Developing robotic systems",
        company="Example Inc",
        url="https://example.com/jobs/5",
        source="test",
        source_type="test",
    )
    archetypes, family, confidence, reason, direction = classify_role(job)
    assert RoleArchetype.ROBOTICS in archetypes
    assert family == "Robotics"
    assert confidence >= 0.9
    assert "robotics" in reason


def test_deeptech():
    job = Job(
        id="test6",
        title="Quantum Engineer",
        description="Working on quantum computing",
        company="Example Inc",
        url="https://example.com/jobs/6",
        source="test",
        source_type="test",
    )
    archetypes, family, confidence, reason, direction = classify_role(job)
    assert RoleArchetype.DEEPTECH in archetypes
    assert family == "Deep Tech"
    assert confidence >= 0.9
    assert "quantum" in reason


def test_ambiguous_role():
    job = Job(
        id="test7",
        title="Project Manager",
        description="Managing projects",
        company="Example Inc",
        url="https://example.com/jobs/7",
        source="test",
        source_type="test",
    )
    archetypes, family, confidence, reason, direction = classify_role(job)
    assert RoleArchetype.UNKNOWN in archetypes
    assert confidence <= 0.5


def test_multiple_archetypes():
    job = Job(
        id="test8",
        title="Technical Product Manager",
        description="AI product strategy and autonomous systems",
        company="Example Inc",
        url="https://example.com/jobs/8",
        source="test",
        source_type="test",
    )
    archetypes, family, confidence, reason, direction = classify_role(job)
    assert RoleArchetype.TECHNICAL_PRODUCT in archetypes
    assert RoleArchetype.AI_PRODUCT in archetypes
    assert RoleArchetype.AUTONOMOUS_SYSTEMS in archetypes
    assert confidence >= 0.7
