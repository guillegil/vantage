"""Manual harness for RQ-25's metadata-capture overhead measurement (design.md
D102, task 11.1). **Not a pytest test and never collected by the suite** --
built by copying ``measure_vcs_overhead.py``'s harness rather than inventing a
second one, for the same reason that script gives: a benchmark inside the
3.10-3.13 x xdist CI matrix is a check people learn to skip. Run it by hand::

    uv run --extra dev python scripts/measure_metadata_overhead.py

Transcribe the printed medians into the "Measurements" paragraph of
``openspec/changes/run-metadata-capture/specs/run-metadata/spec.md`` (task
11.3). A future change to the declaration read or its bounds MUST re-run this
script and update that paragraph.

Design, per design.md D102:

- **Same shape as the `vcs` harness** -- the same two RQ-25 profiles (1,000 x
  ~10 ms for criterion 1, 1,000 x ~1 ms for criterion 3), the same five
  interleaved A/B/A/B... pairs, medians reported never means, the same
  in-process ``_LiveServer`` over ``InMemoryExecutionStore``.
- **One deliberate change from the `vcs` harness's arms**: both arms here
  already have recording ON. Arm A is ``--vantage`` alone; arm B is
  ``--vantage --vantage-metadata`` against the worst legitimate declaration
  (``MAX_DECLARED_FILES`` = 16 files at ``MAX_DECLARED_FILE_BYTES`` = 8 KiB
  each). The delta isolates *this change*'s added cost -- git capture runs in
  both arms and cancels out -- rather than recording as a whole, which the
  `vcs` harness already measured.
- **A third, unpriced arm** (C) is also measured for context: the flag given
  with no declaration file present at all -- the presence-check-and-warn path
  Q3 exercises, expected to cost close to nothing.
- **Two repositories**: this repository, and a synthetic repository with
  >= 20,000 tracked files, generated here -- synthetic data only (CLAUDE.md).
  Both arms run inside a real git repository because ``Recorder.__init__``
  always calls ``vcs.capture()`` once recording is on, regardless of
  ``--vantage-metadata`` -- that cost is identical in both arms and cancels
  out of the delta, but running in a repo with no ``.git`` at all would
  change what ``vcs.capture`` does, not just how long it takes.
"""

from __future__ import annotations

import asyncio
import json
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

REPO_ROOT = Path(__file__).resolve().parent.parent

_PAIRS = 5  # five interleaved A/B pairs, per design.md's own harness description
_SYNTHETIC_TRACKED_FILES = 20_000

_DECLARATION_FILENAME = "vantage-metadata.json"
_WORST_CASE_FILES = 16  # MAX_DECLARED_FILES
_WORST_CASE_FILE_BYTES = 8 * 1024  # MAX_DECLARED_FILE_BYTES
_DECLARED_SUBDIR = "_vantage_bench_declared"

# Both of RQ-25's own profiles: (test count, per-test sleep in seconds).
_PROFILES: dict[str, tuple[int, float]] = {
    "10ms (RQ-25 criterion 1, ~10s suite)": (1000, 0.010),
    "1ms (RQ-25 criterion 3, ~1s suite)": (1000, 0.001),
}

# The pre-measurement forecast this script's result is allowed to disagree
# with (design.md D102's own text) -- printed alongside the measured numbers
# so neither quietly replaces the other.
_FORECAST = (
    "under 2 ms once per session -- an order of magnitude below vcs.capture's "
    "measured cost, because no subprocess is spawned -- i.e. under 0.02% of "
    "the 10ms profile and under 0.12% of the 1ms profile"
)


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


def _git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- literal argv, shell=False, benchmark-only
        ["git", *args],  # noqa: S607 -- literal argv, shell=False, benchmark-only
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


@contextmanager
def _synthetic_repository(num_files: int = _SYNTHETIC_TRACKED_FILES) -> Iterator[Path]:
    """A generated repository with `num_files` tracked files, one commit, a
    clean tree -- synthetic data only, no content copied from anywhere
    (CLAUDE.md)."""
    with tempfile.TemporaryDirectory(prefix="vantage-metadata-bench-") as tmp:
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


# ---------------------------------------------------------------------------
# The worst legitimate declaration (design.md D102's third arm)
# ---------------------------------------------------------------------------


@contextmanager
def _worst_case_declaration(rootpath: Path) -> Iterator[None]:
    """Write `MAX_DECLARED_FILES` files at `MAX_DECLARED_FILE_BYTES` each,
    plus the `vantage-metadata.json` declaring all of them, directly at
    `rootpath` -- removed again in `finally`, the same transient-write-then-
    clean pattern `measure_vcs_overhead.py` already uses for its own
    `_vantage_bench_suite` test directory inside a real repository."""
    declared_dir = rootpath / _DECLARED_SUBDIR
    declared_dir.mkdir(exist_ok=True)
    declaration_path = rootpath / _DECLARATION_FILENAME
    entries = []
    written: list[Path] = []
    try:
        for i in range(_WORST_CASE_FILES):
            key = f"key_{i:02d}"
            # A little under MAX_DECLARED_FILE_BYTES, well inside "at most 8
            # KiB" -- the padding value is what accounts for nearly all of
            # each file's raw size.
            padding = "x" * (_WORST_CASE_FILE_BYTES - 64)
            content = json.dumps({key: padding})
            file_path = declared_dir / f"declared_{i:02d}.json"
            file_path.write_text(content)
            written.append(file_path)
            entries.append(
                {
                    "path": f"{_DECLARED_SUBDIR}/{file_path.name}",
                    "format": "json",
                    "keys": [key],
                }
            )
        declaration_path.write_text(json.dumps({"version": 1, "files": entries}))
        yield
    finally:
        declaration_path.unlink(missing_ok=True)
        for file_path in written:
            file_path.unlink(missing_ok=True)
        if declared_dir.exists():
            declared_dir.rmdir()


