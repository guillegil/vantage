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

**Both routes need the record scope** once the server has a user
(`service/access.py`), checked before the body is read, and the editor
role in the run's project. The report that creates a run records who sent
it, and every later report and heartbeat of the run must come from the
same user, or from none if none sent the first: anything else is
`RunOfAnotherUserError`, with nothing stored. The run id is the client's,
so this is what stops one user's session from finishing, or keeping alive,
another's.

**The report names its project**, a top-level `project`, absent meaning
`default`. Once the report is valid, and before anything is stored,
`ingest` hands that name to `_record`'s `admit`: one the server does not
have is `404 unknown_project`, since a server never makes a project from a
report, and one the sender is not an editor of is `403 not_a_member` or
`403 insufficient_role`. Only then does the store answer: a run of another
user is `409 foreign_run`, a report of a run created in another project
`409 project_mismatch`, so a sender who may not record in the project
learns nothing of the runs in it. None of these stores anything.

**The heartbeat's run is resolved before it is touched**
(`requires_record_run`): `404 unknown_run`, then the caller's role in the
run's project, then `409 foreign_run`.

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

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from vantage.core.domain.projects import EDITOR_ROLE
from vantage.core.ports.storage import (
    ExecutionStore,
    ForeignRunError,
    ProjectMismatchError,
    RunDetail,
    UnknownProjectError,
)
from vantage.ingestion import Ingested, ingest
from vantage.ingestion.decode import decode_json
from vantage.service.access import Caller, require_role, requires_record, requires_record_run
from vantage.service.body import read_bounded_body, require_json_media_type
from vantage.service.dependencies import get_store
from vantage.service.errors import (
    MAX_REPORT_BYTES,
    NoSuchProjectError,
    RunOfAnotherProjectError,
    RunOfAnotherUserError,
)
from vantage.service.schemas import Acknowledgement, HeartbeatAcknowledgement

router = APIRouter()


def _record(store: ExecutionStore, body: bytes, caller: Caller) -> Ingested:
    """Parse, validate, convert and store one complete report body, once
    `caller` may record in the project it names. Every step blocks, so
    `create_run` calls this in the threadpool."""
    report = decode_json(body, replace_lone_surrogates=True)

    def admit(project: str) -> None:
        # Projects are never deleted, so one found here is there to record in.
        if store.get_project(project) is None:
            raise NoSuchProjectError(["project"])
        require_role(store, caller, project, EDITOR_ROLE)

    try:
        return ingest(
            report,
            store,
            received_at=datetime.now(timezone.utc),
            recorded_by=caller.user,
            admit=admit,
        )
    except ForeignRunError:
        raise RunOfAnotherUserError() from None
    except UnknownProjectError:
        raise NoSuchProjectError(["project"]) from None
    except ProjectMismatchError:
        raise RunOfAnotherProjectError() from None


@router.post("/runs")
async def create_run(
    request: Request,
    store: ExecutionStore = Depends(get_store),
    caller: Caller = Depends(requires_record),
) -> JSONResponse:
    require_json_media_type(request)
    body = await read_bounded_body(request, MAX_REPORT_BYTES)
    ingested = await run_in_threadpool(_record, store, body, caller)
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
    detail: RunDetail = Depends(requires_record_run),
    caller: Caller = Depends(requires_record),
    store: ExecutionStore = Depends(get_store),
) -> HeartbeatAcknowledgement:
    """Advance the run's last contact.

    The request body is never read, so a heartbeat cannot change a run's
    finish fields. `get_run_detail` (`requires_record_run`), not
    `touch_last_contact`'s boolean, decides the 404: a no-op update means
    either "unknown run" or "a newer contact is already recorded", and only
    the former is a rejection. It also says who recorded the run and in
    which project, neither of which changes once the run exists, so
    checking them first leaves nothing to race. `caller` is the one
    `requires_record_run` authorized: FastAPI asks a dependency once per
    request.
    """
    if detail.recorded_by != caller.user:
        raise RunOfAnotherUserError()

    run_id = detail.execution.identity.value
    store.touch_last_contact(run_id, datetime.now(timezone.utc))
    return HeartbeatAcknowledgement(run_id=run_id, status="acknowledged")
