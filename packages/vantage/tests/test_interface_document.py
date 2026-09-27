"""The hand-written OpenAPI document, its drift checks, and the generated
documents being disabled.

**The document is authored by hand, never generated from `app.routes`.** A
document derived from the route table it is checked against could never fail
its own drift check. `test_a_served_but_undocumented_route_is_reported` and
`test_a_documented_but_unserved_path_is_reported` each prove their direction
can fail, not only that it currently passes.

`GET /api/v1/openapi.yaml` is itself declared `read` in the document, so it
is exercised by `test_every_documented_path_answers_2xx` like every other
read path. The rejections are exercised the same way: every status a probe
gets back must be one its operation lists.

The schema tests at the end of this module check `components.schemas`: they
read the declared schemas out of the parsed document and the field set out
of `model_fields` **independently**, then compare. Generating one side from
the other would make these checks unfailable for the same reason.
"""

from __future__ import annotations

import importlib.resources
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args

import httpx2 as httpx
import yaml
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from memory_store import InMemoryExecutionStore
from pydantic import BaseModel
from vantage.core.domain.access import RECORD_SCOPE, SCOPES, new_token, token_digest
from vantage.core.domain.liveness import PRESENTATIONS
from vantage.core.domain.metadata import (
    FILE_STATUSES,
    KEY_STATUSES,
    METADATA_CONTENT_TYPES,
    METADATA_SOURCES,
)
from vantage.core.domain.result import OUTCOMES
from vantage.ingestion.schemas import (
    MetadataFileReport,
    MetadataKeyReport,
    MetadataReport,
    MetadataValueReport,
    ResultReport,
    RunReport,
    SessionReport,
    VcsReport,
)
from vantage.service.app import create_app
from vantage.service.errors import MAX_REPORT_BYTES
from vantage.service.routes.sections import MAX_SECTION_BODY_BYTES
from vantage.service.schemas import (
    Acknowledgement,
    FailureProjectionResponse,
    HeartbeatAcknowledgement,
    HistoryEntryResponse,
    HistoryResponse,
    MetadataFileResponse,
    MetadataHorizonResponse,
    MetadataItemResponse,
    RejectionResponse,
    ResultDetailResponse,
    ResultListItemResponse,
    ResultsResponse,
    RunDetailResponse,
    RunListItemResponse,
    RunListResponse,
    RunMetadataResponse,
    RunSectionSummaryResponse,
    RunVcsResponse,
    SectionListResponse,
    SectionResponse,
    SectionSummaryResponse,
    SectionUpsertRequest,
)
from vantage.storage.sqlite_store import SqliteExecutionStore

_DOCUMENT_BYTES = (
    importlib.resources.files("vantage.service.openapi").joinpath("v1.yaml").read_bytes()
)
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_Call = Callable[[], httpx.Response]


def _parsed_document() -> dict[str, Any]:
    return dict(yaml.safe_load(_DOCUMENT_BYTES))


def _declared_operations(document: Mapping[str, Any]) -> set[tuple[str, str]]:
    """`(METHOD, path)` pairs the document declares, `path` relative to the
    document's own `servers: [{url: /api/v1}]` entry -- the same form
    `_mounted_operations` produces after stripping that prefix."""
    return {
        (method.upper(), path)
        for path, operations in document["paths"].items()
        for method in operations
    }


def _report(run_id: str) -> dict[str, Any]:
    """A minimal well-formed `SessionReport` body. `results` is optional and
    omitted: `list_history`/`list_results` answer `200` with an empty page
    for a run/node id with no results."""
    return {
        "run": {
            "id": run_id,
            "started_at": "2026-08-15T09:14:02+00:00",
            "finished_at": "2026-08-15T09:14:47+00:00",
            "exit_status": 0,
            "interrupted": False,
            "interrupt_reason": None,
        }
    }


def _mounted_operations(app: FastAPI) -> set[tuple[str, str]]:
    """`(METHOD, path)` pairs actually mounted on `app`, `/api/v1` stripped
    to match `_declared_operations`'s form.

    Reads `app.openapi()` -- FastAPI's own computed schema of what is
    mounted -- rather than walking `app.routes` by hand: this FastAPI
    version resolves included routers lazily behind a private
    `_IncludedRouter` wrapper, so `app.routes` no longer yields flat
    `APIRoute` instances the way earlier versions did, and `.openapi()` is
    the public, version-stable way to ask "what is actually mounted."
    This does not generate the document from the routes: it only reads the
    *mounted* side of the comparison, never replaces `v1.yaml`, and the HTTP
    endpoint that would have served it is disabled (`openapi_url=None` in
    `create_app`)."""
    schema = app.openapi()
    operations: set[tuple[str, str]] = set()
    for path, methods_at_path in schema.get("paths", {}).items():
        relative_path = path.removeprefix("/api/v1")
        for method in methods_at_path:
            if method.lower() in _HTTP_METHODS:
                operations.add((method.upper(), relative_path))
    return operations


