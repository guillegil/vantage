"""Concrete SQLite implementations of the `vantage.core.storage.repository`
Protocols (design SS2). Rows map to/from the plain domain dataclasses by
hand -- deliberately not an ORM (design SS2 decision: SQLAlchemy 2.0 Core,
not ORM, not raw sqlite3).

Each class here satisfies its Protocol *structurally* (duck typing via
`@runtime_checkable`) -- there is no inheritance relationship to
`vantage.core.storage.repository`, which is the point of the seam (design
SS11, decoupling litmus).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.engine import RowMapping

from vantage.core.domain.events import EventEnvelope
from vantage.core.domain.models import Phase, Run, RunState, TestResult
from vantage.core.storage.repository import StoredEvent

from .tables import events, runs, test_results

# States that terminate a run's lifecycle (spec Domain 2 -- Run State
# Machine); reaching one of these sets `finished_at` in `update_state`.
_TERMINAL_STATES = frozenset(
    {RunState.FINISHED, RunState.STOPPED, RunState.FAILED, RunState.FAILED_TO_START}
)


def _to_naive_utc(value: datetime | None) -> datetime | None:
    """SQLite has no native timezone-aware datetime type -- its DBAPI
    driver silently drops `tzinfo` on round-trip. Normalize to naive UTC
    before every write; `_to_aware_utc` re-attaches `UTC` on read so
    callers always get back timezone-aware datetimes, matching the
    ingestion contract's UTC convention (spec Domain 1)."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _to_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC)


def _row_to_run(row: RowMapping) -> Run:
    return Run(
        run_id=row["run_id"],
        state=RunState(row["state"]),
        testpath=row["testpath"],
        user=row["user"],
        created_at=_to_aware_utc(row["created_at"]),  # type: ignore[arg-type]
        started_at=_to_aware_utc(row["started_at"]),
        finished_at=_to_aware_utc(row["finished_at"]),
        exit_code=row["exit_code"],
        stop_reason=row["stop_reason"],
        invocation=row["invocation"] or {},
        env_snapshot=row["env_snapshot"] or {},
        root_dir=row["root_dir"],
        totals=row["totals"] or {},
        last_heartbeat_at=_to_aware_utc(row["last_heartbeat_at"]),
    )


class SqliteRunRepository:
    """SQLite implementation of `RunRepository` (design SS2). `update_state`
    is the single writer of run state, matching design SS6."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, run: Run) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(runs).values(
                    run_id=run.run_id,
                    state=run.state.value,
                    testpath=run.testpath,
                    user=run.user,
                    created_at=_to_naive_utc(run.created_at),
                    started_at=_to_naive_utc(run.started_at),
                    finished_at=_to_naive_utc(run.finished_at),
                    exit_code=run.exit_code,
                    stop_reason=run.stop_reason,
                    invocation=dict(run.invocation),
                    env_snapshot=dict(run.env_snapshot),
                    root_dir=run.root_dir,
                    totals=dict(run.totals),
                    last_heartbeat_at=_to_naive_utc(run.last_heartbeat_at),
                )
            )

    def get(self, run_id: str) -> Run | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(runs).where(runs.c.run_id == run_id)
            ).mappings().first()
        return _row_to_run(row) if row is not None else None

    def list(self, *, limit: int = 50, before: datetime | None = None) -> list[Run]:
        statement = select(runs).order_by(runs.c.created_at.desc()).limit(limit)
        if before is not None:
            statement = statement.where(runs.c.created_at < before)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_row_to_run(row) for row in rows]

    def update_state(
        self,
        run_id: str,
        state: RunState,
        *,
        at: datetime,
        reason: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"state": state.value}
        if state is RunState.RUNNING:
            values["started_at"] = _to_naive_utc(at)
        elif state in _TERMINAL_STATES:
            values["finished_at"] = _to_naive_utc(at)
        if reason is not None:
            values["stop_reason"] = reason

        with self._engine.begin() as connection:
            connection.execute(update(runs).where(runs.c.run_id == run_id).values(**values))


class SqliteResultRepository:
    """SQLite implementation of `ResultRepository` (design SS2)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, result: TestResult) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(test_results).values(
                    run_id=result.run_id,
                    node_id=result.node_id,
                    outcome=result.outcome,
                    duration=result.duration,
                    phases=_phases_to_json(result.phases),
                    longrepr=result.longrepr,
                    started_at=_to_naive_utc(result.started_at),
                )
            )

    def list_for_run(self, run_id: str) -> list[TestResult]:
        statement = select(test_results).where(test_results.c.run_id == run_id)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_row_to_test_result(row) for row in rows]


def _phases_to_json(phases: Mapping[str, Phase]) -> dict[str, dict[str, Any]]:
    return {
        name: {"name": phase.name, "outcome": phase.outcome, "duration": phase.duration}
        for name, phase in phases.items()
    }


def _json_to_phases(raw: Mapping[str, Any] | None) -> dict[str, Phase]:
    if not raw:
        return {}
    return {name: Phase(**value) for name, value in raw.items()}


def _row_to_test_result(row: RowMapping) -> TestResult:
    return TestResult(
        id=row["id"],
        run_id=row["run_id"],
        node_id=row["node_id"],
        outcome=row["outcome"],
        duration=row["duration"],
        phases=_json_to_phases(row["phases"]),
        longrepr=row["longrepr"],
        started_at=_to_aware_utc(row["started_at"]),
    )


class SqliteEventLog:
    """SQLite implementation of `EventLog` (design SS2, SS5). `append`
    returns the row's autoincrement `seq`, which is also the SSE poll
    cursor consumed by the streaming service (design SS5)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, envelope: EventEnvelope) -> int:
        with self._engine.begin() as connection:
            result = connection.execute(
                insert(events).values(
                    run_id=envelope.run_id,
                    schema_version=envelope.schema_version,
                    event_type=envelope.event_type,
                    timestamp=_to_naive_utc(envelope.timestamp),
                    payload=envelope.payload,
                )
            )
            inserted_pk = result.inserted_primary_key
        assert inserted_pk is not None
        return int(inserted_pk[0])

    def since(self, run_id: str, after_seq: int) -> list[StoredEvent]:
        statement = (
            select(events)
            .where(events.c.run_id == run_id, events.c.seq > after_seq)
            .order_by(events.c.seq)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [
            StoredEvent(
                seq=row["seq"],
                envelope=EventEnvelope(
                    schema_version=row["schema_version"],
                    event_type=row["event_type"],
                    run_id=row["run_id"],
                    timestamp=_to_aware_utc(row["timestamp"]),  # type: ignore[arg-type]
                    payload=row["payload"],
                ),
            )
            for row in rows
        ]
