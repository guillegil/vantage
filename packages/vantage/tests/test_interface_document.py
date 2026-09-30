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
gets back must be one its operation lists. So is who may ask: each
operation's `403` names the scope a token must grant and, within a project,
the role its user needs, and requests are sent that hold each and that fall
short of it.

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
from functools import partial
from pathlib import Path
from typing import Any, get_args

import httpx2 as httpx
import pytest
import yaml
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from memory_store import InMemoryExecutionStore
from pydantic import BaseModel
from vantage.core.config.hosts import HostRule
from vantage.core.domain.access import (
    ADMIN_SCOPE,
    READ_SCOPE,
    RECORD_SCOPE,
    SCOPES,
    USER_NAME_PATTERN,
    new_token,
    token_digest,
)
from vantage.core.domain.changes import CHANGES, COMPARISON_STATES, RESULT_CHANGES
from vantage.core.domain.liveness import PRESENTATIONS
from vantage.core.domain.metadata import (
    FILE_STATUSES,
    KEY_STATUSES,
    METADATA_CONTENT_TYPES,
    METADATA_SOURCES,
)
from vantage.core.domain.passwords import PASSWORD_MAX_CHARS, PASSWORD_MIN_CHARS, hash_password
from vantage.core.domain.projects import (
    DEFAULT_PROJECT,
    EDITOR_ROLE,
    OWNER_ROLE,
    PROJECT_NAME_PATTERN,
    ROLES,
    VIEWER_ROLE,
)
from vantage.core.domain.result import OUTCOMES
from vantage.core.ports.storage import ExecutionStore
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
from vantage.service.access import SESSION_COOKIE
from vantage.service.app import create_app
from vantage.service.errors import MAX_REPORT_BYTES, MisdirectedRequestError
from vantage.service.routes.members import MAX_MEMBERS_BODY_BYTES
from vantage.service.routes.projects import MAX_PROJECTS_BODY_BYTES
from vantage.service.routes.sections import MAX_SECTION_BODY_BYTES
from vantage.service.routes.users import MAX_USERS_BODY_BYTES
from vantage.service.schemas import (
    Acknowledgement,
    BaselineResponse,
    ChangeCountsResponse,
    ChangeItemResponse,
    ChangesResponse,
    ComparisonResponse,
    CreatedTokenResponse,
    FailureProjectionResponse,
    HeartbeatAcknowledgement,
    HistoryEntryResponse,
    HistoryResponse,
    LoginRequest,
    MemberListResponse,
    MemberResponse,
    MemberSetRequest,
    MetadataFileResponse,
    MetadataHorizonResponse,
    MetadataItemResponse,
    OutcomeCountsResponse,
    PasswordChangeRequest,
    PasswordSetRequest,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    RejectionResponse,
    ResultDetailResponse,
    ResultListItemResponse,
    ResultsResponse,
    RunDetailResponse,
    RunListItemResponse,
    RunListResponse,
    RunMetadataResponse,
    RunOutcomesResponse,
    RunSectionSummaryResponse,
    RunVcsResponse,
    SectionListResponse,
    SectionResponse,
    SectionSummaryResponse,
    SectionUpsertRequest,
    SessionResponse,
    SessionUserResponse,
    StreakResponse,
    TokenCreateRequest,
    TokenListResponse,
    TokenResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)
from vantage.storage.sqlite_store import SqliteExecutionStore

# `cheap_passwords`, so the password routes hash at a tiny cost.
pytest_plugins = ["password_fixtures"]

_DOCUMENT_BYTES = (
    importlib.resources.files("vantage.service.openapi").joinpath("v1.yaml").read_bytes()
)
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_Call = Callable[[], httpx.Response]
# A password the rule takes, for the password routes to set and check.
_PASSWORD = "an interface probe password"
# What a browser marks every request from vantage's own pages with.
_SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


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


def _bearer(store: ExecutionStore, user: str, scopes: set[str]) -> dict[str, str]:
    """Make the existing `user` a token holding exactly `scopes`, and return
    the header that sends it."""
    token = new_token()
    store.create_token(
        user,
        digest=token_digest(token),
        label="",
        scopes=frozenset(scopes),
        created_at=datetime.now(timezone.utc),
    )
    return {"Authorization": f"Bearer {token}"}


def _admin(store: ExecutionStore, name: str = "alice") -> dict[str, str]:
    """Make `name` an admin, with a token holding every scope, which closes
    the server, and return the header that sends the token."""
    store.create_user(name, admin=True, created_at=datetime.now(timezone.utc))
    return _bearer(store, name, set(SCOPES))