def test_openapi_yaml_serves_the_handwritten_bytes() -> None:
    """`GET /api/v1/openapi.yaml` serves `v1.yaml` byte for byte as
    `application/yaml`. `pyyaml` parses it only in tests, never at runtime."""
    client = TestClient(create_app(InMemoryExecutionStore()))

    response = client.get("/api/v1/openapi.yaml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/yaml")
    assert response.content == _DOCUMENT_BYTES
    parsed = yaml.safe_load(response.content)
    assert parsed["openapi"].startswith("3.1")


def test_generated_documents_are_disabled() -> None:
    """FastAPI's generated `/openapi.json`, `/docs` and `/redoc` are off."""
    client = TestClient(create_app(InMemoryExecutionStore()))

    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_a_served_but_undocumented_route_is_reported() -> None:
    """Half 1 proves the real app is drift-free; half 2 mounts one extra
    route the document never declares and proves the check reports it --
    the check must be able to fail before it can be trusted."""
    declared = _declared_operations(_parsed_document())
    real_app = create_app(InMemoryExecutionStore())
    assert _mounted_operations(real_app) - declared == set()

    extra_router = APIRouter()

    @extra_router.get("/api/v1/_undocumented-probe")
    async def _probe() -> dict[str, bool]:
        return {"ok": True}

    tainted_app = create_app(InMemoryExecutionStore())
    tainted_app.include_router(extra_router)

    undeclared = _mounted_operations(tainted_app) - declared
    assert ("GET", "/_undocumented-probe") in undeclared


def test_a_documented_but_unserved_path_is_reported() -> None:
    """The reverse direction, `declared - mounted`. Half 1: against the real
    app, empty. Half 2: a document copy carrying one path the app never
    mounts, read through the same `_declared_operations`, proving this
    direction can fail too."""
    mounted = _mounted_operations(create_app(InMemoryExecutionStore()))

    assert _declared_operations(_parsed_document()) - mounted == set()

    tainted = _parsed_document()
    tainted["paths"]["/_never-mounted-probe"] = {"get": {"responses": {}}}
    assert _declared_operations(tainted) - mounted == {("GET", "/_never-mounted-probe")}


def test_every_read_operation_is_get_and_every_write_operation_is_not() -> None:
    """Every `read`-tagged operation is a GET, no `write`-tagged one is, and
    the ingestion endpoints are tagged `write`."""
    document = _parsed_document()
    read_ops: set[tuple[str, str]] = set()
    write_ops: set[tuple[str, str]] = set()
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            tags = operation.get("tags", [])
            if "read" in tags:
                read_ops.add((method.upper(), path))
            if "write" in tags:
                write_ops.add((method.upper(), path))

    assert read_ops, "no read-tagged operation declared"
    assert all(method == "GET" for method, _ in read_ops)
    assert all(method != "GET" for method, _ in write_ops)
    assert ("POST", "/runs") in write_ops
    assert ("POST", "/runs/{run_id}/heartbeat") in write_ops


