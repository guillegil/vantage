"""Guards on the GitHub Actions workflows under ``.github/workflows``.

The shell steps that decide whether a job passes are replayed here under
``bash -e``, the way GitHub runs ``run:`` on Linux, with the commands that
would touch the machine or the network replaced by fakes. That checks the
steps' own logic: that each one fails when what it guards is missing, not
only when everything is in place.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = WORKSPACE_ROOT / ".github" / "workflows"

# `uv run` (or uv by absolute path, as `"$(command -v uv)" run`) followed by
# anything but --no-sync.
_RESYNCING_RUN = re.compile(r'\buv\b\)?"?\s+run\s+(?!--no-sync\b)')

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="the steps are bash scripts")


def _jobs(workflow: Path) -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    return jobs


def _step_script(job: str, step: str) -> str:
    steps = _jobs(WORKFLOWS / "ci.yml")[job]["steps"]
    (script,) = [s["run"] for s in steps if s.get("name") == step]
    return str(script)


def _run_step(
    script: str, workdir: Path, fakes: dict[str, str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` under ``bash -e`` in ``workdir``, with each command
    named in ``fakes`` replaced by that bash body, found first on PATH."""
    fake_bin = workdir / "fake-bin"
    fake_bin.mkdir()
    for name, body in fakes.items():
        fake = fake_bin / name
        fake.write_text(f"#!/bin/bash\n{body}", encoding="utf-8")
        fake.chmod(0o755)
    step = workdir / "step.sh"
    step.write_text(script, encoding="utf-8")
    base = {key: value for key, value in os.environ.items() if key != "BASH_ENV"}
    return subprocess.run(  # noqa: S603
        ["bash", "-e", str(step)],  # noqa: S607
        cwd=workdir,
        env={**base, **env, "PATH": f"{fake_bin}{os.pathsep}{base['PATH']}"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_one_job_points_the_suite_at_the_postgresql_server_it_starts() -> None:
    """The PostgreSQL tests skip wherever `VANTAGE_TEST_POSTGRES_URL` is
    unset, so a job that set it to a server it never started, or never ran
    the suite, would pass without running one of them."""
    jobs = _jobs(WORKFLOWS / "ci.yml")
    setting = [
        name
        for name, job in jobs.items()
        if "VANTAGE_TEST_POSTGRES_URL" in job.get("env", {})
        or any("VANTAGE_TEST_POSTGRES_URL" in step.get("env", {}) for step in job["steps"])
    ]
    assert setting == ["postgres"]

    job = jobs["postgres"]
    (service,) = job["services"].values()
    url = urllib.parse.urlsplit(job["env"]["VANTAGE_TEST_POSTGRES_URL"])
    assert service["image"] == "postgres:17"
    assert url.hostname == "127.0.0.1"
    assert f"{url.port}:5432" in service["ports"]
    assert url.password == service["env"]["POSTGRES_PASSWORD"]
    assert "uv run --no-sync pytest" in [step.get("run") for step in job["steps"]]


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


# `uv pip install` answers with $FAKE_UV_OUTPUT and $FAKE_UV_EXIT; any other
# uv command succeeds.
_FAKE_UV = """\
if [ "$1" = pip ]; then printf '%s' "$FAKE_UV_OUTPUT" >&2; exit "$FAKE_UV_EXIT"; fi
"""
# What uv prints when requires-python refuses the interpreter, wrapped as uv
# wraps it.
_REQUIRES_PYTHON_REFUSAL = """\
  × No solution found when resolving dependencies:
  ╰─▶ Because the current Python version (3.9.23) does not satisfy
      Python>=3.10,<3.14 and pytest-vantage==0.1.0 depends on
      Python>=3.10,<3.14, we can conclude that pytest-vantage==0.1.0 cannot
      be used.
"""
# A failure that has nothing to do with the interpreter.
_UNRELATED_FAILURE = """\
  × No solution found when resolving dependencies:
  ╰─▶ Because pytest was not found in the cache and pytest-vantage==0.1.0
      depends on pytest>=8.0, we can conclude that pytest-vantage==0.1.0
      cannot be used.
"""
_WHEEL = "pytest_vantage-0.1.0-py3-none-any.whl"


@needs_bash
@pytest.mark.parametrize(
    ("wheels", "uv_output", "uv_exit", "passes"),
    [
        pytest.param([_WHEEL], _REQUIRES_PYTHON_REFUSAL, 1, True, id="refused"),
        pytest.param([], "error: invalid wheel filename", 2, False, id="no-wheel"),
        pytest.param(
            [_WHEEL, "pytest_vantage-0.2.0-py3-none-any.whl"],
            _REQUIRES_PYTHON_REFUSAL,
            1,
            False,
            id="two-wheels",
        ),
        pytest.param([_WHEEL], _UNRELATED_FAILURE, 1, False, id="failed-for-another-reason"),
        pytest.param([_WHEEL], "Installed 5 packages", 0, False, id="installed"),
    ],
)
def test_python_3_9_step_passes_only_on_a_requires_python_refusal(
    tmp_path: Path, wheels: list[str], uv_output: str, uv_exit: int, passes: bool
) -> None:
    (tmp_path / "dist").mkdir()
    for wheel in wheels:
        (tmp_path / "dist" / wheel).touch()
    script = _step_script(
        "python-3-9-install-refused", "Assert install is refused, not broken at import"
    )

    result = _run_step(
        script,
        tmp_path,
        {"uv": _FAKE_UV},
        {"FAKE_UV_OUTPUT": uv_output, "FAKE_UV_EXIT": str(uv_exit)},
    )

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


# `iptables -L <chain>` lists the chain with the packet count in
# $FAKE_<chain>, and fails as for a missing chain when that is unset. Every
# other call succeeds.
_FAKE_NETFILTER = """\
if [ "$1" = -L ]; then
  count="FAKE_$2"
  if [ -z "${!count+set}" ]; then echo "No chain/target/match by that name." >&2; exit 1; fi
  echo "Chain $2 (1 references)"
  echo "    pkts      bytes target     prot opt in     out     source    destination"
  echo "       ${!count}        0 REJECT     all  --  *      *       ::/0      ::/0"
fi
"""
# The connection attempts run as `sudo -u <user> -g nonet`; here they are not
# made at all and fail as a rejected connection would. Every other command
# runs as it is.
_FAKE_SUDO = """\
if [ "$1" = -u ]; then exit 1; fi
exec "$@"
"""
_NETWORKING_FAKES = {"sudo": _FAKE_SUDO, "iptables": _FAKE_NETFILTER, "ip6tables": _FAKE_NETFILTER}


@needs_bash
@pytest.mark.parametrize(
    ("counters", "passes"),
    [
        pytest.param({"VANTAGE_NONET": 0, "VANTAGE_NONET6": 0}, True, id="none"),
        pytest.param({"VANTAGE_NONET": 1, "VANTAGE_NONET6": 0}, False, id="ipv4-attempt"),
        pytest.param({"VANTAGE_NONET": 0, "VANTAGE_NONET6": 2}, False, id="ipv6-attempt"),
        pytest.param({"VANTAGE_NONET6": 0}, False, id="ipv4-counter-unreadable"),
        pytest.param({"VANTAGE_NONET": 0}, False, id="ipv6-counter-unreadable"),
    ],
)
def test_networking_job_fails_on_any_counted_attempt(
    tmp_path: Path, counters: dict[str, int], passes: bool
) -> None:
    script = _step_script(
        "networking-disabled", "Assert the suite attempted no outbound connection"
    )

    result = _run_step(
        script,
        tmp_path,
        _NETWORKING_FAKES,
        {f"FAKE_{chain}": str(count) for chain, count in counters.items()},
    )

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


@needs_bash
@pytest.mark.parametrize(
    ("counters", "passes"),
    [
        pytest.param({"VANTAGE_NONET": 1, "VANTAGE_NONET6": 2}, True, id="both-count"),
        pytest.param({"VANTAGE_NONET": 0, "VANTAGE_NONET6": 2}, False, id="ipv4-never-counts"),
        pytest.param({"VANTAGE_NONET": 1, "VANTAGE_NONET6": 0}, False, id="ipv6-never-counts"),
    ],
)
def test_networking_control_proves_every_counter_it_asserts_on(
    tmp_path: Path, counters: dict[str, int], passes: bool
) -> None:
    # A counter that never counts also reads zero, so the control must fail
    # unless every counter the final assertion reads has counted an attempt.
    script = _step_script("networking-disabled", "Prove the rules count a real attempt")

    result = _run_step(
        script,
        tmp_path,
        _NETWORKING_FAKES,
        {f"FAKE_{chain}": str(count) for chain, count in counters.items()},
    )

    assert (result.returncode == 0) is passes, result.stdout + result.stderr
