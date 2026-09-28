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


# --- What each install brings --------------------------------------------------

_INSTALLS = "clean-environment-install"
_VANTAGE_WHEEL = "vantage-0.1.0-py3-none-any.whl"
_BOTH_WHEELS = [_VANTAGE_WHEEL, _WHEEL]

# `uv pip list` prints $FAKE_FREEZE, one requirement per line; every other
# uv command succeeds.
_FAKE_UV_LIST = """\
if [ "$1 $2" = "pip list" ]; then printf '%s\\n' $FAKE_FREEZE; fi
"""
_BASE_INSTALL = [
    "iniconfig==2.3.0",
    "pydantic==2.13.5",
    "pydantic-core==2.46.5",
    "pytest==9.1.1",
    "pytest-vantage==0.1.0",
    "pyyaml==6.0.3",
    "vantage==0.1.0",
]


def _dist(workdir: Path, wheels: list[str]) -> None:
    (workdir / "dist").mkdir()
    for wheel in wheels:
        (workdir / "dist" / wheel).touch()


@needs_bash
@pytest.mark.parametrize(
    ("wheels", "installed", "passes"),
    [
        pytest.param(_BOTH_WHEELS, _BASE_INSTALL, True, id="plugin-and-validation"),
        pytest.param(
            _BOTH_WHEELS,
            [*_BASE_INSTALL[:-3], "PyYAML==6.0.3", "pytest_vantage==0.1.0", "vantage==0.1.0"],
            True,
            id="names-as-written",
        ),
        pytest.param(_BOTH_WHEELS, [*_BASE_INSTALL, "fastapi==0.141.1"], False, id="fastapi"),
        pytest.param(_BOTH_WHEELS, [*_BASE_INSTALL, "starlette==1.7.0"], False, id="starlette"),
        pytest.param(_BOTH_WHEELS, [*_BASE_INSTALL, "uvicorn==0.54.0"], False, id="uvicorn"),
        pytest.param(
            _BOTH_WHEELS,
            [name for name in _BASE_INSTALL if not name.startswith("pytest-vantage")],
            False,
            id="no-plugin",
        ),
        pytest.param(
            _BOTH_WHEELS,
            [name for name in _BASE_INSTALL if not name.startswith("pyyaml")],
            False,
            id="no-pyyaml",
        ),
        pytest.param([_WHEEL], _BASE_INSTALL, False, id="no-vantage-wheel"),
        pytest.param(
            [*_BOTH_WHEELS, "vantage-0.2.0-py3-none-any.whl"],
            _BASE_INSTALL,
            False,
            id="two-vantage-wheels",
        ),
    ],
)
def test_the_vantage_install_step_passes_only_without_the_web_framework(
    tmp_path: Path, wheels: list[str], installed: list[str], passes: bool
) -> None:
    _dist(tmp_path, wheels)
    script = _step_script(_INSTALLS, "Assert vantage brings the plugin and no web framework")

    result = _run_step(
        script,
        tmp_path,
        {"uv": _FAKE_UV_LIST},
        {"BASE_VENV": str(tmp_path / "base"), "FAKE_FREEZE": " ".join(installed)},
    )

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


def _fake_vantage(venv: Path, body: str) -> None:
    """`body`, as the `vantage` command installed into `venv`."""
    command = venv / "bin" / "vantage"
    command.parent.mkdir(parents=True)
    command.write_text(f"#!/bin/bash\n{body}", encoding="utf-8")
    command.chmod(0o755)


_EXTRA_MISSING = "vantage: serving needs the server extra: pip install 'vantage[server]'"


@needs_bash
@pytest.mark.parametrize(
    ("vantage", "passes"),
    [
        pytest.param(f'echo "{_EXTRA_MISSING}" >&2; exit 1', True, id="refused"),
        pytest.param("exit 0", False, id="served"),
        pytest.param(f'echo "{_EXTRA_MISSING}" >&2; exit 2', False, id="another-status"),
        pytest.param(
            "echo 'Traceback (most recent call last):' >&2\n"
            "echo \"ModuleNotFoundError: No module named 'uvicorn'\" >&2\nexit 1",
            False,
            id="traceback",
        ),
        pytest.param(
            f'mkdir -p "$HOME/.local"; echo "{_EXTRA_MISSING}" >&2; exit 1',
            False,
            id="created-something",
        ),
    ],
)
def test_the_serving_refusal_step_passes_only_on_the_one_line(
    tmp_path: Path, vantage: str, passes: bool
) -> None:
    _fake_vantage(tmp_path / "base", vantage)
    script = _step_script(
        _INSTALLS, "Assert serving without the server extra is refused in one line"
    )

    result = _run_step(script, tmp_path, {}, {"BASE_VENV": str(tmp_path / "base")})

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


