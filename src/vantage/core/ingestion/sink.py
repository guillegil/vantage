"""`EventSink` -- the ingestion seam producers write envelopes through
(design §3). Swapping the sink (v1 `SqliteEventSink`, a future
`HttpEventSink`) requires no core change -- this is the ingestion half of
the decoupling litmus (design §11).

Every `EventSink.emit` implementation MUST call `validate_before_dispatch`
first and let its exceptions propagate before attempting any persistence
(spec Domain 1 -- an envelope with an unsupported `schema_version` or a
malformed payload must never be partially or silently persisted).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from vantage.core.domain.events import EventEnvelope, validate_payload


@runtime_checkable
class EventSink(Protocol):
    """Any ingestion adapter satisfies this structurally -- no inheritance
    required (design §11, decoupling litmus)."""

    def emit(self, envelope: EventEnvelope) -> None: ...


def validate_before_dispatch(envelope: EventEnvelope) -> BaseModel:
    """Shared enforcement every `EventSink.emit` implementation calls
    before persisting anything.

    Raises:
        ContractVersionError: `envelope.schema_version` is not recognized.
        UnknownEventTypeError: `envelope.event_type` has no registered
            payload model for this `schema_version`.
        pydantic.ValidationError: `envelope.payload` does not match the
            shape required by the registered payload model.
    """

    return validate_payload(envelope)