def test_every_documented_path_answers_2xx(tmp_path: Path) -> None:
    """A binding table maps every `(method, path)` the document declares to
    a callable producing valid parameters, driven against a dedicated
    fixture database. Includes `GET /api/v1/capabilities` and
    `GET /api/v1/openapi.yaml` themselves."""
    document = _parsed_document()
    declared = _declared_operations(document)
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    client = TestClient(create_app(store))
    run = f"/api/v1/runs/{'6' * 32}"
    node_id = "tests/test_interface_document_probe.py::test_x"
    report = _report(run.rsplit("/", 1)[-1])
    # A single passing result, present so `GET /runs/{run_id}/result` has
    # something to bind against.
    report["results"] = [
        {
            "node_id": node_id,
            "file_path": "tests/test_interface_document_probe.py",
            "class_name": None,
            "function_name": "test_x",
            "param_id": None,
            "outcome": "passed",
            "duration": 0.01,
            "started_at": "2026-08-15T09:14:10+00:00",
            "finished_at": "2026-08-15T09:14:10+00:00",
            "setup_outcome": "passed",
            "call_outcome": "passed",
            "teardown_outcome": "passed",
            "setup_duration": 0.001,
            "call_duration": 0.001,
            "teardown_duration": 0.001,
            "worker_id": None,
        }
    ]

    # Ordered so the fixture data a later binding needs already exists --
    # the run must be reported before it can be read back or heartbeat'd.
    ordered_bindings: list[tuple[tuple[str, str], _Call]] = [
        (("POST", "/runs"), lambda: client.post("/api/v1/runs", json=report)),
        (("POST", "/runs/{run_id}/heartbeat"), lambda: client.post(f"{run}/heartbeat")),
        (("GET", "/runs"), lambda: client.get("/api/v1/runs")),
        (("GET", "/runs/{run_id}"), lambda: client.get(run)),
        (("GET", "/runs/{run_id}/metadata"), lambda: client.get(f"{run}/metadata")),
        (("GET", "/runs/{run_id}/results"), lambda: client.get(f"{run}/results")),
        (
            ("GET", "/runs/{run_id}/result"),
            lambda: client.get(f"{run}/result", params={"node_id": node_id}),
        ),
        (
            ("GET", "/tests/history"),
            lambda: client.get("/api/v1/tests/history", params={"node_id": node_id}),
        ),
        (("GET", "/capabilities"), lambda: client.get("/api/v1/capabilities")),
        (("GET", "/openapi.yaml"), lambda: client.get("/api/v1/openapi.yaml")),
        (
            ("POST", "/config/sections"),
            lambda: client.post(
                "/api/v1/config/sections",
                json={"name": "InterfaceProbe", "prefix": "tests/interface-probe"},
            ),
        ),
        (("GET", "/config/sections"), lambda: client.get("/api/v1/config/sections")),
        (
            ("GET", "/runs/{run_id}/sections"),
            lambda: client.get(f"{run}/sections"),
        ),
        (
            ("DELETE", "/config/sections"),
            lambda: client.delete("/api/v1/config/sections", params={"name": "InterfaceProbe"}),
        ),
    ]
    bound_keys = {key for key, _ in ordered_bindings}
    assert bound_keys == declared, "binding table does not cover every documented path"

    try:
        for key, call in ordered_bindings:
            response = call()
            assert 200 <= response.status_code < 300, (key, response.text)
    finally:
        store.close()


# --- Status codes -----------------------------------------------------------


def _probes(client: TestClient) -> list[tuple[tuple[str, str], _Call]]:
    """One request per rejection the server can give on each documented
    operation, against an empty store. The unreadable-setting 500 needs a
    corrupted row and is left out."""
    json_header = {"content-type": "application/json"}
    known_shape = f"/api/v1/runs/{'7' * 32}"
    malformed = "/api/v1/runs/NOT-AN-ID"
    node = {"node_id": "tests/test_a.py::test_one"}
    return [
        (
            ("POST", "/runs"),
            lambda: client.post("/api/v1/runs", content=b"{}", headers={"content-type": "x/y"}),
        ),
        (("POST", "/runs"), lambda: client.post("/api/v1/runs", content=b"{", headers=json_header)),
        (
            ("POST", "/runs"),
            lambda: client.post(
                "/api/v1/runs", content=b" " * (MAX_REPORT_BYTES + 1), headers=json_header
            ),
        ),
        (
            ("POST", "/runs"),
            lambda: client.post("/api/v1/runs", content=b"{}", headers=json_header),
        ),
        (("GET", "/runs"), lambda: client.get("/api/v1/runs", params={"limit": 0})),
        (("GET", "/runs"), lambda: client.get("/api/v1/runs", params={"metadata_key": "k"})),
        (("GET", "/runs/{run_id}"), lambda: client.get(known_shape)),
        (("GET", "/runs/{run_id}"), lambda: client.get(malformed)),
        (("GET", "/runs/{run_id}/metadata"), lambda: client.get(f"{known_shape}/metadata")),
        (("GET", "/runs/{run_id}/metadata"), lambda: client.get(f"{malformed}/metadata")),
        (("POST", "/runs/{run_id}/heartbeat"), lambda: client.post(f"{known_shape}/heartbeat")),
        (("POST", "/runs/{run_id}/heartbeat"), lambda: client.post(f"{malformed}/heartbeat")),
        (("GET", "/runs/{run_id}/results"), lambda: client.get(f"{known_shape}/results")),
        (("GET", "/runs/{run_id}/results"), lambda: client.get(f"{malformed}/results")),
        (
            ("GET", "/runs/{run_id}/results"),
            lambda: client.get(f"{known_shape}/results", params={"limit": 0}),
        ),
        (
            ("GET", "/runs/{run_id}/result"),
            lambda: client.get(f"{known_shape}/result", params=node),
        ),
        (("GET", "/runs/{run_id}/result"), lambda: client.get(f"{malformed}/result", params=node)),
        (("GET", "/runs/{run_id}/result"), lambda: client.get(f"{known_shape}/result")),
        (("GET", "/tests/history"), lambda: client.get("/api/v1/tests/history")),
        (("GET", "/runs/{run_id}/sections"), lambda: client.get(f"{known_shape}/sections")),
        (("GET", "/runs/{run_id}/sections"), lambda: client.get(f"{malformed}/sections")),
        (
            ("POST", "/config/sections"),
            lambda: client.post("/api/v1/config/sections", json={"name": "", "prefix": "tests"}),
        ),
        (("POST", "/config/sections"), lambda: client.post("/api/v1/config/sections", json={})),
        (
            ("POST", "/config/sections"),
            lambda: client.post(
                "/api/v1/config/sections", content=b"{}", headers={"content-type": "x/y"}
            ),
        ),
        (
            ("POST", "/config/sections"),
            lambda: client.post("/api/v1/config/sections", content=b"{", headers=json_header),
        ),
        (
            ("POST", "/config/sections"),
            lambda: client.post(
                "/api/v1/config/sections",
                content=b" " * (MAX_SECTION_BODY_BYTES + 1),
                headers=json_header,
            ),
        ),
        (
            ("DELETE", "/config/sections"),
            lambda: client.delete("/api/v1/config/sections", params={"name": "never-stored"}),
        ),
        (("DELETE", "/config/sections"), lambda: client.delete("/api/v1/config/sections")),
    ]