def test_every_documented_path_answers_2xx(tmp_path: Path, cheap_passwords: None) -> None:
    """A binding table maps every `(method, path)` the document declares to
    a callable producing valid parameters, driven against a dedicated
    fixture database with an admin's token holding every scope, since the
    users and tokens paths answer nothing else. Logging in, signing a
    browser in and changing a password send, instead, the name and password
    an admin set, and signing in and out send what a browser marks a
    request from vantage's own pages with. Includes
    `GET /api/v1/capabilities` and `GET /api/v1/openapi.yaml` themselves."""
    document = _parsed_document()
    declared = _declared_operations(document)
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    client = TestClient(create_app(store), headers=_admin(store))
    run = f"/api/v1/runs/{'6' * 32}"
    # A project the first binding makes, which the report is recorded in and
    # the per-project paths read, so each is asked of a project but `default`.
    project = "/api/v1/projects/interface-probe"
    minted: dict[str, Any] = {}

    def mint() -> httpx.Response:
        response = client.post("/api/v1/tokens", json={"user": "probe", "label": "probe"})
        minted.update(response.json())
        return response

    node_id = "tests/test_interface_document_probe.py::test_x"
    report = {**_report(run.rsplit("/", 1)[-1]), "project": "interface-probe"}
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
    # the project must be made before a run is reported in it, the run
    # reported before it can be read back or heartbeat'd, a user made
    # before being made a member of the project and removed from it, and
    # given a password before logging in.
    ordered_bindings: list[tuple[tuple[str, str], _Call]] = [
        (
            ("POST", "/projects"),
            lambda: client.post("/api/v1/projects", json={"name": "interface-probe"}),
        ),
        (("GET", "/projects"), lambda: client.get("/api/v1/projects")),
        (("POST", "/runs"), lambda: client.post("/api/v1/runs", json=report)),
        (("POST", "/runs/{run_id}/heartbeat"), lambda: client.post(f"{run}/heartbeat")),
        (("GET", "/projects/{project}/runs"), lambda: client.get(f"{project}/runs")),
        (("GET", "/runs/{run_id}"), lambda: client.get(run)),
        (("GET", "/runs/{run_id}/metadata"), lambda: client.get(f"{run}/metadata")),
        (("GET", "/runs/{run_id}/results"), lambda: client.get(f"{run}/results")),
        (("GET", "/runs/{run_id}/changes"), lambda: client.get(f"{run}/changes")),
        (("GET", "/runs/{run_id}/outcomes"), lambda: client.get(f"{run}/outcomes")),
        (
            ("GET", "/runs/{run_id}/result"),
            lambda: client.get(f"{run}/result", params={"node_id": node_id}),
        ),
        (
            ("GET", "/projects/{project}/tests/history"),
            lambda: client.get(f"{project}/tests/history", params={"node_id": node_id}),
        ),
        (("GET", "/capabilities"), lambda: client.get("/api/v1/capabilities")),
        (("GET", "/openapi.yaml"), lambda: client.get("/api/v1/openapi.yaml")),
        (
            ("POST", "/projects/{project}/config/sections"),
            lambda: client.post(
                f"{project}/config/sections",
                json={"name": "InterfaceProbe", "prefix": "tests/interface-probe"},
            ),
        ),
        (
            ("GET", "/projects/{project}/config/sections"),
            lambda: client.get(f"{project}/config/sections"),
        ),
        (
            ("GET", "/runs/{run_id}/sections"),
            lambda: client.get(f"{run}/sections"),
        ),
        (
            ("DELETE", "/projects/{project}/config/sections"),
            lambda: client.delete(f"{project}/config/sections", params={"name": "InterfaceProbe"}),
        ),
        (("POST", "/users"), lambda: client.post("/api/v1/users", json={"name": "probe"})),
        (
            ("PUT", "/projects/{project}/members/{user}"),
            lambda: client.put(f"{project}/members/probe", json={"role": VIEWER_ROLE}),
        ),
        (("GET", "/projects/{project}/members"), lambda: client.get(f"{project}/members")),
        (
            ("DELETE", "/projects/{project}/members/{user}"),
            lambda: client.delete(f"{project}/members/probe"),
        ),
        (
            ("PATCH", "/users/{name}"),
            lambda: client.patch("/api/v1/users/probe", json={"admin": True}),
        ),
        (("GET", "/users"), lambda: client.get("/api/v1/users")),
        (
            ("PUT", "/users/{name}/password"),
            lambda: client.put("/api/v1/users/probe/password", json={"password": _PASSWORD}),
        ),
        (
            ("POST", "/login"),
            lambda: client.post("/api/v1/login", json={"name": "probe", "password": _PASSWORD}),
        ),
        (
            ("POST", "/password"),
            lambda: client.post(
                "/api/v1/password",
                json={"name": "probe", "password": _PASSWORD, "new_password": _PASSWORD.upper()},
            ),
        ),
        (
            ("POST", "/session"),
            lambda: client.post(
                "/api/v1/session",
                json={"name": "probe", "password": _PASSWORD.upper()},
                headers=_SAME_ORIGIN,
            ),
        ),
        (("GET", "/session"), lambda: client.get("/api/v1/session")),
        (
            ("DELETE", "/session"),
            lambda: client.delete("/api/v1/session", headers=_SAME_ORIGIN),
        ),
        (("POST", "/tokens"), mint),
        (("GET", "/tokens"), lambda: client.get("/api/v1/tokens")),
        (
            ("POST", "/tokens/{token_id}/revoke"),
            lambda: client.post(f"/api/v1/tokens/{minted['id']}/revoke"),
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
    default = f"/api/v1/projects/{DEFAULT_PROJECT}"
    # A name no project has, and one no project can have: both are 404.
    missing = ("/api/v1/projects/ghost", "/api/v1/projects/NOT-A-NAME")
    sections = f"{default}/config/sections"
    section = {"name": "Probe", "prefix": "tests"}

    def mismatch() -> httpx.Response:
        """A run recorded in `default`, then reported again naming another
        project."""
        run_id = "8" * 32
        assert client.post("/api/v1/projects", json={"name": "elsewhere"}).status_code == 201
        assert client.post("/api/v1/runs", json=_report(run_id)).status_code == 201
        return client.post("/api/v1/runs", json={**_report(run_id), "project": "elsewhere"})

    per_project: list[tuple[tuple[str, str], _Call]] = []
    for path in missing:
        per_project += [
            (("GET", "/projects/{project}/runs"), partial(client.get, f"{path}/runs")),
            (
                ("GET", "/projects/{project}/tests/history"),
                partial(client.get, f"{path}/tests/history", params=node),
            ),
            (
                ("GET", "/projects/{project}/config/sections"),
                partial(client.get, f"{path}/config/sections"),
            ),
            (
                ("POST", "/projects/{project}/config/sections"),
                partial(client.post, f"{path}/config/sections", json=section),
            ),
            (
                ("DELETE", "/projects/{project}/config/sections"),
                partial(client.delete, f"{path}/config/sections", params={"name": "Probe"}),
            ),
        ]
    projects_body: list[tuple[tuple[str, str], _Call]] = [
        (
            ("POST", "/projects"),
            partial(
                client.post, "/api/v1/projects", content=b"{}", headers={"content-type": "x/y"}
            ),
        ),
        (
            ("POST", "/projects"),
            partial(client.post, "/api/v1/projects", content=b"{", headers=json_header),
        ),
        (
            ("POST", "/projects"),
            partial(
                client.post,
                "/api/v1/projects",
                content=b" " * (MAX_PROJECTS_BODY_BYTES + 1),
                headers=json_header,
            ),
        ),
        (
            ("POST", "/projects"),
            partial(client.post, "/api/v1/projects", content=b"[]", headers=json_header),
        ),
        (("POST", "/projects"), partial(client.post, "/api/v1/projects", json={})),
        (("POST", "/projects"), partial(client.post, "/api/v1/projects", json={"name": 1})),
        (("POST", "/projects"), partial(client.post, "/api/v1/projects", json={"name": "Bad"})),
        (
            ("POST", "/projects"),
            partial(client.post, "/api/v1/projects", json={"name": DEFAULT_PROJECT}),
        ),
    ]
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
        (
            ("POST", "/runs"),
            lambda: client.post("/api/v1/runs", json={**_report("a" * 32), "project": "Bad"}),
        ),
        (
            ("POST", "/runs"),
            lambda: client.post("/api/v1/runs", json={**_report("b" * 32), "project": "ghost"}),
        ),
        (("POST", "/runs"), mismatch),
        *projects_body,
        *per_project,
        (
            ("GET", "/projects/{project}/runs"),
            lambda: client.get(f"{default}/runs", params={"limit": 0}),
        ),
        (
            ("GET", "/projects/{project}/runs"),
            lambda: client.get(f"{default}/runs", params={"metadata_key": "k"}),
        ),
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
        (("GET", "/runs/{run_id}/changes"), lambda: client.get(f"{known_shape}/changes")),
        (("GET", "/runs/{run_id}/changes"), lambda: client.get(f"{malformed}/changes")),
        (
            ("GET", "/runs/{run_id}/changes"),
            lambda: client.get(f"{known_shape}/changes", params={"limit": 0}),
        ),
        (("GET", "/runs/{run_id}/outcomes"), lambda: client.get(f"{known_shape}/outcomes")),
        (("GET", "/runs/{run_id}/outcomes"), lambda: client.get(f"{malformed}/outcomes")),
        (
            ("GET", "/runs/{run_id}/result"),
            lambda: client.get(f"{known_shape}/result", params=node),
        ),
        (("GET", "/runs/{run_id}/result"), lambda: client.get(f"{malformed}/result", params=node)),
        (("GET", "/runs/{run_id}/result"), lambda: client.get(f"{known_shape}/result")),
        (
            ("GET", "/projects/{project}/tests/history"),
            lambda: client.get(f"{default}/tests/history"),
        ),
        (("GET", "/runs/{run_id}/sections"), lambda: client.get(f"{known_shape}/sections")),
        (("GET", "/runs/{run_id}/sections"), lambda: client.get(f"{malformed}/sections")),
        (
            ("POST", "/projects/{project}/config/sections"),
            lambda: client.post(sections, json={"name": "", "prefix": "tests"}),
        ),
        (("POST", "/projects/{project}/config/sections"), lambda: client.post(sections, json={})),
        (
            ("POST", "/projects/{project}/config/sections"),
            lambda: client.post(sections, content=b"{}", headers={"content-type": "x/y"}),
        ),
        (
            ("POST", "/projects/{project}/config/sections"),
            lambda: client.post(sections, content=b"{", headers=json_header),
        ),
        (
            ("POST", "/projects/{project}/config/sections"),
            lambda: client.post(
                sections, content=b" " * (MAX_SECTION_BODY_BYTES + 1), headers=json_header
            ),
        ),
        (
            ("DELETE", "/projects/{project}/config/sections"),
            lambda: client.delete(sections, params={"name": "never-stored"}),
        ),
        (("DELETE", "/projects/{project}/config/sections"), lambda: client.delete(sections)),
        (("GET", "/projects/{project}/members"), lambda: client.get(f"{default}/members")),
        (
            ("PUT", "/projects/{project}/members/{user}"),
            lambda: client.put(f"{default}/members/a", json={"role": VIEWER_ROLE}),
        ),
        (
            ("DELETE", "/projects/{project}/members/{user}"),
            lambda: client.delete(f"{default}/members/a"),
        ),
        (("GET", "/users"), lambda: client.get("/api/v1/users")),
        (("POST", "/users"), lambda: client.post("/api/v1/users", json={"name": "a"})),
        (
            ("PATCH", "/users/{name}"),
            lambda: client.patch("/api/v1/users/a", json={"admin": True}),
        ),
        (("GET", "/tokens"), lambda: client.get("/api/v1/tokens")),
        (("POST", "/tokens"), lambda: client.post("/api/v1/tokens", json={"user": "a"})),
        (("POST", "/tokens/{token_id}/revoke"), lambda: client.post("/api/v1/tokens/1/revoke")),
        (
            ("PUT", "/users/{name}/password"),
            lambda: client.put("/api/v1/users/a/password", json={"password": _PASSWORD}),
        ),
        (
            ("POST", "/login"),
            lambda: client.post("/api/v1/login", json={"name": "a", "password": _PASSWORD}),
        ),
        (
            ("POST", "/session"),
            lambda: client.post(
                "/api/v1/session",
                json={"name": "a", "password": _PASSWORD},
                headers=_SAME_ORIGIN,
            ),
        ),
        (
            ("POST", "/password"),
            lambda: client.post(
                "/api/v1/password",
                json={"name": "a", "password": _PASSWORD, "new_password": _PASSWORD},
            ),
        ),
    ]


def _undocumented_statuses(
    document: Mapping[str, Any], observed: set[tuple[str, str, int]]
) -> set[tuple[str, str, int]]:
    return {
        (method, path, status)
        for method, path, status in observed
        if str(status) not in document["paths"][path][method.lower()]["responses"]
    }


def _unnamed_errors(
    document: Mapping[str, Any], answered: set[tuple[str, str, int, str]]
) -> set[tuple[str, str, int, str]]:
    """The answers whose error code the description of their status does
    not name, in the parentheses the document names every code in. Each
    status must be documented already."""
    return {
        (method, path, status, error)
        for method, path, status, error in answered
        if f"({error})"
        not in document["paths"][path][method.lower()]["responses"][str(status)]["description"]
    }


def _users_probes(client: TestClient) -> list[tuple[tuple[str, str], _Call]]:
    """One request per rejection the users, tokens and password paths give
    an admin, on a server whose admin is `alice` and has a user `bob`, who
    has no password."""
    json_header = {"content-type": "application/json"}
    too_large = b" " * (MAX_USERS_BODY_BYTES + 1)
    short = _PASSWORD[:14]
    requests: list[tuple[tuple[str, str], _Call]] = []
    for key, path, send in (
        (("POST", "/users"), "/api/v1/users", client.post),
        (("PATCH", "/users/{name}"), "/api/v1/users/bob", client.patch),
        (("POST", "/tokens"), "/api/v1/tokens", client.post),
        (("PUT", "/users/{name}/password"), "/api/v1/users/bob/password", client.put),
        (("POST", "/login"), "/api/v1/login", client.post),
        (("POST", "/password"), "/api/v1/password", client.post),
    ):
        requests += [
            (key, partial(send, path, content=b"{}", headers={"content-type": "x/y"})),
            (key, partial(send, path, content=b"{", headers=json_header)),
            (key, partial(send, path, content=too_large, headers=json_header)),
            (key, partial(send, path, content=b"[]", headers=json_header)),
        ]
    return [
        *requests,
        (("POST", "/users"), lambda: client.post("/api/v1/users", json={"name": "Bob"})),
        (("POST", "/users"), lambda: client.post("/api/v1/users", json={"name": "bob"})),
        (("PATCH", "/users/{name}"), lambda: client.patch("/api/v1/users/bob", json={})),
        (
            ("PATCH", "/users/{name}"),
            lambda: client.patch("/api/v1/users/ghost", json={"admin": True}),
        ),
        (
            ("PATCH", "/users/{name}"),
            lambda: client.patch("/api/v1/users/alice", json={"admin": False}),
        ),
        (("POST", "/tokens"), lambda: client.post("/api/v1/tokens", json={"user": "ghost"})),
        (
            ("POST", "/tokens"),
            lambda: client.post("/api/v1/tokens", json={"user": "bob", "scopes": []}),
        ),
        (
            ("POST", "/tokens"),
            lambda: client.post("/api/v1/tokens", json={"user": "bob", "label": "a\nb"}),
        ),
        (
            ("POST", "/tokens"),
            lambda: client.post("/api/v1/tokens", json={"user": "bob", "scopes": ["admin"]}),
        ),
        (("POST", "/tokens/{token_id}/revoke"), lambda: client.post("/api/v1/tokens/999/revoke")),
        (("POST", "/tokens/{token_id}/revoke"), lambda: client.post("/api/v1/tokens/0/revoke")),
        (
            ("PUT", "/users/{name}/password"),
            lambda: client.put("/api/v1/users/ghost/password", json={"password": _PASSWORD}),
        ),
        (
            ("PUT", "/users/{name}/password"),
            lambda: client.put("/api/v1/users/NOT-A-NAME/password", json={"password": _PASSWORD}),
        ),
        (
            ("PUT", "/users/{name}/password"),
            lambda: client.put("/api/v1/users/bob/password", json={"password": short}),
        ),
        (
            ("POST", "/login"),
            lambda: client.post("/api/v1/login", json={"name": "bob", "password": _PASSWORD}),
        ),
        (
            ("POST", "/password"),
            lambda: client.post(
                "/api/v1/password",
                json={"name": "bob", "password": _PASSWORD, "new_password": short},
            ),
        ),
        (
            ("POST", "/password"),
            lambda: client.post(
                "/api/v1/password",
                json={"name": "bob", "password": _PASSWORD, "new_password": _PASSWORD},
            ),
        ),
    ]


_MEMBERS_PROJECT = "members-probe"


def _members_probes(client: TestClient) -> list[tuple[tuple[str, str], _Call]]:
    """One request per rejection the members paths give an admin, on the
    server `_users_probes` asks, which also has the project
    `_MEMBERS_PROJECT`, of which `bob` is not a member."""
    json_header = {"content-type": "application/json"}
    listing = ("GET", "/projects/{project}/members")
    setting = ("PUT", "/projects/{project}/members/{user}")
    removing = ("DELETE", "/projects/{project}/members/{user}")
    viewer = {"role": VIEWER_ROLE}
    default = f"/api/v1/projects/{DEFAULT_PROJECT}"
    members = f"/api/v1/projects/{_MEMBERS_PROJECT}/members"
    bob = f"{members}/bob"
    requests: list[tuple[tuple[str, str], _Call]] = []
    # A name no project has, and one no project can have: both are 404.
    for path in ("/api/v1/projects/ghost", "/api/v1/projects/NOT-A-NAME"):
        requests += [
            (listing, partial(client.get, f"{path}/members")),
            (setting, partial(client.put, f"{path}/members/bob", json=viewer)),
            (removing, partial(client.delete, f"{path}/members/bob")),
        ]
    # A user nobody has, and a name nobody can have, set and removed.
    for user in ("ghost", "NOT-A-NAME"):
        requests += [
            (setting, partial(client.put, f"{members}/{user}", json=viewer)),
            (removing, partial(client.delete, f"{members}/{user}")),
        ]
    return [
        *requests,
        (setting, partial(client.put, f"{default}/members/bob", json=viewer)),
        (removing, partial(client.delete, f"{default}/members/bob")),
        (setting, partial(client.put, bob, content=b"{}", headers={"content-type": "x/y"})),
        (setting, partial(client.put, bob, content=b"{", headers=json_header)),
        (
            setting,
            partial(
                client.put, bob, content=b" " * (MAX_MEMBERS_BODY_BYTES + 1), headers=json_header
            ),
        ),
        (setting, partial(client.put, bob, content=b"[]", headers=json_header)),
        (setting, partial(client.put, bob, json={"role": 1})),
        (setting, partial(client.put, bob, json={"role": "admin"})),
        (removing, partial(client.delete, bob)),
    ]


def _session_probes(client: TestClient, token: str) -> list[tuple[tuple[str, str], _Call]]:
    """One request per rejection signing a browser in or out can earn, and
    a credentialed read refused for where it came from, on the server
    `_users_probes` asks, from a client sending no token: `token` is the
    admin's, sent as a session cookie from another port of the host."""
    json_header = {"content-type": "application/json", **_SAME_ORIGIN}
    too_large = b" " * (MAX_USERS_BODY_BYTES + 1)
    signing_in = ("POST", "/session")
    cross_site = {"Cookie": f"{SESSION_COOKIE}={token}", "Sec-Fetch-Site": "same-site"}
    return [
        (
            signing_in,
            partial(client.post, "/api/v1/session", json={"name": "bob", "password": _PASSWORD}),
        ),
        (
            signing_in,
            partial(
                client.post,
                "/api/v1/session",
                content=b"{}",
                headers={"content-type": "x/y", **_SAME_ORIGIN},
            ),
        ),
        (signing_in, partial(client.post, "/api/v1/session", content=b"{", headers=json_header)),
        (
            signing_in,
            partial(client.post, "/api/v1/session", content=too_large, headers=json_header),
        ),
        (signing_in, partial(client.post, "/api/v1/session", content=b"[]", headers=json_header)),
        (
            signing_in,
            partial(
                client.post,
                "/api/v1/session",
                json={"name": "bob", "password": _PASSWORD},
                headers=_SAME_ORIGIN,
            ),
        ),
        (("DELETE", "/session"), partial(client.delete, "/api/v1/session", headers=cross_site)),
        (("GET", "/session"), partial(client.get, "/api/v1/session", headers=cross_site)),
        (
            ("GET", "/projects/{project}/runs"),
            partial(client.get, f"/api/v1/projects/{DEFAULT_PROJECT}/runs", headers=cross_site),
        ),
    ]


def test_every_status_the_server_answers_is_documented(cheap_passwords: None) -> None:
    """A generated client decides what to handle from the listed statuses,
    so each rejection the server gives must be listed, and its error named
    where its status is: on an open server, and on one whose admin manages
    users and members. Half 2 removes one status, and one error's name,
    from a copy of the document and proves each check reports it."""
    client = TestClient(create_app(InMemoryExecutionStore()))
    answers = [(key, call()) for key, call in _probes(client)]
    closed = InMemoryExecutionStore()
    admin = _admin(closed)
    closed.create_user("bob", admin=False, created_at=datetime.now(timezone.utc))
    closed.create_project(_MEMBERS_PROJECT, created_at=datetime.now(timezone.utc))
    admin_client = TestClient(create_app(closed), headers=admin)
    answers += [(key, call()) for key, call in _users_probes(admin_client)]
    answers += [(key, call()) for key, call in _members_probes(admin_client)]
    browser = TestClient(create_app(closed))
    admins_token = admin["Authorization"].removeprefix("Bearer ")
    answers += [(key, call()) for key, call in _session_probes(browser, admins_token)]
    observed = {(*key, response.status_code) for key, response in answers}
    errors = {(*key, response.json()["error"]) for key, response in answers}
    answered = {(*key, response.status_code, response.json()["error"]) for key, response in answers}
    assert all(status >= 400 for _, _, status in observed)
    assert {status for method, path, status in observed if path.startswith("/users")} == {
        400,
        404,
        409,
        413,
        415,
        422,
    }
    # And every rejection the members paths can give but a refusal of who
    # asks, which the scope and role tests below earn.
    assert {status for _, path, status in observed if "/members" in path} == {
        400,
        404,
        409,
        413,
        415,
        422,
    }
    # Every rejection a project can earn is probed, as the error it is.
    assert {status for _, path, status in observed if path == "/projects"} == {
        400,
        409,
        413,
        415,
        422,
    }
    # And every rejection logging in or changing a password can earn.
    assert {status for _, path, status in observed if path in ("/login", "/password")} == {
        400,
        401,
        409,
        413,
        415,
        422,
    }
    # And every one signing a browser in or out can, but the busy slots.
    assert {status for _, path, status in observed if path == "/session"} == {
        400,
        401,
        403,
        409,
        413,
        415,
        422,
    }
    assert {
        ("POST", "/projects", "project_exists"),
        ("POST", "/projects", "invalid_project_name"),
        ("POST", "/projects", "invalid_project_request"),
        ("POST", "/runs", "unknown_project"),
        ("POST", "/runs", "project_mismatch"),
        ("POST", "/runs", "invalid_report"),
        ("POST", "/login", "open_server"),
        ("POST", "/login", "invalid_credentials"),
        ("POST", "/login", "invalid_login_request"),
        ("POST", "/session", "open_server"),
        ("POST", "/session", "cross_site_request"),
        ("POST", "/session", "invalid_credentials"),
        ("POST", "/session", "invalid_login_request"),
        ("GET", "/session", "cross_site_request"),
        ("DELETE", "/session", "cross_site_request"),
        ("GET", "/projects/{project}/runs", "cross_site_request"),
        ("POST", "/password", "open_server"),
        ("POST", "/password", "invalid_credentials"),
        ("POST", "/password", "invalid_password_request"),
        ("POST", "/password", "invalid_password"),
        ("PUT", "/users/{name}/password", "open_server"),
        ("PUT", "/users/{name}/password", "unknown_user"),
        ("PUT", "/users/{name}/password", "invalid_password_request"),
        ("PUT", "/users/{name}/password", "invalid_password"),
        ("GET", "/projects/{project}/members", "open_server"),
        ("PUT", "/projects/{project}/members/{user}", "open_server"),
        ("PUT", "/projects/{project}/members/{user}", "default_project"),
        ("PUT", "/projects/{project}/members/{user}", "unknown_user"),
        ("PUT", "/projects/{project}/members/{user}", "invalid_member_request"),
        ("PUT", "/projects/{project}/members/{user}", "invalid_role"),
        ("DELETE", "/projects/{project}/members/{user}", "open_server"),
        ("DELETE", "/projects/{project}/members/{user}", "default_project"),
        ("DELETE", "/projects/{project}/members/{user}", "unknown_member"),
    } <= errors
    nested = {key for key in _declared_operations(_parsed_document()) if "{project}" in key[1]}
    assert {(*key, "unknown_project") for key in nested} <= errors

    assert _undocumented_statuses(_parsed_document(), observed) == set()
    assert _unnamed_errors(_parsed_document(), answered) == set()

    tainted = _parsed_document()
    del tainted["paths"]["/runs"]["post"]["responses"]["415"]
    assert _undocumented_statuses(tainted, observed) == {("POST", "/runs", 415)}
    tainted = _parsed_document()
    conflict = tainted["paths"]["/projects/{project}/members/{user}"]["delete"]["responses"]["409"]
    conflict["description"] = conflict["description"].replace("(default_project)", "")
    assert _unnamed_errors(tainted, answered) == {
        ("DELETE", "/projects/{project}/members/{user}", 409, "default_project")
    }


def test_the_refusal_of_a_host_name_is_documented_first_and_given_on_every_path() -> None:
    """No operation lists it, since every path answers it the same way, so
    the document's description says so, before every refusal of who asks;
    and a server with a rule refuses every documented operation with it,
    before the credential it carries is looked at."""
    description = _parsed_document()["info"]["description"]
    refusal = f"{MisdirectedRequestError.status_code} {MisdirectedRequestError.error}"
    assert f"{refusal}, then 401 for no credential" in description
    assert "--allowed-host" in description
    assert "VANTAGE_ALLOWED_HOSTS" in description

    store = InMemoryExecutionStore()
    admin = _admin(store)
    client = TestClient(
        create_app(store, hosts=HostRule(frozenset())),
        base_url="http://rebound.example.net",
        headers=admin,
    )
    for method, path in sorted(_declared_operations(_parsed_document())):
        concrete = re.sub(r"\{[^}]+\}", "x", path)
        response = client.request(method, f"/api/v1{concrete}")
        assert response.status_code == MisdirectedRequestError.status_code, (method, path)
        assert response.json()["error"] == MisdirectedRequestError.error, (method, path)


_ACCESS_RUN = "5" * 32
_ACCESS_PROJECT = "access-probe"


def _access_requests(
    client: TestClient, headers: dict[str, str], *, victim: int = 1
) -> dict[tuple[str, str], _Call]:
    """One request per documented operation, sending `headers`, in an order
    where each finds what it needs: the project made first, the run
    reported in it next, the section posted before it is deleted, `bob`
    made a viewer of the project before he is removed from it. The users
    bindings add `carol`, demote and mint a token for `bob`, and revoke the
    token `victim`; the password bindings set `bob`'s password to
    `_PASSWORD`, log him in with it and change it to itself, and sign a
    browser in and out as him, marked as coming from vantage's own pages."""
    run = f"/api/v1/runs/{_ACCESS_RUN}"
    project = f"/api/v1/projects/{_ACCESS_PROJECT}"
    node = {"node_id": "tests/test_a.py::test_one"}
    section = {"name": "AccessProbe", "prefix": "tests/access-probe"}
    report = {**_report(_ACCESS_RUN), "project": _ACCESS_PROJECT}
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
        ("POST", "/projects"): lambda: client.post(
            "/api/v1/projects", json={"name": _ACCESS_PROJECT}, headers=headers
        ),
        ("GET", "/projects"): lambda: client.get("/api/v1/projects", headers=headers),
        ("POST", "/runs"): lambda: client.post("/api/v1/runs", json=report, headers=headers),
        ("POST", "/runs/{run_id}/heartbeat"): lambda: client.post(
            f"{run}/heartbeat", headers=headers
        ),
        ("GET", "/projects/{project}/runs"): lambda: client.get(f"{project}/runs", headers=headers),
        ("GET", "/runs/{run_id}"): lambda: client.get(run, headers=headers),
        ("GET", "/runs/{run_id}/metadata"): lambda: client.get(f"{run}/metadata", headers=headers),
        ("GET", "/runs/{run_id}/results"): lambda: client.get(f"{run}/results", headers=headers),
        ("GET", "/runs/{run_id}/changes"): lambda: client.get(f"{run}/changes", headers=headers),
        ("GET", "/runs/{run_id}/outcomes"): lambda: client.get(f"{run}/outcomes", headers=headers),
        ("GET", "/runs/{run_id}/result"): lambda: client.get(
            f"{run}/result", params=node, headers=headers
        ),
        ("GET", "/projects/{project}/tests/history"): lambda: client.get(
            f"{project}/tests/history", params=node, headers=headers
        ),
        ("GET", "/runs/{run_id}/sections"): lambda: client.get(f"{run}/sections", headers=headers),
        ("GET", "/capabilities"): lambda: client.get("/api/v1/capabilities", headers=headers),
        ("GET", "/openapi.yaml"): lambda: client.get("/api/v1/openapi.yaml", headers=headers),
        ("POST", "/projects/{project}/config/sections"): lambda: client.post(
            f"{project}/config/sections", json=section, headers=headers
        ),
        ("GET", "/projects/{project}/config/sections"): lambda: client.get(
            f"{project}/config/sections", headers=headers
        ),
        ("DELETE", "/projects/{project}/config/sections"): lambda: client.delete(
            f"{project}/config/sections", params={"name": section["name"]}, headers=headers
        ),
        ("PUT", "/projects/{project}/members/{user}"): lambda: client.put(
            f"{project}/members/bob", json={"role": VIEWER_ROLE}, headers=headers
        ),
        ("GET", "/projects/{project}/members"): lambda: client.get(
            f"{project}/members", headers=headers
        ),
        ("DELETE", "/projects/{project}/members/{user}"): lambda: client.delete(
            f"{project}/members/bob", headers=headers
        ),
        ("POST", "/users"): lambda: client.post(
            "/api/v1/users", json={"name": "carol"}, headers=headers
        ),
        ("PATCH", "/users/{name}"): lambda: client.patch(
            "/api/v1/users/bob", json={"admin": False}, headers=headers
        ),
        ("GET", "/users"): lambda: client.get("/api/v1/users", headers=headers),
        ("POST", "/tokens"): lambda: client.post(
            "/api/v1/tokens", json={"user": "bob"}, headers=headers
        ),
        ("GET", "/tokens"): lambda: client.get("/api/v1/tokens", headers=headers),
        ("POST", "/tokens/{token_id}/revoke"): lambda: client.post(
            f"/api/v1/tokens/{victim}/revoke", headers=headers
        ),
        ("PUT", "/users/{name}/password"): lambda: client.put(
            "/api/v1/users/bob/password", json={"password": _PASSWORD}, headers=headers
        ),
        ("POST", "/login"): lambda: client.post(
            "/api/v1/login", json={"name": "bob", "password": _PASSWORD}, headers=headers
        ),
        ("POST", "/session"): lambda: client.post(
            "/api/v1/session",
            json={"name": "bob", "password": _PASSWORD},
            headers={**headers, **_SAME_ORIGIN},
        ),
        ("GET", "/session"): lambda: client.get("/api/v1/session", headers=headers),
        ("DELETE", "/session"): lambda: client.delete(
            "/api/v1/session", headers={**headers, **_SAME_ORIGIN}
        ),
        ("POST", "/password"): lambda: client.post(
            "/api/v1/password",
            json={"name": "bob", "password": _PASSWORD, "new_password": _PASSWORD},
            headers=headers,
        ),
    }


