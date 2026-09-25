"""Unit tests for `service/routes/runs.py`'s wire-to-domain mapping helpers:
`_to_execution`, `_to_result` and `_to_run_metadata`. Endpoint-level
behaviour lives in `test_ingestion.py`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pytest_vantage import metadata as plugin_metadata
from vantage.core.domain.metadata import MAX_METADATA_ENTRIES, MAX_METADATA_KEY_CHARS
from vantage.core.domain.result import CapturedOutput
from vantage.core.ports.storage import EMPTY_RUN_METADATA, MetadataEntry, MetadataFile
from vantage.service import metadata_parse
from vantage.service.routes import runs as runs_route
from vantage.service.routes.runs import _to_execution, _to_result, _to_run_metadata
from vantage.service.schemas import (
    MetadataFileReport,
    MetadataReport,
    ResultReport,
    RunReport,
    VcsReport,
)
from vantage.service.truncation import MAX_TEXT_FIELD_BYTES


def _run_report(**overrides: object) -> RunReport:
    payload: dict[str, object] = {
        "id": "a" * 32,
        "started_at": datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc),
        "finished_at": None,
        "exit_status": None,
        "interrupted": False,
        "interrupt_reason": None,
    }
    payload.update(overrides)
    return RunReport.model_validate(payload)


def test_to_execution_maps_vcs_none_when_the_section_is_absent() -> None:
    execution = _to_execution(_run_report(), vcs=None)

    assert execution.vcs is None


def test_to_execution_maps_vcs_none_when_every_field_in_the_section_is_null() -> None:
    """A session recorded outside a repository -- five nulls on the wire --
    reads back as `execution.vcs is None`, never as a `VcsContext` full of
    nulls."""
    vcs = VcsReport.model_validate(
        {
            "commit": None,
            "branch": None,
            "commit_subject": None,
            "dirty": None,
            "root": None,
        }
    )

    execution = _to_execution(_run_report(), vcs=vcs)

    assert execution.vcs is None


def test_to_execution_maps_a_well_formed_vcs_section_to_a_vcs_context() -> None:
    vcs = VcsReport.model_validate(
        {
            "commit": "a" * 40,
            "branch": "main",
            "commit_subject": "Fix the thing",
            "dirty": True,
            "root": "/repo",
        }
    )

    execution = _to_execution(_run_report(), vcs=vcs)

    assert execution.vcs is not None
    assert execution.vcs.commit == "a" * 40
    assert execution.vcs.branch == "main"
    assert execution.vcs.commit_subject == "Fix the thing"
    assert execution.vcs.commit_subject_truncated is False
    assert execution.vcs.dirty is True
    assert execution.vcs.root == "/repo"


def test_to_execution_truncates_an_oversized_commit_subject_and_sets_the_flag() -> None:
    """`_to_execution` applies `truncate()` to `commit_subject`."""
    vcs = VcsReport.model_validate(
        {
            "commit": "a" * 40,
            "branch": "main",
            "commit_subject": "x" * (MAX_TEXT_FIELD_BYTES + 1024),
            "dirty": False,
            "root": "/repo",
        }
    )

    execution = _to_execution(_run_report(), vcs=vcs)

    assert execution.vcs is not None
    assert execution.vcs.commit_subject is not None
    assert len(execution.vcs.commit_subject.encode("utf-8")) <= MAX_TEXT_FIELD_BYTES
    assert execution.vcs.commit_subject_truncated is True


def test_to_execution_truncates_an_oversized_interrupt_reason() -> None:
    """Stored like every other text field: cut to the byte bound. A reason
    within it is kept whole."""
    oversized = _to_execution(
        _run_report(interrupt_reason="x" * (MAX_TEXT_FIELD_BYTES + 1024)), vcs=None
    )
    short = _to_execution(_run_report(interrupt_reason="KeyboardInterrupt"), vcs=None)

    assert oversized.interrupt_reason is not None
    assert len(oversized.interrupt_reason.encode("utf-8")) == MAX_TEXT_FIELD_BYTES
    assert short.interrupt_reason == "KeyboardInterrupt"


def test_to_execution_a_partial_vcs_section_is_not_all_null_and_maps_through() -> None:
    """Only commit and branch present -- a detached HEAD or no-commits
    shape -- is NOT the all-null case, so it must map to a real
    `VcsContext`, not `None`."""
    vcs = VcsReport.model_validate(
        {
            "commit": None,
            "branch": "main",
            "commit_subject": None,
            "dirty": None,
            "root": "/repo",
        }
    )

    execution = _to_execution(_run_report(), vcs=vcs)

    assert execution.vcs is not None
    assert execution.vcs.commit is None
    assert execution.vcs.branch == "main"
    assert execution.vcs.root == "/repo"


# --- `_to_result`'s failure evidence mapping ---------------------------------


def _result_report(**overrides: object) -> ResultReport:
    payload: dict[str, object] = {
        "node_id": "packages/vantage/tests/test_x.py::test_case",
        "file_path": "packages/vantage/tests/test_x.py",
        "class_name": None,
        "function_name": "test_case",
        "param_id": None,
        "outcome": "failed",
        "duration": 0.0031,
        "started_at": None,
        "finished_at": None,
        "setup_outcome": "passed",
        "call_outcome": "failed",
        "teardown_outcome": "passed",
        "setup_duration": 0.0008,
        "call_duration": 0.0019,
        "teardown_duration": 0.0004,
        "worker_id": None,
    }
    payload.update(overrides)
    return ResultReport.model_validate(payload)


def test_to_result_bounds_a_64kib_oversized_traceback_and_flags_it() -> None:
    """A field over the 64 KiB bound is stored truncated and flagged."""
    item = _result_report(traceback="x" * (MAX_TEXT_FIELD_BYTES + 1024))

    result = _to_result(item)

    assert result.failure is not None
    assert result.failure.traceback is not None
    assert len(result.failure.traceback.encode("utf-8")) <= MAX_TEXT_FIELD_BYTES
    assert result.failure.traceback_truncated is True


def test_to_result_a_field_within_bound_is_stored_whole_unflagged() -> None:
    """A traceback under 64 KiB is stored unchanged, flag clear."""
    item = _result_report(traceback="a short traceback")

    result = _to_result(item)

    assert result.failure is not None
    assert result.failure.traceback == "a short traceback"
    assert result.failure.traceback_truncated is False


# Every field with a client-side truncation flag, as `(wire value, wire flag,
# container, stored flag)`. The plugin's report budget drops `traceback` and
# captured output before `failure_message`, so the client's flag must survive
# at every field, not only there.
_DISJUNCTION_FIELDS = (
    ("failure_message", "failure_message_truncated", "failure", "failure_message_truncated"),
    ("failure_repr", "failure_repr_truncated", "failure", "failure_repr_truncated"),
    ("traceback", "traceback_truncated", "failure", "traceback_truncated"),
    ("skip_reason", "skip_reason_truncated", "failure", "skip_reason_truncated"),
    ("xfail_reason", "xfail_reason_truncated", "failure", "xfail_reason_truncated"),
    ("captured_stdout", "captured_stdout_truncated", "captured", "stdout_truncated"),
    ("captured_stderr", "captured_stderr_truncated", "captured", "stderr_truncated"),
)


@pytest.mark.parametrize(
    ("wire_value", "wire_flag", "container", "stored_flag"),
    _DISJUNCTION_FIELDS,
    ids=[f[0] for f in _DISJUNCTION_FIELDS],
)
def test_to_result_disjunction_holds_at_every_budgeted_field(
    wire_value: str, wire_flag: str, container: str, stored_flag: str
) -> None:
    """At every budgeted field, a client-reported truncation on a value that
    fits the server's bound survives: assigning only the server's flag would
    lose the fact that the field was dropped rather than never captured.
    """
    item = _result_report(**{wire_value: "short value", wire_flag: True})

    result = _to_result(item)

    subject = getattr(result, container)
    assert subject is not None
    assert getattr(subject, stored_flag) is True


def test_to_result_normalizes_all_null_failure_to_none() -> None:
    """Every failure field absent, `None` or `False` normalises
    `Result.failure` to `None`."""
    item = _result_report()

    result = _to_result(item)

    assert result.failure is None


def test_to_result_captured_output_is_never_none() -> None:
    """`captured_stdout=None` (never captured) and `captured_stdout=""`
    (captured, empty) both produce a `CapturedOutput` instance -- the
    distinction lives inside it, never as `Result.captured is None`."""
    absent = _to_result(_result_report(captured_stdout=None))
    empty = _to_result(_result_report(captured_stdout=""))

    assert isinstance(absent.captured, CapturedOutput)
    assert absent.captured.stdout is None
    assert isinstance(empty.captured, CapturedOutput)
    assert empty.captured.stdout == ""


# --- `_to_run_metadata` -------------------------------------------------------


def _metadata_file_report(**overrides: object) -> MetadataFileReport:
    payload: dict[str, object] = {
        "path": "config/firmware.json",
        "format": "json",
        "status": "captured",
        "keys": ["firmware_version"],
        "content": json.dumps({"firmware_version": "2.1"}),
    }
    payload.update(overrides)
    return MetadataFileReport.model_validate(payload)


def _metadata_report(*files: MetadataFileReport) -> MetadataReport:
    return MetadataReport(declaration="vantage-metadata.json", files=list(files))


def test_to_run_metadata_returns_empty_when_the_section_is_absent() -> None:
    assert _to_run_metadata(None) == EMPTY_RUN_METADATA


def test_to_run_metadata_captures_a_well_formed_declared_key() -> None:
    metadata = _metadata_report(_metadata_file_report())

    result = _to_run_metadata(metadata)

    assert result.files == (
        MetadataFile(source_file="config/firmware.json", content_type="json", status="captured"),
    )
    assert result.entries == (
        MetadataEntry(
            key="firmware_version",
            value="2.1",
            source_file="config/firmware.json",
            status="captured",
        ),
    )


@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd",
        "../escape.json",
        "config/../../escape.json",
        "x" * 1025,
        "C:\\x.json",
        "C:/x.json",
        "C:x.json",
        "..\\escape.json",
        "config\\..\\..\\escape.json",
        "\\\\server\\share\\x.json",
        "//server/share/x.json",
        "\\x.json",
        "config\\firmware.json",
    ],
)
def test_to_run_metadata_drops_an_entry_whose_source_file_fails_the_shape_recheck(
    bad_path: str,
) -> None:
    """An oversized, absolute or `..` `source_file` is dropped whole, never
    rejected: no file row and no key row, and no exception. The client's
    platform is unknown, so Windows forms -- a drive, a UNC share, a
    backslash separator -- are refused on any server."""
    metadata = _metadata_report(_metadata_file_report(path=bad_path))

    result = _to_run_metadata(metadata)

    assert result == EMPTY_RUN_METADATA


@pytest.mark.parametrize("path", ["config/firmware.json", "x..y.json", "...json", "a b.json"])
def test_to_run_metadata_keeps_a_relative_path_with_dots_in_its_names(path: str) -> None:
    result = _to_run_metadata(_metadata_report(_metadata_file_report(path=path)))

    assert result.files == (MetadataFile(source_file=path, content_type="json", status="captured"),)


def test_to_run_metadata_drops_an_entry_with_an_unrecognised_status() -> None:
    """`status` is unconstrained on the wire; a value the schema cannot store
    never reaches `record_session` -- the file and its keys are dropped
    whole."""
    metadata = _metadata_report(_metadata_file_report(status="not-a-real-status", content=None))

    result = _to_run_metadata(metadata)

    assert result == EMPTY_RUN_METADATA


def test_to_run_metadata_drops_an_entry_with_an_unrecognised_format() -> None:
    """The `content_type` sibling of the status guard above."""
    metadata = _metadata_report(
        _metadata_file_report(format="xml", status="not_found", content=None)
    )

    result = _to_run_metadata(metadata)

    assert result == EMPTY_RUN_METADATA


def test_to_run_metadata_drops_a_file_repeating_an_earlier_path_with_its_keys() -> None:
    """The store keeps one row per path, so the repeat's keys would be
    stored under the first file's status. Neither its file row nor any of
    its keys is kept, and the first file is untouched."""
    first = _metadata_file_report()
    repeat = _metadata_file_report(
        content=json.dumps({"firmware_version": "9.9", "board": "C"}),
        keys=["firmware_version", "board"],
    )

    result = _to_run_metadata(_metadata_report(first, repeat))

    assert result.files == (
        MetadataFile(source_file="config/firmware.json", content_type="json", status="captured"),
    )
    assert result.entries == (
        MetadataEntry(
            key="firmware_version",
            value="2.1",
            source_file="config/firmware.json",
            status="captured",
        ),
    )


def test_to_run_metadata_marks_a_non_captured_file_and_all_its_keys_source_unavailable() -> None:
    """The plugin's own status is trusted verbatim, and every declared key
    under that file still becomes a `source_unavailable` row."""
    metadata = _metadata_report(
        _metadata_file_report(status="not_found", content=None, keys=["a", "b"])
    )

    result = _to_run_metadata(metadata)

    assert result.files == (
        MetadataFile(source_file="config/firmware.json", content_type="json", status="not_found"),
    )
    assert set(result.entries) == {
        MetadataEntry(
            key="a", value=None, source_file="config/firmware.json", status="source_unavailable"
        ),
        MetadataEntry(
            key="b", value=None, source_file="config/firmware.json", status="source_unavailable"
        ),
    }


def test_to_run_metadata_marks_an_unparseable_document_malformed() -> None:
    """A document the server cannot parse is marked `malformed` by the
    server itself."""
    metadata = _metadata_report(
        _metadata_file_report(content="not json at all", keys=["firmware_version"])
    )

    result = _to_run_metadata(metadata)

    assert result.files == (
        MetadataFile(source_file="config/firmware.json", content_type="json", status="malformed"),
    )
    assert result.entries == (
        MetadataEntry(
            key="firmware_version",
            value=None,
            source_file="config/firmware.json",
            status="source_unavailable",
        ),
    )


def test_to_run_metadata_marks_a_captured_file_without_content_malformed() -> None:
    """`captured` with nothing to parse would store a file row that
    contradicts its own keys; the server records what it found instead."""
    metadata = _metadata_report(_metadata_file_report(content=None, keys=["a", "b"]))

    result = _to_run_metadata(metadata)

    assert result.files == (
        MetadataFile(source_file="config/firmware.json", content_type="json", status="malformed"),
    )
    assert set(result.entries) == {
        MetadataEntry(
            key=key, value=None, source_file="config/firmware.json", status="source_unavailable"
        )
        for key in ("a", "b")
    }


# --- server-side metadata bounds ---------------------------------------------


def _document_of_size(size: int) -> str:
    """A JSON document of exactly `size` UTF-8 bytes holding one key."""
    shell = '{"firmware_version": "2.1", "pad": ""}'
    return shell[:-2] + "x" * (size - len(shell)) + '"}'


def _file(index: int, content: str | None, **overrides: object) -> MetadataFileReport:
    return _metadata_file_report(
        path=f"config/file_{index}.json", content=content, keys=[f"key_{index}"], **overrides
    )


def test_to_run_metadata_marks_a_document_over_the_file_bound_too_large_without_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plugin never ships a declared file over its per-file bound, and
    parsing YAML costs CPU per byte that every other request waits on, so
    any other client's oversized document is refused before the parser
    sees it."""

    def _never_called(*_args: object) -> None:
        raise AssertionError("an oversized document reached the parser")

    monkeypatch.setattr(metadata_parse, "parse", _never_called)
    content = _document_of_size(runs_route._MAX_DECLARED_FILE_BYTES + 1)

    result = _to_run_metadata(_metadata_report(_metadata_file_report(content=content)))

    assert result.files == (
        MetadataFile(source_file="config/firmware.json", content_type="json", status="too_large"),
    )
    assert result.entries == (
        MetadataEntry(
            key="firmware_version",
            value=None,
            source_file="config/firmware.json",
            status="source_unavailable",
        ),
    )


