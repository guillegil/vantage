"""RED: the versioned ingestion envelope validates structure and enforces
contract versioning without ever crashing the caller on a single bad event
(spec Domain 1 -- Ingestion Event Contract)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from vantage.core.domain.events import (
    CONTRACT_VERSION,
    ContractVersionError,
    EventEnvelope,
    RunStartedPayload,
    TestFinishedPayload,
    UnknownEventTypeError,
    parse_envelope,
    validate_payload,
)


def _raw_run_started(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "schema_version": CONTRACT_VERSION,
        "event_type": "run_started",
        "run_id": "01J000000000000000000000",
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "payload": {
            "invocation": {"cmd": ["pytest"], "args": []},
            "env_snapshot": {},
            "user": "alice",
            "root_dir": "/repo",
        },
    }
    raw.update(overrides)
    return raw


def test_valid_envelope_parses_and_validates_payload() -> None:
    envelope = parse_envelope(_raw_run_started())
    assert isinstance(envelope, EventEnvelope)
    assert envelope.run_id == "01J000000000000000000000"

    payload = validate_payload(envelope)
    assert payload.user == "alice"  # type: ignore[attr-defined]


def test_missing_run_id_raises() -> None:
    raw = _raw_run_started()
    del raw["run_id"]

    with pytest.raises(ValidationError):
        parse_envelope(raw)


def test_unknown_schema_version_raises_contract_version_error() -> None:
    envelope = parse_envelope(_raw_run_started(schema_version=CONTRACT_VERSION + 1))

    with pytest.raises(ContractVersionError):
        validate_payload(envelope)


def test_unknown_event_type_is_isolated_and_does_not_block_next_event() -> None:
    good_one = parse_envelope(_raw_run_started())
    bad = parse_envelope(_raw_run_started(event_type="totally_unknown"))
    good_two = parse_envelope(_raw_run_started())

    validate_payload(good_one)

    with pytest.raises(UnknownEventTypeError):
        validate_payload(bad)

    # The rejection above must not corrupt registry/validator state -- the
    # next valid event for the same run_id still processes cleanly.
    validate_payload(good_two)


def test_run_started_payload_accepts_enriched_selection_metadata() -> None:
    envelope = parse_envelope(
        _raw_run_started(
            payload={
                "invocation": {"cmd": ["pytest"], "args": []},
                "env_snapshot": {},
                "user": "alice",
                "root_dir": "/repo",
                "testpath": "tests/",
                "seed": 42,
                "seed_source": "pytest-strategies",
                "marker_expr": "slow",
                "keyword_expr": "test_foo",
            }
        )
    )

    payload = validate_payload(envelope)
    assert isinstance(payload, RunStartedPayload)
    assert payload.testpath == "tests/"
    assert payload.seed == 42
    assert payload.seed_source == "pytest-strategies"
    assert payload.marker_expr == "slow"
    assert payload.keyword_expr == "test_foo"


def test_run_started_payload_defaults_enriched_fields_to_none_when_absent() -> None:
    envelope = parse_envelope(_raw_run_started())

    payload = validate_payload(envelope)
    assert isinstance(payload, RunStartedPayload)
    assert payload.testpath is None
    assert payload.seed is None
    assert payload.seed_source is None
    assert payload.marker_expr is None
    assert payload.keyword_expr is None


def _raw_test_finished(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "schema_version": CONTRACT_VERSION,
        "event_type": "test_finished",
        "run_id": "01J000000000000000000000",
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "payload": {
            "node_id": "tests/test_x.py::test_y",
            "outcome": "passed",
            "duration": 0.02,
            "phases": {"call": {"name": "call", "outcome": "passed", "duration": 0.01}},
        },
    }
    raw.update(overrides)
    return raw


def test_test_finished_payload_accepts_enriched_identity_and_parameters() -> None:
    envelope = parse_envelope(
        _raw_test_finished(
            payload={
                "node_id": "tests/test_x.py::test_y[1-2]",
                "outcome": "passed",
                "duration": 0.02,
                "phases": {"call": {"name": "call", "outcome": "passed", "duration": 0.01}},
                "base_test_id": "abc123",
                "relpath": "tests/test_x.py",
                "lineno": 10,
                "originalname": "test_y",
                "parameters": {
                    "a": {"value_repr": "1", "type": "int"},
                    "b": {"value_repr": "2", "type": "int"},
                },
                "fixture_names": ["tmp_path", "monkeypatch", "capsys"],
            }
        )
    )

    payload = validate_payload(envelope)
    assert isinstance(payload, TestFinishedPayload)
    assert payload.base_test_id == "abc123"
    assert payload.relpath == "tests/test_x.py"
    assert payload.lineno == 10
    assert payload.originalname == "test_y"
    assert payload.parameters == {
        "a": {"value_repr": "1", "type": "int"},
        "b": {"value_repr": "2", "type": "int"},
    }
    assert payload.fixture_names == ["tmp_path", "monkeypatch", "capsys"]


def test_test_finished_payload_thin_legacy_shape_still_validates() -> None:
    envelope = parse_envelope(_raw_test_finished())

    payload = validate_payload(envelope)
    assert isinstance(payload, TestFinishedPayload)
    assert payload.base_test_id is None
    assert payload.relpath is None
    assert payload.lineno is None
    assert payload.originalname is None
    assert payload.parameters == {}
    assert payload.fixture_names == []
