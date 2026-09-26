"""The refusals ingestion raises, as plain exceptions.

Each carries exactly what a rejection may say -- a status code, an error
code, one fixed sentence and dotted field paths -- and nothing pydantic hands
back. `vantage.service.errors` turns them into HTTP responses; the local
store turns them into one line. Neither needs a web framework to read them.

**Why build the fields from an allow-list.** Pydantic's own error dicts
mirror the client's submitted value back in an ``"input"`` key, include
pydantic's internal error ``"type"`` string, and can carry a ``"url"``
pointing at versioned pydantic documentation. A test report can
legitimately carry a filesystem path, a node id, or an environment-derived
string; the field that fails validation is exactly the field whose value
would be echoed back. Stripping known-dangerous keys requires naming every
one correctly, and a pydantic upgrade can add one a deny-list has never
heard of, so only dotted paths of safe segments are ever kept.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

# A path segment safe to echo: a schema field name, or a list index. The
# allow-list is deliberate -- see `safe_segment`.
_SAFE_SEGMENT = re.compile(r"\A([A-Za-z_][A-Za-z0-9_]{0,63}|[0-9]{1,9})\Z")

_UNNAMEABLE_SEGMENT = "<unnamed>"


def safe_segment(part: object) -> str:
    """Return ``part`` only when it is a name this schema could have declared.

    Almost every ``loc`` segment is a field name from our own models, and
    echoing it is the entire point -- the client needs to know what to fix.
    The exception is ``extra_forbidden``, whose final segment is a key the
    CLIENT chose, and it is not a name at all: it is arbitrary bytes. Echoed
    verbatim it reflects up to a whole report of attacker-chosen text back
    in the response, and carries CR/LF into any log line that records the
    rejection -- forged log entries from an unauthenticated caller.

    So this is an allow-list too: a deny-list of dangerous characters has
    to guess right every time, and it only has to be wrong once.

    Also applied to `Ingested.ignored`: an unknown key on a tolerated
    `ResultReport` is the same client-chosen text, echoed on the accept path
    instead.
    """
    text = str(part)
    return text if _SAFE_SEGMENT.match(text) else _UNNAMEABLE_SEGMENT


def _dotted_path(location: Iterable[object]) -> str:
    """A pydantic ``loc`` tuple, e.g. ``("body", "run", "started_at")``, to
    ``"run.started_at"`` -- dotted paths only, never pydantic's list form,
    and never the ``"body"`` segment FastAPI prepends (it names the
    transport layer, not anything the client wrote in the payload).
    """
    return ".".join(safe_segment(part) for part in location if part != "body")


def fields_from_errors(errors: Iterable[Mapping[str, Any]]) -> list[str]:
    """Every failing field's dotted path. A failure of the body as a whole
    (not an object at all) has an empty path, which names nothing, so it
    contributes no entry -- the same empty `fields` as any other whole-body
    rejection."""
    paths = (_dotted_path(error["loc"]) for error in errors)
    return [path for path in paths if path]


class RejectionError(Exception):
    """Base for every refusal of a request or a report.

    One shape, one place: every subclass carries only ``status_code``,
    ``error``, ``detail`` and ``fields`` -- exactly what a rejection body is
    allowed to emit, and nothing pydantic-specific.
    """

    status_code: int
    error: str

    def __init__(self, detail: str, fields: list[str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.fields = fields or []


class InvalidReportError(RejectionError):
    """Valid JSON, but the report fails schema validation."""

    status_code = 422
    error = "invalid_report"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidReportError:
        return cls(
            "The submitted report does not match the expected shape.",
            fields_from_errors(errors),
        )


class InvalidJsonError(RejectionError):
    """Complete bytes, but not parseable JSON."""

    status_code = 400
    error = "invalid_json"

    def __init__(self) -> None:
        super().__init__("The request body is not valid JSON.")


__all__ = [
    "InvalidJsonError",
    "InvalidReportError",
    "RejectionError",
    "fields_from_errors",
    "safe_segment",
]
