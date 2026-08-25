"""Manual harness for RQ-25's failure-capture overhead measurement (design.md
D79, task 9.1). **Not a pytest test and never collected by the suite** -- it
spawns a real HTTP server and real pytest subprocesses, and this benchmark
alone runs dozens of thousand-test sessions; a network-disabled or
time-boxed CI job is exactly the wrong place for it. Run it by hand::

    uv run --extra dev python scripts/measure_failure_capture_overhead.py

Transcribe the printed six-cell table into the "Measurements" paragraph of
``openspec/changes/failure-capture/specs/failure-evidence/spec.md`` (task
9.2). A future change to `evidence.py`'s rendering or `budget.py`'s spend
loop MUST re-run this script and update that paragraph.

Follows ``scripts/measure_vcs_overhead.py``'s own harness shape --
**paired, interleaved A/B/A/B... runs, medians reported, never means** --
with three changes design.md D79 specifies:

- **Baseline (A) is current `main` with recording ON**: recording and VCS
  capture on, failure-text capture absent -- the default, no invocation flag
  given (design.md D72, revised after this very measurement to flip the
  polarity from opt-out to opt-in). Not recording-off --
  `version-control-context`'s own table already spent part of RQ-25's budget
  on the git read, and measuring against recording-off would re-measure that
  cost and hide this change's own.
- **Treatment (B) is the identical session with failure capture opted in**
  via `--vantage-failure-text` -- the only difference between A and B is
  this change's second rendering, budget spend and captured-output plumbing.
- **Two new axes, crossed**: failure density (1%, 10%, 100% of 1,000 tests
  at ~10 ms) and the display flag (`--tb=auto`, `--tb=no`) -- six cells.
  `--tb=no` is measured separately because D79 forecasts it as the more
  expensive branch: under `--tb=auto` pytest already rendered the failure
  once, so the source files are warm in the linecache for this change's
  second rendering; under `--tb=no` nothing was rendered first and this
  change's rendering pays the cold cost alone.

Also reported, once per profile (not crossed with `--tb`, since recording-off
never renders anything and so cannot depend on the display flag): the
recording-off median, so the numbers stay commensurable with
`version-control-context`'s existing table. Measured with fewer repeats
(3, not 5) since it is context, not the primary A/B comparison this script
exists to make.
"""

from __future__ import annotations

import asyncio
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PAIRS = 5  # five interleaved A/B pairs, per design.md D79's own harness description
_OFF_REPEATS = 3  # context only, not the primary comparison -- see module docstring

# 1,000 tests at ~10 ms (RQ-25 criterion 1's ~10 s suite) at three failure densities.
_TEST_COUNT = 1000
_PER_TEST_SLEEP = 0.010
_DENSITIES: dict[str, float] = {"1%": 0.01, "10%": 0.10, "100%": 1.00}
_TB_FLAGS: tuple[str, ...] = ("auto", "no")

# The pre-measurement forecast this script's result is allowed to disagree
# with (design.md D79's own text) -- printed alongside the measured numbers
# so neither quietly replaces the other.
_FORECAST = (
    "~55 ms of RQ-25 headroom remains after version-control-context's own "
    "spend; a style='long' rendering is expected in the 1-5 ms range warm, "
    "more cold. The 1% profile (10 failures) is expected to fit; the 10% "
    "profile to be marginal; the 100% profile to exceed RQ-25's 2% budget. "
    "--tb=no is expected to be the more expensive branch (cold rendering, "
    "nothing pre-rendered for the terminal)."
)


def _write_synthetic_suite(
    test_dir: Path, count: int, per_test_seconds: float, fail_fraction: float
) -> None:
    """`count` tests sleeping `per_test_seconds` each; the first
    `round(count * fail_fraction)` fail via a three-frame raise (module ->
    helper -> assert) so the traceback rendered has real frames to walk,
    not a one-line assert at the call site."""
    test_dir.mkdir(parents=True, exist_ok=True)
    fail_count = round(count * fail_fraction)
    lines = [
        "import time",
        "",
        "",
        "def _raise_at_depth(n: int) -> None:",
        "    if n <= 0:",
        "        assert False, 'synthetic failure for RQ-25 overhead measurement'",
        "    _raise_at_depth(n - 1)",
        "",
        "",
    ]
    for i in range(count):
        lines.append(f"def test_{i:05d}() -> None:")
        lines.append(f"    time.sleep({per_test_seconds!r})")
        if i < fail_count:
            lines.append("    _raise_at_depth(2)")
        lines.append("")
    (test_dir / "test_generated.py").write_text("\n".join(lines))
    return


