"""User-declared run metadata: the file- and key-level status vocabularies
and the size bounds on declared keys and values.

No logic lives here: parsing, bounding and path containment belong to
`pytest-vantage` and `vantage.service`; this module only names the values
those layers agree on.

``FILE_STATUSES`` and ``KEY_STATUSES`` are module-level ``frozenset``s of
plain ``str``, never an ``Enum``, for the ``__format__`` reason recorded in
``liveness.py``. Both mirror ``schema.sql``'s ``CHECK`` constraints on
``run_metadata_file.status`` and ``run_metadata.status`` exactly, and must be
kept in step with them by hand.
"""

from __future__ import annotations

FILE_STATUSES = frozenset(
    {
        "captured",
        "not_found",
        "path_rejected",
        "too_large",
        "not_text",
        "unreadable",
        "over_budget",
        "malformed",
    }
)
"""The eight values `run_metadata_file.status`'s `CHECK` constraint accepts.
A declared file that is never rejected, dropped or unreadable is `captured`;
every other value is a reason it was not."""

KEY_STATUSES = frozenset(
    {
        "captured",
        "absent",
        "not_scalar",
        "value_too_large",
        "source_unavailable",
    }
)
"""The five values `run_metadata.status`'s `CHECK` constraint accepts.
`source_unavailable` is a key whose *file* failed -- distinct from `absent`
(the key itself was never found in a file that WAS read), so "too large"
stays distinguishable from "never declared"."""

MAX_METADATA_VALUE_BYTES = 1024
"""Bound on one captured value, in bytes not characters. `MAX_IDENTITY_CHARS`'s
value, because it is a short, indexed, client-supplied string. Not
`MAX_TEXT_FIELD_BYTES`: a 64 KiB value in a `(key, value)` index is bloat with
no query value."""

MAX_METADATA_KEY_CHARS = 1024
"""Bound on one declared key's length: `MAX_IDENTITY_CHARS`'s value, since a
declared key is the same class of short, client-supplied, indexed string."""

MAX_METADATA_ENTRIES = 200
"""Bound on stored keys per run, total: `MAX_PAGE_ITEMS`. A run's metadata is
presented unpaginated on the run detail, so this cap on stored entries *is*
the bound on that response."""


__all__ = [
    "FILE_STATUSES",
    "KEY_STATUSES",
    "MAX_METADATA_ENTRIES",
    "MAX_METADATA_KEY_CHARS",
    "MAX_METADATA_VALUE_BYTES",
]
