"""`FILE_STATUSES`/`KEY_STATUSES` vocabulary and the three bounds
`core/domain/metadata.py` carries (design.md D91, D94, D95).

Stdlib only, no I/O -- this module is pure vocabulary, matching
`liveness.py`'s and `result.py`'s precedent: a vocabulary is a module-level
`frozenset` of plain `str`, never an `Enum`. `class X(str, Enum)` changes
`__format__` between Python 3.10 and 3.13 on this project's own supported
range (measured, `liveness.py`'s module docstring), and a third shape for
the same kind of value is one shape too many.
"""

from __future__ import annotations

from vantage.core.domain.metadata import (
    FILE_STATUSES,
    KEY_STATUSES,
    MAX_METADATA_ENTRIES,
    MAX_METADATA_KEY_CHARS,
    MAX_METADATA_VALUE_BYTES,
)


def test_file_statuses_match_run_metadata_files_check_constraint_exactly() -> None:
    """`design.md` D91: `run_metadata_file.status`'s SQL `CHECK` names exactly
    these eight values, in `schema.sql`'s own order."""
    assert FILE_STATUSES == {
        "captured",
        "not_found",
        "path_rejected",
        "too_large",
        "not_text",
        "unreadable",
        "over_budget",
        "malformed",
    }


def test_key_statuses_match_run_metadata_check_constraint_exactly() -> None:
    """`design.md` D91: `run_metadata.status`'s SQL `CHECK` names exactly
    these five values, in `schema.sql`'s own order."""
    assert KEY_STATUSES == {
        "captured",
        "absent",
        "not_scalar",
        "value_too_large",
        "source_unavailable",
    }


def test_file_statuses_is_a_plain_str_frozenset_never_an_enum() -> None:
    """`liveness.PRESENTATIONS`'s measured 3.10-vs-3.13 `__format__` reason,
    applied to this module's own vocabulary."""
    assert type(FILE_STATUSES) is frozenset
    for status in FILE_STATUSES:
        assert type(status) is str


def test_key_statuses_is_a_plain_str_frozenset_never_an_enum() -> None:
    assert type(KEY_STATUSES) is frozenset
    for status in KEY_STATUSES:
        assert type(status) is str


def test_max_metadata_value_bytes_is_max_identity_chars_value() -> None:
    """design.md D94: bounded at `MAX_IDENTITY_CHARS`'s value (1024) for
    D89's reason -- a short, indexed, client-supplied string, not
    `MAX_TEXT_FIELD_BYTES` (P-2)."""
    assert MAX_METADATA_VALUE_BYTES == 1024


def test_max_metadata_key_chars_is_max_identity_chars_value() -> None:
    """design.md's file-changes table names `MAX_METADATA_KEY_CHARS` beside
    `MAX_METADATA_VALUE_BYTES` and `MAX_METADATA_ENTRIES` (D94, D95) without
    a separate derivation row of its own; a declared key is the same class of
    short, client-supplied, indexed string D89 already bounds at
    `MAX_IDENTITY_CHARS`, so it carries the identical value the sibling
    bounds in the same table both use."""
    assert MAX_METADATA_KEY_CHARS == 1024


def test_max_metadata_entries_is_max_page_items_value() -> None:
    """design.md D94: bounded at `MAX_PAGE_ITEMS` (200) -- a run's metadata
    is presented unpaginated, so the stored-entry cap is the response bound."""
    assert MAX_METADATA_ENTRIES == 200
