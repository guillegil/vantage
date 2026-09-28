"""Every value the plugin and the server each keep a copy of, pinned together.

The two distributions never import each other, so a bound or vocabulary
mirrored on both sides can drift apart without either side's own tests
noticing, and the end-to-end tests notice only once a session crosses the
edge that moved. Both are installed in the workspace, so this module
imports both and compares the copies directly.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pytest_vantage import budget, capture, config, metadata, recorder, transport, vcs
from pytest_vantage.boundary import VantageWarning
from pytest_vantage.metadata import CapturedFile, DeclaredKey, MetadataSection, session_value
from pytest_vantage.session_metadata import plan_values
from vantage.core.config import resolution
from vantage.core.domain import access, projects
from vantage.core.domain import metadata as core_metadata
from vantage.core.domain.result import OUTCOMES
from vantage.core.ports.storage import MetadataEntry, RunMetadata
from vantage.ingestion import conversion, truncation
from vantage.ingestion.schemas import MetadataReport
from vantage.service import errors
from vantage.service.app import create_app
from vantage.storage.connection import isoformat_utc as server_isoformat_utc
from vantage.storage.sqlite_store import SqliteExecutionStore

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
    ("MAX_DECLARED_PATH_CHARS", conversion, "_MAX_DECLARED_PATH_CHARS"),
    ("MAX_DECLARED_FILE_BYTES", conversion, "_MAX_DECLARED_FILE_BYTES"),
    ("MAX_METADATA_SECTION_BYTES", conversion, "_MAX_METADATA_SECTION_BYTES"),
    ("MAX_METADATA_ENTRIES", core_metadata, "MAX_METADATA_ENTRIES"),
    ("MAX_DECLARED_KEY_CHARS", core_metadata, "MAX_METADATA_KEY_CHARS"),
    ("MAX_METADATA_VALUE_BYTES", core_metadata, "MAX_METADATA_VALUE_BYTES"),
    ("MAX_KEY_NAME_CHARS", core_metadata, "MAX_METADATA_NAME_CHARS"),
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
    """The server parses and stores exactly the core's formats, and drops a
    file in any other format together with its declared keys."""
    assert metadata._ADMISSIBLE_FORMATS <= core_metadata.METADATA_CONTENT_TYPES


def test_the_file_statuses_are_the_servers_but_malformed() -> None:
    """The server drops a file entry whose status it does not know, and
    assigns `malformed` itself, after parsing. Each declared entry is also
    charged to the section budget at the longest status the plugin can
    give, so a status the plugin gains must land in `_FILE_STATUSES`.
    """
    assert set(metadata._FILE_STATUSES) == core_metadata.FILE_STATUSES - {"malformed"}


def test_every_status_a_reported_value_carries_is_one_the_server_stores() -> None:
    """The plugin decides every reported value's status, `absent` and
    `value_too_large` included; the server derives none of them, and drops
    a reported value whose status it does not take from a session."""
    assert set(metadata.SESSION_VALUE_STATUSES) <= core_metadata.SESSION_KEY_STATUSES


def _declare(root: Path, paths: list[str]) -> None:
    (root / metadata.DECLARATION_FILENAME).write_text(
        json.dumps(
            {
                "version": 1,
                "files": [
                    {"path": declared, "format": "json", "keys": [f"key_{index}"]}
                    for index, declared in enumerate(paths)
                ],
            }
        )
    )


def _stored_metadata(section: metadata.MetadataSection) -> RunMetadata:
    """What the server keeps of `section`, sent as `Recorder` sends it."""
    return conversion.to_run_metadata(
        MetadataReport.model_validate(
            {
                "declaration": section.declaration,
                "files": [
                    {
                        "path": file.path,
                        "format": file.format,
                        "status": file.status,
                        "keys": list(file.keys),
                        "content": file.content,
                    }
                    for file in section.files
                ],
            }
        )
    )


def test_every_file_the_plugin_reads_is_recorded_under_its_declared_path(
    tmp_path: Path,
) -> None:
    """The server re-checks each declared path's shape and drops a file that
    fails it, with every key it declared. Each shape the plugin reads must
    pass that check, or a captured file would vanish from the run.
    """
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    read = ["top.json", "./dot.json", "config/nested.json", "config/./dot.json", "a b: c.json"]
    for declared in read:
        (root / declared).write_text("{}")
    _declare(root, read)
    # `warn` is reached only for a refused declaration, which this is not.
    config: pytest.Config = SimpleNamespace()  # type: ignore[assignment]

    section = metadata.capture_metadata(config, root)

    assert section is not None
    assert {file.path: file.status for file in section.files} == dict.fromkeys(read, "captured")
    stored = _stored_metadata(section)
    assert [file.source_file for file in stored.files] == read
    assert {entry.source_file for entry in stored.entries} == set(read)


@pytest.mark.parametrize(
    "shape",
    ["/top.json", "../outside.json", "config/../top.json", "config\\top.json", "C:top.json"],
)
def test_every_path_shape_the_server_drops_is_refused_with_a_warning(
    tmp_path: Path, shape: str
) -> None:
    """A shape the server drops, keys and all, is never sent: the plugin
    refuses the declaration naming it, with one warning, so no declared key
    vanishes from the run unannounced.
    """
    root = tmp_path / "project"
    root.mkdir()
    (root / "top.json").write_text("{}")
    dropped = metadata.MetadataSection(
        declaration=metadata.DECLARATION_FILENAME,
        files=(metadata.CapturedFile(shape, "json", "path_rejected", ("key",), None),),
    )
    assert _stored_metadata(dropped) == RunMetadata(files=(), entries=())
    _declare(root, ["top.json", shape])
    config: pytest.Config = SimpleNamespace()  # type: ignore[assignment]

    with pytest.warns(VantageWarning, match="the declaration is ignored"):
        assert metadata.capture_metadata(config, root) is None


def _stored_values(section: MetadataSection, reported: list[metadata.SessionValue]) -> RunMetadata:
    """What the server keeps of the last report `Recorder` sends for
    `reported` against `section`, having checked that it keeps every value
    sent: the plugin warns about each value it leaves out, and a value the
    server drops instead is lost without a word."""
    plan = plan_values(reported, section)
    wire = metadata.wire_section(section, plan.values, plan.named_keys)
    assert wire is not None
    stored = conversion.to_run_metadata(MetadataReport.model_validate(wire))
    kept = [
        (entry.key, entry.value, entry.status)
        for entry in stored.entries
        if entry.source == "session"
    ]
    assert kept == [(value.key, value.value, value.status) for value in plan.values]
    return stored


_BOARD = CapturedFile(
    path="board.json",
    format="json",
    status="captured",
    keys=("board.rev", "board.serial"),
    content='{"board.rev": "b"}',
)
_DECLARED_KEYS = (
    DeclaredKey("fpga.firmware", "FPGA firmware version"),
    DeclaredKey("fmc.hardware"),
)
_REPORTED = [
    session_value("fpga.firmware", "1.1.0"),
    session_value("board.rev", "c"),
    session_value("bench", "lab-3"),
]


def test_every_reported_value_is_stored_declared_as_the_plugin_counts_it() -> None:
    """The plugin decides which reported keys are declared, and warns about
    the others; the server sets `declared` from the report alone. With the
    files unread their keys are not in the report, so the plugin declares a
    file key it sends a value for itself, or the server would store it as
    undeclared without a warning."""
    section = MetadataSection(
        declaration=metadata.DECLARATION_FILENAME,
        files=(),
        keys=_DECLARED_KEYS,
        unread_file_keys=_BOARD.keys,
    )

    stored = _stored_values(section, _REPORTED)

    assert stored.files == ()
    assert stored.entries == (
        MetadataEntry(
            key="fpga.firmware",
            value="1.1.0",
            source_file=None,
            status="captured",
            source="session",
            name="FPGA firmware version",
        ),
        MetadataEntry(
            key="board.rev", value="c", source_file=None, status="captured", source="session"
        ),
        MetadataEntry(
            key="bench",
            value="lab-3",
            source_file=None,
            status="captured",
            source="session",
            declared=False,
        ),
        MetadataEntry(
            key="fmc.hardware", value=None, source_file=None, status="absent", source="session"
        ),
    )


def test_a_file_read_keeps_its_keys_over_the_values_the_session_reported() -> None:
    """The plugin sends no value for a key a read file supplies, and the
    server would drop it if it did: both sides keep the file's row."""
    section = MetadataSection(
        declaration=metadata.DECLARATION_FILENAME, files=(_BOARD,), keys=_DECLARED_KEYS
    )

    stored = _stored_values(section, _REPORTED)

    assert [(entry.key, entry.source, entry.value) for entry in stored.entries] == [
        ("board.rev", "file", "b"),
        ("board.serial", "file", None),
        ("fpga.firmware", "session", "1.1.0"),
        ("bench", "session", "lab-3"),
        ("fmc.hardware", "session", None),
    ]