def _needs_a_user(operation: Mapping[str, Any]) -> bool:
    """Whether an operation is one a server with no user refuses."""
    return not {"users", "members"}.isdisjoint(operation.get("tags", []))


def test_an_open_server_serves_every_path_but_the_users_and_members_ones() -> None:
    """Driven by the document, with no token and no user: every operation
    tagged `users` or `members` answers `409 open_server`, logging in and
    changing a password among them, and every other one serves anyone, as
    before the server had users -- making a project included, as changing
    sections is, with no role asked. Nothing asked makes a user."""
    store = InMemoryExecutionStore()
    client = TestClient(create_app(store))
    document = _parsed_document()
    requests = _access_requests(client, {})
    assert set(requests) == _declared_operations(document)
    observed: set[tuple[str, str, int]] = set()

    for (method, path), call in requests.items():
        response = call()
        observed.add((method, path, response.status_code))
        if _needs_a_user(document["paths"][path][method.lower()]):
            assert response.status_code == 409, (method, path, response.text)
            assert response.json()["error"] == "open_server"
        else:
            assert 200 <= response.status_code < 300, (method, path, response.text)

    assert store.access_required() is False
    assert store.get_project(_ACCESS_PROJECT) is not None
    assert _undocumented_statuses(document, {key for key in observed if key[2] >= 400}) == set()


