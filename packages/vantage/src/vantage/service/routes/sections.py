"""The four sections routes: `GET`/`POST`/`DELETE /api/v1/config/sections`
and `GET /api/v1/runs/{run_id}/sections`.

**A section name is never a path segment.** A section name may contain `/`,
and an encoded slash in a path is decoded before routing, so the name
travels as a body field on write and as a query value on delete. `run_id` in the aggregate
route is the same 32-hex identity segment `routes/read.py` uses, so it does
not have that problem.

Each handler binds `store: ExecutionStore = request.app.state.store`, because
`app.state` is untyped and every store call would otherwise go unchecked.

**Section definitions are read fresh on every request, never cached.** No
`app.state` field remembers them between requests, so an edit takes effect
on the very next read, with no restart and no invalidation logic.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse, Response

from vantage.core.domain.execution import IDENTITY_PATTERN
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

router = APIRouter()

TEST_SECTIONS_NAMESPACE = "test_sections"
"""Service vocabulary, not store vocabulary -- the store takes this as an
ordinary namespace parameter and attaches no meaning to it."""


def _load_definitions(store: ExecutionStore) -> list[SectionDefinition]:
    """Every stored section, read fresh on every call -- never cached.
    Raises `UnreadableSettingError` the moment one row's `value` fails
    `SectionValue` or its key is a reserved name, naming the row's key and
    never the value."""
    definitions: list[SectionDefinition] = []
    for setting in store.list_settings(TEST_SECTIONS_NAMESPACE):
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


@router.get("/config/sections")
async def list_sections(request: Request) -> SectionListResponse:
    store: ExecutionStore = request.app.state.store
    definitions = _load_definitions(store)
    items = [SectionResponse(name=d.name, prefix=d.prefix) for d in definitions]
    return SectionListResponse(items=items)


@router.post("/config/sections")
async def upsert_section(request: Request, payload: SectionUpsertRequest) -> Response:
    store: ExecutionStore = request.app.state.store

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
    if not store.delete_setting(TEST_SECTIONS_NAMESPACE, _stored_name(name)):
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
    request: Request, run_id: str = Path(pattern=IDENTITY_PATTERN)
) -> RunSectionSummaryResponse:
    """`GET /api/v1/runs/{run_id}/sections`. An unknown `run_id` is
    `404 unknown_run`, checked the same cheap way `list_results` does
    (`get_execution`, not a full detail read). `summarize_sections` does
    every count and every rounding, once."""
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
