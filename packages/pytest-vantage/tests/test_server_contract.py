"""Every value the plugin and the server each keep a copy of, pinned together.

The two distributions never import each other, so a bound or vocabulary
mirrored on both sides can drift apart without either side's own tests
noticing, and the end-to-end tests notice only once a session crosses the
edge that moved. Both are installed in the workspace, so this module
imports both and compares the copies directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import ModuleType

import pytest
from pytest_vantage import budget, capture, metadata, recorder, vcs
from vantage.core.config import resolution
from vantage.core.domain import metadata as core_metadata
from vantage.core.domain.result import OUTCOMES
from vantage.service import errors, metadata_parse, truncation
from vantage.service.routes import runs as runs_route
from vantage.storage.connection import isoformat_utc as server_isoformat_utc

# --- the report's size ------------------------------------------------------


def test_the_report_cap_is_the_servers_body_limit() -> None:
    """A cap above the server's limit gets whole sessions refused."""
    assert budget._REPORT_BYTES_CAP == errors.MAX_REPORT_BYTES


def test_the_field_cap_is_the_servers_text_field_bound() -> None:
    """The plugin cuts each failure-text field where the server would, so it
    never spends the budget on text the server discards on arrival."""
    assert budget._FIELD_BYTES_CAP == truncation.MAX_TEXT_FIELD_BYTES


def test_the_commit_subject_cap_stays_above_the_servers_text_field_bound() -> None:
    """Deliberately above: a subject cut to the server's bound or below
    arrives with nothing left to cut, and the server records it untruncated.
    """
    assert vcs._MAX_SUBJECT_BYTES > truncation.MAX_TEXT_FIELD_BYTES


# --- metadata -----------------------------------------------------------------

_MIRRORED_METADATA_BOUNDS: list[tuple[str, ModuleType, str]] = [
    ("MAX_DECLARED_PATH_CHARS", runs_route, "_MAX_DECLARED_PATH_CHARS"),
    ("MAX_DECLARED_FILE_BYTES", runs_route, "_MAX_DECLARED_FILE_BYTES"),
    ("MAX_METADATA_SECTION_BYTES", runs_route, "_MAX_METADATA_SECTION_BYTES"),
    ("MAX_METADATA_ENTRIES", core_metadata, "MAX_METADATA_ENTRIES"),
    ("MAX_DECLARED_KEY_CHARS", core_metadata, "MAX_METADATA_KEY_CHARS"),
]


@pytest.mark.parametrize(
    ("plugin_name", "server_module", "server_name"),
    _MIRRORED_METADATA_BOUNDS,
    ids=[plugin_name for plugin_name, _module, _name in _MIRRORED_METADATA_BOUNDS],
)
def test_a_mirrored_metadata_bound_matches_the_server(
    plugin_name: str, server_module: ModuleType, server_name: str
) -> None:
    """The server applies each bound again and drops what exceeds it, so a
    bound raised on the plugin alone captures metadata that is then thrown
    away, and one lowered alone refuses declarations the server would take.
    """
    assert getattr(metadata, plugin_name) == getattr(server_module, server_name)


def test_every_admissible_format_is_one_the_server_parses_and_stores() -> None:
    """A format the server cannot parse records every declared key as
    unavailable; one its schema cannot store drops the file entirely."""
    assert metadata._ADMISSIBLE_FORMATS <= metadata_parse._ADMISSIBLE_CONTENT_TYPES
    assert metadata._ADMISSIBLE_FORMATS <= core_metadata.METADATA_CONTENT_TYPES


def test_the_file_statuses_are_the_servers_but_malformed() -> None:
    """The server drops a file entry whose status it does not know, and
    assigns `malformed` itself, after parsing. Each declared entry is also
    charged to the section budget at the longest status the plugin can
    give, so a status the plugin gains must land in `_FILE_STATUSES`.
    """
    assert set(metadata._FILE_STATUSES) == core_metadata.FILE_STATUSES - {"malformed"}


# --- results ------------------------------------------------------------------


def test_the_outcomes_the_plugin_derives_are_the_servers_vocabulary() -> None:
    """The server refuses a report carrying an outcome it does not know,
    whole. Every derived outcome is ranked in `_SEVERITY`, and pytest's own
    phase outcomes must be outcomes the server accepts too.
    """
    assert set(capture._SEVERITY) == OUTCOMES
    assert capture._REPORT_OUTCOMES <= OUTCOMES
    assert budget._FAILING_OUTCOMES <= OUTCOMES


@pytest.mark.parametrize(
    "moment",
    [
        pytest.param(datetime(2026, 8, 15, 9, 14, 2, 0, tzinfo=timezone.utc), id="whole-second"),
        pytest.param(
            datetime(2026, 8, 15, 9, 14, 2, 481930, tzinfo=timezone.utc), id="microseconds"
        ),
        pytest.param(datetime(999, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc), id="year-below-1000"),
        pytest.param(
            datetime(2026, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=1))),
            id="crosses-a-year-into-utc",
        ),
    ],
)
def test_timestamps_are_sent_as_the_text_the_server_stores(moment: datetime) -> None:
    """Stored timestamps are compared as text, so the plugin's must be the
    exact fixed-width form the server writes, and read back as the same
    instant.
    """
    sent = capture.isoformat_utc(moment)

    assert sent == server_isoformat_utc(moment)
    assert datetime.fromisoformat(sent) == moment


# --- liveness -----------------------------------------------------------------


def test_the_heartbeat_interval_is_the_one_the_default_grace_period_counts() -> None:
    """The server's default grace period is a number of heartbeat intervals;
    a run that beats less often than the grace period reads as abandoned
    while it is still running.
    """
    assert recorder._BEAT_INTERVAL_SECONDS == resolution._BEAT_INTERVAL_HINT_SECONDS
    assert recorder._BEAT_INTERVAL_SECONDS < resolution.DEFAULT_GRACE_PERIOD_SECONDS