_PUSH_HELP = 'if [ "$1 $2" = "push --help" ]; then echo "usage: vantage push"; exit 0; fi\n'
_NOTHING_QUEUED = 'if [ "$1" = push ]; then echo "vantage: nothing queued (no queue at x)"; fi\n'


@needs_bash
@pytest.mark.parametrize(
    ("vantage", "passes"),
    [
        pytest.param(_PUSH_HELP + _NOTHING_QUEUED, True, id="runs"),
        pytest.param('[ "$2" = --help ] && exit 1\n' + _NOTHING_QUEUED, False, id="help-fails"),
        pytest.param(_PUSH_HELP + "exit 1", False, id="push-fails"),
        pytest.param(_PUSH_HELP + "echo 'vantage: sent 1 queued run'", False, id="found-a-queue"),
        pytest.param(
            _PUSH_HELP + 'touch "$HOME/vantage.db-outbox"\n' + _NOTHING_QUEUED,
            False,
            id="created-a-queue",
        ),
    ],
)
def test_the_push_step_passes_only_when_push_runs_and_creates_nothing(
    tmp_path: Path, vantage: str, passes: bool
) -> None:
    _fake_vantage(tmp_path / "base", vantage)
    script = _step_script(_INSTALLS, "Assert vantage push runs without the server extra")

    result = _run_step(script, tmp_path, {}, {"BASE_VENV": str(tmp_path / "base")})

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


# `curl` answers $FAKE_ANSWER with exit status $FAKE_CURL_EXIT once the
# server has created $FAKE_READY, and until then fails as for a refused
# connection: the real server answers only after its start's own lines.
_FAKE_CURL = """\
if [ ! -e "$FAKE_READY" ]; then exit 7; fi
printf '%s' "$FAKE_ANSWER"; exit "$FAKE_CURL_EXIT"
"""
_PASSWORD = "Hv3kQ9mNpR7sT2wXyZ4aBc6d"  # noqa: S105
_ADMIN_CREATED = (
    "vantage: created the user admin; change its password at once "
    f"(vantage user password admin, or POST /api/v1/password). Shown this once: {_PASSWORD}"
)
_LISTENING = "INFO:     Listening on http://127.0.0.1:8765 (Press CTRL+C to quit)"


def _serving(*stderr: str) -> str:
    """A `vantage` that writes the lines `stderr` at its start, then
    serves until it is killed."""
    lines = "".join(f"echo '{line}' >&2\n" for line in stderr)
    return f'{lines}touch "$FAKE_READY"\nexec sleep 30'


@needs_bash
@pytest.mark.parametrize(
    ("vantage", "answer", "curl_exit", "passes"),
    [
        pytest.param(
            _serving(_ADMIN_CREATED, _LISTENING),
            '{"session_lifecycle":true}',
            0,
            True,
            id="serves",
        ),
        pytest.param(
            _serving(_ADMIN_CREATED, _LISTENING),
            '{"session_lifecycle": true}',
            0,
            True,
            id="serves-spaced-json",
        ),
        pytest.param(
            _serving(_ADMIN_CREATED, _LISTENING),
            '{"session_lifecycle":false}',
            0,
            False,
            id="no-lifecycle",
        ),
        pytest.param("exit 1", "", 7, False, id="exits"),
        pytest.param(
            _serving(_LISTENING), '{"session_lifecycle":true}', 0, False, id="no-admin-created"
        ),
        pytest.param(
            _serving(_ADMIN_CREATED, _ADMIN_CREATED, _LISTENING),
            '{"session_lifecycle":true}',
            0,
            False,
            id="admin-created-twice",
        ),
        pytest.param(
            _serving(_ADMIN_CREATED.removesuffix(_PASSWORD), _LISTENING),
            '{"session_lifecycle":true}',
            0,
            False,
            id="no-password-shown",
        ),
    ],
)
def test_the_server_install_step_passes_only_when_it_answers_having_created_the_admin(
    tmp_path: Path, vantage: str, answer: str, curl_exit: int, passes: bool
) -> None:
    """A fresh database of the server's own gets the admin `admin` at the
    first start, in exactly one line that ends with its password."""
    _dist(tmp_path, _BOTH_WHEELS)
    _fake_vantage(tmp_path / "server", vantage)
    script = _step_script(_INSTALLS, "Assert vantage[server] serves")

    result = _run_step(
        script,
        tmp_path,
        {"uv": "exit 0", "curl": _FAKE_CURL},
        {
            "SERVER_VENV": str(tmp_path / "server"),
            "FAKE_READY": str(tmp_path / "ready"),
            "FAKE_ANSWER": answer,
            "FAKE_CURL_EXIT": str(curl_exit),
        },
    )

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


