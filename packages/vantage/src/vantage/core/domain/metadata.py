"""User-declared run metadata: the file- and key-level status vocabularies
and the three bounds this project already argued elsewhere (design.md D91,
D94, D95).

Stdlib only (RQ-26) -- no Pydantic, no ORM, matching every other module in
`vantage.core.domain`. No logic beyond vocabulary lives here: parsing,
bounding and path containment are `pytest-vantage` and `vantage.service`
concerns (D92, D93, D97); this module only names the values those layers
agree on.

``FILE_STATUSES`` and ``KEY_STATUSES`` are module-level ``frozenset``s of
plain ``str``, never an ``Enum`` -- ``liveness.PRESENTATIONS`` and
``result.OUTCOMES`` already record why on this project's Python 3.10 floor:
``class X(str, Enum)`` changes ``__format__`` between interpreter versions,
measured as ``f"{X.A}"`` returning ``'abandoned'`` on Python 3.10 and
``'X.A'`` on Python 3.13 for the identical source. This module follows the
same precedent rather than inventing a third shape for the same kind of
vocabulary.

Both vocabularies mirror ``schema.sql``'s ``CHECK`` constraints on
``run_metadata_file.status`` and ``run_metadata.status`` exactly (D91): the
SQL ``CHECK``, this ``frozenset`` and the Pydantic report models the server
builds from it (D96) must agree, and keeping those three in step is a task
in its own right -- the same note `design.md` already carries for
``result.OUTCOMES``.
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
"""The eight values `run_metadata_file.status`'s `CHECK` constraint accepts
(design.md D91). A declared file that is never rejected, dropped or
unreadable is `captured`; every other value is a reason it was not."""

KEY_STATUSES = frozenset(
    {
        "captured",
        "absent",
        "not_scalar",
        "value_too_large",
        "source_unavailable",
    }
)
"""The five values `run_metadata.status`'s `CHECK` constraint accepts
(design.md D91). `source_unavailable` is a key whose *file* failed --
distinct from `absent` (the key itself was never found in a file that WAS
read), so "too large" stays distinguishable from "never declared" (D95)."""

MAX_METADATA_VALUE_BYTES = 1024
"""Bound on one captured value, in bytes not characters (design.md D94):
`MAX_IDENTITY_CHARS`'s value, for D89's reason -- a short, indexed,
client-supplied string. Not `MAX_TEXT_FIELD_BYTES`: a 64 KiB value in a
`(key, value)` index is bloat with no query value (P-2)."""

MAX_METADATA_KEY_CHARS = 1024
"""Bound on one declared key's length (design.md's D94/D95 file-changes
table names this constant without a separate derivation row of its own). A
declared key is the same class of short, client-supplied, indexed string
D89 already bounds at `MAX_IDENTITY_CHARS`, so it carries the identical
value its two sibling bounds in the same table both use."""

MAX_METADATA_ENTRIES = 200
"""Bound on stored keys per run, total (design.md D94): `MAX_PAGE_ITEMS`. A
run's metadata is presented unpaginated on the run detail, so this cap on
stored entries *is* the bound on that response -- D89's argument for
`MAX_SECTIONS`, unchanged."""


__all__ = [
    "FILE_STATUSES",
    "KEY_STATUSES",
    "MAX_METADATA_ENTRIES",
    "MAX_METADATA_KEY_CHARS",
    "MAX_METADATA_VALUE_BYTES",
]
