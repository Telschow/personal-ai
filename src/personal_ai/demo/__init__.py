"""Deterministic, synthetic-only architecture demonstration.

This package executes the project's real pipeline against a fixed synthetic
corpus and records what actually happened. It exists so the architecture can be
demonstrated, and tested, without a network, a language model, or any personal
data.

It demonstrates architecture only. It makes no accuracy, latency, or throughput
claim, and it is not a benchmark. The run stops at the human approval boundary
by design: the policy engine refuses the outward-facing action, so the system is
not autonomous where it would otherwise have an effect.

Entry point::

    uv run python -m personal_ai.demo
"""

from personal_ai.demo.artifacts import build_metadata, write_artifacts
from personal_ai.demo.corpus import SYNTHETIC_MARKER
from personal_ai.demo.scenario import DemoResult, demo_stages, run_demo

__all__ = [
    "SYNTHETIC_MARKER",
    "DemoResult",
    "build_metadata",
    "demo_stages",
    "run_demo",
    "write_artifacts",
]
