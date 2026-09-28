"""The four sections routes: `GET`/`POST`/`DELETE
/api/v1/projects/{project}/config/sections` and
`GET /api/v1/runs/{run_id}/sections`.

**Sections are a project's**: each project defines its own, bounded by
`MAX_SECTIONS` on its own, and a run's summary uses the sections of the
project the run was created in.

**A section name is never a path segment.** A section name may contain `/`,
and an encoded slash in a path is decoded before routing, so the name
travels as a body field on write and as a query value on delete. `run_id` in the aggregate
route is the same 32-hex identity segment `routes/read.py` uses, so it does
not have that problem.

Every handler reads the store, so each is a plain `def` that FastAPI runs
in its threadpool (see `app.py`) -- except the upsert, which reads its body
the way `POST /runs` does (`service/body.py`): the media type from the
header, then at most `MAX_SECTION_BODY_BYTES` of body on the event loop,
then the parse, validation and store write in the threadpool. Declared as a
parameter instead, the body would be read with no bound and parsed on the
event loop, stalling every other request -- heartbeats included -- for as
long as a large body takes.

**A name is stored as the body decoder leaves it**, U+0000 replaced by
U+FFFD (`ingestion/text.py`), so a delete naming U+0000 matches no section and
is answered without asking the store.

**Reading sections needs the read scope, changing them the admin scope**,
once the server has a user (`service/access.py`): the definitions group
every user's results, so they are changed by those who run the server.

**Section definitions are read fresh on every request, never cached.** No
`app.state` field remembers them between requests, so an edit takes effect
on the very next read, with no restart and no invalidation logic.

**The section bound is checked by the store, in the write itself.** Counting
here and then writing would let two requests racing for the last free slot
both see it free; `upsert_setting`'s `max_keys` makes the count and the
insert one step.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from vantage.core.domain.execution import IDENTITY_PATTERN
from vantage.core.domain.projects import Project
from vantage.core.domain.sections import (
    MAX_SECTIONS,
    SECTION_NAME_MAX_CHARS,
    SECTION_PREFIX_MAX_CHARS,
    SectionDefinition,
    SectionSummary,
    is_reserved_section_name,
    normalize_prefix,
    summarize_sections,
)
from vantage.core.ports.storage import ExecutionStore, NamespaceFullError
from vantage.ingestion.decode import decode_json
from vantage.ingestion.text import NUL
from vantage.service.access import requires_admin_project, requires_read, requires_read_project
from vantage.service.body import read_bounded_body, require_json_media_type
from vantage.service.dependencies import get_store
from vantage.service.errors import (
    InvalidSectionError,
    InvalidSectionNameError,
    InvalidSectionPrefixError,
    ReservedSectionNameError,
    TooManySectionsError,
    UnknownRunError,
    UnknownSectionError,
    UnreadableSettingError,
)
from vantage.service.schemas import (
    RunSectionSummaryResponse,
    SectionListResponse,
    SectionResponse,
    SectionSummaryResponse,
    SectionUpsertRequest,
    SectionValue,
)

router = APIRouter()

TEST_SECTIONS_NAMESPACE = "test_sections"
"""Service vocabulary, not store vocabulary -- the store takes this as an
ordinary namespace parameter and attaches no meaning to it."""

MAX_SECTION_BODY_BYTES = 64 * 1024
"""The upsert body's cap. A name and a prefix at their bounds, every
character written as a 12-byte escaped surrogate pair, take under 14 KiB;
the rest leaves room for whitespace around them."""


def _load_definitions(store: ExecutionStore, project: str) -> list[SectionDefinition]:
    """Every section `project` stores, read fresh on every call -- never cached.
    Raises `UnreadableSettingError` the moment one row's `value` fails
    `SectionValue` or its key is a reserved name, naming the row's key and
    never the value."""
    definitions: list[SectionDefinition] = []
    for setting in store.list_settings(TEST_SECTIONS_NAMESPACE, project=project):
        try:
            value = SectionValue.model_validate_json(setting.value)
            definition = SectionDefinition(name=setting.key, prefix=value.prefix)
        except ValueError as exc:
            # Pydantic's `ValidationError` is a `ValueError`, and so is the
            # domain's refusal of a reserved name a hand-edited row can hold.
            raise UnreadableSettingError(setting.namespace, setting.key) from exc
        definitions.append(definition)
    return definitions


def _stored_name(raw: str) -> str:
    """The key a section name is stored under. Upsert and delete share it,
    so the spelling that created a section also removes it."""
    return raw.strip()


def _encodable(text: str) -> bool:
    """False for text holding a lone surrogate: JSON can escape one
    (`\\ud800`), but no store or response serializer can encode it, so it
    would otherwise surface as a bare `500`."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