def test_to_run_metadata_parses_a_document_exactly_at_the_file_bound() -> None:
    content = _document_of_size(runs_route._MAX_DECLARED_FILE_BYTES)
    assert len(content.encode("utf-8")) == runs_route._MAX_DECLARED_FILE_BYTES

    result = _to_run_metadata(_metadata_report(_metadata_file_report(content=content)))

    assert [file.status for file in result.files] == ["captured"]
    assert [entry.value for entry in result.entries] == ["2.1"]


def test_to_run_metadata_marks_every_document_past_the_section_budget_over_budget() -> None:
    """The budget is spent in order, as the plugin spends it: once one
    document does not fit, it and every later captured document are
    `over_budget`; a file the client already reported uncaptured keeps its
    own status."""
    per_file = runs_route._MAX_DECLARED_FILE_BYTES
    fitting = runs_route._MAX_METADATA_SECTION_BYTES // per_file
    files = [_file(index, _document_of_size(per_file)) for index in range(fitting)]
    files.append(_file(fitting, "{}"))
    files.append(_file(fitting + 1, None, status="not_found"))
    files.append(_file(fitting + 2, "{}"))

    result = _to_run_metadata(_metadata_report(*files))

    assert [file.status for file in result.files] == [
        *["captured"] * fitting,
        "over_budget",
        "not_found",
        "over_budget",
    ]
    assert {entry.key: entry.status for entry in result.entries}[f"key_{fitting}"] == (
        "source_unavailable"
    )