def test_the_entry_bound_is_spent_alike_on_both_sides() -> None:
    """Files first, then the session's keys in the order set: every value
    the plugin sends fits under the server's bound, and none is dropped."""
    file_keys = tuple(f"file.{index}" for index in range(metadata.MAX_METADATA_ENTRIES - 3))
    section = MetadataSection(
        declaration=metadata.DECLARATION_FILENAME,
        files=(CapturedFile("many.json", "json", "captured", file_keys, "{}"),),
    )
    reported = [session_value(f"session.{index}", "x") for index in range(10)]

    stored = _stored_values(section, reported)

    assert len(stored.entries) == core_metadata.MAX_METADATA_ENTRIES
    assert [entry.key for entry in stored.entries if entry.source == "session"] == [
        "session.0",
        "session.1",
        "session.2",
    ]


# --- the acknowledgement ------------------------------------------------------


def test_the_acknowledgements_the_plugin_accepts_are_the_ones_the_server_sends(
    tmp_path: Path,
) -> None:
    """A report is sent more than once per run -- the start-write, then the
    finish-write -- and the server answers the first `created` and every
    later one otherwise. The plugin takes any status it does not know for a
    failed send and warns, although the server stored the report.
    """
    report = {
        "run": {
            "id": "a" * 32,
            "started_at": "2026-08-15T09:14:02.481930+00:00",
            "finished_at": "2026-08-15T09:14:47.002118+00:00",
            "exit_status": 0,
            "interrupted": False,
            "interrupt_reason": None,
        }
    }
    store = SqliteExecutionStore(tmp_path / "vantage.db")
    try:
        client = TestClient(create_app(store))
        answered = [client.post("/api/v1/runs", json=report).json()["status"] for _ in range(2)]
    finally:
        store.close()

    assert set(answered) == transport._ACKNOWLEDGED_STATUSES


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


