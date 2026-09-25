"""Measure the server-side latency of `GET /api/v1/tests/history`.

Not collected by the test suite: a hard timing assertion is flaky in CI, so
this reports a distribution for a person to read. Run it by hand::

    uv run --extra dev python scripts/measure_history_latency.py

Re-run it after any change to the history query or its indexes. The target
is a p95 under 100 ms; the slowest single response is printed alongside it.

Fixture: 500 runs x 200 results (100,000 results), ~200 distinct node ids,
written through `record_session` on the real SQLite adapter rather than with
hand-written `INSERT`s, which could diverge from what production writes. The
queried node id is present in every run, so its history spans ~500 rows; a
second target is present in exactly one run. Requests go through the ASGI app
in-process (`TestClient`, no socket), because a loopback socket would measure
the kernel too. 5 warm-up requests are discarded, then 200 are timed with
`time.perf_counter_ns`; p95 is nearest-rank on the sorted samples.

An index added to speed up this query also slows every recorded session's
write, so the script also re-runs `measure_vcs_overhead.py`'s
synthetic-repository 10 ms profile (recording off vs on), giving both numbers
from the same sitting.
"""

from __future__ import annotations

import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from vantage.core.domain.execution import Execution, Identity
from vantage.core.domain.result import CaseIdentity, Result
from vantage.service.app import create_app
from vantage.storage.sqlite_store import SqliteExecutionStore

sys.path.insert(0, str(Path(__file__).parent))
from measure_vcs_overhead import (  # noqa: E402
    _git_version,
    _paired_session_overhead,
    _synthetic_repository,
)

_RUNS = 500
_RESULTS_PER_RUN = 200
_DISTINCT_NODE_IDS = 200
_TARGET_ALWAYS = "tests/test_target_always.py::test_it"
_TARGET_ONCE = "tests/test_target_once.py::test_it"
_WARMUP = 5
_SAMPLES = 200


def _node_ids_for_run(run_index: int) -> list[str]:
    """`_RESULTS_PER_RUN` ids: the always-present target; the once-present
    target only in run 0; the rest from a rotating `_DISTINCT_NODE_IDS`-wide
    pool -- ~200 distinct ids across the whole fixture."""
    ids = [_TARGET_ALWAYS, *([_TARGET_ONCE] if run_index == 0 else [])]
    ids += [
        f"tests/test_pool_{(run_index + i) % _DISTINCT_NODE_IDS:03d}.py::test_it"
        for i in range(_RESULTS_PER_RUN - len(ids))
    ]
    return ids


def _result_for(node_id: str, started: datetime) -> Result:
    return Result(
        identity=CaseIdentity(
            node_id=node_id,
            file_path=node_id.split("::", 1)[0],
            class_name=None,
            function_name=node_id.rsplit("::", 1)[-1],
            param_id=None,
        ),
        outcome="passed",
        duration=0.003,
        started_at=started,
        finished_at=started + timedelta(seconds=0.003),
        setup_outcome="passed",
        call_outcome="passed",
        teardown_outcome="passed",
        setup_duration=0.001,
        call_duration=0.001,
        teardown_duration=0.001,
        worker_id=None,
    )


def _build_fixture(db_path: Path) -> None:
    store = SqliteExecutionStore(db_path)
    base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
    for run_index in range(_RUNS):
        started = base + timedelta(minutes=run_index)
        execution = Execution(
            identity=Identity(f"{run_index:032x}"),
            started_at=started,
            finished_at=started + timedelta(seconds=30),
            exit_status=0,
            interrupted=False,
            interrupt_reason=None,
            vcs=None,
        )
        results = tuple(_result_for(node_id, started) for node_id in _node_ids_for_run(run_index))
        store.record_session(execution, results=results, received_at=started)
    store.close()


def _history_latencies_ns(client: TestClient) -> list[int]:
    params = {"node_id": _TARGET_ALWAYS}
    for _ in range(_WARMUP):
        client.get("/api/v1/tests/history", params=params)
    samples: list[int] = []
    for _ in range(_SAMPLES):
        start = time.perf_counter_ns()
        response = client.get("/api/v1/tests/history", params=params)
        samples.append(time.perf_counter_ns() - start)
        assert response.status_code == 200  # noqa: S101 -- diagnostic, benchmark-only
    return samples


def _nearest_rank_p95(samples: list[int]) -> int:
    ordered = sorted(samples)
    rank = -(-95 * len(ordered) // 100)  # ceil(95% of n): nearest-rank
    return ordered[rank - 1]


def main() -> None:
    print(f"date: {time.strftime('%Y-%m-%d')}")
    print(f"git: {_git_version()}")
    print(
        f"fixture: {_RUNS} runs x {_RESULTS_PER_RUN} results = {_RUNS * _RESULTS_PER_RUN} results"
    )

    with tempfile.TemporaryDirectory(prefix="vantage-history-bench-") as tmp:
        db_path = Path(tmp) / "vantage.db"
        _build_fixture(db_path)
        store = SqliteExecutionStore(db_path)
        client = TestClient(create_app(store))
        samples = _history_latencies_ns(client)
        store.close()

    p95_ms = _nearest_rank_p95(samples) / 1_000_000
    max_ms = max(samples) / 1_000_000
    print()
    print("== GET /api/v1/tests/history latency, server-side, in-process ==")
    print(f"p95 (nearest-rank, n={_SAMPLES}): {p95_ms:.2f} ms")
    print(f"max (single slowest response):    {max_ms:.2f} ms")

    print()
    print("== Recording overhead, off vs on: synthetic repository, 10 ms profile ==")
    with _synthetic_repository() as synth_root:
        off_median, on_median = _paired_session_overhead(synth_root, 1000, 0.010)
    delta_ms = (on_median - off_median) * 1000
    pct = (delta_ms / (off_median * 1000)) * 100 if off_median else float("nan")
    print(f"OFF={off_median:.3f}s ON={on_median:.3f}s delta={delta_ms:.1f}ms ({pct:.2f}%)")


if __name__ == "__main__":
    main()