class _LiveServer:
    """A real `vantage` server (uvicorn + `create_app`), bound to an ephemeral
    loopback port -- the same construction `measure_vcs_overhead.py` uses."""

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


def _run_pytest_session(
    test_dir: Path,
    *,
    vantage_address: str | None,
    tb_flag: str,
    failure_text: bool,
) -> float:
    argv = [
        sys.executable,
        "-m",
        "pytest",
        str(test_dir),
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        f"--tb={tb_flag}",
    ]
    if vantage_address:
        argv += ["--vantage", "--vantage-server", vantage_address]
        if failure_text:
            argv.append("--vantage-failure-text")
    else:
        argv += ["-p", "no:vantage"]
    start = time.perf_counter()
    subprocess.run(  # noqa: S603 -- literal argv, shell=False, benchmark-only
        argv, cwd=test_dir.parent, capture_output=True, text=True, check=False
    )
    return time.perf_counter() - start


def _cleanup(test_dir: Path) -> None:
    for path in test_dir.glob("**/*"):
        if path.is_file():
            path.unlink()
    for path in sorted(test_dir.glob("**/*"), reverse=True):
        if path.is_dir():
            path.rmdir()
    if test_dir.exists():
        test_dir.rmdir()


def _measure_cell(
    server: _LiveServer, fail_fraction: float, tb_flag: str
) -> tuple[float, float, float, int]:
    """One (density, --tb) cell: `_PAIRS` interleaved A/B pairs (A =
    failure capture absent, the default; B = failure capture opted in),
    plus `_OFF_REPEATS` recording-off context samples. Returns
    `(off_median, a_median, b_median, fail_count)`."""
    test_dir = REPO_ROOT / "_vantage_bench_suite"
    fail_count = round(_TEST_COUNT * fail_fraction)
    _write_synthetic_suite(test_dir, _TEST_COUNT, _PER_TEST_SLEEP, fail_fraction)
    try:
        off_samples = [
            _run_pytest_session(test_dir, vantage_address=None, tb_flag=tb_flag, failure_text=False)
            for _ in range(_OFF_REPEATS)
        ]
        a_samples: list[float] = []
        b_samples: list[float] = []
        for _ in range(_PAIRS):
            a_samples.append(
                _run_pytest_session(
                    test_dir, vantage_address=server.address, tb_flag=tb_flag, failure_text=False
                )
            )
            b_samples.append(
                _run_pytest_session(
                    test_dir, vantage_address=server.address, tb_flag=tb_flag, failure_text=True
                )
            )
    finally:
        _cleanup(test_dir)
    return (
        statistics.median(off_samples),
        statistics.median(a_samples),
        statistics.median(b_samples),
        fail_count,
    )


def main() -> None:
    print(f"date: {time.strftime('%Y-%m-%d')}")
    print(f"machine: {platform.platform()} / {platform.processor() or platform.machine()}")
    print(f"python: {platform.python_version()}")
    print(f"pre-measurement forecast: {_FORECAST}")
    print()
    print(
        f"== {_PAIRS} interleaved A/B pairs per cell (A=default/absent, B=opted-in), "
        f"{_OFF_REPEATS} recording-off context samples per cell =="
    )
    print()

    header = (
        f"{'density':>8} {'--tb':>6} {'OFF':>9} {'A (default)':>13} {'B (opt-in)':>10} "
        f"{'A->B delta':>11} {'% of A':>8} {'per-failure':>12} {'fails':>6}"
    )
    print(header)
    print("-" * len(header))

    with _live_server() as server:
        for density_label, fail_fraction in _DENSITIES.items():
            for tb_flag in _TB_FLAGS:
                off_median, a_median, b_median, fail_count = _measure_cell(
                    server, fail_fraction, tb_flag
                )
                delta = b_median - a_median
                pct = (delta / a_median) * 100 if a_median else float("nan")
                per_failure_ms = (delta / fail_count) * 1000 if fail_count else float("nan")
                print(
                    f"{density_label:>8} {tb_flag:>6} {off_median:>8.3f}s {a_median:>12.3f}s "
                    f"{b_median:>8.3f}s {delta * 1000:>10.1f}ms {pct:>7.2f}% "
                    f"{per_failure_ms:>10.3f}ms {fail_count:>6}"
                )


if __name__ == "__main__":
    main()