def _documented_scope(operation: Mapping[str, Any]) -> str | None:
    """The scope an operation's `403` says a token must grant, or `None`
    for one whose `security` is empty: served without a token."""
    if operation.get("security") == []:
        return None
    match = re.search(r"grant the (\w+) scope", operation["responses"]["403"]["description"])
    assert match is not None, operation["operationId"]
    return str(match.group(1))


def _as_cookie(headers: dict[str, str]) -> dict[str, str]:
    """The token `headers` sends as `Authorization: Bearer`, sent instead
    as a browser session's cookie, from vantage's own pages."""
    token = headers["Authorization"].removeprefix("Bearer ")
    return {"Cookie": f"{SESSION_COOKIE}={token}", **_SAME_ORIGIN}


# How a credential is sent: as the plugin and CI send one, or as a browser.
_CREDENTIALS: dict[str, Callable[[dict[str, str]], dict[str, str]]] = {
    "bearer": lambda headers: headers,
    "cookie": _as_cookie,
}


@pytest.mark.parametrize("credential", sorted(_CREDENTIALS))
def test_every_operation_needs_the_scope_its_document_names(
    cheap_passwords: None, credential: str
) -> None:
    """On a server with users, driven by the document: an operation whose
    `security` is empty answers without a token, and whatever token is
    sent; every other one refuses a request without one (401) and one
    granting every scope but the one its `403` names (403), and takes an
    admin's granting that scope, since an admin acts as an owner of every
    project. A report or heartbeat of another user's run is 409, even from
    an editor of its project. Each is asked with the token in the header,
    and again in the session cookie, which every operation takes as it
    takes the header; the cookie also answers 403 cross_site_request, which
    the document names, from anywhere but vantage's own pages, and the 401
    names the cookie. Every status is documented, so a route added
    without its scope, or a document that names the wrong one, fails
    here."""
    store = InMemoryExecutionStore()
    now = datetime.now(timezone.utc)
    store.create_user("alice", admin=True, created_at=now)
    store.create_user("bob", admin=False, created_at=now)
    store.set_password("bob", password_hash=hash_password(_PASSWORD), changed_at=now)
    client = TestClient(create_app(store))
    document = _parsed_document()
    send = _CREDENTIALS[credential]
    bobs = send(_bearer(store, "bob", {RECORD_SCOPE}))
    _bearer(store, "bob", {READ_SCOPE})
    victim = store.list_tokens(user="bob")[-1].id
    anonymous = _access_requests(client, {}, victim=victim)
    assert set(anonymous) == _declared_operations(document)
    observed: set[tuple[str, str, int]] = set()
    refused: set[tuple[str, str, int, str]] = set()

    for key, call in anonymous.items():
        method, path = key
        operation = document["paths"][path][method.lower()]
        scope = _documented_scope(operation)
        answered = {"none": call().status_code}
        if scope is not None:
            lacking = send(_bearer(store, "alice", set(SCOPES) - {scope}))
            holding = send(_bearer(store, "alice", {scope}))
            answered["lacking"] = _access_requests(client, lacking, victim=victim)[
                key
            ]().status_code
            answered["holding"] = _access_requests(client, holding, victim=victim)[
                key
            ]().status_code
            assert answered["none"] == 401, (key, answered)
            assert answered["lacking"] == 403, (key, answered)
            assert 200 <= answered["holding"] < 300, (key, answered)
            if credential == "cookie":
                assert SESSION_COOKIE in operation["responses"]["401"]["description"], key
                elsewhere = {**holding, "Sec-Fetch-Site": "cross-site"}
                response = _access_requests(client, elsewhere, victim=victim)[key]()
                answered["cross-site"] = response.status_code
                assert response.status_code == 403, (key, answered)
                refused.add((*key, 403, response.json()["error"]))
        else:
            forged = send({"Authorization": f"Bearer {new_token()}"})
            answered["forged"] = _access_requests(client, forged, victim=victim)[key]().status_code
            assert 200 <= answered["none"] < 300, (key, answered)
            assert 200 <= answered["forged"] < 300, (key, answered)
        observed |= {(method, path, status) for status in answered.values() if status >= 400}

    # An editor of the project, so it is the run's recorder that refuses him.
    store.set_member("bob", project=_ACCESS_PROJECT, role=EDITOR_ROLE)
    for key in (("POST", "/runs"), ("POST", "/runs/{run_id}/heartbeat")):
        status = _access_requests(client, bobs)[key]().status_code
        assert status == 409, key
        observed.add((*key, status))

    assert _undocumented_statuses(document, observed) == set()
    assert {error for *_, error in refused} <= {"cross_site_request"}
    assert _unnamed_errors(document, refused) == set()