def _undocumented_statuses(
    document: Mapping[str, Any], observed: set[tuple[str, str, int]]
) -> set[tuple[str, str, int]]:
    return {
        (method, path, status)
        for method, path, status in observed
        if str(status) not in document["paths"][path][method.lower()]["responses"]
    }


def test_every_status_the_server_answers_is_documented() -> None:
    """A generated client decides what to handle from the listed statuses,
    so each rejection the server gives must be listed. Half 2 removes one
    code from a copy of the document and proves the check reports it."""
    client = TestClient(create_app(InMemoryExecutionStore()))
    observed = {(*key, call().status_code) for key, call in _probes(client)}
    assert all(status >= 400 for _, _, status in observed)

    assert _undocumented_statuses(_parsed_document(), observed) == set()

    tainted = _parsed_document()
    del tainted["paths"]["/runs"]["post"]["responses"]["415"]
    assert _undocumented_statuses(tainted, observed) == {("POST", "/runs", 415)}


_ACCESS_RUN = "5" * 32


def _access_requests(client: TestClient, headers: dict[str, str]) -> dict[tuple[str, str], _Call]:
    """One request per documented operation, sending `headers`, in an order
    where each finds what it needs: the run reported first, the section
    posted before it is deleted."""
    run = f"/api/v1/runs/{_ACCESS_RUN}"
    node = {"node_id": "tests/test_a.py::test_one"}
    section = {"name": "AccessProbe", "prefix": "tests/access-probe"}
    report = _report(_ACCESS_RUN)
    report["results"] = [
        {
            "node_id": node["node_id"],
            "file_path": "tests/test_a.py",
            "class_name": None,
            "function_name": "test_one",
            "param_id": None,
            "outcome": "passed",
            "duration": None,
            "started_at": None,
            "finished_at": None,
            "setup_outcome": None,
            "call_outcome": None,
            "teardown_outcome": None,
            "setup_duration": None,
            "call_duration": None,
            "teardown_duration": None,
            "worker_id": None,
        }
    ]
    return {
        ("POST", "/runs"): lambda: client.post("/api/v1/runs", json=report, headers=headers),
        ("POST", "/runs/{run_id}/heartbeat"): lambda: client.post(
            f"{run}/heartbeat", headers=headers
        ),
        ("GET", "/runs"): lambda: client.get("/api/v1/runs", headers=headers),
        ("GET", "/runs/{run_id}"): lambda: client.get(run, headers=headers),
        ("GET", "/runs/{run_id}/metadata"): lambda: client.get(f"{run}/metadata", headers=headers),
        ("GET", "/runs/{run_id}/results"): lambda: client.get(f"{run}/results", headers=headers),
        ("GET", "/runs/{run_id}/result"): lambda: client.get(
            f"{run}/result", params=node, headers=headers
        ),
        ("GET", "/tests/history"): lambda: client.get(
            "/api/v1/tests/history", params=node, headers=headers
        ),
        ("GET", "/runs/{run_id}/sections"): lambda: client.get(f"{run}/sections", headers=headers),
        ("GET", "/capabilities"): lambda: client.get("/api/v1/capabilities", headers=headers),
        ("GET", "/openapi.yaml"): lambda: client.get("/api/v1/openapi.yaml", headers=headers),
        ("POST", "/config/sections"): lambda: client.post(
            "/api/v1/config/sections", json=section, headers=headers
        ),
        ("GET", "/config/sections"): lambda: client.get("/api/v1/config/sections", headers=headers),
        ("DELETE", "/config/sections"): lambda: client.delete(
            "/api/v1/config/sections", params={"name": section["name"]}, headers=headers
        ),
    }


