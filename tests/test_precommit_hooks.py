"""Guards on ``.pre-commit-config.yaml``: each hook runs the tool the rest of
the project runs, and does what its name says.

A hook repository pinned by its own ``rev`` installs a second copy of a tool
that drifts from ``uv.lock``, so a commit can pass a formatter CI rejects, or
fail on a rule CI no longer has. Running the tools through
``uv run --no-sync`` leaves the lockfile as the only pin.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
LOCKED_LAUNCHER = ["uv", "run", "--no-sync"]


def _repos() -> list[dict[str, Any]]:
    text = (WORKSPACE_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    repos: list[dict[str, Any]] = yaml.safe_load(text)["repos"]
    return repos


def _local_hooks() -> list[dict[str, Any]]:
    return [hook for repo in _repos() if repo["repo"] == "local" for hook in repo["hooks"]]


def _command(hook: dict[str, Any]) -> list[str]:
    """The hook's entry without the launcher, as pre-commit splits it."""
    argv = shlex.split(hook["entry"])
    return argv[len(LOCKED_LAUNCHER) :]


def test_local_hooks_run_in_the_locked_environment() -> None:
    hooks = _local_hooks()
    assert hooks, "no local hooks found"
    for hook in hooks:
        assert shlex.split(hook["entry"])[: len(LOCKED_LAUNCHER)] == LOCKED_LAUNCHER, (
            f"hook {hook['id']!r} runs {hook['entry']!r}, not through `uv run --no-sync`"
        )


def test_ruff_comes_from_the_lockfile_not_a_hook_repository() -> None:
    pinned = [
        f"{repo['repo']}@{repo.get('rev')}"
        for repo in _repos()
        if repo["repo"] != "local" and "ruff" in repo["repo"]
    ]
    assert not pinned, f"ruff is pinned apart from uv.lock by {pinned}"

    commands = {tuple(_command(hook)[:2]) for hook in _local_hooks()}
    assert {("ruff", "check"), ("ruff", "format")} <= commands


def _collected(*args: str) -> set[str]:
    env = {key: value for key, value in os.environ.items() if key != "PYTEST_ADDOPTS"}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=WORKSPACE_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    # 5 is "no tests collected", a legitimate answer for a marker filter.
    assert result.returncode in (0, 5), result.stdout + result.stderr
    return {line for line in result.stdout.splitlines() if "::" in line}


def test_pre_push_test_hook_runs_everything_but_the_slow_tests() -> None:
    (hook,) = [
        hook
        for hook in _local_hooks()
        if "pre-push" in hook.get("stages", []) and _command(hook)[:1] == ["pytest"]
    ]
    selected = _collected(*_command(hook)[1:])
    slow = _collected("-m", "slow")

    assert not selected & slow, f"the pre-push hook runs slow tests: {sorted(selected & slow)}"
    assert selected | slow == _collected(), "the pre-push hook leaves out tests that are not slow"
    assert "slow" in hook["name"], f"hook name {hook['name']!r} does not say it skips slow tests"
