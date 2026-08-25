"""The read types on the storage port: `Page`, `RunListEntry`, `RunDetail`,
`HistoryEntry`, and the pagination constants (design.md D57, D58, D61).

Phase 1 adds these types and constants only -- `ExecutionStore` itself gains
no method in this slice, so no adapter goes out of structural conformance
mid-slice (tasks.md Phase 1). New tests carry no `req` marker -- this change
mints no numeric requirement identifiers (CLAUDE.md).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from vantage.core.domain.execution import Execution, Identity
from vantage.core.domain.projection import FailureProjection, VcsProjection
from vantage.core.domain.result import CaseIdentity
from vantage.core.ports.storage import (
    MAX_IDENTITY_CHARS,
    MAX_PAGE_ITEMS,
    HistoryEntry,
    Page,
    ResultListEntry,
    RunDetail,
    RunListEntry,
)

_STARTED = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)


def _execution() -> Execution:
    return Execution(
        identity=Identity("a" * 32),
        started_at=_STARTED,
        finished_at=None,
        exit_status=None,
        interrupted=False,
        interrupt_reason=None,
    )


def test_max_page_items_is_200() -> None:
    """history-read-api -> Bounded pagination -> A list response never
    exceeds 200 items (D61)."""
    assert MAX_PAGE_ITEMS == 200


def test_max_identity_chars_is_1024() -> None:
    """D54's 1,024-character identity bound."""
    assert MAX_IDENTITY_CHARS == 1024


def test_page_carries_items_and_has_more_and_no_total() -> None:
    """D58: `Page[T]` is a two-field envelope -- no `total`, because no
    scenario asks for one and a total requires a `COUNT(*)` on every page."""
    page: Page[int] = Page(items=(1, 2, 3), has_more=True)

    assert page.items == (1, 2, 3)
    assert page.has_more is True
    assert "total" not in {f.name for f in dataclasses.fields(Page)}


def test_run_list_entry_carries_execution_last_contact_at_and_vcs_projection() -> None:
    """D58, D59: `RunListEntry` is the lean list type -- its `vcs` field is a
    `VcsProjection`, distinct from `RunDetail`'s full `VcsContext`."""
    projection = VcsProjection(
        commit="a" * 40,
        branch="main",
        commit_subject="subject",
        commit_subject_truncated=False,
        dirty=False,
    )

    entry = RunListEntry(execution=_execution(), last_contact_at=_STARTED, vcs=projection)

    assert entry.execution.identity.value == "a" * 32
    assert entry.last_contact_at == _STARTED
    assert entry.vcs is projection


def test_run_detail_carries_execution_and_last_contact_at_only() -> None:
    """D58: `RunDetail` is the full-record type -- its `execution.vcs` is
    the whole `VcsContext`, unbounded, so `RunDetail` itself carries no
    separate `vcs` field."""
    detail = RunDetail(execution=_execution(), last_contact_at=_STARTED)

    assert detail.execution.identity.value == "a" * 32
    assert detail.last_contact_at == _STARTED
    assert "vcs" not in {f.name for f in dataclasses.fields(RunDetail)}


def test_result_list_entry_carries_identity_outcome_timings_worker_and_failure_projection() -> None:
    """D77: `ResultListEntry` is the lean-list type for `list_results` --
    identity, outcome, every phase timing `_result_item` already reads off
    the pre-existing `/runs/{id}/results` wire contract, `worker_id`, and a
    `FailureProjection | None` -- no field to carry `traceback`,
    `failure_repr` or captured output (design.md D76)."""
    identity = CaseIdentity(
        node_id="t.py::test_x",
        file_path="t.py",
        class_name=None,
        function_name="test_x",
        param_id=None,
    )
    projection = FailureProjection(
        failure_type="AssertionError",
        failure_message="boom",
        failure_message_truncated=False,
        failure_path="t.py",
        failure_lineno=10,
        skip_reason=None,
        xfail_reason=None,
    )

    entry = ResultListEntry(
        identity=identity,
        outcome="failed",
        duration=0.01,
        started_at=_STARTED,
        finished_at=_STARTED,
        setup_outcome="passed",
        call_outcome="failed",
        teardown_outcome="passed",
        setup_duration=0.001,
        call_duration=0.008,
        teardown_duration=0.001,
        worker_id="gw0",
        failure=projection,
    )

    assert entry.identity is identity
    assert entry.outcome == "failed"
    assert entry.duration == 0.01
    assert entry.started_at == _STARTED
    assert entry.finished_at == _STARTED
    assert entry.setup_outcome == "passed"
    assert entry.call_outcome == "failed"
    assert entry.teardown_outcome == "passed"
    assert entry.worker_id == "gw0"
    assert entry.failure is projection
    field_names = {f.name for f in dataclasses.fields(ResultListEntry)}
    assert "traceback" not in field_names
    assert "failure_repr" not in field_names
    assert "captured" not in field_names


def test_history_entry_carries_run_shape_fields_and_vcs_projection() -> None:
    """D57, D58, D59: `HistoryEntry` is the lean list type for a test's
    execution history -- one entry per run that test appeared in."""
    entry = HistoryEntry(
        run_id="a" * 32,
        started_at=_STARTED,
        finished_at=None,
        last_contact_at=_STARTED,
        outcome="passed",
        duration=0.31,
        vcs=None,
    )

    assert entry.run_id == "a" * 32
    assert entry.started_at == _STARTED
    assert entry.finished_at is None
    assert entry.last_contact_at == _STARTED
    assert entry.outcome == "passed"
    assert entry.duration == 0.31
    assert entry.vcs is None