def _documented_scope(operation: Mapping[str, Any]) -> str | None:
    """The scope an operation's `403` says a token must grant, or `None`
    for one whose `security` is empty: served without a token."""
    if operation.get("security") == []:
        return None
    match = re.search(r"grant the (\w+) scope", operation["responses"]["403"]["description"])
    assert match is not None, operation["operationId"]
    return str(match.group(1))


def test_every_operation_needs_the_scope_its_document_names() -> None:
    """On a server with users, driven by the document: an operation whose
    `security` is empty answers without a token; every other one refuses a
    request without one (401) and one granting every scope but the one its
    `403` names (403), and takes one granting that scope. A report or
    heartbeat of another user's run is 409. Every status is documented, so
    a route added without its scope, or a document that names the wrong
    one, fails here."""
    store = InMemoryExecutionStore()
    now = datetime.now(timezone.utc)
    store.create_user("alice", admin=True, created_at=now)
    store.create_user("bob", admin=False, created_at=now)

    def bearer(user: str, scopes: set[str]) -> dict[str, str]:
        token = new_token()
        store.create_token(
            user, digest=token_digest(token), label="", scopes=frozenset(scopes), created_at=now
        )
        return {"Authorization": f"Bearer {token}"}

    client = TestClient(create_app(store))
    document = _parsed_document()
    anonymous = _access_requests(client, {})
    assert set(anonymous) == _declared_operations(document)
    observed: set[tuple[str, str, int]] = set()

    for key, call in anonymous.items():
        method, path = key
        scope = _documented_scope(document["paths"][path][method.lower()])
        answered = {"none": call().status_code}
        if scope is not None:
            lacking = bearer("alice", set(SCOPES) - {scope})
            holding = bearer("alice", {scope})
            answered["lacking"] = _access_requests(client, lacking)[key]().status_code
            answered["holding"] = _access_requests(client, holding)[key]().status_code
            assert answered["none"] == 401, (key, answered)
            assert answered["lacking"] == 403, (key, answered)
            assert 200 <= answered["holding"] < 300, (key, answered)
        else:
            assert answered["none"] == 200, (key, answered)
        observed |= {(method, path, status) for status in answered.values() if status >= 400}

    bobs = bearer("bob", {RECORD_SCOPE})
    for key in (("POST", "/runs"), ("POST", "/runs/{run_id}/heartbeat")):
        status = _access_requests(client, bobs)[key]().status_code
        assert status == 409, key
        observed.add((*key, status))

    assert _undocumented_statuses(document, observed) == set()


def test_every_response_declares_its_body() -> None:
    """Every rejection carries the `Rejection` body, and every other
    response but a `204` states what it returns."""
    rejection = {"application/json": {"schema": {"$ref": "#/components/schemas/Rejection"}}}
    for path, operations in _parsed_document()["paths"].items():
        for method, operation in operations.items():
            for status, response in operation["responses"].items():
                where = f"{method.upper()} {path} {status}"
                if int(status) >= 400:
                    assert response.get("content") == rejection, where
                elif status != "204":
                    assert response.get("content"), where


def test_the_capabilities_schema_matches_what_the_server_answers() -> None:
    """Declared inline, since no model produces this body."""
    responses = _parsed_document()["paths"]["/capabilities"]["get"]["responses"]
    schema = responses["200"]["content"]["application/json"]["schema"]

    body = TestClient(create_app(InMemoryExecutionStore())).get("/api/v1/capabilities").json()

    assert set(schema["required"]) == set(schema["properties"]) == set(body)
    assert all(schema["properties"][name] == {"type": "boolean"} for name in body)
    assert all(isinstance(value, bool) for value in body.values())


def _report_with(run_id: str, field: str, value: int) -> dict[str, Any]:
    """`_report` with `value` at `run.exit_status`, or at `field` on one
    result."""
    report = _report(run_id)
    if field == "exit_status":
        report["run"]["exit_status"] = value
        return report
    result = {
        "node_id": "tests/test_bounds.py::test_x",
        "file_path": "tests/test_bounds.py",
        "class_name": None,
        "function_name": "test_x",
        "param_id": None,
        "outcome": "failed",
        "duration": None,
        "started_at": None,
        "finished_at": None,
        "setup_outcome": None,
        "call_outcome": None,
        "teardown_outcome": None,
        "setup_duration": None,
        "call_duration": None,
        "teardown_duration": None,
        "worker_id": None,
        field: value,
    }
    report["results"] = [result]
    return report