def test_every_admin_operation_refuses_a_non_admins_token_holding_every_scope() -> None:
    """The admin scope is not enough: its user must be an admin now. A
    token holding every scope, made for a user who is not one, as the store
    allows and only `authenticate` catches, gets 403 on every operation
    whose `403` names the admin scope."""
    store = InMemoryExecutionStore()
    now = datetime.now(timezone.utc)
    store.create_user("bob", admin=False, created_at=now)
    token = new_token()
    store.create_token(
        "bob", digest=token_digest(token), label="", scopes=frozenset(SCOPES), created_at=now
    )
    client = TestClient(create_app(store))
    document = _parsed_document()
    requests = _access_requests(client, {"Authorization": f"Bearer {token}"})
    admin_operations = [
        key
        for key in requests
        if _documented_scope(document["paths"][key[1]][key[0].lower()]) == ADMIN_SCOPE
    ]

    assert len(admin_operations) == 8
    assert ("POST", "/projects") in admin_operations
    for key in admin_operations:
        response = requests[key]()
        assert response.status_code == 403, (key, response.text)
    assert [user.name for user in store.list_users()] == ["bob"]
    assert [project.name for project in store.list_projects()] == [DEFAULT_PROJECT]


# The roles from least to most: each may do what the ones before it may.
_RANKED_ROLES = (VIEWER_ROLE, EDITOR_ROLE, OWNER_ROLE)