# --- tokens -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "vantage_" + "A" * 43,
        "x",
        "!~",
        "x" * 512,
        "x" * 513,
        "",
        "two words",
        "tab\there",
        "café",
        "del\x7f",
        "nul\x00",
    ],
)
def test_a_token_the_plugin_sends_is_one_the_server_could_accept(text: str) -> None:
    """The plugin refuses a `VANTAGE_TOKEN` the server would refuse
    unasked, and sends every one it could accept."""
    assert transport.well_formed_token(text) is access.well_formed_token(text)


def test_every_refusal_the_plugin_explains_is_the_one_the_server_gives() -> None:
    """The plugin reads a refusal of who sent a request by its status and
    the error its body names; each must be the pair the server answers."""
    refusals = {
        errors.UnauthenticatedError.status_code: errors.UnauthenticatedError.error,
        errors.InsufficientScopeError.status_code: errors.InsufficientScopeError.error,
        errors.RunOfAnotherUserError.status_code: errors.RunOfAnotherUserError.error,
    }

    assert transport._ACCESS_REFUSALS == refusals


# --- projects -------------------------------------------------------------------


def test_the_project_name_rule_is_the_servers() -> None:
    """A name the plugin accepts and the server refuses loses the run to a
    422 at the finish; one the plugin refuses and the server accepts
    stops a session that would have been recorded."""
    assert config._PROJECT_NAME_PATTERN == projects.PROJECT_NAME_PATTERN


def test_the_plugin_describes_the_rule_in_the_servers_words() -> None:
    """The usage error and the server's refusal state one rule, so they
    should read alike."""
    with pytest.raises(projects.InvalidProjectNameError) as server_side:
        projects.check_project_name("Foo")
    rule = str(server_side.value).removeprefix("a project name is ")

    with pytest.raises(config.VantageConfigError) as plugin_side:
        config.resolve_project(SimpleNamespace(getoption=lambda name, default=None: "Foo"))  # type: ignore[arg-type]

    assert f"must be a project name: {rule} (got 'Foo')" in str(plugin_side.value)


def test_a_run_naming_no_project_goes_where_the_server_puts_one_naming_none() -> None:
    """The plugin names `default` in every report and in its header; the
    server files a report naming none in its own default. The two must be
    one project, or a queued report from before projects lands elsewhere."""
    assert config.DEFAULT_PROJECT == projects.DEFAULT_PROJECT


def test_the_unknown_project_refusal_the_plugin_keeps_is_the_one_the_server_gives() -> None:
    """The outbox keeps a run the server refused for its project; any other
    404 is dropped. Reading the wrong pair would drop runs an admin could
    have let in, or keep forever runs no server will take."""
    refusal = (errors.NoSuchProjectError.status_code, errors.NoSuchProjectError.error)

    assert transport._UNKNOWN_PROJECT == refusal


def test_the_membership_refusals_the_plugin_keeps_are_the_ones_the_server_gives() -> None:
    """A run refused because the token's user may not record in its project
    is kept, and sending moves on to other projects' runs; any other 403
    refuses the token itself and stops it. Reading the wrong pairs would
    stop at a run an owner of the project could let in, or pass over a
    project for a refusal no grant there would lift."""
    refusals = {
        (errors.NotAMemberError.status_code, errors.NotAMemberError.error),
        (errors.InsufficientRoleError.status_code, errors.InsufficientRoleError.error),
    }

    assert {
        (transport._MEMBERSHIP_REFUSAL_STATUS, error) for error in transport._MEMBERSHIP_REFUSALS
    } == refusals
