"""The four sections routes: `GET`/`POST`/`DELETE /api/v1/config/sections`
and `GET /api/v1/runs/{run_id}/sections` (design.md D85, D87, D88, D89).

**A section name is never a path segment.** D54 already decided that a
value which may contain `/` cannot ride in a path segment, and a section
name may -- so the name travels as a body field on write and as a query
value on delete, never as `{name}` in the path (design.md D87). All four
routes live in this one module together with the namespace constant and the
definition loader, because they share both. `run_id` is not a section name --
it is the same 32-hex identity segment `routes/read.py` already uses -- so
the aggregate route's path parameter is not the case D54 speaks to.

**One cheap typing improvement, taken only inside this module.**
`request.app.state.store` resolves to `Any` at every pre-existing call site;
each handler here binds it once as `store: ExecutionStore =
request.app.state.store`, restoring checking inside this module without
touching a single existing route (design.md D87).

**Section definitions are read fresh on every call, never cached
(design.md D88).** `_load_definitions` is called once per request by every
handler that needs the current definitions, including the aggregate below --
no `app.state` field remembers them between requests, so an edit takes
effect on the very next read, with no restart and no invalidation logic to
get wrong.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from vantage.core.domain.sections import (
    MAX_SECTIONS,
    SECTION_NAME_MAX_CHARS,
    SECTION_PREFIX_MAX_CHARS,
    UNASSIGNED,
    SectionDefinition,
    SectionSummary,
    normalize_prefix,
    summarize_sections,
)
from vantage.core.ports.storage import ExecutionStore
from vantage.service.errors import (
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

_IDENTITY_PATTERN = r"^[0-9a-f]{32}$"

router = APIRouter()

TEST_SECTIONS_NAMESPACE = "test_sections"
"""Service vocabulary, not store vocabulary (design.md D87) -- the store
takes this as an ordinary namespace parameter and attaches no meaning to it."""


def _load_definitions(store: ExecutionStore) -> list[SectionDefinition]:
    """Every stored section, read fresh on every call (design.md D88) --
    never cached. Raises `UnreadableSettingError` the moment one row's
    `value` fails `SectionValue` (design.md D83), naming the row's key and
    never the value."""
    definitions: list[SectionDefinition] = []
    for setting in store.list_settings(TEST_SECTIONS_NAMESPACE):
        try:
            value = SectionValue.model_validate_json(setting.value)
        except ValidationError as exc:
            raise UnreadableSettingError(setting.namespace, setting.key) from exc
        definitions.append(SectionDefinition(name=setting.key, prefix=value.prefix))
    return definitions


@router.get("/config/sections")
async def list_sections(request: Request) -> SectionListResponse:
    store: ExecutionStore = request.app.state.store
    definitions = _load_definitions(store)
    items = [SectionResponse(name=d.name, prefix=d.prefix) for d in definitions]
    return SectionListResponse(items=items)


@router.post("/config/sections")
async def upsert_section(request: Request, payload: SectionUpsertRequest) -> Response:
    store: ExecutionStore = request.app.state.store

    name = payload.name.strip()
    if not name or len(name) > SECTION_NAME_MAX_CHARS:
        raise InvalidSectionNameError()
    if name.casefold() == UNASSIGNED:
        raise ReservedSectionNameError()

    prefix = payload.prefix.strip()
    if not prefix or len(prefix) > SECTION_PREFIX_MAX_CHARS:
        raise InvalidSectionPrefixError()
    normalized_prefix = normalize_prefix(prefix)

    existing_names = {definition.name for definition in _load_definitions(store)}
    if name not in existing_names and len(existing_names) >= MAX_SECTIONS:
        raise TooManySectionsError()

    value = SectionValue(prefix=normalized_prefix).model_dump_json()
    created = store.upsert_setting(
        TEST_SECTIONS_NAMESPACE, name, value=value, updated_at=datetime.now(timezone.utc)
    )
    body = SectionResponse(name=name, prefix=normalized_prefix)
    return JSONResponse(status_code=201 if created else 200, content=body.model_dump())


@router.delete("/config/sections", status_code=204)
async def delete_section(request: Request, name: str = Query(...)) -> Response:
    store: ExecutionStore = request.app.state.store
    if not store.delete_setting(TEST_SECTIONS_NAMESPACE, name):
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


@router.get("/runs/{run_id}/sections")
async def get_run_sections(
    request: Request, run_id: str = Path(pattern=_IDENTITY_PATTERN)
) -> RunSectionSummaryResponse:
    """`GET /api/v1/runs/{run_id}/sections` (design.md D85, D87, D88). An
    unknown `run_id` is `404 unknown_run`, checked the same cheap way
    `list_results` does (`get_execution`, not a full detail read). Section
    definitions are loaded fresh through `_load_definitions` -- never cached
    -- and `store.get_run_case_outcomes` supplies the aggregate's other
    input; `summarize_sections` does every count and every round, once."""
    store: ExecutionStore = request.app.state.store
    if store.get_execution(run_id) is None:
        raise UnknownRunError()

    definitions = _load_definitions(store)
    case_outcomes = store.get_run_case_outcomes(run_id)
    summary = summarize_sections(case_outcomes, definitions)
    return RunSectionSummaryResponse(
        items=[_section_summary_response(item) for item in summary.items],
        unassigned=_section_summary_response(summary.unassigned),
    )


__all__ = ["TEST_SECTIONS_NAMESPACE", "router"]
