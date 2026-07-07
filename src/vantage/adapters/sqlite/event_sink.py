"""`SqliteEventSink` -- the v1 local-write ingestion adapter (design SS3).
Projects a validated envelope into the appropriate table
(`runs`/`test_results`/`discovery`) and always appends an audit row to
`events`, all within one transaction per `emit` call -- either the whole
event lands, or none of it does.

Contract enforcement (spec Domain 1) happens via
`vantage.core.ingestion.sink.validate_before_dispatch` BEFORE any of that:
- an unsupported `schema_version` raises `ContractVersionError`, which
  propagates -- an explicit rejection, never a silent mis-parse.
- an unrecognized `event_type` raises `UnknownEventTypeError`, which this
  adapter catches, logs, and skips -- isolated so it cannot block
  subsequent valid events for the same or any other run.

`run_finished`/`run_heartbeat` are registered, known event types (they
validate successfully) but have no projection in this slice: run-lifecycle
state transitions belong to the Run Lifecycle domain, owned by later
slices (design SS2 handoff notes; spec Domain -> Slice map). They are still
audited in `events` here; wiring their projection is a TODO for whichever
slice consumes them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast

from pydantic import BaseModel
from sqlalchemy import Connection, Engine, insert, update

from vantage.core.domain.events import (
    EventEnvelope,
    RunDiscoveredPayload,
    RunStartedPayload,
    TestFinishedPayload,
    UnknownEventTypeError,
)
from vantage.core.domain.models import RunState
from vantage.core.ingestion.sink import validate_before_dispatch

from .repository import _to_naive_utc
from .tables import discovery, events, runs, test_results

logger = logging.getLogger(__name__)

_Projector = Callable[[Connection, EventEnvelope, BaseModel], None]


def _project_run_started(
    connection: Connection, envelope: EventEnvelope, payload: BaseModel
) -> None:
    # design §6: the run row is pre-created (by the launcher, out of scope
    # here) before `run_started` arrives; this event fills in runtime
    # details and transitions the run to RUNNING.
    started = cast(RunStartedPayload, payload)
    result = connection.execute(
        update(runs)
        .where(runs.c.run_id == envelope.run_id)
        .values(
            state=RunState.RUNNING.value,
            started_at=_to_naive_utc(envelope.timestamp),
            invocation=dict(started.invocation),
            env_snapshot=dict(started.env_snapshot),
            user=started.user,
            root_dir=started.root_dir,
        )
    )
    if result.rowcount == 0:
        raise LookupError(
            f"run_started for unknown run_id={envelope.run_id!r} "
            "(the run row must already exist)"
        )


def _project_test_finished(
    connection: Connection, envelope: EventEnvelope, payload: BaseModel
) -> None:
    finished = cast(TestFinishedPayload, payload)
    connection.execute(
        insert(test_results).values(
            run_id=envelope.run_id,
            node_id=finished.node_id,
            outcome=finished.outcome,
            duration=finished.duration,
            phases=dict(finished.phases),
            longrepr=finished.longrepr,
            started_at=_to_naive_utc(envelope.timestamp),
        )
    )


def _project_run_discovered(
    connection: Connection, envelope: EventEnvelope, payload: BaseModel
) -> None:
    discovered = cast(RunDiscoveredPayload, payload)
    connection.execute(
        insert(discovery).values(
            run_id=envelope.run_id,
            node_ids=list(discovered.node_ids),
            collected_at=_to_naive_utc(envelope.timestamp),
        )
    )


def _no_projection(connection: Connection, envelope: EventEnvelope, payload: BaseModel) -> None:
    return None


_PROJECTORS: dict[str, _Projector] = {
    "run_started": _project_run_started,
    "test_finished": _project_test_finished,
    "run_discovered": _project_run_discovered,
}


class SqliteEventSink:
    """SQLite implementation of `EventSink` (design SS2, SS3)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def emit(self, envelope: EventEnvelope) -> None:
        try:
            payload = validate_before_dispatch(envelope)
        except UnknownEventTypeError:
            logger.warning(
                "skipping unrecognized event_type=%r for run_id=%s",
                envelope.event_type,
                envelope.run_id,
            )
            return
        # ContractVersionError and pydantic.ValidationError propagate --
        # explicit rejection, no partial persistence (spec Domain 1).

        with self._engine.begin() as connection:
            connection.execute(
                insert(events).values(
                    run_id=envelope.run_id,
                    schema_version=envelope.schema_version,
                    event_type=envelope.event_type,
                    timestamp=_to_naive_utc(envelope.timestamp),
                    payload=envelope.payload,
                )
            )
            projector = _PROJECTORS.get(envelope.event_type, _no_projection)
            projector(connection, envelope, payload)