@needs_bash
@pytest.mark.parametrize(
    "answer", ['{"session_lifecycle":true}', '{"session_lifecycle":false}'], ids=["passes", "fails"]
)
def test_the_server_install_step_shows_the_servers_stderr_without_the_password(
    tmp_path: Path, answer: str
) -> None:
    """What the server printed is what tells a failed step apart, so it is
    shown either way; the password it printed never is."""
    _dist(tmp_path, _BOTH_WHEELS)
    _fake_vantage(tmp_path / "server", _serving(_ADMIN_CREATED, _LISTENING))
    script = _step_script(_INSTALLS, "Assert vantage[server] serves")

    result = _run_step(
        script,
        tmp_path,
        {"uv": "exit 0", "curl": _FAKE_CURL},
        {
            "SERVER_VENV": str(tmp_path / "server"),
            "FAKE_READY": str(tmp_path / "ready"),
            "FAKE_ANSWER": answer,
            "FAKE_CURL_EXIT": "0",
        },
    )

    shown = result.stdout + result.stderr
    assert _ADMIN_CREATED.removesuffix(_PASSWORD) + "<masked>" in shown
    assert _LISTENING in shown
    assert _PASSWORD not in shown


# The base install's `python`: `-m pytest` runs $FAKE_SESSION; `-c` prints
# $FAKE_RUNS, the runs the store reads back.
_FAKE_PYTHON = """\
if [ "$1" = -m ]; then eval "$FAKE_SESSION"; exit; fi
echo "$FAKE_RUNS"
"""
_HEADER = (
    'echo "vantage: recording run 0123456789abcdef0123456789abcdef in project default to $DB"\n'
)
_STORE = 'mkdir -p "$(dirname "$DB")"; touch "$DB"\n'
_DEFAULT_DB = 'DB="$HOME/.local/share/vantage/vantage.db"\n'


@needs_bash
@pytest.mark.parametrize(
    ("session", "runs", "passes"),
    [
        pytest.param(_DEFAULT_DB + _HEADER + _STORE, "1", True, id="records"),
        pytest.param(_DEFAULT_DB + _HEADER + _STORE + "exit 1", "1", False, id="session-fails"),
        pytest.param(
            'DB="$HOME/elsewhere.db"\n' + _HEADER + _STORE, "1", False, id="another-database"
        ),
        pytest.param(
            _DEFAULT_DB + _HEADER + _STORE + "echo 'VantageWarning: vantage: could not store'",
            "1",
            False,
            id="warned",
        ),
        pytest.param(_DEFAULT_DB + _HEADER, "1", False, id="nothing-stored"),
        pytest.param(_DEFAULT_DB + _HEADER + _STORE, "0", False, id="no-run-read-back"),
    ],
)
def test_the_local_recording_step_passes_only_when_the_run_is_stored_where_vantage_serves(
    tmp_path: Path, session: str, runs: str, passes: bool
) -> None:
    _fake_vantage(tmp_path / "base", "exit 0")
    python = tmp_path / "base" / "bin" / "python"
    python.write_text(f"#!/bin/bash\n{_FAKE_PYTHON}", encoding="utf-8")
    python.chmod(0o755)
    script = _step_script(_INSTALLS, "Assert pytest records locally with only vantage installed")

    result = _run_step(
        script,
        tmp_path,
        {},
        {"BASE_VENV": str(tmp_path / "base"), "FAKE_SESSION": session, "FAKE_RUNS": runs},
    )

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


