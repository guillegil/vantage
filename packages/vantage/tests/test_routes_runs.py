"""`_to_execution`'s `vcs` normalisation (design.md D48) -- a unit-level
precursor to Phase 4's endpoint-level scenarios (`test_ingestion.py`),
proven directly against `service/routes/runs.py`'s own helper before any
storage adapter persists the section.
"""

from __future__ import annotations

from datetime import datetime, timezone

from vantage.core.domain.result import CapturedOutput
from vantage.service.routes.runs import _to_execution, _to_result
from vantage.service.schemas import ResultReport, RunReport, VcsReport
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
    nulls (design.md D48's own normalisation rule)."""
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
    """`_to_execution` applies `truncate()` to `commit_subject` (D49, 3.9)."""
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


# --- Phase 6: `_to_result`'s failure-evidence mapping (design.md D75) ------


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
    """failure-evidence → Per-field 64 KiB bound → An oversized field is
    stored truncated, flagged (task 6.3)."""
    item = _result_report(traceback="x" * (MAX_TEXT_FIELD_BYTES + 1024))

    result = _to_result(item)

    assert result.failure is not None
    assert result.failure.traceback is not None
    assert len(result.failure.traceback.encode("utf-8")) <= MAX_TEXT_FIELD_BYTES
    assert result.failure.traceback_truncated is True


def test_to_result_a_field_within_bound_is_stored_whole_unflagged() -> None:
    """task 6.4: a sub-64-KiB traceback is unchanged, flag clear."""
    item = _result_report(traceback="a short traceback")

    result = _to_result(item)

    assert result.failure is not None
    assert result.failure.traceback == "a short traceback"
    assert result.failure.traceback_truncated is False


def test_to_result_truncation_flag_is_a_disjunction_client_true_server_false() -> None:
    """**The D75 test proven able to fail** (task 6.5): the client reports a
    budget drop (`failure_message_truncated=True`) on a message that fits
    the server's own bound whole -- `truncate()` reports `False` for it. A
    naive `stored_flag = server_flag` assignment clears the client's report;
    the correct disjunction keeps it `True`."""
    item = _result_report(failure_message="short message", failure_message_truncated=True)

    result = _to_result(item)

    assert result.failure is not None
    assert result.failure.failure_message == "short message"
    assert result.failure.failure_message_truncated is True


def test_to_result_disjunction_other_direction_server_flag_still_wins() -> None:
    """task 6.6: the client sends `False` on a field the server itself must
    cut; the stored flag is `True` regardless of what the client claimed."""
    item = _result_report(traceback="x" * (MAX_TEXT_FIELD_BYTES + 1024), traceback_truncated=False)

    result = _to_result(item)

    assert result.failure is not None
    assert result.failure.traceback_truncated is True


def test_to_result_normalizes_all_null_failure_to_none() -> None:
    """task 6.7, D77 (mirroring D48's `_to_vcs_context`): every failure
    field absent/None/False normalises `Result.failure` to `None`."""
    item = _result_report()

    result = _to_result(item)

    assert result.failure is None


def test_to_result_captured_output_is_never_none() -> None:
    """task 6.8, D77's asymmetry: `captured_stdout=None` (never captured)
    and `captured_stdout=""` (captured, empty) both produce a
    `CapturedOutput` instance -- the distinction lives inside it, never as
    `Result.captured is None`."""
    absent = _to_result(_result_report(captured_stdout=None))
    empty = _to_result(_result_report(captured_stdout=""))

    assert isinstance(absent.captured, CapturedOutput)
    assert absent.captured.stdout is None
    assert isinstance(empty.captured, CapturedOutput)
    assert empty.captured.stdout == ""
