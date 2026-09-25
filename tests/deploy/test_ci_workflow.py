"""Guard the CI workflow against accidental job loss.

The web console gate and the main-image build were silently truncated out of
this workflow during a scripted edit (observed 2026-09-26): the pipeline kept
passing with two gates missing because nothing asserted their presence.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(".github/workflows/ci.yml")

REQUIRED_JOBS = ("secrets-scan", "backend", "web", "image-build")
REQUIRED_BACKEND_STEPS = ("ruff", "mypy", "pytest")


def test_ci_workflow_defines_every_gate() -> None:
    """Losing any gate must fail here, not silently weaken the pipeline."""
    jobs = yaml.safe_load(WORKFLOW.read_text("utf-8"))["jobs"]
    for name in REQUIRED_JOBS:
        assert name in jobs, f"CI job {name} is missing from {WORKFLOW}"


def test_backend_gate_keeps_all_three_checks() -> None:
    """The backend job must still run ruff, mypy and pytest in order."""
    steps = [
        step["name"]
        for step in yaml.safe_load(WORKFLOW.read_text("utf-8"))["jobs"]["backend"]["steps"]
        if "name" in step
    ]
    positions = [steps.index(name) for name in REQUIRED_BACKEND_STEPS]
    assert positions == sorted(positions), f"backend steps out of order: {steps}"
