"""`POST /api/v1/runs` -- session report ingestion -- and the run heartbeat.

**The media type and size checks run before the body is buffered.** The
route does not declare `payload: SessionReport` as a parameter, because
FastAPI would then read and parse the whole body before the first line
runs. `service/body.py` checks `Content-Type` from the header and streams
the body under `MAX_REPORT_BYTES`; only then is it parsed. Nothing is
written unless all three succeed. Only the streaming is `async`: the parse
and `vantage.ingestion.ingest` -- validation, the conversion, declared YAML
metadata included, and the store write -- run in the threadpool, off the
event loop every request shares.

**201 versus 200 comes from `Ingested.created`**, which the store decides
inside its own write transaction. Asking the store whether the id exists
before calling it would reintroduce a check-then-act race at the HTTP layer.

**A lone surrogate is replaced, not rejected.** A `\\udXXX` escape with no
partner is valid JSON, and pytest produces such text itself from file names
decoded with `surrogateescape`. It cannot be encoded as UTF-8, so it would
fail the first encode on the way to storage; rejecting the report instead
would lose the whole session over one character. `decode_json` replaces
every one, in keys and values alike, with U+FFFD before validation, together
with every U+0000, which PostgreSQL cannot store.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Path, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from vantage.core.domain.execution import IDENTITY_PATTERN
from vantage.core.ports.storage import ExecutionStore
from vantage.ingestion import Ingested, ingest
from vantage.ingestion.decode import decode_json
from vantage.service.body import read_bounded_body, require_json_media_type
from vantage.service.dependencies import get_store
from vantage.service.errors import MAX_REPORT_BYTES, UnknownRunError
from vantage.service.schemas import Acknowledgement, HeartbeatAcknowledgement

router = APIRouter()


def _record(store: ExecutionStore, body: bytes) -> Ingested:
    """Parse, validate, convert and store one complete report body. Every
    step blocks, so `create_run` calls this in the threadpool."""
    report = decode_json(body, replace_lone_surrogates=True)
    return ingest(report, store, received_at=datetime.now(timezone.utc))


@router.post("/runs")
async def create_run(request: Request, store: ExecutionStore = Depends(get_store)) -> JSONResponse:
    require_json_media_type(request)
    body = await read_bounded_body(request, MAX_REPORT_BYTES)
    ingested = await run_in_threadpool(_record, store, body)
    acknowledgement = Acknowledgement(
        run_id=ingested.run_id,
        status="created" if ingested.created else "duplicate",
        ignored=list(ingested.ignored),
    )
    return JSONResponse(
        status_code=201 if ingested.created else 200,
        content=acknowledgement.model_dump(),
    )


@router.post("/runs/{run_id}/heartbeat")
def heartbeat(
    run_id: str = Path(pattern=IDENTITY_PATTERN), store: ExecutionStore = Depends(get_store)
) -> HeartbeatAcknowledgement:
    """Advance `run_id`'s last contact.

    The request body is never read, so a heartbeat cannot change a run's
    finish fields. `get_execution`, not `touch_last_contact`'s boolean,
    decides the 404: a no-op update means either "unknown run" or "a newer
    contact is already recorded", and only the former is a rejection.
    """
    if store.get_execution(run_id) is None:
        raise UnknownRunError()

    store.touch_last_contact(run_id, datetime.now(timezone.utc))
    return HeartbeatAcknowledgement(run_id=run_id, status="acknowledged")
