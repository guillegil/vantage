"""Guards on the GitHub Actions workflows under ``.github/workflows``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = WORKSPACE_ROOT / ".github" / "workflows"

# `uv run` (or uv by absolute path, as `"$(command -v uv)" run`) followed by
# anything but --no-sync.
_RESYNCING_RUN = re.compile(r'\buv\b\)?"?\s+run\s+(?!--no-sync\b)')


def _jobs(workflow: Path) -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    return jobs


def test_no_step_re_syncs_the_locked_environment() -> None:
    # `uv run` without --no-sync re-syncs the environment first, undoing
    # whatever a job changed after `uv sync --locked`, such as the matrix
    # leg that uninstalls pytest-xdist.
    workflows = sorted(WORKFLOWS.glob("*.yml"))
    assert workflows, f"no workflows found in {WORKFLOWS}"
    offenders = [
        f"{workflow.name}: {name}: {step['run'].strip()}"
        for workflow in workflows
        for name, job in _jobs(workflow).items()
        for step in job["steps"]
        if "run" in step and _RESYNCING_RUN.search(step["run"])
    ]
    assert not offenders, "steps that re-sync the environment:\n" + "\n".join(offenders)
