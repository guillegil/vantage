"""Ingestion: a session report in, stored rows out.

The one path by which a report becomes rows, shared by the server's
`POST /api/v1/runs` and by the local store the plugin writes to without a
server, so a run reads back the same whichever way it arrived. It knows
nothing of HTTP: the server reads and caps the body and maps a
`RejectionError` to a response, the local store turns one into a line of
its own.

`decode.decode_json` turns a body into a payload, `ingest` validates,
converts and stores it. Standard library, the core, Pydantic and PyYAML
only; never a web framework or a storage adapter -- the store is handed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from vantage.core.ports.storage import ExecutionStore
from vantage.ingestion.conversion import (
    ignored_result_keys,
    to_execution,
    to_result,
    to_run_metadata,
)
from vantage.ingestion.errors import InvalidReportError
from vantage.ingestion.schemas import SessionReport


@dataclass(frozen=True, slots=True)
class Ingested:
    """What storing one report did: the run it belongs to, whether it
    created that run, and each unknown key a result carried and the report
    was recorded without, as `results[].<name>`."""

    run_id: str
    created: bool
    ignored: tuple[str, ...]


def ingest(
    report: object,
    store: ExecutionStore,
    *,
    received_at: datetime,
    recorded_by: str | None = None,
) -> Ingested:
    """Validate `report`, a decoded JSON payload, convert it and record it
    in `store` as received at `received_at`, sent by the user `recorded_by`,
    or by nobody in particular.

    Raises `InvalidReportError` for a payload that is not a session report,
    before the store is touched. Whatever the store raises passes through
    unchanged, `ForeignRunError` included. `created` is the store's own
    answer, decided inside its write transaction: asking first whether the
    run exists would race another report for the same run.
    """
    try:
        payload = SessionReport.model_validate(report)
    except ValidationError as exc:
        raise InvalidReportError.from_errors(exc.errors()) from exc

    execution = to_execution(payload.run, payload.vcs)
    reported_results = payload.results or []
    results = [to_result(item) for item in reported_results]
    metadata = to_run_metadata(payload.metadata)

    created = store.record_session(
        execution,
        results=results,
        received_at=received_at,
        metadata=metadata,
        recorded_by=recorded_by,
    )
    return Ingested(
        run_id=payload.run.id,
        created=created,
        ignored=tuple(ignored_result_keys(reported_results)),
    )


__all__ = ["Ingested", "ingest"]