def test_every_documented_integer_bound_is_the_one_the_server_enforces(tmp_path: Path) -> None:
    """The document states the signed 64-bit range SQLite can bind. Each
    bound is read from the document, then sent to a real store: the bound
    itself is accepted and one past it is refused, never a `500`."""
    document = _parsed_document()
    schemas = document["components"]["schemas"]
    offset = document["components"]["parameters"]["offset"]["schema"]
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    client = TestClient(create_app(store))
    try:
        assert client.get("/api/v1/runs", params={"offset": offset["maximum"]}).status_code == 200
        past = client.get("/api/v1/runs", params={"offset": offset["maximum"] + 1})
        assert past.status_code == 422

        cases = [
            (field, value, expected)
            for schema, field in (("RunReport", "exit_status"), ("ResultReport", "failure_lineno"))
            for value, expected in (
                (schemas[schema]["properties"][field]["minimum"], 201),
                (schemas[schema]["properties"][field]["maximum"], 201),
                (schemas[schema]["properties"][field]["minimum"] - 1, 422),
                (schemas[schema]["properties"][field]["maximum"] + 1, 422),
            )
        ]
        for case, (field, value, expected) in enumerate(cases, start=1):
            response = client.post("/api/v1/runs", json=_report_with(f"{case:032x}", field, value))
            assert response.status_code == expected, (field, value)
    finally:
        store.close()


# --- Schema checks ----------------------------------------------------------

# `v1.yaml`'s schema name -> the model that produces or accepts that shape.
# Split by direction because `required` means two different things either
# way: on a request body it is what the client must send, so a field with a
# default is optional; on a response body it is what the client can rely on
# being present, and Pydantic serializes every field of a response model
# regardless of whether it was set, so every field is required. Collapsing
# the two would silently excuse `Acknowledgement.ignored`, which has a
# default and is nonetheless always on the wire.
_REQUEST_SCHEMAS: dict[str, type[BaseModel]] = {
    "SessionReport": SessionReport,
    "RunReport": RunReport,
    "ResultReport": ResultReport,
    "VcsReport": VcsReport,
    "MetadataReport": MetadataReport,
    "MetadataKeyReport": MetadataKeyReport,
    "MetadataFileReport": MetadataFileReport,
    "MetadataValueReport": MetadataValueReport,
    "SectionUpsertRequest": SectionUpsertRequest,
}
_RESPONSE_SCHEMAS: dict[str, type[BaseModel]] = {
    "Rejection": RejectionResponse,
    "Acknowledgement": Acknowledgement,
    "HeartbeatAcknowledgement": HeartbeatAcknowledgement,
    "RunVcs": RunVcsResponse,
    "RunListItem": RunListItemResponse,
    "RunListResponse": RunListResponse,
    "MetadataHorizon": MetadataHorizonResponse,
    "RunDetailResponse": RunDetailResponse,
    "MetadataItem": MetadataItemResponse,
    "MetadataFile": MetadataFileResponse,
    "RunMetadataResponse": RunMetadataResponse,
    "FailureProjection": FailureProjectionResponse,
    "ResultListItem": ResultListItemResponse,
    "ResultsResponse": ResultsResponse,
    "ResultDetailResponse": ResultDetailResponse,
    "HistoryEntry": HistoryEntryResponse,
    "HistoryResponse": HistoryResponse,
    "SectionResponse": SectionResponse,
    "SectionListResponse": SectionListResponse,
    "SectionSummary": SectionSummaryResponse,
    "RunSectionSummaryResponse": RunSectionSummaryResponse,
}
_BOUND_MODELS: dict[str, type[BaseModel]] = {**_REQUEST_SCHEMAS, **_RESPONSE_SCHEMAS}