# --- The server image ------------------------------------------------------------

_IMAGE = "image"

# `docker` as the image job calls it. `inspect` answers the template it is
# given from $FAKE_HEALTH, $FAKE_RUNNING and $FAKE_EXIT; `logs` prints
# $FAKE_LOGS on stderr, where the server writes its own lines; `exec` prints
# $FAKE_TOKEN for `vantage token create`, with exit status $FAKE_TOKEN_EXIT,
# and runs $FAKE_SESSION for the pytest session; `run` lists $FAKE_FILES for
# `--entrypoint ls` and runs $FAKE_START for a start. Everything else
# succeeds.
_FAKE_DOCKER = """\
case "$1" in
  inspect)
    case "$3" in
      *Health.Status*) echo "$FAKE_HEALTH" ;;
      *Running*) echo "$FAKE_RUNNING" ;;
      *ExitCode*) echo "$FAKE_EXIT" ;;
    esac ;;
  logs) printf '%s\\n' "$FAKE_LOGS" >&2 ;;
  exec)
    case "$*" in
      *"token create"*) echo "$FAKE_TOKEN"; exit "$FAKE_TOKEN_EXIT" ;;
      *pytest*) eval "$FAKE_SESSION" ;;
    esac ;;
  run)
    case "$*" in
      *"--entrypoint ls"*) printf '%s\\n' $FAKE_FILES ;;
      *) eval "$FAKE_START" ;;
    esac ;;
esac
"""
# `curl` as the image job calls it: the report without a token gets the
# status $FAKE_REPORT_STATUS, the capabilities $FAKE_CAPABILITIES with exit
# status $FAKE_CURL_EXIT, and the run list $FAKE_RUNS, only when it carries
# the token `vantage token create` printed.
_FAKE_IMAGE_CURL = """\
case "$*" in
  *"-X POST"*) printf '%s' "$FAKE_REPORT_STATUS" ;;
  */capabilities*) printf '%s' "$FAKE_CAPABILITIES"; exit "$FAKE_CURL_EXIT" ;;
  */runs*)
    if [[ "$*" != *"Authorization: Bearer $FAKE_TOKEN"* ]]; then exit 22; fi
    printf '%s' "$FAKE_RUNS" ;;
esac
"""
_IMAGE_FAKES = {"docker": _FAKE_DOCKER, "curl": _FAKE_IMAGE_CURL, "sleep": "exit 0"}
_SERVING = {
    "FAKE_HEALTH": "healthy",
    "FAKE_RUNNING": "true",
    "FAKE_EXIT": "0",
    "FAKE_LOGS": f"{_ADMIN_CREATED}\n{_LISTENING}",
    "FAKE_TOKEN": "vt_0123456789abcdef",
    "FAKE_TOKEN_EXIT": "0",
    "FAKE_SESSION": "echo '1 passed'",
    "FAKE_FILES": "vantage.db",
    "FAKE_START": "",
    "FAKE_REPORT_STATUS": "401",
    "FAKE_CAPABILITIES": '{"session_lifecycle":true}',
    "FAKE_CURL_EXIT": "0",
    "FAKE_RUNS": '{"items":[{"run_id":"r1","recorded_by":"admin"}],"has_more":false}',
}
_ONE_RUN = _SERVING["FAKE_RUNS"]


def _image_step(tmp_path: Path, step: str, **fake: str) -> subprocess.CompletedProcess[str]:
    """Replay the image job's `step` against a server that does everything
    right, but for what `fake` says."""
    env = {
        **_SERVING,
        **fake,
        "IMAGE": "vantage:ci",
        "CONTAINER": "vantage-ci",
        "VOLUME": "vantage-ci-data",
        "PORT": "8765",
    }
    return _run_step(_step_script(_IMAGE, step), tmp_path, _IMAGE_FAKES, env)


_STARTED = "Start the image on a fresh volume"


