"""The metadata vocabularies agree with the `CHECK` constraints in
`schema.sql`, and a metadata row outside them cannot be constructed.

The constraints are parsed out of the schema file rather than restated here,
so a value added on one side only fails this test instead of being refused
by the database at runtime.
"""

from __future__ import annotations

import importlib.resources
import re

import pytest
from vantage.core.domain.metadata import FILE_STATUSES, KEY_STATUSES, METADATA_CONTENT_TYPES
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


def test_content_types_match_the_run_metadata_file_content_type_check() -> None:
    assert _check_values("run_metadata_file", "content_type") == METADATA_CONTENT_TYPES


@pytest.mark.parametrize(
    ("content_type", "status"), [("xml", "captured"), ("json", "bogus")], ids=["format", "status"]
)
def test_a_metadata_file_outside_the_vocabulary_is_refused(content_type: str, status: str) -> None:
    with pytest.raises(ValueError, match="must be one of"):
        MetadataFile(source_file="m.json", content_type=content_type, status=status)


def test_a_metadata_entry_outside_the_vocabulary_is_refused() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        MetadataEntry(key="fw", value=None, source_file="m.json", status="bogus")