def _documented_role(operation: Mapping[str, Any]) -> str | None:
    """The role within a project an operation's `403` says its user needs:
    the one `insufficient_role` names, or the viewer role where only
    `not_a_member` can refuse them; `None` for an operation no role
    bounds."""
    description = operation["responses"].get("403", {}).get("description", "")
    match = re.search(r"below (\w+) \(insufficient_role\)", description)
    if match is not None:
        return str(match.group(1))
    return VIEWER_ROLE if "(not_a_member)" in description else None


@pytest.mark.parametrize("credential", sorted(_CREDENTIALS))
def test_every_operation_in_a_project_needs_the_role_its_document_names(credential: str) -> None:
    """On a server with users, driven by the document: every operation
    under a project or a run id, and a report, names the role its user
    needs in the project, and a user who is not an admin, with a token
    granting exactly the scope it names, is refused `not_a_member` with no
    role there, `insufficient_role` with the one just below it, and served
    with it -- recording the run the later ones ask about, and keeping it
    alive, as its own -- whether the token is sent in the header or in the
    session cookie. Neither refusal carries a challenge, since no other
    token of the user's would help. A route that checks the wrong role, or
    a document that names the wrong one, fails here."""
    store = InMemoryExecutionStore()
    now = datetime.now(timezone.utc)
    store.create_user("bob", admin=False, created_at=now)
    store.create_project(_ACCESS_PROJECT, created_at=now)
    client = TestClient(create_app(store))
    document = _parsed_document()
    roles = {
        key: role
        for key in _access_requests(client, {})
        for role in [_documented_role(document["paths"][key[1]][key[0].lower()])]
        if role is not None
    }
    assert set(roles) == {
        (method, path)
        for method, path in _declared_operations(document)
        if "{project}" in path or "{run_id}" in path
    } | {("POST", "/runs")}
    assert set(roles.values()) == ROLES

    def answer(key: tuple[str, str], role: str | None) -> httpx.Response:
        """`key`'s request from `bob`, holding `role` in the project and a
        token granting exactly the scope `key`'s `403` names."""
        store.remove_member("bob", project=_ACCESS_PROJECT)
        if role is not None:
            store.set_member("bob", project=_ACCESS_PROJECT, role=role)
        scope = _documented_scope(document["paths"][key[1]][key[0].lower()])
        assert scope is not None, key
        credentials = _CREDENTIALS[credential](_bearer(store, "bob", {scope}))
        return _access_requests(client, credentials)[key]()

    for key, role in roles.items():
        rank = _RANKED_ROLES.index(role)
        refused = {"not_a_member": answer(key, None)}
        if rank > 0:
            refused["insufficient_role"] = answer(key, _RANKED_ROLES[rank - 1])
        for error, response in refused.items():
            assert (response.status_code, response.json()["error"]) == (403, error), (key, role)
            assert "WWW-Authenticate" not in response.headers, key
        served = answer(key, role)
        assert 200 <= served.status_code < 300, (key, role, served.text)


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
    token_id = document["components"]["parameters"]["token_id"]["schema"]
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    client = TestClient(create_app(store), headers=_admin(store))
    try:
        runs = f"/api/v1/projects/{DEFAULT_PROJECT}/runs"
        assert client.get(runs, params={"offset": offset["maximum"]}).status_code == 200
        past = client.get(runs, params={"offset": offset["maximum"] + 1})
        assert past.status_code == 422
        revoke = "/api/v1/tokens/{}/revoke"
        assert client.post(revoke.format(token_id["maximum"])).status_code == 404
        assert client.post(revoke.format(token_id["maximum"] + 1)).status_code == 422
        assert client.post(revoke.format(token_id["minimum"] - 1)).status_code == 422

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


