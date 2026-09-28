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

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from vantage.core.domain.projects import DEFAULT_PROJECT
from vantage.core.ports.storage import ExecutionStore, ProjectExistsError
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
    create_missing_project: bool = False,
    admit: Callable[[str], None] | None = None,
) -> Ingested:
    """Validate `report`, a decoded JSON payload, convert it and record it
    in `store` as received at `received_at`, sent by the user `recorded_by`,
    or by nobody in particular, in the project it names, or `default`.

    Raises `InvalidReportError` for a payload that is not a session report,
    before the store is touched. Whatever the store raises passes through
    unchanged, `ForeignRunError`, `UnknownProjectError` and
    `ProjectMismatchError` included. `created` is the store's own answer,
    decided inside its write transaction: asking first whether the run
    exists would race another report for the same run.

    A server never makes a project from a report: only an admin does. With
    `create_missing_project`, the project the report names is made first if
    the store lacks it -- for the local store, whose database belongs to
    whoever writes it, so there is nobody else to make it.

    `admit`, if given, is called with the project the report names once the
    report is valid and converted, and before anything is made or recorded:
    whatever it raises passes through, and nothing is stored. The server
    checks there that the project exists and that the sender may record in
    it; the local store passes none.
    """
    try:
        payload = SessionReport.model_validate(report)
    except ValidationError as exc:
        raise InvalidReportError.from_errors(exc.errors()) from exc

    execution = to_execution(payload.run, payload.vcs)
    reported_results = payload.results or []
    results = [to_result(item) for item in reported_results]
    metadata = to_run_metadata(payload.metadata)

    project = DEFAULT_PROJECT if payload.project is None else payload.project
    if admit is not None:
        admit(project)
    if create_missing_project and store.get_project(project) is None:
        # Not for a report of a run already filed in another project: the
        # store refuses it, and a refused report changes nothing, so it
        # leaves no empty project behind either.
        stored = store.get_run_detail(execution.identity.value)
        if stored is None or stored.project == project:
            # Another session storing into the same file may make it first.
            with contextlib.suppress(ProjectExistsError):
                store.create_project(project, created_at=received_at)
    created = store.record_session(
        execution,
        project=project,
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
