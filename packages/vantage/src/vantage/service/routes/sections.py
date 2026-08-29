"""The definitions API: `GET`/`POST`/`DELETE /api/v1/config/sections`
(design.md D87, D89).

**A section name is never a path segment.** D54 already decided that a
value which may contain `/` cannot ride in a path segment, and a section
name may -- so the name travels as a body field on write and as a query
value on delete, never as `{name}` in the path (design.md D87). All three
routes live in this one module together with the namespace constant and the
definition loader, because they share both.

**One cheap typing improvement, taken only inside this module.**
`request.app.state.store` resolves to `Any` at every pre-existing call site;
each handler here binds it once as `store: ExecutionStore =
request.app.state.store`, restoring checking inside this module without
touching a single existing route (design.md D87).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from vantage.core.domain.sections import (
    MAX_SECTIONS,
    SECTION_NAME_MAX_CHARS,
    SECTION_PREFIX_MAX_CHARS,
    UNASSIGNED,
    SectionDefinition,
    normalize_prefix,
)
from vantage.core.ports.storage import ExecutionStore
from vantage.service.errors import (
    InvalidSectionNameError,
    InvalidSectionPrefixError,
    ReservedSectionNameError,
    TooManySectionsError,
    UnknownSectionError,
    UnreadableSettingError,
)
from vantage.service.schemas import (
    SectionListResponse,
    SectionResponse,
    SectionUpsertRequest,
    SectionValue,
)

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


__all__ = ["TEST_SECTIONS_NAMESPACE", "router"]