# ---------------------------------------------------------------------------
# Component 2: whole-session overhead, three arms, interleaved
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


def _run_pytest_session(test_dir: Path, rootpath: Path, *, vantage_address: str, arm: str) -> float:
    """`arm` is `"a"` (`--vantage` alone), `"b"` (`--vantage
    --vantage-metadata`, worst-case declaration present) or `"c"` (`--vantage
    --vantage-metadata`, no declaration present at all)."""
    argv = [
        sys.executable,
        "-m",
        "pytest",
        str(test_dir),
        f"--rootdir={rootpath}",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "--vantage",
        "--vantage-server",
        vantage_address,
    ]
    if arm in ("b", "c"):
        argv.append("--vantage-metadata")
    start = time.perf_counter()
    subprocess.run(  # noqa: S603 -- literal argv, shell=False, benchmark-only
        argv, cwd=rootpath, capture_output=True, text=True, check=False
    )
    return time.perf_counter() - start


def _paired_session_overhead(
    repo_root: Path, count: int, per_test_seconds: float, pairs: int = _PAIRS
) -> tuple[float, float, float]:
    """Interleaved A/B/A/B... pairs (A = `--vantage` alone, B = `--vantage
    --vantage-metadata` against the worst legitimate declaration), then C
    (`--vantage --vantage-metadata` with nothing declared) measured on its
    own afterward, since it needs the declaration file absent rather than
    present. Returns `(median_a, median_b, median_c)`, all in seconds."""
    test_dir = repo_root / "_vantage_bench_suite"
    _write_synthetic_suite(test_dir, count, per_test_seconds)
    a_samples: list[float] = []
    b_samples: list[float] = []
    c_samples: list[float] = []
    try:
        with _live_server() as server:
            # Arm C (no declaration present) runs OUTSIDE the worst-case
            # declaration context manager below, but the server stays up for
            # all three arms -- only the declaration files are scoped to A/B.
            with _worst_case_declaration(repo_root):
                for _ in range(pairs):
                    a_samples.append(
                        _run_pytest_session(
                            test_dir, repo_root, vantage_address=server.address, arm="a"
                        )
                    )
                    b_samples.append(
                        _run_pytest_session(
                            test_dir, repo_root, vantage_address=server.address, arm="b"
                        )
                    )
            for _ in range(pairs):
                c_samples.append(
                    _run_pytest_session(
                        test_dir, repo_root, vantage_address=server.address, arm="c"
                    )
                )
    finally:
        for path in test_dir.glob("**/*"):
            if path.is_file():
                path.unlink()
        for path in sorted(test_dir.glob("**/*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        if test_dir.exists():
            test_dir.rmdir()
    return statistics.median(a_samples), statistics.median(b_samples), statistics.median(c_samples)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"date: {time.strftime('%Y-%m-%d')}")
    print(f"machine: {platform.platform()} / {platform.processor() or platform.machine()}")
    print(f"python: {platform.python_version()}")
    print(f"git: {_git_version()}")
    print(f"pre-measurement forecast: {_FORECAST}")
    print()

    print(
        "== Whole-session overhead: A (--vantage) vs B (worst-case declaration) "
        "vs C (flag, no declaration) =="
    )
    print(
        f"({_PAIRS} interleaved A/B pairs, then {_PAIRS} C runs, "
        "per profile per repository, medians reported)"
    )
    with _synthetic_repository() as synth_root:
        for repo_label, repo_root in (
            ("this repository", REPO_ROOT),
            ("synthetic repo", synth_root),
        ):
            for profile_name, (count, per_test_seconds) in _PROFILES.items():
                a_median, b_median, c_median = _paired_session_overhead(
                    repo_root, count, per_test_seconds
                )
                delta_worst = b_median - a_median
                pct_worst = (delta_worst / a_median) * 100 if a_median else float("nan")
                delta_empty = c_median - a_median
                pct_empty = (delta_empty / a_median) * 100 if a_median else float("nan")
                print(
                    f"{repo_label} / {profile_name}: A={a_median:.3f}s "
                    f"B(worst-case)={b_median:.3f}s C(no declaration)={c_median:.3f}s "
                    f"delta(B-A)={delta_worst * 1000:.1f}ms ({pct_worst:.2f}%) "
                    f"delta(C-A)={delta_empty * 1000:.1f}ms ({pct_empty:.2f}%)"
                )


if __name__ == "__main__":
    main()
