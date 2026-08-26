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

**The `components.schemas` half of the document is checked too, and until
verify round 2 it was not** -- the document claimed its response shapes were
bounded by `test_routes_read.py`'s key-set assertions, but those compare a
response body against a literal written in the test file and never open this
document at all. Three tampers on `v1.yaml` alone -- a required `root` added
to `RunVcs`, `presentation` dropped from `RunListItem.required`, and
`ResultItem.outcome`'s enum replaced with a vocabulary the server never
emits -- each left the whole suite green. The four schema tests at the end of
this module close that: they read the declared schemas out of the parsed
document and the field set out of `model_fields` **independently**, then
compare. Nothing here generates one side from the other -- that is the
derivation design.md Q5 rejects, and it would make these checks unfailable
for the same reason a generated document could never fail the path drift
check above.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, get_args

import httpx2 as httpx
import yaml
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from vantage.core.domain.liveness import PRESENTATIONS
from vantage.core.domain.result import OUTCOMES
from vantage.service.app import create_app
from vantage.service.schemas import (
    Acknowledgement,
    FailureProjectionResponse,
    HeartbeatAcknowledgement,
    HistoryEntryResponse,
    HistoryResponse,
    ResultDetailResponse,
    ResultListItemResponse,
    ResultsResponse,
    RunDetailResponse,
    RunListItemResponse,
    RunListResponse,
    RunReport,
    RunVcsResponse,
    SessionReport,
)
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
    # A single passing result, present so `GET /runs/{run_id}/result` (the
    # single-result endpoint, design.md D78) has something to bind against.
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
    ]
    bound_keys = {key for key, _ in ordered_bindings}
    assert bound_keys == declared, "binding table does not cover every documented path"

    for key, call in ordered_bindings:
        response = call()
        assert 200 <= response.status_code < 300, (key, response.text)

    store.close()


# --- 6.7 (verify round 2) -------------------------------------------------

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
}
_RESPONSE_SCHEMAS: dict[str, type[BaseModel]] = {
    "Acknowledgement": Acknowledgement,
    "HeartbeatAcknowledgement": HeartbeatAcknowledgement,
    "RunVcs": RunVcsResponse,
    "RunListItem": RunListItemResponse,
    "RunListResponse": RunListResponse,
    "RunDetailResponse": RunDetailResponse,
    "FailureProjection": FailureProjectionResponse,
    "ResultListItem": ResultListItemResponse,
    "ResultsResponse": ResultsResponse,
    "ResultDetailResponse": ResultDetailResponse,
    "HistoryEntry": HistoryEntryResponse,
    "HistoryResponse": HistoryResponse,
}
_BOUND_MODELS: dict[str, type[BaseModel]] = {**_REQUEST_SCHEMAS, **_RESPONSE_SCHEMAS}

# Where the document declares a closed vocabulary, the values it must equal.
# Taken from the domain frozensets rather than the response model, because
# the response models type these fields as plain `str` -- the document is
# stricter than the model on purpose, and the domain is what the server can
# actually emit. Every `enum` in the document must appear here, and every
# entry here must appear in the document (both directions, below).
_DECLARED_ENUMS: dict[tuple[str, str], frozenset[str]] = {
    ("RunListItem", "presentation"): PRESENTATIONS,
    ("RunDetailResponse", "presentation"): PRESENTATIONS,
    ("ResultListItem", "outcome"): OUTCOMES,
    ("ResultDetailResponse", "outcome"): OUTCOMES,
    ("HistoryEntry", "outcome"): OUTCOMES,
}


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
    """*(api-interface-document -> Machine-readable interface document;
    design.md D56 -- one source per fact.)*

    The binding table itself, in both directions. A schema added to
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
    """*(D56 -- the response schemas are a second statement of
    `service/schemas.py`'s facts, and this is the check that binds them.)*

    Both directions, per schema: a model field the document never declares
    is an undocumented part of the contract, and a declared property with no
    model behind it is a promise the server does not keep. `RunVcs` gaining
    a `root` property -- the exact field design.md D59 exists to keep off the
    wire -- fails the second direction."""
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
    """*(D56.)* A property the document declares optional while the model
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
    """*(D56.)* Whether each declared property admits `null` must match
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
    """*(D56; `vantage.core.domain.result.OUTCOMES` and
    `vantage.core.domain.liveness.PRESENTATIONS`.)*

    Both directions again, at two levels: which properties declare a closed
    vocabulary at all, and what that vocabulary contains. Replacing
    `ResultItem.outcome`'s enum with values the server never emits fails the
    second; adding an enum to a property nobody vetted fails the first,
    rather than passing unchecked because no expectation was written for
    it."""
    found: dict[tuple[str, str], frozenset[str]] = {
        (name, field): frozenset(declaration["enum"])
        for name, schema in _declared_schemas().items()
        for field, declaration in schema.get("properties", {}).items()
        if "enum" in declaration
    }

    assert set(found) == set(_DECLARED_ENUMS), (
        "the document's enum-declaring properties are "
        f"{sorted(found)}, expected {sorted(_DECLARED_ENUMS)}"
    )
    for key, declared in found.items():
        expected = _DECLARED_ENUMS[key]
        assert declared == expected, (
            f"{key[0]}.{key[1]}: the document declares {sorted(declared)}, "
            f"the domain permits {sorted(expected)}"
        )