def test_to_run_metadata_drops_a_key_over_the_key_bound_and_keeps_one_at_it() -> None:
    at_bound = "k" * MAX_METADATA_KEY_CHARS
    over_bound = "k" * (MAX_METADATA_KEY_CHARS + 1)
    content = json.dumps({at_bound: "a", over_bound: "b"})

    result = _to_run_metadata(
        _metadata_report(_metadata_file_report(content=content, keys=[at_bound, over_bound]))
    )

    assert [(entry.key, entry.value) for entry in result.entries] == [(at_bound, "a")]
    assert [file.status for file in result.files] == ["captured"]


def test_to_run_metadata_keeps_at_most_the_entry_bound_across_files() -> None:
    """Keys are counted per run, across files, first declared first kept;
    a key declared twice is stored once, as the store's primary key would
    keep it. Every file still gets its row."""
    first = [f"a_{index}" for index in range(MAX_METADATA_ENTRIES - 1)]
    second = ["a_0", "b_0", "b_1"]
    metadata = _metadata_report(
        _metadata_file_report(path="config/a.json", content="{}", keys=first),
        _metadata_file_report(path="config/b.json", content="{}", keys=second),
    )

    result = _to_run_metadata(metadata)

    assert len(result.files) == 2
    assert [entry.key for entry in result.entries] == [*first, "b_0"]
    assert result.entries[0].source_file == "config/a.json"


def test_the_mirrored_plugin_bounds_equal_the_plugins_own() -> None:
    """The two distributions cannot import each other at runtime, so the
    server carries copies; a plugin bound raised alone would make the
    server drop files the plugin captured."""
    assert runs_route._MAX_DECLARED_PATH_CHARS == plugin_metadata.MAX_DECLARED_PATH_CHARS
    assert runs_route._MAX_DECLARED_FILE_BYTES == plugin_metadata.MAX_DECLARED_FILE_BYTES
    assert runs_route._MAX_METADATA_SECTION_BYTES == plugin_metadata.MAX_METADATA_SECTION_BYTES
