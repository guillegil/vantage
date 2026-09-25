"""`FILE_STATUSES`/`KEY_STATUSES` vocabulary and the three bounds
`core/domain/metadata.py` carries.

Stdlib only, no I/O. A vocabulary is a module-level `frozenset` of plain
`str`, never an `Enum`: `class X(str, Enum)` changes `__format__` between
Python 3.10 and 3.13.
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
    """`run_metadata_file.status`'s SQL `CHECK` names exactly these eight
    values, in `schema.sql`'s own order."""
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
    """`run_metadata.status`'s SQL `CHECK` names exactly these five values,
    in `schema.sql`'s own order."""
    assert KEY_STATUSES == {
        "captured",
        "absent",
        "not_scalar",
        "value_too_large",
        "source_unavailable",
    }


def test_file_statuses_is_a_plain_str_frozenset_never_an_enum() -> None:
    """Plain `str` members, for the 3.10-vs-3.13 `__format__` reason."""
    assert type(FILE_STATUSES) is frozenset
    for status in FILE_STATUSES:
        assert type(status) is str


def test_key_statuses_is_a_plain_str_frozenset_never_an_enum() -> None:
    assert type(KEY_STATUSES) is frozenset
    for status in KEY_STATUSES:
        assert type(status) is str


def test_max_metadata_value_bytes_is_max_identity_chars_value() -> None:
    """Bounded at `MAX_IDENTITY_CHARS`'s value (1024) -- a short, indexed,
    client-supplied string, not a `MAX_TEXT_FIELD_BYTES` text field."""
    assert MAX_METADATA_VALUE_BYTES == 1024


def test_max_metadata_key_chars_is_max_identity_chars_value() -> None:
    """A declared key is the same class of short, client-supplied, indexed
    string as an identity, so it carries `MAX_IDENTITY_CHARS`'s value."""
    assert MAX_METADATA_KEY_CHARS == 1024


def test_max_metadata_entries_is_max_page_items_value() -> None:
    """Bounded at `MAX_PAGE_ITEMS` (200) -- a run's metadata is presented
    unpaginated, so the stored-entry cap is the response bound."""
    assert MAX_METADATA_ENTRIES == 200
