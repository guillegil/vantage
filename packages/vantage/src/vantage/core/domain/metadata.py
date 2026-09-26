"""Run metadata: the file formats, the file- and key-level status
vocabularies, where a key's value came from, and the size bounds on keys,
display names and values.

No logic lives here: parsing, bounding and path containment belong to
`pytest-vantage` and `vantage.service`; this module only names the values
those layers agree on.

The vocabularies are module-level ``frozenset``s of plain ``str``, never an
``Enum``, for the ``__format__`` reason recorded in ``liveness.py``. Each but
``SESSION_KEY_STATUSES``, a subset of ``KEY_STATUSES``, mirrors one of
``schema.sql``'s ``CHECK`` constraints exactly; ``test_metadata.py`` parses
the constraints to hold them in step.
"""

from __future__ import annotations

METADATA_CONTENT_TYPES = frozenset({"json", "yaml"})
"""The formats `run_metadata_file.content_type`'s `CHECK` constraint accepts:
the ones the plugin declares and the server can parse. A file reported in
any other format is dropped, with its keys, before it reaches the store."""

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
(the key itself was never found in a file that WAS read, or was declared and
never given a value by the session), so "too large" stays distinguishable
from "never declared"."""

SESSION_KEY_STATUSES = frozenset({"captured", "absent", "value_too_large"})
"""The subset of `KEY_STATUSES` a key the test session reported itself can
have. `not_scalar` and `source_unavailable` describe a declared file, which a
session value never comes from."""

METADATA_SOURCES = frozenset({"file", "session"})
"""The values `run_metadata.source`'s `CHECK` constraint accepts: a key read
from a declared file, or one the test session reported itself. Every row
keeps its source, so the two stay distinguishable on every read."""

MAX_METADATA_VALUE_BYTES = 1024
"""Bound on one captured value, in bytes not characters. `MAX_IDENTITY_CHARS`'s
value, because it is a short, indexed, client-supplied string. Not
`MAX_TEXT_FIELD_BYTES`: a 64 KiB value in a `(key, value)` index is bloat with
no query value."""

MAX_METADATA_KEY_CHARS = 1024
"""Bound on one key's length, declared or reported by the session:
`MAX_IDENTITY_CHARS`'s value, since a key is the same class of short,
client-supplied, indexed string."""

MAX_METADATA_NAME_CHARS = 256
"""Bound on the display name a declaration gives a key. The plugin refuses a
declaration holding a longer one; the server stores a longer name another
client sends as no name at all, and keeps the key and its value."""

MAX_METADATA_ENTRIES = 200
"""Bound on metadata rows per run: the keys of every declared file first,
then the keys the session reported, in the order first set. The plugin
refuses a declaration past it and drops the session keys past it, and the
server drops every row past it that another client sends, in one report or
spread over several. It also keeps a run's metadata small enough to return
whole, in one unpaged response."""


__all__ = [
    "FILE_STATUSES",
    "KEY_STATUSES",
    "MAX_METADATA_ENTRIES",
    "MAX_METADATA_KEY_CHARS",
    "MAX_METADATA_NAME_CHARS",
    "MAX_METADATA_VALUE_BYTES",
    "METADATA_CONTENT_TYPES",
    "METADATA_SOURCES",
    "SESSION_KEY_STATUSES",
]
