"""`GET /api/v1/capabilities` -- the compatibility advertisement.

Answers one capability flag, `session_lifecycle`, rather than a version
string: there is only one thing a client needs to know today. A server that
predates this route answers `404` from its router, and clients read that
`404` as "capability absent", not as a transport failure.
Mounted only under `/api/v1`, like every other route. `async`, unlike the
routes that reach the store: it never blocks, so it answers even while
every worker thread is waiting on a slow write.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/capabilities")
async def capabilities() -> dict[str, bool]:
    return {"session_lifecycle": True}
