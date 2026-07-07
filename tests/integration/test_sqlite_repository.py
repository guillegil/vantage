"""RED/GREEN target for `repository.py` -- round-trip behavior against a
tmp-file SQLite DB, plus Protocol-conformance checks (design SS2, SS11 --
the storage half of the decoupling litmus).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from vantage.adapters.sqlite.engine import create_sqlite_engine
from vantage.adapters.sqlite.repository import (
    SqliteEventLog,
    SqliteResultRepository,
    SqliteRunRepository,
)
from vantage.adapters.sqlite.tables import metadata
from vantage.core.domain.events import EventEnvelope
from vantage.core.domain.models import Phase, Run, RunState, TestResult
from vantage.core.storage.repository import EventLog, ResultRepository, RunRepository


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db_engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'vantage.db'}")
    metadata.create_all(db_engine)
    return db_engine


def _run(**overrides: Any) -> Run:
    defaults: dict[str, Any] = {
        "run_id": "run-1",
        "state": RunState.PENDING,
        "testpath": "tests/",
        "user": "alice",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "invocation": {"cmd": ["pytest", "tests/"]},
        "env_snapshot": {"CI": "true"},
        "totals": {"passed": 0, "failed": 0},
    }
    defaults.update(overrides)
    return Run(**defaults)


class TestSqliteRunRepository:
    def test_satisfies_run_repository_protocol(self, engine: Engine) -> None:
        assert isinstance(SqliteRunRepository(engine), RunRepository)

    def test_create_and_get_round_trip(self, engine: Engine) -> None:
        repo = SqliteRunRepository(engine)
        run = _run()

        repo.create(run)

        assert repo.get(run.run_id) == run

    def test_get_missing_run_returns_none(self, engine: Engine) -> None:
        repo = SqliteRunRepository(engine)

        assert repo.get("does-not-exist") is None

    def test_list_orders_newest_first_and_respects_limit(self, engine: Engine) -> None:
        repo = SqliteRunRepository(engine)
        older = _run(run_id="run-old", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        newer = _run(run_id="run-new", created_at=datetime(2026, 1, 2, tzinfo=UTC))
        repo.create(older)
        repo.create(newer)

        result = repo.list(limit=1)

        assert [r.run_id for r in result] == ["run-new"]

    def test_list_before_filters_by_created_at(self, engine: Engine) -> None:
        repo = SqliteRunRepository(engine)
        older = _run(run_id="run-old", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        newer = _run(run_id="run-new", created_at=datetime(2026, 1, 2, tzinfo=UTC))
        repo.create(older)
        repo.create(newer)

        result = repo.list(limit=10, before=datetime(2026, 1, 2, tzinfo=UTC))

        assert [r.run_id for r in result] == ["run-old"]

    def test_update_state_to_running_sets_started_at(self, engine: Engine) -> None:
        repo = SqliteRunRepository(engine)
        run = _run()
        repo.create(run)
        started_at = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)

        repo.update_state(run.run_id, RunState.RUNNING, at=started_at)

        fetched = repo.get(run.run_id)
        assert fetched is not None
        assert fetched.state is RunState.RUNNING
        assert fetched.started_at == started_at

    def test_update_state_to_terminal_sets_finished_at_and_reason(self, engine: Engine) -> None:
        repo = SqliteRunRepository(engine)
        run = _run()
        repo.create(run)
        finished_at = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)

        repo.update_state(run.run_id, RunState.STOPPED, at=finished_at, reason="user requested")

        fetched = repo.get(run.run_id)
        assert fetched is not None
        assert fetched.state is RunState.STOPPED
        assert fetched.finished_at == finished_at
        assert fetched.stop_reason == "user requested"


class TestSqliteResultRepository:
    def test_satisfies_result_repository_protocol(self, engine: Engine) -> None:
        assert isinstance(SqliteResultRepository(engine), ResultRepository)

    def test_create_and_list_for_run_round_trip(self, engine: Engine) -> None:
        SqliteRunRepository(engine).create(_run())
        repo = SqliteResultRepository(engine)
        result = TestResult(
            id=None,
            run_id="run-1",
            node_id="tests/test_x.py::test_a",
            outcome="passed",
            duration=0.012,
            phases={"call": Phase(name="call", outcome="passed", duration=0.01)},
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        repo.create(result)
        fetched = repo.list_for_run("run-1")

        assert len(fetched) == 1
        assert fetched[0].id is not None
        assert fetched[0].node_id == result.node_id
        assert fetched[0].outcome == "passed"
        assert fetched[0].phases == {"call": Phase(name="call", outcome="passed", duration=0.01)}

    def test_list_for_run_empty_returns_empty_list(self, engine: Engine) -> None:
        repo = SqliteResultRepository(engine)

        assert repo.list_for_run("does-not-exist") == []


class TestSqliteEventLog:
    def test_satisfies_event_log_protocol(self, engine: Engine) -> None:
        assert isinstance(SqliteEventLog(engine), EventLog)

    def test_append_returns_monotonically_increasing_seq(self, engine: Engine) -> None:
        SqliteRunRepository(engine).create(_run())
        log = SqliteEventLog(engine)
        envelope = EventEnvelope(
            schema_version=1,
            event_type="run_started",
            run_id="run-1",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            payload={},
        )

        first_seq = log.append(envelope)
        second_seq = log.append(envelope)

        assert second_seq > first_seq

    def test_since_returns_only_events_after_given_seq(self, engine: Engine) -> None:
        SqliteRunRepository(engine).create(_run())
        log = SqliteEventLog(engine)
        first = EventEnvelope(
            schema_version=1,
            event_type="run_started",
            run_id="run-1",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            payload={"n": 1},
        )
        second = EventEnvelope(
            schema_version=1,
            event_type="run_heartbeat",
            run_id="run-1",
            timestamp=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            payload={"n": 2},
        )
        first_seq = log.append(first)
        second_seq = log.append(second)

        result = log.since("run-1", first_seq)

        assert [stored.seq for stored in result] == [second_seq]
        assert result[0].envelope.event_type == "run_heartbeat"
        assert result[0].envelope.payload == {"n": 2}

    def test_since_returns_empty_list_when_no_newer_events(self, engine: Engine) -> None:
        SqliteRunRepository(engine).create(_run())
        log = SqliteEventLog(engine)
        envelope = EventEnvelope(
            schema_version=1,
            event_type="run_started",
            run_id="run-1",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            payload={},
        )
        seq = log.append(envelope)

        assert log.since("run-1", seq) == []