@needs_bash
@pytest.mark.parametrize(
    ("fake", "passes"),
    [
        pytest.param({}, True, id="healthy"),
        pytest.param(
            {"FAKE_CAPABILITIES": '{"session_lifecycle": true}'}, True, id="healthy-spaced-json"
        ),
        pytest.param({"FAKE_HEALTH": "starting"}, False, id="never-healthy"),
        pytest.param({"FAKE_HEALTH": "unhealthy", "FAKE_RUNNING": "false"}, False, id="stopped"),
        pytest.param({"FAKE_CURL_EXIT": "56"}, False, id="port-unreachable"),
        pytest.param(
            {"FAKE_CAPABILITIES": '{"session_lifecycle":false}'}, False, id="no-lifecycle"
        ),
    ],
)
def test_the_image_start_passes_only_once_it_is_healthy_and_answers_on_the_port(
    tmp_path: Path, fake: dict[str, str], passes: bool
) -> None:
    result = _image_step(tmp_path, _STARTED, **fake)

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


_BINDING_WARNING = "Binding to 0.0.0.0 with no user in the database, which ..."


@needs_bash
@pytest.mark.parametrize(
    ("logs", "passes"),
    [
        pytest.param([_ADMIN_CREATED, _LISTENING], True, id="once"),
        pytest.param([_LISTENING], False, id="no-admin-created"),
        pytest.param([_ADMIN_CREATED, _ADMIN_CREATED, _LISTENING], False, id="twice"),
        pytest.param(
            [_ADMIN_CREATED.removesuffix(_PASSWORD), _LISTENING], False, id="no-password-shown"
        ),
        pytest.param([_ADMIN_CREATED, _BINDING_WARNING, _LISTENING], False, id="wide-bind-warned"),
        pytest.param(
            ["Traceback (most recent call last):", _ADMIN_CREATED, _LISTENING],
            False,
            id="traceback",
        ),
    ],
)
def test_the_image_admin_step_passes_only_on_one_password_and_no_warning(
    tmp_path: Path, logs: list[str], passes: bool
) -> None:
    step = "Assert the first start showed the admin's password once"

    result = _image_step(tmp_path, step, FAKE_LOGS="\n".join(logs))

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


@needs_bash
@pytest.mark.parametrize(
    ("status", "passes"),
    [
        pytest.param("401", True, id="unauthenticated"),
        pytest.param("201", False, id="recorded"),
        pytest.param("422", False, id="validated-first"),
        pytest.param("000", False, id="no-answer"),
    ],
)
def test_the_image_token_step_passes_only_when_a_report_without_one_is_refused(
    tmp_path: Path, status: str, passes: bool
) -> None:
    result = _image_step(tmp_path, "Assert a report needs a token", FAKE_REPORT_STATUS=status)

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


@needs_bash
@pytest.mark.parametrize(
    ("fake", "passes"),
    [
        pytest.param({}, True, id="one-run"),
        pytest.param({"FAKE_RUNS": '{"items":[],"has_more":false}'}, False, id="none"),
        pytest.param(
            {"FAKE_RUNS": _ONE_RUN.replace("]", ',{"run_id":"r2","recorded_by":"admin"}]')},
            False,
            id="two",
        ),
        pytest.param(
            {"FAKE_RUNS": _ONE_RUN.replace('"admin"', "null")}, False, id="recorded-by-nobody"
        ),
        pytest.param({"FAKE_SESSION": "echo '1 failed'; exit 1"}, False, id="session-fails"),
        pytest.param({"FAKE_TOKEN": "", "FAKE_TOKEN_EXIT": "1"}, False, id="no-token-made"),
    ],
)
def test_the_image_recording_step_passes_only_on_one_run_the_token_recorded(
    tmp_path: Path, fake: dict[str, str], passes: bool
) -> None:
    result = _image_step(tmp_path, "Assert a token made with docker exec records a run", **fake)

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


