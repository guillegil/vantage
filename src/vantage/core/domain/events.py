"""The versioned ingestion event envelope -- the contract producers (the
collector) and core (storage) agree on (design §3).

`CONTRACT_VERSION` increments for any breaking change to the envelope or a
payload shape (spec Domain 1 -- Contract Versioning Policy). Core rejects
envelopes it does not recognize explicitly rather than attempting a
best-effort parse.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

CONTRACT_VERSION = 1


class EventType(StrEnum):
    """Known event types for `CONTRACT_VERSION` 1 (design §3)."""

    RUN_STARTED = "run_started"
    RUN_DISCOVERED = "run_discovered"
    TEST_FINISHED = "test_finished"
    RUN_FINISHED = "run_finished"
    RUN_HEARTBEAT = "run_heartbeat"


class ContractVersionError(Exception):
    """Raised when an envelope declares a `schema_version` core does not
    recognize (spec Domain 1 -- Unsupported schema_version rejected)."""


class UnknownEventTypeError(Exception):
    """Raised when an envelope's `event_type` has no registered payload
    model for its `schema_version`.

    Callers MUST treat this as an isolated, per-event rejection: log/skip
    and keep processing subsequent events for the same or other runs (spec
    Domain 1 -- Unknown Event Type Handling).
    """


class RunStartedPayload(BaseModel):
    invocation: dict[str, Any]
    env_snapshot: dict[str, str]
    user: str
    root_dir: str


class RunDiscoveredPayload(BaseModel):
    node_ids: list[str]


class TestFinishedPayload(BaseModel):
    node_id: str
    outcome: str
    duration: float
    phases: dict[str, dict[str, Any]]
    longrepr: str | None = None


class RunFinishedPayload(BaseModel):
    exit_code: int
    totals: dict[str, int]


class RunHeartbeatPayload(BaseModel):
    at: datetime


# Validator registry (design §3): schema_version -> event_type -> payload
# model. `EventType` members hash and compare equal to their plain string
# value (StrEnum), so lookups by the envelope's raw `event_type` string work
# directly against these enum-keyed dicts.
_PAYLOAD_REGISTRY: dict[int, dict[str, type[BaseModel]]] = {
    CONTRACT_VERSION: {
        EventType.RUN_STARTED: RunStartedPayload,
        EventType.RUN_DISCOVERED: RunDiscoveredPayload,
        EventType.TEST_FINISHED: TestFinishedPayload,
        EventType.RUN_FINISHED: RunFinishedPayload,
        EventType.RUN_HEARTBEAT: RunHeartbeatPayload,
    }
}


class EventEnvelope(BaseModel):
    """The versioned envelope every ingestion event crosses core through.

    `event_type` is intentionally a plain `str` here (not the `EventType`
    enum): an unrecognized event_type must be an isolated, catchable
    rejection (`UnknownEventTypeError` from `validate_payload`), not a
    structural validation failure indistinguishable from a malformed
    envelope.
    """

    schema_version: int
    event_type: str
    run_id: str
    timestamp: datetime
    payload: dict[str, Any]


def parse_envelope(raw: dict[str, Any]) -> EventEnvelope:
    """Validate the envelope's own shape (required fields, types).

    Raises:
        pydantic.ValidationError: the envelope is structurally malformed
            (e.g. missing `run_id`). This does NOT validate `payload`
            against its per-event-type model -- see `validate_payload`.
    """

    return EventEnvelope.model_validate(raw)


def validate_payload(envelope: EventEnvelope) -> BaseModel:
    """Validate `envelope.payload` against the model registered for its
    `(schema_version, event_type)` pair.

    Raises:
        ContractVersionError: `schema_version` is not recognized by core.
        UnknownEventTypeError: `event_type` has no registered payload model
            for this `schema_version`. Callers must treat this as an
            isolated, skippable rejection (spec Domain 1).
    """

    version_registry = _PAYLOAD_REGISTRY.get(envelope.schema_version)
    if version_registry is None:
        raise ContractVersionError(
            f"unsupported schema_version={envelope.schema_version} "
            f"(core supports up to {CONTRACT_VERSION})"
        )

    payload_model = version_registry.get(envelope.event_type)
    if payload_model is None:
        raise UnknownEventTypeError(
            f"unrecognized event_type={envelope.event_type!r} for "
            f"schema_version={envelope.schema_version}"
        )

    return payload_model.model_validate(envelope.payload)
