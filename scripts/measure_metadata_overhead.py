"""Measure what ``--vantage-metadata`` adds to a recorded pytest session.

Not collected by the test suite, for the same reasons as
``measure_vcs_overhead.py``, whose harness this copies. Run it by hand::

    uv run --extra dev python scripts/measure_metadata_overhead.py

Re-run it after any change to how the declaration is read or bounded.

Every arm has recording on, so the git read runs in all of them and cancels
out of the deltas, leaving the cost of metadata capture alone:

- **A**: ``--vantage`` alone, the baseline.
- **B**: ``--vantage --vantage-metadata`` with a declaration naming
  ``MAX_DECLARED_FILES`` (16) files of just under ``MAX_DECLARED_FILE_BYTES``
  (8 KiB) each. Only the first four fit the 32 KiB metadata section budget,
  so the plugin reads five and never opens the rest.
- **C**: ``--vantage --vantage-metadata`` with no declaration file at all,
  the check-and-warn path, expected to cost close to nothing.

A and B run as interleaved pairs. C needs the declaration absent, so it runs
separately afterwards. Medians are reported, never means, for two synthetic
suites (1,000 tests of ~10 ms and of ~1 ms), both in this repository and in
a generated repository of 20,000 tracked files. Both are real git
repositories because recording always runs ``vcs.capture``, and a directory
with no ``.git`` would change what that does, not just how long it takes.
Read the deltas against the overhead budget of 2% of suite runtime.
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

_PAIRS = 5  # interleaved A/B pairs (and C runs) per profile per repository
_SYNTHETIC_TRACKED_FILES = 20_000
_OVERHEAD_BUDGET = "2% of suite runtime"

_DECLARATION_FILENAME = "vantage-metadata.json"
_WORST_CASE_FILES = 16  # MAX_DECLARED_FILES
_WORST_CASE_FILE_BYTES = 8 * 1024  # MAX_DECLARED_FILE_BYTES
_DECLARED_SUBDIR = "_vantage_bench_declared"

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
    """A generated repository with `num_files` tracked files, one commit and
    a clean tree."""
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
# Arm B's declaration: the most files, each near the per-file size limit
# ---------------------------------------------------------------------------


@contextmanager
def _worst_case_declaration(rootpath: Path) -> Iterator[None]:
    """Write `MAX_DECLARED_FILES` files of just under `MAX_DECLARED_FILE_BYTES`
    each, plus a `vantage-metadata.json` at `rootpath` declaring them all;
    everything is removed again on exit."""
    declared_dir = rootpath / _DECLARED_SUBDIR
    declared_dir.mkdir(exist_ok=True)
    declaration_path = rootpath / _DECLARATION_FILENAME
    entries = []
    written: list[Path] = []
    try:
        for i in range(_WORST_CASE_FILES):
            key = f"key_{i:02d}"
            # Leaves room for the JSON wrapper, so each file lands just under
            # MAX_DECLARED_FILE_BYTES.
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
# Whole-session overhead, three arms
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
    print(f"overhead budget: {_OVERHEAD_BUDGET}")
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