# Where the document declares a closed vocabulary, the values it must equal.
# Taken from the domain frozensets rather than the response model, because
# the response models type these fields as plain `str` -- the document is
# stricter than the model on purpose, and the domain is what the server can
# actually emit. Every `enum` in the document must appear here, and every
# entry here must appear in the document (both directions, below). A
# nullable property's enum also lists `null`, which is not part of the
# vocabulary.
_DECLARED_ENUMS: dict[tuple[str, str], frozenset[str]] = {
    ("ResultReport", "outcome"): OUTCOMES,
    ("ResultReport", "setup_outcome"): OUTCOMES,
    ("ResultReport", "call_outcome"): OUTCOMES,
    ("ResultReport", "teardown_outcome"): OUTCOMES,
    ("RunListItem", "presentation"): PRESENTATIONS,
    ("RunDetailResponse", "presentation"): PRESENTATIONS,
    ("ResultListItem", "outcome"): OUTCOMES,
    ("ResultDetailResponse", "outcome"): OUTCOMES,
    ("HistoryEntry", "outcome"): OUTCOMES,
    ("MetadataItem", "status"): KEY_STATUSES,
    ("MetadataItem", "source"): METADATA_SOURCES,
    ("MetadataFile", "content_type"): METADATA_CONTENT_TYPES,
    ("MetadataFile", "status"): FILE_STATUSES,
}

# `extra=` on a model, to the `additionalProperties` its schema must declare.
# `ignore` (and a response model's unset default) declares nothing.
_ADDITIONAL_PROPERTIES = {"forbid": False, "allow": True}


def _declared_schemas() -> dict[str, Any]:
    return dict(_parsed_document()["components"]["schemas"])


def _document_allows_null(declaration: Mapping[str, Any]) -> bool:
    """Whether a property declaration admits `null`, in either of the two
    forms this document uses: a `type` list containing `"null"`, or a
    `oneOf` with a `{type: "null"}` member beside a `$ref`."""
    declared_type = declaration.get("type")
    if isinstance(declared_type, list):
        return "null" in declared_type
    return any(variant.get("type") == "null" for variant in declaration.get("oneOf", []))


def _model_allows_none(annotation: Any) -> bool:
    return type(None) in get_args(annotation)


def test_every_declared_schema_is_bound_to_a_model() -> None:
    """The binding table itself, in both directions. A schema added to
    `v1.yaml` with no model behind it, or a table entry naming a schema the
    document dropped, is caught here rather than silently skipping the three
    checks below -- a comparison that quietly iterates over nothing is the
    vacuous-pass shape this module exists to avoid."""
    declared = set(_declared_schemas())
    bound = set(_BOUND_MODELS)

    assert declared - bound == set(), (
        f"the document declares schemas with no bound model: {sorted(declared - bound)}"
    )
    assert bound - declared == set(), (
        f"the binding table names schemas the document does not declare: {sorted(bound - declared)}"
    )


def test_declared_schema_properties_match_their_model_fields() -> None:
    """The document's schemas restate the Pydantic models; this binds them.

    Both directions, per schema: a model field the document never declares
    is an undocumented part of the contract, and a declared property with no
    model behind it is a promise the server does not keep. `RunVcs` gaining
    a `root` property -- a field deliberately kept off the wire -- fails the
    second direction."""
    schemas = _declared_schemas()

    for name, model in _BOUND_MODELS.items():
        declared = set(schemas[name].get("properties", {}))
        modelled = set(model.model_fields)

        assert declared - modelled == set(), (
            f"{name}: the document declares {sorted(declared - modelled)}, "
            f"which {model.__name__} has no field for"
        )
        assert modelled - declared == set(), (
            f"{name}: {model.__name__} carries {sorted(modelled - declared)}, "
            f"which the document does not declare"
        )


def test_declared_required_sets_match_their_models() -> None:
    """A property the document declares optional while the model
    always sends it understates the contract; one it declares required while
    the model may omit it overstates it. See `_REQUEST_SCHEMAS` for why the
    two directions of the boundary compute this differently."""
    schemas = _declared_schemas()
    expected_by_name: dict[str, set[str]] = {
        **{
            name: {field for field, info in model.model_fields.items() if info.is_required()}
            for name, model in _REQUEST_SCHEMAS.items()
        },
        **{name: set(model.model_fields) for name, model in _RESPONSE_SCHEMAS.items()},
    }

    for name, expected in expected_by_name.items():
        declared = set(schemas[name].get("required", []))

        assert declared - expected == set(), (
            f"{name}: the document requires {sorted(declared - expected)}, "
            f"which {_BOUND_MODELS[name].__name__} does not guarantee"
        )
        assert expected - declared == set(), (
            f"{name}: {_BOUND_MODELS[name].__name__} always carries "
            f"{sorted(expected - declared)}, which the document does not require"
        )


