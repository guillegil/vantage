"""The metadata vocabularies agree with the `CHECK` constraints in
`schema.sql`.

The constraints are parsed out of the schema file rather than restated here,
so a value added on one side only fails this test instead of being dropped
or refused by the database at runtime.
"""

from __future__ import annotations

import importlib.resources
import re

from vantage.core.domain.metadata import FILE_STATUSES, KEY_STATUSES
from vantage.service.routes.runs import _KNOWN_METADATA_CONTENT_TYPES

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


def test_content_types_the_service_admits_match_the_content_type_check() -> None:
    assert _check_values("run_metadata_file", "content_type") == _KNOWN_METADATA_CONTENT_TYPES