def test_every_created_token_is_declared_and_sent_as_not_to_be_stored(
    cheap_passwords: None,
) -> None:
    """The three answers that carry a token, a made one's, a login's and a
    browser session's, say no cache may keep it, in the document and on the
    wire, and they and signing out, which clears the session's cookie, are
    the only ones whose document declares a header. On the wire every
    other answer says so too, by the default `service/web.py`'s
    `SecurityHeaders` adds, once."""
    document = _parsed_document()
    store = InMemoryExecutionStore()
    client = TestClient(create_app(store), headers=_admin(store))
    store.set_password(
        "alice", password_hash=hash_password(_PASSWORD), changed_at=datetime.now(timezone.utc)
    )

    answers = {
        "/tokens": client.post("/api/v1/tokens", json={"user": "alice"}),
        "/login": client.post("/api/v1/login", json={"name": "alice", "password": _PASSWORD}),
    }

    signed_in = client.post(
        "/api/v1/session", json={"name": "alice", "password": _PASSWORD}, headers=_SAME_ORIGIN
    )
    signed_out = client.delete("/api/v1/session", headers=_SAME_ORIGIN)

    for path, response in answers.items():
        created = document["paths"][path]["post"]["responses"]["201"]
        assert set(created["headers"]) == {"Cache-Control"}, path
        assert response.status_code == 201, path
        assert response.headers["Cache-Control"] == "no-store", path
    session = document["paths"]["/session"]
    assert set(session["post"]["responses"]["201"]["headers"]) == {"Set-Cookie", "Cache-Control"}
    assert set(session["delete"]["responses"]["204"]["headers"]) == {"Set-Cookie"}
    assert signed_in.status_code == 201
    assert signed_in.headers.get_list("Cache-Control") == ["no-store"]
    assert signed_in.headers["Set-Cookie"].startswith(f"{SESSION_COOKIE}=vantage_")
    assert signed_out.status_code == 204
    assert signed_out.headers["Set-Cookie"].startswith(f'{SESSION_COOKIE}="";')
    assert client.get("/api/v1/tokens").headers.get_list("Cache-Control") == ["no-store"]
    headed = {
        (method, path, status)
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
        for status, answer in operation["responses"].items()
        if "headers" in answer
    }
    assert headed == {
        ("post", "/tokens", "201"),
        ("post", "/login", "201"),
        ("post", "/session", "201"),
        ("delete", "/session", "204"),
    }