def test_declared_nullability_matches_its_model_field() -> None:
    """Whether each declared property admits `null` must match
    whether the model's annotation admits `None`. A generated client trusts
    this to decide what it has to handle, and it is the property most likely
    to drift silently: `finished_at` losing its `"null"` member reads as a
    guarantee the server cannot make for a run still in flight.

    Iterates the intersection deliberately -- a name present on only one side
    is `test_declared_schema_properties_match_their_model_fields`'s finding,
    reported there rather than as a confusing `KeyError` here."""
    schemas = _declared_schemas()

    for name, model in _BOUND_MODELS.items():
        properties = schemas[name].get("properties", {})
        for field in sorted(set(properties) & set(model.model_fields)):
            declared_nullable = _document_allows_null(properties[field])
            modelled_nullable = _model_allows_none(model.model_fields[field].annotation)

            assert declared_nullable == modelled_nullable, (
                f"{name}.{field}: the document {'admits' if declared_nullable else 'forbids'} "
                f"null, {model.__name__} {'admits' if modelled_nullable else 'forbids'} None"
            )


def test_declared_enums_match_the_vocabulary_the_server_can_emit() -> None:
    """Checked against `vantage.core.domain.result.OUTCOMES`,
    `vantage.core.domain.liveness.PRESENTATIONS` and the metadata
    vocabularies in `vantage.core.domain.metadata`.

    Both directions again, at two levels: which properties declare a closed
    vocabulary at all, and what that vocabulary contains. Replacing
    `ResultListItem.outcome`'s enum with values the server never emits fails the
    second; adding an enum to a property nobody vetted fails the first,
    rather than passing unchecked because no expectation was written for
    it."""
    found: dict[tuple[str, str], Mapping[str, Any]] = {
        (name, field): declaration
        for name, schema in _declared_schemas().items()
        for field, declaration in schema.get("properties", {}).items()
        if "enum" in declaration
    }

    assert set(found) == set(_DECLARED_ENUMS), (
        "the document's enum-declaring properties are "
        f"{sorted(found)}, expected {sorted(_DECLARED_ENUMS)}"
    )
    for key, declaration in found.items():
        declared = frozenset(value for value in declaration["enum"] if value is not None)
        expected = _DECLARED_ENUMS[key]
        assert declared == expected, (
            f"{key[0]}.{key[1]}: the document declares {sorted(declared)}, "
            f"the domain permits {sorted(expected)}"
        )
        # A JSON Schema enum rejects every value it does not list, `null`
        # included, whatever `type` says.
        assert (None in declaration["enum"]) == _document_allows_null(declaration), (
            f"{key[0]}.{key[1]}: the enum and the type disagree about null"
        )


def _models_in(annotation: Any) -> set[type[BaseModel]]:
    """Every model an annotation holds, through unions, lists and
    `Annotated`."""
    found: set[type[BaseModel]] = set()
    pending = [annotation]
    while pending:
        current = pending.pop()
        if isinstance(current, type) and issubclass(current, BaseModel):
            found.add(current)
        else:
            pending.extend(get_args(current))
    return found


def _refs_in(declaration: object) -> set[str]:
    """Every schema name a property declaration references, at any depth."""
    if isinstance(declaration, Mapping):
        return {
            name
            for key, value in declaration.items()
            for name in ({value.rsplit("/", 1)[-1]} if key == "$ref" else _refs_in(value))
        }
    if isinstance(declaration, list):
        return {name for item in declaration for name in _refs_in(item)}
    return set()


def test_every_nested_model_is_bound_and_referenced_by_its_schema() -> None:
    """A model reachable from a bound one -- a section of the report, an
    item of a response list -- is part of the contract too, so it must be
    bound, and the property holding it must `$ref` the schema bound to that
    same model. Without this, a nested model could be left out of the
    document entirely while every check above still passed."""
    schemas = _declared_schemas()
    name_of = {model: name for name, model in _BOUND_MODELS.items()}

    for name, model in _BOUND_MODELS.items():
        properties = schemas[name].get("properties", {})
        for field in sorted(set(properties) & set(model.model_fields)):
            held = _models_in(model.model_fields[field].annotation)
            unbound = sorted(nested.__name__ for nested in held if nested not in name_of)
            assert not unbound, f"{name}.{field} holds {unbound}, bound to no schema"
            assert _refs_in(properties[field]) == {name_of[nested] for nested in held}, (
                f"{name}.{field}: the document references {sorted(_refs_in(properties[field]))}"
            )


def test_declared_additional_properties_match_the_model_extra_setting() -> None:
    """Whether an unknown key is refused, tolerated or ignored differs per
    model on purpose; a generated client learns which from
    `additionalProperties`."""
    schemas = _declared_schemas()

    for name, model in _BOUND_MODELS.items():
        expected = _ADDITIONAL_PROPERTIES.get(str(model.model_config.get("extra")))
        assert schemas[name].get("additionalProperties") == expected, (
            f"{name}: {model.__name__} has extra={model.model_config.get('extra')!r}"
        )
