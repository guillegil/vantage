"""RED: dict-backed fakes satisfy the storage Protocols by structural typing
alone -- proving services can depend on these interfaces without any
concrete adapter (design §2, the decoupling litmus)."""

from __future__ import annotations

from datetime import UTC, datetime

from vantage.core.domain.events import CONTRACT_VERSION, EventEnvelope
from vantage.core.domain.models import Run, RunState, TestResult
from vantage.core.storage.repository import (
    EventLog,
    ResultRepository,
    RunRepository,
    StoredEvent,
)


class FakeRunRepository:
    """Dict-backed fake -- no I/O, satisfies `RunRepository` structurally."""

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def create(self, run: Run) -> None:
        self._runs[run.run_id] = run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self, *, limit: int = 50, before: datetime | None = None) -> list[Run]:
        return list(self._runs.values())[:limit]

    def update_state(
        self,
        run_id: str,
        state: RunState,
        *,
        at: datetime,
        reason: str | None = None,
    ) -> None:
        current = self._runs[run_id]
        self._runs[run_id] = Run(
            run_id=current.run_id,
            state=state,
            testpath=current.testpath,
            user=current.user,
            created_at=current.created_at,
            started_at=current.started_at,
            finished_at=at,
            exit_code=current.exit_code,
            stop_reason=reason,
            invocation=current.invocation,
            env_snapshot=current.env_snapshot,
            root_dir=current.root_dir,
            totals=current.totals,
            last_heartbeat_at=current.last_heartbeat_at,
        )


class FakeResultRepository:
    """Dict-backed fake -- satisfies `ResultRepository` structurally."""

    def __init__(self) -> None:
        self._results: dict[str, list[TestResult]] = {}

    def create(self, result: TestResult) -> None:
        self._results.setdefault(result.run_id, []).append(result)

    def list_for_run(self, run_id: str) -> list[TestResult]:
        return list(self._results.get(run_id, []))


class FakeEventLog:
    """List-backed fake -- satisfies `EventLog` structurally."""

    def __init__(self) -> None:
        self._events: list[StoredEvent] = []

    def append(self, envelope: EventEnvelope) -> int:
        seq = len(self._events) + 1
        self._events.append(StoredEvent(seq=seq, envelope=envelope))
        return seq

    def since(self, run_id: str, after_seq: int) -> list[StoredEvent]:
        return [
            stored
            for stored in self._events
            if stored.envelope.run_id == run_id and stored.seq > after_seq
        ]


def _make_run(**overrides: object) -> Run:
    defaults: dict[str, object] = {
        "run_id": "01J000000000000000000000",
        "state": RunState.PENDING,
        "testpath": "tests/",
        "user": "alice",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Run(**defaults)  # type: ignore[arg-type]


def test_fake_run_repository_satisfies_protocol_and_round_trips() -> None:
    repo: RunRepository = FakeRunRepository()
    run = _make_run()

    repo.create(run)

    assert isinstance(repo, RunRepository)
    assert repo.get(run.run_id) == run
    assert repo.get("missing") is None
    assert repo.list() == [run]


def test_run_repository_update_state_transitions_run() -> None:
    repo: RunRepository = FakeRunRepository()
    run = _make_run()
    repo.create(run)

    repo.update_state(
        run.run_id,
        RunState.FAILED_TO_START,
        at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        reason="invalid testpath",
    )

    updated = repo.get(run.run_id)
    assert updated is not None
    assert updated.state is RunState.FAILED_TO_START
    assert updated.stop_reason == "invalid testpath"


def test_fake_result_repository_satisfies_protocol_and_round_trips() -> None:
    repo: ResultRepository = FakeResultRepository()
    result = TestResult(
        id=None,
        run_id="01J000000000000000000000",
        node_id="tests/test_x.py::test_y",
        outcome="passed",
        duration=0.01,
    )

    repo.create(result)

    assert isinstance(repo, ResultRepository)
    assert repo.list_for_run(result.run_id) == [result]
    assert repo.list_for_run("missing") == []


def test_fake_event_log_satisfies_protocol_and_round_trips() -> None:
    log: EventLog = FakeEventLog()
    envelope = EventEnvelope(
        schema_version=CONTRACT_VERSION,
        event_type="run_started",
        run_id="01J000000000000000000000",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        payload={},
    )

    seq = log.append(envelope)

    assert isinstance(log, EventLog)
    assert seq == 1
    assert log.since(envelope.run_id, after_seq=0) == [StoredEvent(seq=1, envelope=envelope)]
    assert log.since(envelope.run_id, after_seq=1) == []