@needs_bash
@pytest.mark.parametrize(
    ("fake", "passes"),
    [
        pytest.param({}, True, id="no-second-password"),
        pytest.param(
            {"FAKE_LOGS": "\n".join([_ADMIN_CREATED, _LISTENING, _ADMIN_CREATED, _LISTENING])},
            False,
            id="second-password",
        ),
        pytest.param({"FAKE_HEALTH": "starting"}, False, id="never-healthy"),
        pytest.param({"FAKE_HEALTH": "unhealthy", "FAKE_RUNNING": "false"}, False, id="stopped"),
    ],
)
def test_the_image_restart_step_passes_only_when_no_password_is_shown_again(
    tmp_path: Path, fake: dict[str, str], passes: bool
) -> None:
    result = _image_step(tmp_path, "Assert a restart shows no password", **fake)

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


@needs_bash
@pytest.mark.parametrize(
    ("status", "files", "passes"),
    [
        pytest.param("0", "vantage.db", True, id="closed"),
        pytest.param("143", "vantage.db", False, id="killed-by-its-own-signal"),
        pytest.param("137", "vantage.db", False, id="killed-after-the-grace-period"),
        pytest.param(
            "0", "vantage.db vantage.db-shm vantage.db-wal", False, id="write-ahead-log-left"
        ),
        pytest.param("0", "", False, id="nothing-stored"),
    ],
)
def test_the_image_stop_step_passes_only_on_status_0_and_a_closed_store(
    tmp_path: Path, status: str, files: str, passes: bool
) -> None:
    result = _image_step(
        tmp_path,
        "Assert docker stop ends it cleanly, with the store closed",
        FAKE_EXIT=status,
        FAKE_FILES=files,
    )

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


_POSTGRES_URL = "postgresql://vantage@127.0.0.1:1/vantage"
_CONNECTION_REFUSED = (
    f"vantage: cannot open the database at {_POSTGRES_URL}: connection failed: connection to "
    'server at "127.0.0.1", port 1 failed: Connection refused'
)
_DRIVER_MISSING = "vantage: PostgreSQL needs the postgres extra: pip install 'vantage[postgres]'"


@needs_bash
@pytest.mark.parametrize(
    ("start", "passes"),
    [
        pytest.param(f"echo '{_CONNECTION_REFUSED}' >&2; exit 1", True, id="refused-to-connect"),
        pytest.param(f'echo "{_DRIVER_MISSING}" >&2; exit 1', False, id="driver-missing"),
        pytest.param(
            "echo 'vantage: /data exists but is not writable by this process' >&2; exit 1",
            False,
            id="took-the-volume",
        ),
        pytest.param(
            f"echo 'Traceback (most recent call last):' >&2; echo '{_CONNECTION_REFUSED}' >&2;"
            " exit 1",
            False,
            id="traceback",
        ),
        pytest.param("exit 0", False, id="served"),
    ],
)
def test_the_image_postgres_step_passes_only_on_the_refusal_to_connect(
    tmp_path: Path, start: str, passes: bool
) -> None:
    result = _image_step(
        tmp_path, "Assert VANTAGE_DATABASE points the image at PostgreSQL", FAKE_START=start
    )

    assert (result.returncode == 0) is passes, result.stdout + result.stderr


@needs_bash
def test_the_image_log_is_shown_without_the_password(tmp_path: Path) -> None:
    """Shown whether the job passed or not, since it is what tells a failed
    step apart; the password in it never is."""
    steps = _jobs(WORKFLOWS / "ci.yml")[_IMAGE]["steps"]
    (step,) = [s for s in steps if s.get("name", "").startswith("Show")]
    assert step.get("if") == "always()"

    result = _image_step(tmp_path, step["name"])

    shown = result.stdout + result.stderr
    assert _ADMIN_CREATED.removesuffix(_PASSWORD) + "<masked>" in shown
    assert _LISTENING in shown
    assert _PASSWORD not in shown


# What publishes from a step: a registry login or push by any docker
# command, a buildx push or manifest, a package upload, or an action that
# does either.
_PUBLISHING_COMMAND = re.compile(
    r"\bdocker\b[^\n|;&]*\b(push|login)\b|--push\b|\bimagetools\s+create\b"
    r"|\buv\s+publish\b|\btwine\s+upload\b|\bpoetry\s+publish\b"
)
_PUBLISHING_ACTIONS = (
    "docker/login-action",
    "docker/build-push-action",
    "pypa/gh-action-pypi-publish",
)


