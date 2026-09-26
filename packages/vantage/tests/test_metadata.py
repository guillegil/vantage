"""The metadata vocabularies agree with the `CHECK` constraints in
`schema.sql`, and a metadata row outside them, or contradicting itself,
cannot be constructed.

The constraints are parsed out of the schema file rather than restated here,
so a value added on one side only fails this test instead of being refused
by the database at runtime.
"""

from __future__ import annotations

import importlib.resources
import re

import pytest
from vantage.core.domain.metadata import (
    FILE_STATUSES,
    KEY_STATUSES,
    METADATA_CONTENT_TYPES,
    METADATA_SOURCES,
    SESSION_KEY_STATUSES,
)
from vantage.core.ports.storage import MetadataEntry, MetadataFile

_SCHEMA_SQL = importlib.resources.files("vantage.storage").joinpath("schema.sql").read_text("utf-8")


def _check_values(table: str, column: str) -> frozenset[str]:
    """The quoted values of `CHECK (<column> IN (...))` inside `table`'s
    `CREATE TABLE` statement."""
    table_match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", _SCHEMA_SQL, re.DOTALL
    )
    assert table_match is not None, f"no CREATE TABLE for {table}"
    check_match = re.search(rf"CHECK \({column} IN \(([^)]+)\)\)", table_match.group(1))
    assert check_match is not None, f"no CHECK on {table}.{column}"
    return frozenset(re.findall(r"'([^']+)'", check_match.group(1)))


def test_file_statuses_match_the_run_metadata_file_status_check() -> None:
    assert _check_values("run_metadata_file", "status") == FILE_STATUSES


def test_key_statuses_match_the_run_metadata_status_check() -> None:
    assert _check_values("run_metadata", "status") == KEY_STATUSES


def test_sources_match_the_run_metadata_source_check() -> None:
    assert _check_values("run_metadata", "source") == METADATA_SOURCES


def test_a_session_keys_statuses_are_a_subset_of_every_keys() -> None:
    assert SESSION_KEY_STATUSES < KEY_STATUSES


def test_content_types_match_the_run_metadata_file_content_type_check() -> None:
    assert _check_values("run_metadata_file", "content_type") == METADATA_CONTENT_TYPES


@pytest.mark.parametrize(
    ("content_type", "status"), [("xml", "captured"), ("json", "bogus")], ids=["format", "status"]
)
def test_a_metadata_file_outside_the_vocabulary_is_refused(content_type: str, status: str) -> None:
    with pytest.raises(ValueError, match="must be one of"):
        MetadataFile(source_file="m.json", content_type=content_type, status=status)


@pytest.mark.parametrize(
    ("status", "source"), [("bogus", "file"), ("absent", "bogus")], ids=["status", "source"]
)
def test_a_metadata_entry_outside_the_vocabulary_is_refused(status: str, source: str) -> None:
    with pytest.raises(ValueError, match="must be one of"):
        MetadataEntry(key="fw", value=None, source_file="m.json", status=status, source=source)


@pytest.mark.parametrize("status", sorted(KEY_STATUSES - SESSION_KEY_STATUSES))
def test_a_session_entry_with_a_status_only_a_file_can_have_is_refused(status: str) -> None:
    with pytest.raises(ValueError, match="must be one of"):
        MetadataEntry(key="fw", value=None, source_file=None, status=status, source="session")


@pytest.mark.parametrize(
    ("value", "status"),
    [(None, "captured"), ("2.1", "absent"), ("2.1", "value_too_large")],
    ids=["captured-without-value", "absent-with-value", "too-large-with-value"],
)
def test_an_entry_whose_value_contradicts_its_status_is_refused(
    value: str | None, status: str
) -> None:
    """Only a captured key has a value, so a filter on values can never
    match a key whose value was dropped."""
    with pytest.raises(ValueError, match="value must be set exactly when status is 'captured'"):
        MetadataEntry(key="fw", value=value, source_file=None, status=status, source="session")


def test_a_file_entry_without_its_file_is_refused() -> None:
    with pytest.raises(ValueError, match="a file key must name its source_file"):
        MetadataEntry(key="fw", value="2.1", source_file=None, status="captured")


def test_a_session_entry_naming_a_file_is_refused() -> None:
    with pytest.raises(ValueError, match="a session key has no source_file"):
        MetadataEntry(
            key="fw", value="2.1", source_file="m.json", status="captured", source="session"
        )


def test_an_undeclared_file_entry_is_refused() -> None:
    """A file's keys are the ones its declaration lists."""
    with pytest.raises(ValueError, match="a file key is always declared"):
        MetadataEntry(
            key="fw", value="2.1", source_file="m.json", status="captured", declared=False
        )


def test_an_undeclared_session_entry_with_no_name_is_accepted() -> None:
    entry = MetadataEntry(
        key="bench",
        value="lab-3",
        source_file=None,
        status="captured",
        source="session",
        declared=False,
    )

    assert (entry.source, entry.name, entry.declared) == ("session", None, False)
