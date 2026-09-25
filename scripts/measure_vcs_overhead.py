"""Measure what version-control capture adds to a recorded pytest session.

Not collected by the test suite: it spawns real `git` processes, a real HTTP
server and real pytest subprocesses, and takes around ten minutes -- too slow
and too timing-sensitive for CI. Run it by hand::

    uv run --extra dev python scripts/measure_vcs_overhead.py

Re-run it after any change to which git commands ``vcs.py`` runs, or how many.
Every figure is a median, never a mean, because one scheduler hiccup ruins a
mean:

- **Component 1**: the git cost alone, timing ``vcs.capture``, the plugin's
  whole five-command git read.
- **Component 1b**: ``git status --porcelain --untracked-files=no`` against a
  default ``git status --porcelain``, showing what skipping the walk over
  untracked files saves.
- **Component 2**: whole-session wall time with recording off vs on, as
  interleaved off/on pairs so drift hits both arms equally. ``report-only``
  is the session delta minus the Component 1 git cost: what recording costs
  beyond the git read, chiefly the HTTP report.

Everything runs in this repository and in a generated repository of 20,000
tracked files. Component 2 uses two synthetic suites: 1,000 tests of ~10 ms
(a ~10 s suite) and 1,000 tests of ~1 ms (a ~1 s suite, where a fixed
per-session cost dominates). Read the session deltas against the overhead
budget of 2% of suite runtime.
"""

from __future__ import annotations

import asyncio
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pytest_vantage import vcs

REPO_ROOT = Path(__file__).resolve().parent.parent

_PAIRS = 5  # interleaved off/on pairs per profile per repository
_CAPTURE_REPEATS = 10
_SYNTHETIC_TRACKED_FILES = 20_000
_OVERHEAD_BUDGET = "2% of suite runtime"

# Synthetic suites: (test count, per-test sleep in seconds).
_PROFILES: dict[str, tuple[int, float]] = {
    "10ms (~10s suite)": (1000, 0.010),
    "1ms (~1s suite)": (1000, 0.001),
}