def _publishing(name: str, document: dict[str, Any]) -> list[str]:
    """Every way the workflow `document` could publish: a scope that
    writes packages (or everything), and each step that logs in, pushes or
    uploads, or asks an action to push, however the flag is spelt."""
    offenders = []
    scopes = [document.get("permissions")] + [
        job.get("permissions") for job in document["jobs"].values()
    ]
    for scope in scopes:
        if scope == "write-all" or (isinstance(scope, dict) and scope.get("packages") == "write"):
            offenders.append(f"{name}: writes packages")
    for job_name, job in document["jobs"].items():
        for step in job.get("steps", []):
            label = f"{name}: {job_name}: {step.get('name', step.get('uses', 'a step'))}"
            if _PUBLISHING_COMMAND.search(str(step.get("run", ""))):
                offenders.append(label)
            if str(step.get("uses", "")).startswith(_PUBLISHING_ACTIONS):
                offenders.append(label)
            if step.get("with", {}).get("push") not in (None, False, "false"):
                offenders.append(f"{label}: push")
    return offenders


def test_no_workflow_publishes_anything() -> None:
    """Nothing is published yet: no step logs in to a registry, pushes an
    image or uploads a package, and no workflow may write packages."""
    workflows = sorted(WORKFLOWS.glob("*.yml"))
    assert workflows, f"no workflows found in {WORKFLOWS}"
    offenders = [
        offender
        for workflow in workflows
        for offender in _publishing(
            workflow.name, yaml.safe_load(workflow.read_text(encoding="utf-8"))
        )
    ]
    assert not offenders, "steps that publish:\n" + "\n".join(offenders)


def _workflow(step: dict[str, Any], permissions: object = None) -> dict[str, Any]:
    document: dict[str, Any] = {"jobs": {"image": {"steps": [step]}}}
    if permissions is not None:
        document["permissions"] = permissions
    return document


@pytest.mark.parametrize(
    "document",
    [
        _workflow({"run": "docker push ghcr.io/guillegil/vantage:1"}),
        _workflow({"run": "docker image push ghcr.io/guillegil/vantage:1"}),
        _workflow({"run": "docker compose push"}),
        _workflow({"run": "docker --config /tmp/cfg login ghcr.io -u x --password-stdin"}),
        _workflow({"run": "docker buildx build --push --tag ghcr.io/guillegil/vantage ."}),
        _workflow({"run": "docker buildx imagetools create -t ghcr.io/x:1 ghcr.io/x:a"}),
        _workflow({"run": "uv publish"}),
        _workflow({"run": "twine upload dist/*"}),
        _workflow({"uses": "docker/login-action@v3"}),
        _workflow({"uses": "docker/build-push-action@v6", "with": {"push": True}}),
        _workflow({"uses": "docker/build-push-action@v6"}),
        _workflow(
            {"uses": "some/action@v1", "with": {"push": "${{ github.ref == 'refs/heads/main' }}"}}
        ),
        _workflow({"uses": "pypa/gh-action-pypi-publish@release/v1"}),
        _workflow({"run": "true"}, permissions={"packages": "write"}),
        _workflow({"run": "true"}, permissions="write-all"),
    ],
    ids=[
        "push",
        "image-push",
        "compose-push",
        "login-after-options",
        "buildx-push",
        "imagetools",
        "uv-publish",
        "twine",
        "login-action",
        "build-push-action",
        "build-push-action-bare",
        "push-expression",
        "pypi-action",
        "packages-write",
        "write-all",
    ],
)
def test_the_publishing_guard_catches_every_way_to_publish(document: dict[str, Any]) -> None:
    assert _publishing("ci.yml", document)


@pytest.mark.parametrize(
    "run",
    [
        'docker build --tag "$IMAGE" .',
        'docker run --rm --volume "$VOLUME:/data" "$IMAGE" user list',
        'docker logs "$CONTAINER" 2>&1 | grep -c pushed || true',
        "git push --dry-run origin HEAD",
    ],
    ids=["build", "run", "logs-then-grep", "git"],
)
def test_the_publishing_guard_passes_what_publishes_nothing(run: str) -> None:
    assert _publishing("ci.yml", _workflow({"run": run})) == []