@router.get("/projects/{project}/config/sections")
def list_sections(
    project: Project = Depends(requires_read_project),
    store: ExecutionStore = Depends(get_store),
) -> SectionListResponse:
    definitions = _load_definitions(store, project.name)
    items = [SectionResponse(name=d.name, prefix=d.prefix) for d in definitions]
    return SectionListResponse(items=items)


def _upsert(store: ExecutionStore, project: str, body: bytes) -> tuple[bool, SectionResponse]:
    """Parse, validate and store one complete upsert body, and return
    whether the name was new with the section as stored. Every step blocks,
    so `upsert_section` calls this in the threadpool."""
    try:
        payload = SectionUpsertRequest.model_validate(decode_json(body))
    except ValidationError as exc:
        raise InvalidSectionError.from_errors(exc.errors()) from exc

    name = _stored_name(payload.name)
    if not name or len(name) > SECTION_NAME_MAX_CHARS or not _encodable(name):
        raise InvalidSectionNameError()
    if is_reserved_section_name(name):
        raise ReservedSectionNameError()

    prefix = payload.prefix.strip()
    if not prefix or not _encodable(prefix):
        raise InvalidSectionPrefixError()
    # Bounded after normalization: the coerced trailing `/` is part of what
    # is stored and listed, and a listed prefix must post back unchanged.
    normalized_prefix = normalize_prefix(prefix)
    if len(normalized_prefix) > SECTION_PREFIX_MAX_CHARS:
        raise InvalidSectionPrefixError()

    value = SectionValue(prefix=normalized_prefix).model_dump_json()
    try:
        created = store.upsert_setting(
            TEST_SECTIONS_NAMESPACE,
            name,
            project=project,
            value=value,
            updated_at=datetime.now(timezone.utc),
            max_keys=MAX_SECTIONS,
        )
    except NamespaceFullError as exc:
        raise TooManySectionsError() from exc
    return created, SectionResponse(name=name, prefix=normalized_prefix)


@router.post("/projects/{project}/config/sections")
async def upsert_section(
    request: Request,
    project: Project = Depends(requires_admin_project),
    store: ExecutionStore = Depends(get_store),
) -> Response:
    require_json_media_type(request)
    body = await read_bounded_body(request, MAX_SECTION_BODY_BYTES)
    created, section = await run_in_threadpool(_upsert, store, project.name, body)
    return JSONResponse(status_code=201 if created else 200, content=section.model_dump())


@router.delete("/projects/{project}/config/sections", status_code=204)
def delete_section(
    name: str = Query(...),
    project: Project = Depends(requires_admin_project),
    store: ExecutionStore = Depends(get_store),
) -> Response:
    stored_name = _stored_name(name)
    if NUL in stored_name or not store.delete_setting(
        TEST_SECTIONS_NAMESPACE, stored_name, project=project.name
    ):
        raise UnknownSectionError()
    return Response(status_code=204)


def _section_summary_response(summary: SectionSummary) -> SectionSummaryResponse:
    """Field by field, never `model_validate(..., from_attributes=True)` --
    `summary` is the pure core's own dataclass, and `pass_percentage` is
    carried through exactly as `summarize_sections` rounded it, never
    recomputed here."""
    return SectionSummaryResponse(
        name=summary.name,
        total=summary.total,
        measured=summary.measured,
        passing=summary.passing,
        pass_percentage=summary.pass_percentage,
    )


@router.get("/runs/{run_id}/sections", dependencies=[Depends(requires_read)])
def get_run_sections(
    run_id: str = Path(pattern=IDENTITY_PATTERN), store: ExecutionStore = Depends(get_store)
) -> RunSectionSummaryResponse:
    """`GET /api/v1/runs/{run_id}/sections`, against the sections of the
    project the run was created in. An unknown `run_id` is
    `404 unknown_run`. `summarize_sections` does every count and every
    rounding, once."""
    detail = store.get_run_detail(run_id)
    if detail is None:
        raise UnknownRunError()

    definitions = _load_definitions(store, detail.project)
    case_outcomes = store.get_run_case_outcomes(run_id)
    summary = summarize_sections(case_outcomes, definitions)
    return RunSectionSummaryResponse(
        items=[_section_summary_response(item) for item in summary.items],
        unassigned=_section_summary_response(summary.unassigned),
    )


__all__ = ["MAX_SECTION_BODY_BYTES", "TEST_SECTIONS_NAMESPACE", "router"]