def _git_version() -> str:
    result = subprocess.run(  # noqa: S603 -- literal argv, shell=False, diagnostic only
        ["git", "--version"],  # noqa: S607 -- literal argv, shell=False, diagnostic only
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


@contextmanager
def _synthetic_repository(num_files: int = _SYNTHETIC_TRACKED_FILES) -> Iterator[Path]:
    """A generated repository with `num_files` tracked files, one commit and
    a clean tree."""
    with tempfile.TemporaryDirectory(prefix="vantage-vcs-bench-") as tmp:
        root = Path(tmp)
        _git(root, ["init", "--quiet"])
        _git(root, ["config", "user.email", "bench@example.invalid"])
        _git(root, ["config", "user.name", "vantage-bench"])
        for i in range(num_files):
            shard = root / f"shard_{i // 1000:03d}"
            shard.mkdir(exist_ok=True)
            (shard / f"file_{i:06d}.txt").write_text(f"synthetic content {i}\n")
        _git(root, ["add", "-A"])
        _git(root, ["commit", "--quiet", "-m", "synthetic fixture commit"])
        yield root


def _git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- literal argv, shell=False, benchmark-only
        ["git", *args],  # noqa: S607 -- literal argv, shell=False, benchmark-only
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


# ---------------------------------------------------------------------------
# Component 1: the git cost itself
# ---------------------------------------------------------------------------


def _time_capture(rootpath: Path, repeats: int = _CAPTURE_REPEATS) -> float:
    """Median wall time of `vcs.capture(rootpath)`: the plugin's whole
    five-command git read, exactly as the plugin performs it."""
    samples: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        vcs.capture(rootpath)
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


# ---------------------------------------------------------------------------
# Component 1b: git status with --untracked-files=no vs the default
# ---------------------------------------------------------------------------


def _time_git_status(
    rootpath: Path, *, untracked_files_no: bool, repeats: int = _CAPTURE_REPEATS
) -> float:
    """Median wall time of `git status --porcelain[ --untracked-files=no]`
    alone, isolating the cost `--untracked-files=no` avoids: the walk over
    untracked files that a default `git status` pays."""
    argv = ["status", "--porcelain"]
    if untracked_files_no:
        argv.append("--untracked-files=no")
    samples: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        subprocess.run(  # noqa: S603 -- literal argv, shell=False, benchmark-only
            ["git", *argv],  # noqa: S607 -- literal argv, shell=False, benchmark-only
            cwd=rootpath,
            capture_output=True,
            text=True,
            check=False,
        )
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


# ---------------------------------------------------------------------------
# Component 2: whole-session overhead, recording on vs off, interleaved
# ---------------------------------------------------------------------------


def _write_synthetic_suite(test_dir: Path, count: int, per_test_seconds: float) -> None:
    test_dir.mkdir(parents=True, exist_ok=True)
    lines = ["import time", "", ""]
    for i in range(count):
        lines.append(f"def test_{i:05d}() -> None:")
        lines.append(f"    time.sleep({per_test_seconds!r})")
        lines.append("")
    (test_dir / "test_generated.py").write_text("\n".join(lines))


class _LiveServer:
    """A real `vantage` server (uvicorn + `create_app`), bound to an ephemeral
    loopback port -- the same construction `vantage_test_server.py` uses for
    the plugin's own end-to-end tests, without the pytest fixture wrapper."""

    def __init__(self) -> None:
        import uvicorn
        from vantage.service.app import create_app
        from vantage.storage.memory import InMemoryExecutionStore

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        port = self._sock.getsockname()[1]
        self.address = f"http://127.0.0.1:{port}"

        config = uvicorn.Config(
            create_app(InMemoryExecutionStore()),
            host="127.0.0.1",
            log_level="warning",
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="vantage-bench-server", daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._server.serve(sockets=[self._sock]))

    def start(self) -> None:
        self._thread.start()
        while not self._server.started:
            time.sleep(0.001)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@contextmanager
def _live_server() -> Iterator[_LiveServer]:
    server = _LiveServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _run_pytest_session(test_dir: Path, *, vantage_address: str | None) -> float:
    argv = [
        sys.executable,
        "-m",
        "pytest",
        str(test_dir),
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
    ]
    argv += (
        ["--vantage", "--vantage-server", vantage_address]
        if vantage_address
        else ["-p", "no:vantage"]
    )
    start = time.perf_counter()
    subprocess.run(  # noqa: S603 -- literal argv, shell=False, benchmark-only
        argv, cwd=test_dir.parent, capture_output=True, text=True, check=False
    )
    return time.perf_counter() - start


def _paired_session_overhead(
    repo_root: Path, count: int, per_test_seconds: float, pairs: int = _PAIRS
) -> tuple[float, float]:
    """Interleaved A/B/A/B... where A = recording OFF, B = recording ON,
    inside `repo_root` (so `vcs.capture` sees that repository). Returns
    `(median_off, median_on)`, both in seconds."""
    test_dir = repo_root / "_vantage_bench_suite"
    _write_synthetic_suite(test_dir, count, per_test_seconds)
    off_samples: list[float] = []
    on_samples: list[float] = []
    try:
        with _live_server() as server:
            for _ in range(pairs):
                off_samples.append(_run_pytest_session(test_dir, vantage_address=None))
                on_samples.append(_run_pytest_session(test_dir, vantage_address=server.address))
    finally:
        for path in test_dir.glob("**/*"):
            if path.is_file():
                path.unlink()
        for path in sorted(test_dir.glob("**/*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        if test_dir.exists():
            test_dir.rmdir()
    return statistics.median(off_samples), statistics.median(on_samples)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"date: {time.strftime('%Y-%m-%d')}")
    print(f"machine: {platform.platform()} / {platform.processor() or platform.machine()}")
    print(f"python: {platform.python_version()}")
    print(f"git: {_git_version()}")
    print(f"overhead budget: {_OVERHEAD_BUDGET}")
    print()

    print("== Component 1: git cost (vcs.capture, the whole 5-command read) ==")
    this_repo_git_cost = _time_capture(REPO_ROOT)
    print(
        f"this repository:                {this_repo_git_cost * 1000:.2f} ms "
        f"(median of {_CAPTURE_REPEATS})"
    )

    with _synthetic_repository() as synth_root:
        synth_git_cost = _time_capture(synth_root)
        print(
            f"synthetic repo ({_SYNTHETIC_TRACKED_FILES:,} files): "
            f"{synth_git_cost * 1000:.2f} ms (median of {_CAPTURE_REPEATS})"
        )

        print()
        print("== Component 1b: git status, --untracked-files=no vs default ==")
        for label, root in (("this repository", REPO_ROOT), ("synthetic repo", synth_root)):
            no_untracked = _time_git_status(root, untracked_files_no=True)
            default = _time_git_status(root, untracked_files_no=False)
            print(
                f"{label}: --untracked-files=no {no_untracked * 1000:.2f} ms vs "
                f"default {default * 1000:.2f} ms"
            )

        print()
        print("== Component 2: whole-session overhead, recording OFF vs ON ==")
        print(f"({_PAIRS} interleaved A/B pairs per profile per repository, medians reported)")
        for repo_label, repo_root, git_cost in (
            ("this repository", REPO_ROOT, this_repo_git_cost),
            ("synthetic repo", synth_root, synth_git_cost),
        ):
            for profile_name, (count, per_test_seconds) in _PROFILES.items():
                off_median, on_median = _paired_session_overhead(repo_root, count, per_test_seconds)
                delta = on_median - off_median
                pct = (delta / off_median) * 100 if off_median else float("nan")
                report_only = delta - git_cost
                print(
                    f"{repo_label} / {profile_name}: OFF={off_median:.3f}s ON={on_median:.3f}s "
                    f"delta={delta * 1000:.1f}ms ({pct:.2f}%) "
                    f"[git={git_cost * 1000:.1f}ms, report-only={report_only * 1000:.1f}ms]"
                )


if __name__ == "__main__":
    main()
