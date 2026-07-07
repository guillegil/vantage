"""RED: `EventSink` Protocol -- any concrete sink structurally satisfies it,
and `emit` must reject an invalid envelope (unsupported `schema_version`,
malformed payload) BEFORE any dispatch/persistence is attempted (spec
Domain 1; design SS3, SS11 decoupling litmus)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from vantage.core.domain.events import CONTRACT_VERSION, ContractVersionError, EventEnvelope
from vantage.core.ingestion.sink import EventSink, validate_before_dispatch


class FakeEventSink:
    """In-memory fake -- only records envelopes that pass validation,
    proving `EventSink.emit` implementations enforce validate-before-
    dispatch rather than persisting first and checking later."""

    def __init__(self) -> None:
        self.dispatched: list[EventEnvelope] = []

    def emit(self, envelope: EventEnvelope) -> None:
        validate_before_dispatch(envelope)
        self.dispatched.append(envelope)


def _envelope(**overrides: Any) -> EventEnvelope:
    defaults: dict[str, Any] = {
        "schema_version": CONTRACT_VERSION,
        "event_type": "run_discovered",
        "run_id": "run-1",
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "payload": {"node_ids": ["tests/test_x.py::test_a"]},
    }
    defaults.update(overrides)
    return EventEnvelope(**defaults)


def test_fake_sink_satisfies_event_sink_protocol() -> None:
    assert isinstance(FakeEventSink(), EventSink)


def test_emit_dispatches_valid_envelope() -> None:
    sink = FakeEventSink()
    envelope = _envelope()

    sink.emit(envelope)

    assert sink.dispatched == [envelope]


def test_emit_rejects_unsupported_schema_version_before_dispatch() -> None:
    sink = FakeEventSink()
    envelope = _envelope(schema_version=CONTRACT_VERSION + 1)

    with pytest.raises(ContractVersionError):
        sink.emit(envelope)

    assert sink.dispatched == []


def test_emit_rejects_malformed_payload_before_dispatch() -> None:
    sink = FakeEventSink()
    envelope = _envelope(payload={"node_ids": "not-a-list"})

    with pytest.raises(ValidationError):
        sink.emit(envelope)

    assert sink.dispatched == []
