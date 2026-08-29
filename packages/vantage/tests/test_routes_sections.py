"""Definitions API -- the three CRUD routes for `test_sections` (design.md
D87, D89; spec `test-sections`, `user-configuration`).

Runs the app factory against an injected `InMemoryExecutionStore`, the same
choice `test_ingestion.py` makes for a route slice that does not depend on
the SQLite row-to-domain mappers -- the port contract
(`vantage_port_contract.py`) already proves the two adapters agree beneath
the port. No `req` marker: each test names its capability and scenario in
its own docstring.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from vantage.core.domain.sections import (
    MAX_SECTIONS,
    SECTION_NAME_MAX_CHARS,
    SECTION_PREFIX_MAX_CHARS,
)
from vantage.service.app import create_app
from vantage.service.routes.sections import TEST_SECTIONS_NAMESPACE
from vantage.storage.memory import InMemoryExecutionStore
from vantage_port_contract import _execution, _result

_SECTIONS = "/api/v1/config/sections"

_UNKNOWN_RUN_ID = "0" * 32


@pytest.fixture
def store() -> Iterator[InMemoryExecutionStore]:
    store = InMemoryExecutionStore()
    yield store
    store.close()


@pytest.fixture
def client(store: InMemoryExecutionStore) -> TestClient:
    return TestClient(create_app(store))


def _upsert(client: TestClient, name: str, prefix: str) -> httpx.Response:
    return client.post(_SECTIONS, json={"name": name, "prefix": prefix})


# --- POST: create/update, trailing-slash coercion ---------------------------


def test_posting_a_new_section_returns_201(client: TestClient) -> None:
    response = _upsert(client, "Checkout", "tests/checkout")

    assert response.status_code == 201
    assert response.json() == {"name": "Checkout", "prefix": "tests/checkout/"}


def test_posting_an_existing_name_returns_200_not_201(client: TestClient) -> None:
    _upsert(client, "Checkout", "tests/checkout")

    response = _upsert(client, "Checkout", "tests/checkout-v2")

    assert response.status_code == 200
    assert response.json() == {"name": "Checkout", "prefix": "tests/checkout-v2/"}


def test_a_missing_trailing_slash_is_coerced_on_write(client: TestClient) -> None:
    """Scenario: A missing trailing slash is coerced on write."""
    response = _upsert(client, "Billing", "tests/billing")

    assert response.json()["prefix"] == "tests/billing/"


# --- POST: rejections --------------------------------------------------------


def test_an_empty_or_whitespace_only_name_is_rejected(client: TestClient) -> None:
    """Scenario: An empty or whitespace-only name is rejected."""
    response = _upsert(client, "   ", "tests/x")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_section_name"


@pytest.mark.parametrize("name", ["Unassigned", "UNASSIGNED", "unassigned"])
def test_unassigned_is_reserved_regardless_of_casing(client: TestClient, name: str) -> None:
    """Scenario: "unassigned" is reserved regardless of casing."""
    response = _upsert(client, name, "tests/x")

    assert response.status_code == 422
    assert response.json()["error"] == "reserved_section_name"


@pytest.mark.parametrize(
    ("name", "prefix", "expected_error"),
    [
        ("x" * (SECTION_NAME_MAX_CHARS + 1), "tests/x", "invalid_section_name"),
        ("Billing", "x" * (SECTION_PREFIX_MAX_CHARS + 1), "invalid_section_prefix"),
    ],
)
def test_an_over_length_name_or_prefix_is_rejected(
    client: TestClient, name: str, prefix: str, expected_error: str
) -> None:
    response = _upsert(client, name, prefix)

    assert response.status_code == 422
    assert response.json()["error"] == expected_error


def test_too_many_sections_is_rejected_at_the_bound(client: TestClient) -> None:
    for index in range(MAX_SECTIONS):
        response = _upsert(client, f"Section{index}", f"tests/section{index}")
        assert response.status_code == 201

    response = _upsert(client, "OneTooMany", "tests/one-too-many")

    assert response.status_code == 422
    assert response.json()["error"] == "too_many_sections"


# --- DELETE -------------------------------------------------------------


def test_delete_then_delete_again_is_204_then_404(client: TestClient) -> None:
    """Scenario: A deleted setting is not read back."""
    _upsert(client, "Checkout", "tests/checkout")

    first = client.delete(_SECTIONS, params={"name": "Checkout"})
    second = client.delete(_SECTIONS, params={"name": "Checkout"})

    assert first.status_code == 204
    assert second.status_code == 404
    assert second.json()["error"] == "unknown_section"


# --- GET ------------------------------------------------------------------


def test_an_upserted_section_is_listed(client: TestClient) -> None:
    """Scenario: An upserted section is listed."""
    _upsert(client, "Checkout", "tests/checkout")

    response = client.get(_SECTIONS)

    assert response.status_code == 200
    assert response.json() == {"items": [{"name": "Checkout", "prefix": "tests/checkout/"}]}


# --- Threat matrix: no echo, byte-identical quoting -------------------------


def test_a_crlf_and_script_tag_name_is_rejected_without_appearing_in_the_body(
    client: TestClient,
) -> None:
    """Threat matrix: "Client-chosen text reaching a rejection body" -- a
    name made hostile AND over-length still triggers only the fixed
    `invalid_section_name` message; the submitted text never rides along."""
    hostile = ("\r\n</script>\r\n" * 20) + ("x" * SECTION_NAME_MAX_CHARS)

    response = _upsert(client, hostile, "tests/x")

    assert response.status_code == 422
    assert "</script>" not in response.text
    assert "\r\n" not in response.text


def test_a_quoting_shaped_name_round_trips_byte_identically(client: TestClient) -> None:
    """Threat matrix: "Client-chosen text reaching SQL" -- bound parameters
    only; a name containing quote characters is stored and returned intact,
    never escaped or normalised."""
    name = 'He said "hi", didn\'t he?'

    response = _upsert(client, name, "tests/quoting")

    assert response.status_code == 201
    assert response.json()["name"] == name

    listing = client.get(_SECTIONS)
    assert listing.json()["items"][0]["name"] == name


# --- GET /runs/{run_id}/sections: the run aggregate (Phase 4) ---------------

_SECTIONED_START = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)


def _run_sections(client: TestClient, run_id: str) -> httpx.Response:
    return client.get(f"/api/v1/runs/{run_id}/sections")


def test_run_sections_summary_unknown_run_is_404(client: TestClient) -> None:
    """Scenario: A run's summary reflects its sections -- the unknown-run
    half (design.md D87's fourth route)."""
    response = _run_sections(client, _UNKNOWN_RUN_ID)

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_run"


def test_run_sections_summary_worked_example_yields_94_4_percent(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """Scenario: The worked example yields 94.4% (spec test-sections: pass
    percentage) -- 80 passed, 5 xfailed, 2 xpassed, 3 failed, 10 skipped,
    reached through the live route rather than `summarize_sections`
    directly."""
    _upsert(client, "Billing", "tests/billing")
    run_id = "1" * 32
    outcomes = (
        ["passed"] * 80 + ["xfailed"] * 5 + ["xpassed"] * 2 + ["failed"] * 3 + ["skipped"] * 10
    )
    results = [
        _result(f"tests/billing/test_x.py::test_{index}", outcome=outcome)
        for index, outcome in enumerate(outcomes)
    ]
    store.record_session(
        _execution(run_id, started=_SECTIONED_START), results=results, received_at=_SECTIONED_START
    )

    response = _run_sections(client, run_id)

    assert response.status_code == 200
    body = response.json()
    billing = next(item for item in body["items"] if item["name"] == "Billing")
    assert billing == {
        "name": "Billing",
        "total": 100,
        "measured": 90,
        "passing": 85,
        "pass_percentage": 94.4,
    }


def test_run_sections_summary_totals_reconcile_with_unassigned_results(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """Scenario: Section totals plus unassigned equal the run total (spec
    test-sections: `unassigned` bucket is always present and reconciles) --
    a run carrying results that match no section."""
    _upsert(client, "Billing", "tests/billing")
    run_id = "2" * 32
    results = [
        _result("tests/billing/test_x.py::test_0"),
        _result("tests/billing/test_x.py::test_1", outcome="failed"),
        _result("tests/other/test_y.py::test_z"),
        _result("tests/other/test_y.py::test_w", outcome="skipped"),
    ]
    store.record_session(
        _execution(run_id, started=_SECTIONED_START), results=results, received_at=_SECTIONED_START
    )

    response = _run_sections(client, run_id)

    assert response.status_code == 200
    body = response.json()
    total = sum(item["total"] for item in body["items"]) + body["unassigned"]["total"]
    assert total == len(results)
    assert body["unassigned"]["total"] == 2


def test_renaming_a_section_regroups_history_with_zero_writes(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """Scenario: Renaming a section re-groups history with no backfill (spec
    test-sections: longest-prefix-wins derivation at read time) -- the
    load-bearing test for "derived at read time": the run/result rows must
    be byte-identical before and after the rename."""
    _upsert(client, "Billing", "tests/billing")
    run_id = "3" * 32
    results = [_result("tests/billing/test_x.py::test_0")]
    store.record_session(
        _execution(run_id, started=_SECTIONED_START), results=results, received_at=_SECTIONED_START
    )
    before_results = store.get_results(run_id)
    before_execution = store.get_execution(run_id)

    _upsert(client, "Accounts", "tests/billing")
    delete_response = client.delete(_SECTIONS, params={"name": "Billing"})
    assert delete_response.status_code == 204

    response = _run_sections(client, run_id)

    assert response.status_code == 200
    body = response.json()
    assert {item["name"] for item in body["items"]} == {"Accounts"}
    accounts = next(item for item in body["items"] if item["name"] == "Accounts")
    assert accounts["total"] == 1
    assert body["unassigned"]["total"] == 0
    assert store.get_results(run_id) == before_results
    assert store.get_execution(run_id) == before_execution


def test_run_sections_summary_malformed_stored_value_is_500(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """Scenario: a stored `value` failing its namespace's model is a named
    `500`, never a traceback (design.md D89 -- `UnreadableSettingError`)."""
    run_id = "4" * 32
    store.record_session(
        _execution(run_id, started=_SECTIONED_START), results=[], received_at=_SECTIONED_START
    )
    store.upsert_setting(
        TEST_SECTIONS_NAMESPACE, "Broken", value="not valid json", updated_at=_SECTIONED_START
    )

    response = _run_sections(client, run_id)

    assert response.status_code == 500
    assert response.json()["error"] == "unreadable_setting"
