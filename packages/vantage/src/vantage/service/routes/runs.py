"""`POST /api/v1/runs` -- session report ingestion (RQ-41, design.md D3).

**The 201-vs-200 decision comes from the boolean `record_execution` already
returns.** `record_execution` is `INSERT ... ON CONFLICT(id) DO NOTHING`,
deciding its own return value from the INSERT's own row count -- no
preceding `SELECT`. This route does not ask the store whether the id exists
and then decide: that would reintroduce, at the HTTP layer, precisely the
check-then-act race the storage adapter's `ON CONFLICT` avoids at the SQL
layer. One call, one boolean, one branch.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from vantage.core.domain.execution import Execution, Identity
from vantage.service.schemas import Acknowledgement, RunReport, SessionReport

router = APIRouter()


def _to_execution(run: RunReport) -> Execution:
    return Execution(
        identity=Identity(run.id),
        started_at=run.started_at,
        finished_at=run.finished_at,
        exit_status=run.exit_status,
        interrupted=run.interrupted,
        interrupt_reason=run.interrupt_reason,
    )


@router.post("/runs")
def create_run(payload: SessionReport, request: Request) -> JSONResponse:
    store = request.app.state.store
    execution = _to_execution(payload.run)

    created = store.record_execution(execution, received_at=datetime.now(timezone.utc))

    acknowledgement = Acknowledgement(
        run_id=payload.run.id,
        status="created" if created else "duplicate",
        ignored=[],
    )
    return JSONResponse(
        status_code=201 if created else 200,
        content=acknowledgement.model_dump(),
    )
