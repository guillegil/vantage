"""`GET /api/v1/capabilities` -- the compatibility advertisement (design
decisions D38-D40, `plugin-server-compatibility`).

Mounted only under `/api/v1` by `app.py`'s `include_router(..., prefix=
"/api/v1")`, the same mounting rule `runs.py` follows for the run routes
(RQ-41's third criterion, served by absence): there is no second,
unversioned route and no redirect from one.

Answers exactly one capability, `session_lifecycle`, not a version string
(D38): there is exactly one thing a client needs to know today, and
inventing a version scheme before there is a second capability to name would
be guessing at a shape nothing yet constrains. A client that predates this
route gets a plain `404` from FastAPI's own routing -- no dispatch here --
and D39 makes that `404` the answer a client is expected to read, not a
transport failure to warn about.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/capabilities")
async def capabilities() -> dict[str, bool]:
    return {"session_lifecycle": True}
