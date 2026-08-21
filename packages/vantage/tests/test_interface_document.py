"""The hand-written OpenAPI document, its drift check, and the generated
documents being disabled (design.md D53, D55/Q5, D66; Phase 6).

**The document is authored by hand, never generated from `app.routes`.**
That is the entire reason this file exists: a document derived from the
route table it is checked against could never fail its own drift check --
it would always equal itself. `test_a_served_but_undocumented_route_is_reported`
and `test_a_documented_but_unserved_path_is_reported` each prove their
direction can fail, not only that it currently passes (Phases 4 and 5 both
found tests that passed vacuously before their guard existed; this module's
two drift tests are written specifically not to repeat that).

`GET /api/v1/openapi.yaml` is itself declared `read` in the document (task
6.8's own requirement, `api-interface-document` -> "This document, served
as raw bytes"), so it is exercised by `test_every_documented_path_answers_2xx`
like every other read path.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx2 as httpx
import yaml
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from vantage.service.app import create_app
from vantage.storage.memory import InMemoryExecutionStore
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
    """A minimal well-formed `SessionReport` body -- `results` is optional
    (design.md D15) and omitted here on purpose: `list_history`/
    `list_results` answer `200` with an empty page for a run/node id with
    no results, so no result fixture is needed to reach 2xx."""
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
    **This is not the forbidden derivation** (design.md Q5): it only reads
    the *mounted* side of the comparison, never replaces `v1.yaml`, and the
    HTTP endpoint that would have served it is disabled (`openapi_url=None`
    in `create_app`)."""
    schema = app.openapi()
    operations: set[tuple[str, str]] = set()
    for path, methods_at_path in schema.get("paths", {}).items():
        relative_path = path.removeprefix("/api/v1")
        for method in methods_at_path:
            if method.lower() in _HTTP_METHODS:
                operations.add((method.upper(), relative_path))
    return operations


# --- 6.1 ------------------------------------------------------------------


def test_openapi_yaml_serves_the_handwritten_bytes() -> None:
    """*(api-interface-document -> Machine-readable interface document)*.
    `pyyaml` parses the response only here -- never at runtime (module
    docstring, task 6.11)."""
    client = TestClient(create_app(InMemoryExecutionStore()))

    response = client.get("/api/v1/openapi.yaml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/yaml")
    assert response.content == _DOCUMENT_BYTES
    parsed = yaml.safe_load(response.content)
    assert parsed["openapi"].startswith("3.1")


# --- 6.2 ------------------------------------------------------------------


def test_generated_documents_are_disabled() -> None:
    """*(The generated interface documents are disabled)*."""
    client = TestClient(create_app(InMemoryExecutionStore()))

    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


# --- 6.3 ------------------------------------------------------------------


def test_a_served_but_undocumented_route_is_reported() -> None:
    """*(A served-but-undocumented endpoint is reported; The document is
    not derived from the route table it checks)*. **The falsifier.** Half 1
    proves the real app is drift-free; half 2 mounts one extra route the
    document never declares and proves the check reports it -- the check
    must be able to fail before it can be trusted."""
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


# --- 6.4 ------------------------------------------------------------------


def test_a_documented_but_unserved_path_is_reported() -> None:
    """*(the reverse direction, `declared - mounted`)*. Half 1: against the
    real app, empty. Half 2: a document copy carrying one path the app
    never mounts, proving this direction can fail too, not only the
    mounted-but-undeclared direction 6.3 names."""
    declared = _declared_operations(_parsed_document())
    mounted = _mounted_operations(create_app(InMemoryExecutionStore()))

    assert declared - mounted == set()

    tainted_declared = declared | {("GET", "/_never-mounted-probe")}
    assert tainted_declared - mounted == {("GET", "/_never-mounted-probe")}


# --- 6.5 ------------------------------------------------------------------


def test_every_read_operation_is_get_and_every_write_operation_is_not() -> None:
    """*(D53 consistency check; session-ingestion -> Ingestion endpoints
    are marked as writing, not reading)*."""
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


# --- 6.6 ------------------------------------------------------------------


def test_every_documented_path_answers_2xx(tmp_path: Path) -> None:
    """*(Every documented path answers 2xx)*. A binding table maps every
    `(method, path)` the document declares to a callable producing valid
    parameters, driven against a dedicated fixture database -- separate
    from PR7's read-only fixture (design.md D65). Includes
    `GET /api/v1/capabilities` and `GET /api/v1/openapi.yaml` themselves."""
    document = _parsed_document()
    declared = _declared_operations(document)
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    client = TestClient(create_app(store))
    run = f"/api/v1/runs/{'6' * 32}"
    node_id = "tests/test_interface_document_probe.py::test_x"
    report = _report(run.rsplit("/", 1)[-1])

    # Ordered so the fixture data a later binding needs already exists --
    # the run must be reported before it can be read back or heartbeat'd.
    ordered_bindings: list[tuple[tuple[str, str], _Call]] = [
        (("POST", "/runs"), lambda: client.post("/api/v1/runs", json=report)),
        (("POST", "/runs/{run_id}/heartbeat"), lambda: client.post(f"{run}/heartbeat")),
        (("GET", "/runs"), lambda: client.get("/api/v1/runs")),
        (("GET", "/runs/{run_id}"), lambda: client.get(run)),
        (("GET", "/runs/{run_id}/results"), lambda: client.get(f"{run}/results")),
        (
            ("GET", "/tests/history"),
            lambda: client.get("/api/v1/tests/history", params={"node_id": node_id}),
        ),
        (("GET", "/capabilities"), lambda: client.get("/api/v1/capabilities")),
        (("GET", "/openapi.yaml"), lambda: client.get("/api/v1/openapi.yaml")),
    ]
    bound_keys = {key for key, _ in ordered_bindings}
    assert bound_keys == declared, "binding table does not cover every documented path"

    for key, call in ordered_bindings:
        response = call()
        assert 200 <= response.status_code < 300, (key, response.text)

    store.close()
