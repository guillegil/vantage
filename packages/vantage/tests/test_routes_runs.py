"""`_to_execution`'s `vcs` normalisation (design.md D48) -- a unit-level
precursor to Phase 4's endpoint-level scenarios (`test_ingestion.py`),
proven directly against `service/routes/runs.py`'s own helper before any
storage adapter persists the section.
"""

from __future__ import annotations

from datetime import datetime, timezone

from vantage.service.routes.runs import _to_execution
from vantage.service.schemas import RunReport, VcsReport


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
    """`_to_execution` applies `truncate()` to `commit_subject`, setting
    `commit_subject_truncated` (design.md D49, tasks.md 3.9)."""
    from vantage.service.truncation import MAX_TEXT_FIELD_BYTES

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
