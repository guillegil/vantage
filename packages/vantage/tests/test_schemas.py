"""`VcsReport`'s wire shape (design.md D47): `extra="forbid"`, and a
`commit` field that accepts a SHA-256 (64 hex chars), never a 40-hex
pattern -- git is migrating away from SHA-1.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from vantage.service.schemas import ResultReport, SessionReport, VcsReport


def _well_formed_vcs(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "commit": "a" * 40,
        "branch": "main",
        "commit_subject": "Fix the thing",
        "dirty": False,
        "root": "/repo",
    }
    payload.update(overrides)
    return payload


def test_vcs_report_accepts_a_well_formed_section() -> None:
    report = VcsReport.model_validate(_well_formed_vcs())

    assert report.commit == "a" * 40
    assert report.branch == "main"


def test_vcs_report_accepts_a_sha256_commit_sixty_four_hex_characters() -> None:
    report = VcsReport.model_validate(_well_formed_vcs(commit="f" * 64))

    assert report.commit == "f" * 64


def test_vcs_report_rejects_a_commit_longer_than_sixty_four_characters() -> None:
    with pytest.raises(ValidationError, match="commit"):
        VcsReport.model_validate(_well_formed_vcs(commit="a" * 65))


def test_vcs_report_accepts_all_five_fields_null() -> None:
    report = VcsReport.model_validate(
        _well_formed_vcs(commit=None, branch=None, commit_subject=None, dirty=None, root=None)
    )

    assert report.commit is None
    assert report.branch is None
    assert report.commit_subject is None
    assert report.dirty is None
    assert report.root is None


def test_vcs_report_rejects_an_unknown_field_inside_the_section() -> None:
    """`extra="forbid"`, matching `RunReport` -- an unknown field inside
    `vcs` means the two sides disagree about what a VCS snapshot is
    (design.md D47), unlike `ResultReport`'s deliberately different
    `extra="allow"`."""
    with pytest.raises(ValidationError, match="extra"):
        VcsReport.model_validate(_well_formed_vcs(tag="v1.2.3"))


def test_vcs_report_rejects_a_missing_required_field() -> None:
    incomplete = _well_formed_vcs()
    del incomplete["root"]

    with pytest.raises(ValidationError, match="root"):
        VcsReport.model_validate(incomplete)


def test_session_report_vcs_defaults_to_none_when_the_section_is_absent() -> None:
    """An older plugin's report shape -- no `vcs` key at all -- still
    validates; `SessionReport.vcs` defaults to `None` (design.md D47)."""
    report = SessionReport.model_validate(
        {
            "run": {
                "id": "a" * 32,
                "started_at": "2026-08-15T09:14:02.481930+00:00",
                "finished_at": None,
                "exit_status": None,
                "interrupted": False,
                "interrupt_reason": None,
            }
        }
    )

    assert report.vcs is None


def test_session_report_carries_a_well_formed_vcs_section() -> None:
    report = SessionReport.model_validate(
        {
            "run": {
                "id": "a" * 32,
                "started_at": "2026-08-15T09:14:02.481930+00:00",
                "finished_at": None,
                "exit_status": None,
                "interrupted": False,
                "interrupt_reason": None,
            },
            "vcs": _well_formed_vcs(),
        }
    )

    assert report.vcs is not None
    assert report.vcs.commit == "a" * 40


def _minimal_result_entry(**overrides: object) -> dict[str, object]:
    """The pre-`failure-capture` wire shape: no failure-evidence keys at
    all, the exact shape an older plugin still sends (design.md D75)."""
    payload: dict[str, object] = {
        "node_id": "packages/vantage/tests/test_x.py::test_case",
        "file_path": "packages/vantage/tests/test_x.py",
        "class_name": None,
        "function_name": "test_case",
        "param_id": None,
        "outcome": "passed",
        "duration": 0.0031,
        "started_at": None,
        "finished_at": None,
        "setup_outcome": "passed",
        "call_outcome": "passed",
        "teardown_outcome": "passed",
        "setup_duration": 0.0008,
        "call_duration": 0.0019,
        "teardown_duration": 0.0004,
        "worker_id": None,
    }
    payload.update(overrides)
    return payload


def test_result_report_failure_evidence_fields_all_default_to_absent() -> None:
    """design.md D75: every new failure-evidence field on `ResultReport` is
    optional and defaults to the absent shape, so an older plugin's report
    -- carrying none of these keys -- still validates. *(session-ingestion →
    Optional failure-evidence fields)*"""
    report = ResultReport.model_validate(_minimal_result_entry())

    assert report.failure_type is None
    assert report.failure_message is None
    assert report.failure_message_truncated is False
    assert report.failure_path is None
    assert report.failure_lineno is None
    assert report.failure_repr is None
    assert report.failure_repr_truncated is False
    assert report.traceback is None
    assert report.traceback_truncated is False
    assert report.skip_reason is None
    assert report.skip_reason_truncated is False
    assert report.xfail_reason is None
    assert report.xfail_reason_truncated is False
    assert report.captured_stdout is None
    assert report.captured_stdout_truncated is False
    assert report.captured_stderr is None
    assert report.captured_stderr_truncated is False
