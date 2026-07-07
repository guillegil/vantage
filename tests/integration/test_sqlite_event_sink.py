"""RED/GREEN target for `event_sink.py` -- `SqliteEventSink` projects
validated envelopes into `runs`/`test_results`/`discovery`, always
appending an audit row to `events`, all within one transaction per `emit`
call (design SS3, SS11). An unrecognized `event_type` is logged/skipped and
must not block the next valid event for the same `run_id` (spec Domain 1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select

from vantage.adapters.sqlite.engine import create_sqlite_engine
from vantage.adapters.sqlite.event_sink import SqliteEventSink
from vantage.adapters.sqlite.repository import SqliteRunRepository
from vantage.adapters.sqlite.tables import discovery, events, metadata, runs, test_results
from vantage.core.domain.events import CONTRACT_VERSION, EventEnvelope
from vantage.core.domain.models import Run, RunState
from vantage.core.ingestion.sink import EventSink


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db_engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'vantage.db'}")
    metadata.create_all(db_engine)
    return db_engine


def _pending_run(run_id: str = "run-1") -> Run:
    return Run(
        run_id=run_id,
        state=RunState.PENDING,
        testpath="tests/",
        user="alice",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _envelope(**overrides: Any) -> EventEnvelope:
    defaults: dict[str, Any] = {
        "schema_version": CONTRACT_VERSION,
        "event_type": "run_started",
        "run_id": "run-1",
        "timestamp": datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        "payload": {
            "invocation": {"cmd": ["pytest", "tests/"]},
            "env_snapshot": {"CI": "true"},
            "user": "alice",
            "root_dir": "/repo",
        },
    }
    defaults.update(overrides)
    return EventEnvelope(**defaults)


def test_satisfies_event_sink_protocol(engine: Engine) -> None:
    assert isinstance(SqliteEventSink(engine), EventSink)


def test_emit_run_started_updates_the_preexisting_runs_row(engine: Engine) -> None:
    # design §6: the run row is pre-created (server launch, or a test
    # fixture standing in for it) before `run_started` arrives; the event
    # fills in runtime details and transitions the run to RUNNING.
    SqliteRunRepository(engine).create(_pending_run())
    sink = SqliteEventSink(engine)

    sink.emit(_envelope())

    with engine.connect() as connection:
        row = connection.execute(select(runs).where(runs.c.run_id == "run-1")).mappings().one()
    assert row["state"] == RunState.RUNNING.value
    assert row["invocation"] == {"cmd": ["pytest", "tests/"]}
    assert row["env_snapshot"] == {"CI": "true"}
    assert row["root_dir"] == "/repo"


def test_emit_test_finished_inserts_test_results_row(engine: Engine) -> None:
    SqliteRunRepository(engine).create(_pending_run())
    sink = SqliteEventSink(engine)

    sink.emit(
        _envelope(
            event_type="test_finished",
            payload={
                "node_id": "tests/test_x.py::test_a",
                "outcome": "passed",
                "duration": 0.02,
                "phases": {"call": {"name": "call", "outcome": "passed", "duration": 0.02}},
            },
        )
    )

    with engine.connect() as connection:
        row = connection.execute(
            select(test_results).where(test_results.c.run_id == "run-1")
        ).mappings().one()
    assert row["node_id"] == "tests/test_x.py::test_a"
    assert row["outcome"] == "passed"
    assert row["duration"] == 0.02


def test_emit_run_discovered_inserts_discovery_row(engine: Engine) -> None:
    SqliteRunRepository(engine).create(_pending_run())
    sink = SqliteEventSink(engine)

    sink.emit(
        _envelope(
            event_type="run_discovered",
            payload={"node_ids": ["tests/test_x.py::test_a", "tests/test_x.py::test_b"]},
        )
    )

    with engine.connect() as connection:
        row = connection.execute(
            select(discovery).where(discovery.c.run_id == "run-1")
        ).mappings().one()
    assert row["node_ids"] == ["tests/test_x.py::test_a", "tests/test_x.py::test_b"]


def test_emit_appends_audit_row_for_every_processed_event(engine: Engine) -> None:
    SqliteRunRepository(engine).create(_pending_run())
    sink = SqliteEventSink(engine)

    sink.emit(_envelope())

    with engine.connect() as connection:
        rows = connection.execute(select(events).where(events.c.run_id == "run-1")).mappings().all()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "run_started"


def test_emit_unknown_event_type_is_skipped_and_does_not_block_next_event(engine: Engine) -> None:
    SqliteRunRepository(engine).create(_pending_run())
    sink = SqliteEventSink(engine)

    sink.emit(_envelope(event_type="totally_unknown", payload={}))
    sink.emit(
        _envelope(
            event_type="run_discovered",
            payload={"node_ids": ["tests/test_x.py::test_a"]},
        )
    )

    with engine.connect() as connection:
        discovery_rows = connection.execute(
            select(discovery).where(discovery.c.run_id == "run-1")
        ).mappings().all()
        event_rows = connection.execute(
            select(events).where(events.c.run_id == "run-1")
        ).mappings().all()
    assert len(discovery_rows) == 1
    # only the valid, processed event is audited -- the unknown one never
    # reaches persistence at all.
    assert len(event_rows) == 1
    assert event_rows[0]["event_type"] == "run_discovered"
