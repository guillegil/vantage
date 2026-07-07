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