def test_the_documented_user_name_pattern_is_the_domains() -> None:
    """The document spells the domain's anchors the way JSON Schema does."""
    declared = _declared_schemas()["UserCreateRequest"]["properties"]["name"]["pattern"]

    assert declared == USER_NAME_PATTERN.replace("\\A", "^").replace("\\Z", "$")


def test_the_documented_project_name_patterns_are_the_domains() -> None:
    """Both places a client sends a project name, a new project and a
    report's `project`, declare the domain's rule, anchors spelt as JSON
    Schema spells them."""
    schemas = _declared_schemas()
    expected = PROJECT_NAME_PATTERN.replace("\\A", "^").replace("\\Z", "$")

    assert schemas["ProjectCreateRequest"]["properties"]["name"]["pattern"] == expected
    assert schemas["SessionReport"]["properties"]["project"]["pattern"] == expected


def test_the_documented_password_rule_is_the_domains() -> None:
    """Every place the document states the rule a password to set follows
    -- each such field, and each `invalid_password` rejection -- states the
    domain's bounds."""
    document = _parsed_document()
    schemas = document["components"]["schemas"]
    rule = f"{PASSWORD_MIN_CHARS} to {PASSWORD_MAX_CHARS} characters once NFKC-normalised"
    statements = [
        schemas["PasswordChangeRequest"]["properties"]["new_password"]["description"],
        schemas["PasswordSetRequest"]["properties"]["password"]["description"],
        *(
            description
            for operations in document["paths"].values()
            for operation in operations.values()
            for description in [operation["responses"].get("422", {}).get("description", "")]
            if "(invalid_password)" in description
        ),
    ]

    assert len(statements) == 4
    assert all(rule in statement for statement in statements), statements


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
    "UserCreateRequest": UserCreateRequest,
    "UserUpdateRequest": UserUpdateRequest,
    "TokenCreateRequest": TokenCreateRequest,
    "LoginRequest": LoginRequest,
    "PasswordChangeRequest": PasswordChangeRequest,
    "PasswordSetRequest": PasswordSetRequest,
    "ProjectCreateRequest": ProjectCreateRequest,
    "MemberSetRequest": MemberSetRequest,
}
_RESPONSE_SCHEMAS: dict[str, type[BaseModel]] = {
    "Rejection": RejectionResponse,
    "Acknowledgement": Acknowledgement,
    "HeartbeatAcknowledgement": HeartbeatAcknowledgement,
    "RunVcs": RunVcsResponse,
    "OutcomeCounts": OutcomeCountsResponse,
    "Baseline": BaselineResponse,
    "ChangeCounts": ChangeCountsResponse,
    "Comparison": ComparisonResponse,
    "Streak": StreakResponse,
    "ChangeItem": ChangeItemResponse,
    "ChangesResponse": ChangesResponse,
    "RunListItem": RunListItemResponse,
    "RunListResponse": RunListResponse,
    "MetadataHorizon": MetadataHorizonResponse,
    "RunDetailResponse": RunDetailResponse,
    "RunOutcomesResponse": RunOutcomesResponse,
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
    "UserResponse": UserResponse,
    "UserListResponse": UserListResponse,
    "TokenResponse": TokenResponse,
    "TokenListResponse": TokenListResponse,
    "CreatedTokenResponse": CreatedTokenResponse,
    "ProjectResponse": ProjectResponse,
    "ProjectListResponse": ProjectListResponse,
    "MemberResponse": MemberResponse,
    "MemberListResponse": MemberListResponse,
    "SessionUserResponse": SessionUserResponse,
    "SessionResponse": SessionResponse,
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
    ("Comparison", "state"): COMPARISON_STATES,
    ("ChangeItem", "change"): CHANGES,
    ("ChangeItem", "outcome"): OUTCOMES,
    ("ChangeItem", "was"): OUTCOMES,
    ("ResultDetailResponse", "change"): RESULT_CHANGES,
    ("ResultDetailResponse", "was"): OUTCOMES,
    ("HistoryEntry", "change"): RESULT_CHANGES,
    ("ResultListItem", "outcome"): OUTCOMES,
    ("ResultDetailResponse", "outcome"): OUTCOMES,
    ("HistoryEntry", "outcome"): OUTCOMES,
    ("MetadataItem", "status"): KEY_STATUSES,
    ("MetadataItem", "source"): METADATA_SOURCES,
    ("MetadataFile", "content_type"): METADATA_CONTENT_TYPES,
    ("MetadataFile", "status"): FILE_STATUSES,
    ("TokenCreateRequest", "scopes"): SCOPES,
    ("TokenResponse", "scopes"): SCOPES,
    ("CreatedTokenResponse", "scopes"): SCOPES,
    ("ProjectResponse", "role"): ROLES,
    ("MemberSetRequest", "role"): ROLES,
    ("MemberResponse", "role"): ROLES,
    ("MemberListResponse", "everyone"): ROLES,
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
    `vantage.core.domain.liveness.PRESENTATIONS`, the metadata
    vocabularies in `vantage.core.domain.metadata`, the token scopes and
    the project roles.

    Both directions again, at two levels: which properties declare a closed
    vocabulary at all, and what that vocabulary contains. Replacing
    `ResultListItem.outcome`'s enum with values the server never emits fails the
    second; adding an enum to a property nobody vetted fails the first,
    rather than passing unchecked because no expectation was written for
    it. An array property's vocabulary is on its `items`, and is read from
    there."""
    found: dict[tuple[str, str], Mapping[str, Any]] = {
        (name, field): declared
        for name, schema in _declared_schemas().items()
        for field, declaration in schema.get("properties", {}).items()
        for declared in (declaration, declaration.get("items", {}))
        if "enum" in declared
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


def test_every_password_is_declared_write_only() -> None:
    """A field a request model keeps out of its `repr` is a password, and
    its schema says so -- `format: password` and `writeOnly` -- so a
    generated client masks it and never expects it back. Both directions:
    a password left unmarked fails, and so does either mark on anything
    else."""
    schemas = _declared_schemas()
    marked = {
        (name, field)
        for name, schema in schemas.items()
        for field, declaration in schema.get("properties", {}).items()
        if "writeOnly" in declaration or declaration.get("format") == "password"
    }
    passwords = {
        (name, field)
        for name, model in _REQUEST_SCHEMAS.items()
        for field, info in model.model_fields.items()
        if not info.repr
    }

    assert passwords, "no request model keeps a field out of its repr"
    assert marked == passwords
    for name, field in sorted(passwords):
        declaration = schemas[name]["properties"][field]
        assert (declaration.get("format"), declaration.get("writeOnly")) == ("password", True), (
            f"{name}.{field}"
        )
