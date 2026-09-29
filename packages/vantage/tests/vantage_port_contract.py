"""Shared contract for any `ExecutionStore` implementation.

Never collected directly by pytest -- it is not named ``test_*`` -- and kept
beside the tests rather than in the package, because ``vantage.core`` must not
import pytest. Each adapter's ``test_*_store.py`` subclasses
``ExecutionStoreContract``, provides ``store`` and ``stored_metadata``
fixtures, and inherits every test unchanged, so every adapter is held to the
same behaviour. An adapter whose databases the local store can make also
subclasses ``LocalDatabaseContract`` and provides ``local_store``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from vantage.core.domain.access import (
    ADMIN_SCOPE,
    LOGIN_TOKEN_LABEL,
    LOGIN_TOKEN_LIFETIME,
    MANAGE_SCOPE,
    READ_SCOPE,
    RECORD_SCOPE,
    SCOPES,
    Grant,
    Token,
    User,
    token_digest,
)
from vantage.core.domain.changes import Streak, classify, is_baseline_candidate, ran_to_its_end
from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.metadata import MAX_METADATA_ENTRIES
from vantage.core.domain.projection import (
    LIST_COMMIT_SUBJECT_CHARS,
    LIST_FAILURE_MESSAGE_CHARS,
    project_failure,
    project_vcs,
)
from vantage.core.domain.projects import (
    DEFAULT_PROJECT,
    EDITOR_ROLE,
    OWNER_ROLE,
    VIEWER_ROLE,
    Membership,
    Project,
)
from vantage.core.domain.result import CapturedOutput, CaseIdentity, FailureEvidence, Result
from vantage.core.ports.storage import (
    MAX_PAGE_ITEMS,
    BaselineRef,
    ChangeEntry,
    Comparison,
    ExecutionStore,
    ForeignRunError,
    MetadataEntry,
    MetadataFile,
    NamespaceFullError,
    ProjectExistsError,
    ProjectMismatchError,
    ProjectSetting,
    ResultChange,
    RunKey,
    RunMetadata,
    UnknownProjectError,
    UnknownUserError,
    UserExistsError,
)


def _execution(
    hex_id: str,
    *,
    finished: bool = True,
    started: datetime | None = None,
    vcs: VcsContext | None = None,
) -> Execution:
    started = (
        started if started is not None else datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
    )
    return Execution(
        identity=Identity(hex_id),
        started_at=started,
        finished_at=(started + timedelta(seconds=5)) if finished else None,
        exit_status=0 if finished else None,
        interrupted=not finished,
        interrupt_reason=None if finished else "ctrl-c",
        vcs=vcs,
    )


def _start_only_execution(
    hex_id: str, *, started: datetime | None = None, vcs: VcsContext | None = None
) -> Execution:
    """The shape a start-write reports: `exit_status` is the only field never
    sent, `interrupted`/`interrupt_reason` are their defaults because nothing
    is yet known about how the session will end."""
    started = (
        started if started is not None else datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
    )
    return Execution(
        identity=Identity(hex_id),
        started_at=started,
        finished_at=None,
        exit_status=None,
        interrupted=False,
        interrupt_reason=None,
        vcs=vcs,
    )


def _vcs(
    *,
    commit: str | None = "a" * 40,
    branch: str | None = "main",
    commit_subject: str | None = "a commit subject",
    # `True` by default: with `False`, both branches of the conflict clause's
    # `CASE WHEN excluded.vcs_commit_subject IS NOT NULL` return the same
    # value, so a broken CASE would go unnoticed.
    commit_subject_truncated: bool = True,
    dirty: bool | None = False,
    root: str | None = "/repo",
) -> VcsContext:
    return VcsContext(
        commit=commit,
        branch=branch,
        commit_subject=commit_subject,
        commit_subject_truncated=commit_subject_truncated,
        dirty=dirty,
        root=root,
    )


def _result(
    node_id: str,
    *,
    outcome: str = "passed",
    param_id: str | None = None,
    duration: float | None = 0.003,
    setup_outcome: str | None = "passed",
    call_outcome: str | None = "passed",
    teardown_outcome: str | None = "passed",
    setup_duration: float | None = 0.001,
    call_duration: float | None = 0.001,
    teardown_duration: float | None = 0.001,
    failure: FailureEvidence | None = None,
    captured: CapturedOutput | None = None,
) -> Result:
    started = datetime(2026, 8, 15, 9, 0, 1, tzinfo=timezone.utc)
    return Result(
        identity=CaseIdentity(
            node_id=node_id,
            file_path=node_id.split("::", 1)[0],
            class_name=None,
            function_name=node_id.rsplit("::", 1)[-1],
            param_id=param_id,
        ),
        outcome=outcome,
        duration=duration,
        started_at=started,
        finished_at=started + timedelta(seconds=duration or 0),
        setup_outcome=setup_outcome,
        call_outcome=call_outcome,
        teardown_outcome=teardown_outcome,
        setup_duration=setup_duration,
        call_duration=call_duration,
        teardown_duration=teardown_duration,
        worker_id=None,
        failure=failure,
        captured=captured
        if captured is not None
        else CapturedOutput(
            stdout=None, stdout_truncated=False, stderr=None, stderr_truncated=False
        ),
    )


def _failure(
    *,
    failure_type: str | None = "AssertionError",
    failure_message: str | None = "boom",
    failure_message_truncated: bool = False,
    failure_path: str | None = "t.py",
    failure_lineno: int | None = 10,
    failure_repr: str | None = "AssertionError('boom')",
    failure_repr_truncated: bool = False,
    traceback: str | None = "t.py:10: in test_x\n    assert False\nAssertionError",
    traceback_truncated: bool = False,
    skip_reason: str | None = None,
    skip_reason_truncated: bool = False,
    xfail_reason: str | None = None,
    xfail_reason_truncated: bool = False,
) -> FailureEvidence:
    return FailureEvidence(
        failure_type=failure_type,
        failure_message=failure_message,
        failure_message_truncated=failure_message_truncated,
        failure_path=failure_path,
        failure_lineno=failure_lineno,
        failure_repr=failure_repr,
        failure_repr_truncated=failure_repr_truncated,
        traceback=traceback,
        traceback_truncated=traceback_truncated,
        skip_reason=skip_reason,
        skip_reason_truncated=skip_reason_truncated,
        xfail_reason=xfail_reason,
        xfail_reason_truncated=xfail_reason_truncated,
    )


def _captured(
    *,
    stdout: str | None = None,
    stdout_truncated: bool = False,
    stderr: str | None = None,
    stderr_truncated: bool = False,
) -> CapturedOutput:
    return CapturedOutput(
        stdout=stdout,
        stdout_truncated=stdout_truncated,
        stderr=stderr,
        stderr_truncated=stderr_truncated,
    )


StoredMetadata = Callable[[str], RunMetadata]
"""Reads back the metadata files and entries an adapter stored for one run
id, straight from that adapter's own storage rather than through
`get_run_metadata`, so what a write stored is checked by a read that shares
no code with the port's."""


class ExecutionStoreContract:
    """Inherit this and override the `store` fixture with a fresh adapter
    instance, and `stored_metadata` with a reader of that instance's storage."""

    @pytest.fixture
    def store(self) -> ExecutionStore:
        raise NotImplementedError("subclasses must override the `store` fixture")

    @pytest.fixture
    def stored_metadata(self) -> StoredMetadata:
        raise NotImplementedError("subclasses must override the `stored_metadata` fixture")

    def test_first_write_creates_a_row(self, store: ExecutionStore) -> None:
        execution = _execution("a" * 32)

        created = store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        assert created is True
        assert store.count_executions() == 1

    def test_replaying_the_same_id_reports_no_new_row(self, store: ExecutionStore) -> None:
        execution = _execution("b" * 32)
        store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        created_again = store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        assert created_again is False
        assert store.count_executions() == 1

    def test_get_execution_returns_what_was_stored(self, store: ExecutionStore) -> None:
        execution = _execution("c" * 32, finished=False)
        store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        found = store.get_execution(execution.identity.value)

        assert found == execution

    def test_get_execution_returns_none_for_an_unknown_id(self, store: ExecutionStore) -> None:
        assert store.get_execution("d" * 32) is None

    def test_recording_a_session_with_results_persists_both(self, store: ExecutionStore) -> None:
        execution = _execution("e" * 32)
        results = (_result("t.py::test_a"), _result("t.py::test_b"))

        created = store.record_session(
            execution,
            results=results,
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        assert created is True
        assert store.count_results() == 2

    def test_replaying_the_same_report_does_not_duplicate_results(
        self, store: ExecutionStore
    ) -> None:
        execution = _execution("f" * 32)
        results = (_result("t.py::test_a"), _result("t.py::test_b"))
        store.record_session(
            execution,
            results=results,
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        replayed = store.record_session(
            execution,
            results=results,
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        assert replayed is False
        assert store.count_results() == 2

    def test_finish_after_start_applies_in_full(self, store: ExecutionStore) -> None:
        """A finish-write following an accepted start-write for the same run id
        applies in full -- `exit_status` goes NULL -> int, and the finish's
        other fields and results land."""
        identity = "1" + "0" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        start = _start_only_execution(identity, started=started)
        store.record_session(
            start, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        finish = _execution(identity, finished=True, started=started)
        results = (_result("t.py::test_a"),)
        store.record_session(
            finish, results=results, received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        stored = store.get_execution(identity)
        assert stored is not None
        assert stored.exit_status == finish.exit_status
        assert stored.finished_at == finish.finished_at
        assert stored.interrupted == finish.interrupted
        assert stored.interrupt_reason == finish.interrupt_reason
        assert store.count_results() == 1

    def test_reordered_start_after_finish_never_nulls_the_recorded_finish(
        self, store: ExecutionStore
    ) -> None:
        """A start-write arriving after the finish never nulls it -- the
        finish, its exit fields and its result rows survive."""
        identity = "1" + "1" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        finish = _execution(identity, finished=True, started=started)
        results = (_result("t.py::test_a"),)
        store.record_session(
            finish, results=results, received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        late_start = _start_only_execution(identity, started=started)
        store.record_session(
            late_start, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        stored = store.get_execution(identity)
        assert stored == finish
        assert store.count_results() == 1

    def test_replayed_finish_is_a_no_op_first_finish_wins(self, store: ExecutionStore) -> None:
        """A second finish for the same run is a no-op -- the first accepted
        finish wins."""
        identity = "1" + "2" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        first_finish = _execution(identity, finished=True, started=started)
        store.record_session(
            first_finish,
            results=(),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        second_finish = Execution(
            identity=Identity(identity),
            started_at=started,
            finished_at=started + timedelta(seconds=104),
            exit_status=1,
            interrupted=True,
            interrupt_reason="a-different-reason",
        )
        store.record_session(
            second_finish,
            results=(),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        stored = store.get_execution(identity)
        assert stored == first_finish

    def test_results_before_the_finish_accumulate_and_the_finish_adds_its_own(
        self, store: ExecutionStore
    ) -> None:
        """A run not yet finished takes every report's new results: the
        in-progress reports a split session sends first, then the finishing
        report's own."""
        identity = "1" + "4" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        start = _start_only_execution(identity, started=started)
        store.record_session(start, results=(), received_at=started, project=DEFAULT_PROJECT)
        store.record_session(
            start, results=(_result("t.py::test_a"),), received_at=started, project=DEFAULT_PROJECT
        )

        store.record_session(
            _execution(identity, started=started),
            results=(_result("t.py::test_b"),),
            received_at=started,
            project=DEFAULT_PROJECT,
        )

        stored = {result.identity.node_id for result in store.get_results(identity)}
        assert stored == {"t.py::test_a", "t.py::test_b"}

    def test_a_report_after_the_finish_adds_no_results(self, store: ExecutionStore) -> None:
        """A finished run is final. Whatever arrives for it later -- a finish
        replayed with another result set, an in-progress report -- stores no
        result and puts no node id in the catalogue."""
        identity = "1" + "5" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        finish = _execution(identity, started=started)
        store.record_session(
            finish, results=(_result("t.py::test_a"),), received_at=started, project=DEFAULT_PROJECT
        )

        store.record_session(
            finish, results=(_result("t.py::test_b"),), received_at=started, project=DEFAULT_PROJECT
        )
        store.record_session(
            _start_only_execution(identity, started=started),
            results=(_result("t.py::test_c"),),
            received_at=started,
            project=DEFAULT_PROJECT,
        )

        stored = [result.identity.node_id for result in store.get_results(identity)]
        assert stored == ["t.py::test_a"]
        assert store.get_catalogue_entry("t.py::test_b", project=DEFAULT_PROJECT) is None
        assert store.get_catalogue_entry("t.py::test_c", project=DEFAULT_PROJECT) is None

    def test_a_report_after_the_finish_adds_no_metadata(
        self, store: ExecutionStore, stored_metadata: StoredMetadata
    ) -> None:
        """A report arriving after the finish stores none of its metadata
        files or keys, not even ones the run does not hold yet."""
        identity = "1" + "6" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        first = RunMetadata(
            files=(MetadataFile(source_file="a.yaml", content_type="yaml", status="captured"),),
            entries=(MetadataEntry(key="k", value="v", source_file="a.yaml", status="captured"),),
        )
        later = RunMetadata(
            files=(MetadataFile(source_file="b.yaml", content_type="yaml", status="captured"),),
            entries=(MetadataEntry(key="k2", value="v2", source_file="b.yaml", status="captured"),),
        )
        finish = _execution(identity, started=started)
        store.record_session(
            finish, results=(), received_at=started, metadata=first, project=DEFAULT_PROJECT
        )

        store.record_session(
            finish, results=(), received_at=started, metadata=later, project=DEFAULT_PROJECT
        )

        stored = stored_metadata(identity)
        assert set(stored.files) == set(first.files)
        assert set(stored.entries) == set(first.entries)

    def test_duplicate_start_after_start_is_a_no_op(self, store: ExecutionStore) -> None:
        """A second start-write for the same run id changes nothing --
        `excluded.exit_status IS NULL` never satisfies the conflict `WHERE`,
        regardless of what `run.exit_status` holds."""
        identity = "1" + "3" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        first_start = _start_only_execution(identity, started=started)
        store.record_session(
            first_start, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        second_start = _start_only_execution(identity, started=started + timedelta(seconds=5))
        store.record_session(
            second_start,
            results=(),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        stored = store.get_execution(identity)
        assert stored == first_start

    def test_created_is_true_only_on_a_true_first_insert(self, store: ExecutionStore) -> None:
        """`record_session` returns True only for a true first insert. A finish
        applied over an existing start-only row, and a true duplicate, both
        return False -- `rowcount` cannot answer this under `DO UPDATE`, so
        the adapter must probe first."""
        identity = "1" + "4" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        start = _start_only_execution(identity, started=started)
        created_by_start = store.record_session(
            start, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )
        assert created_by_start is True

        finish = _execution(identity, finished=True, started=started)
        created_by_finish = store.record_session(
            finish, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )
        assert created_by_finish is False

        created_by_duplicate = store.record_session(
            finish, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )
        assert created_by_duplicate is False

    def test_get_results_preserves_phase_outcomes_and_durations_exactly(
        self, store: ExecutionStore
    ) -> None:
        execution = _execution("1" + "a" * 31)
        never_ran_call = _result(
            "t.py::test_setup_failure",
            outcome="error",
            setup_outcome="failed",
            call_outcome=None,
            teardown_outcome=None,
            call_duration=None,
            teardown_duration=None,
        )
        instant = _result("t.py::test_instant", outcome="passed", call_duration=0.0)
        store.record_session(
            execution,
            results=(never_ran_call, instant),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        stored = {r.identity.node_id: r for r in store.get_results(execution.identity.value)}

        assert stored["t.py::test_setup_failure"].call_duration is None
        assert stored["t.py::test_setup_failure"].call_outcome is None
        assert stored["t.py::test_setup_failure"].setup_outcome == "failed"
        assert stored["t.py::test_instant"].call_duration == 0.0

    def test_empty_param_id_is_distinct_from_no_param_id(self, store: ExecutionStore) -> None:
        execution = _execution("3" + "c" * 31)
        empty_param = _result("t.py::test_x[]", param_id="")
        no_param = _result("t.py::test_y", param_id=None)
        store.record_session(
            execution,
            results=(empty_param, no_param),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        stored = store.get_results(execution.identity.value)
        by_node_id = {r.identity.node_id: r.identity.param_id for r in stored}

        assert by_node_id["t.py::test_x[]"] == ""
        assert by_node_id["t.py::test_y"] is None
        without_param = [r for r in stored if r.identity.param_id is None]
        assert [r.identity.node_id for r in without_param] == ["t.py::test_y"]

    def test_catalogue_entry_advances_last_seen_and_keeps_first_seen(
        self, store: ExecutionStore
    ) -> None:
        node_id = "t.py::test_recurring"
        first_execution = _execution("4" + "d" * 31)
        store.record_session(
            first_execution,
            results=(_result(node_id),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        entry_after_first = store.get_catalogue_entry(node_id, project=DEFAULT_PROJECT)
        assert entry_after_first is not None
        assert entry_after_first.first_seen_at == first_execution.started_at
        assert entry_after_first.last_seen_at == first_execution.started_at
        assert entry_after_first.last_seen_run_id == first_execution.identity.value

        second_execution = _execution(
            "5" + "e" * 31, started=first_execution.started_at + timedelta(days=1)
        )
        store.record_session(
            second_execution,
            results=(_result(node_id),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        entry_after_second = store.get_catalogue_entry(node_id, project=DEFAULT_PROJECT)
        assert entry_after_second is not None
        assert entry_after_second.first_seen_at == first_execution.started_at
        assert entry_after_second.last_seen_at == second_execution.started_at
        assert entry_after_second.last_seen_run_id == second_execution.identity.value

    def test_an_older_session_does_not_roll_back_the_catalogue_entry(
        self, store: ExecutionStore
    ) -> None:
        node_id = "t.py::test_recurring_2"
        later_execution = _execution("6" + "f" * 31)
        store.record_session(
            later_execution,
            results=(_result(node_id),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        older_execution = _execution(
            "7" + "0" * 31, started=later_execution.started_at - timedelta(days=1)
        )
        store.record_session(
            older_execution,
            results=(_result(node_id),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        entry = store.get_catalogue_entry(node_id, project=DEFAULT_PROJECT)
        assert entry is not None
        assert entry.last_seen_at == later_execution.started_at
        assert entry.last_seen_run_id == later_execution.identity.value

    def test_a_late_report_of_an_earlier_session_moves_first_seen_back(
        self, store: ExecutionStore
    ) -> None:
        """Results arrive with the finish report, so of two overlapping
        sessions the one that started later can report first. The session
        that started earlier still saw the test first."""
        node_id = "t.py::test_overlapping"
        started = datetime(2026, 9, 1, 10, 30, 0, tzinfo=timezone.utc)
        short_job = _execution("a" * 32, started=started)
        long_job = _execution("b" * 32, started=started - timedelta(minutes=30))
        store.record_session(
            short_job,
            results=(_result(node_id),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            long_job,
            results=(_result(node_id),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        entry = store.get_catalogue_entry(node_id, project=DEFAULT_PROJECT)

        assert entry is not None
        assert entry.first_seen_at == long_job.started_at
        assert entry.last_seen_at == short_job.started_at
        assert entry.last_seen_run_id == short_job.identity.value

    def test_a_report_without_a_node_id_leaves_its_catalogue_entry_untouched(
        self, store: ExecutionStore
    ) -> None:
        stable_node_id = "t.py::test_untouched"
        other_node_id = "t.py::test_other"
        first_execution = _execution("8" + "1" * 31)
        store.record_session(
            first_execution,
            results=(_result(stable_node_id),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )
        entry_before = store.get_catalogue_entry(stable_node_id, project=DEFAULT_PROJECT)
        assert entry_before is not None

        second_execution = _execution("9" + "2" * 31)
        store.record_session(
            second_execution,
            results=(_result(other_node_id),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        entry_after = store.get_catalogue_entry(stable_node_id, project=DEFAULT_PROJECT)
        assert entry_after == entry_before

    def test_every_run_reads_a_node_ids_identity_from_its_newest_report(
        self, store: ExecutionStore
    ) -> None:
        """One catalogue entry per node id holds the file, class, function
        and parameter a report decomposed it into, and every run's result
        of that node id reads it, so a run's results and section totals are
        the same from either adapter. It follows the newest run: a late
        report of an older run, decomposing the node id some other way,
        changes nothing a newer run reads."""
        node_id = "tests/a.py::T::test_x"
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)

        def _reported(file_path: str, class_name: str | None) -> Result:
            identity = CaseIdentity(
                node_id=node_id,
                file_path=file_path,
                class_name=class_name,
                function_name="test_x",
                param_id=None,
            )
            return replace(_result(node_id), identity=identity)

        runs = [
            (_execution("1" * 32, started=started), _reported("tests/a.py", "T")),
            (_execution("2" * 32, started=started + timedelta(days=1)), _reported("b.py", None)),
            (_execution("3" * 32, started=started - timedelta(days=1)), _reported("c.py", "L")),
        ]
        for execution, result in runs:
            store.record_session(
                execution, results=(result,), received_at=started, project=DEFAULT_PROJECT
            )

        newest = runs[1][1].identity
        for execution, _result_reported in runs:
            run_id = execution.identity.value
            found = store.get_result(run_id, node_id=node_id)
            (listed,) = store.list_results(run_id, limit=10, offset=0).items
            assert found is not None
            assert found.identity == newest
            assert listed.identity == newest
            assert [r.identity for r in store.get_results(run_id)] == [newest]
            assert store.get_run_case_outcomes(run_id) == (("b.py", "passed"),)

    def test_touch_last_contact_is_monotonic_and_reports_unknown_runs(
        self, store: ExecutionStore
    ) -> None:
        """`touch_last_contact` advances on a known run (returns True); an
        earlier-or-equal contact leaves it unchanged (returns False); an
        unknown execution id also returns False -- the route tells the two
        `False` cases apart by calling `get_execution`."""
        identity = "2" + "0" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        start = _start_only_execution(identity, started=started)
        store.record_session(start, results=(), received_at=started, project=DEFAULT_PROJECT)

        first_contact = started + timedelta(seconds=30)
        assert store.touch_last_contact(identity, first_contact) is True

        earlier_contact = first_contact - timedelta(seconds=5)
        assert store.touch_last_contact(identity, earlier_contact) is False

        equal_contact = first_contact
        assert store.touch_last_contact(identity, equal_contact) is False

        later_contact = first_contact + timedelta(seconds=5)
        assert store.touch_last_contact(identity, later_contact) is True

        assert store.touch_last_contact("9" * 32, later_contact) is False

        # A heartbeat touches the contact clock and nothing else. The return
        # values above cannot show that: an UPDATE that also wrote
        # `finished_at` would satisfy all of them.
        after = store.get_execution(identity)
        assert after is not None
        assert after.started_at == started
        assert after.finished_at is None
        assert after.exit_status is None
        assert after.interrupted is False
        assert after.interrupt_reason is None

    def test_second_report_with_null_vcs_section_leaves_recorded_vcs_intact(
        self, store: ExecutionStore
    ) -> None:
        """A start-write carries a vcs snapshot; the finish-write that follows
        carries no `vcs` section at all (`vcs=None`). The conflict branch's
        per-column `COALESCE` (SQL) / field-by-field merge (memory) must leave the
        previously-recorded vcs values untouched -- a report with no vcs data
        is not a report that nulls the vcs it does not carry."""
        identity = "2" + "1" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        start = _start_only_execution(identity, started=started, vcs=_vcs())
        store.record_session(
            start, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        finish = _execution(identity, finished=True, started=started, vcs=None)
        store.record_session(
            finish, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        stored = store.get_execution(identity)
        assert stored is not None
        assert stored.vcs == _vcs()

    def test_a_partial_finish_snapshot_does_not_clobber_a_fuller_start_one(
        self, store: ExecutionStore
    ) -> None:
        """The case that discriminates a per-field merge from a whole-object
        swap: the other vcs tests give both writes the same snapshot or none,
        where the two agree.

        A partial snapshot is what git itself produces: a detached HEAD has a
        null branch, and a repository with no commits has a null commit and
        subject.
        """
        identity = "2" + "6" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        full = _vcs()
        store.record_session(
            _start_only_execution(identity, started=started, vcs=full),
            results=(),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        # A strict subset: only the commit survives, as a detached-HEAD or
        # unborn-branch read would produce.
        partial = _vcs(branch=None, commit_subject=None, dirty=None, root=None)
        store.record_session(
            _execution(identity, finished=True, started=started, vcs=partial),
            results=(),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        stored = store.get_execution(identity)
        assert stored is not None
        assert stored.vcs is not None
        # Null never clobbers a recorded value; the fuller snapshot survives
        # field by field rather than being replaced wholesale.
        assert stored.vcs.branch == full.branch
        assert stored.vcs.commit_subject == full.commit_subject
        assert stored.vcs.dirty == full.dirty
        assert stored.vcs.root == full.root
        assert stored.vcs.commit == full.commit

    @pytest.mark.parametrize(
        ("finish_subject", "expected_subject", "expected_flag"),
        [(None, "A long subject that was cut", True), ("Short", "Short", False)],
        ids=["no-subject-keeps-the-flag", "a-new-subject-brings-its-own"],
    )
    def test_the_truncation_flag_travels_with_the_subject_it_describes(
        self,
        store: ExecutionStore,
        finish_subject: str | None,
        expected_subject: str,
        expected_flag: bool,
    ) -> None:
        """The flag describes a subject, so it is kept or replaced with the
        subject, not by its own null-coalesce."""
        identity = "2" + "7" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        start_vcs = _vcs(
            commit_subject="A long subject that was cut", commit_subject_truncated=True
        )
        store.record_session(
            _start_only_execution(identity, started=started, vcs=start_vcs),
            results=(),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )
        finish_vcs = _vcs(
            commit=None,
            branch=None,
            commit_subject=finish_subject,
            commit_subject_truncated=False,
            dirty=None,
            root=None,
        )
        store.record_session(
            _execution(identity, finished=True, started=started, vcs=finish_vcs),
            results=(),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        stored = store.get_execution(identity)

        assert stored is not None
        assert stored.vcs == replace(
            start_vcs, commit_subject=expected_subject, commit_subject_truncated=expected_flag
        )

    def test_identical_vcs_snapshots_across_start_and_finish_apply_once(
        self, store: ExecutionStore
    ) -> None:
        """Both reports of one session carry the identical vcs snapshot, so the
        upsert is idempotent -- the finish's (identical) snapshot applying over
        the start's own is a no-op, not a divergence."""
        identity = "2" + "2" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        snapshot = _vcs(commit="b" * 40)
        start = _start_only_execution(identity, started=started, vcs=snapshot)
        store.record_session(
            start, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        finish = _execution(identity, finished=True, started=started, vcs=snapshot)
        store.record_session(
            finish, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        stored = store.get_execution(identity)
        assert stored is not None
        assert stored.vcs == snapshot

    def test_reordered_start_after_finish_never_nulls_vcs_columns(
        self, store: ExecutionStore
    ) -> None:
        """A start-write arriving after the finish fails the row-level
        `exit_status` guard and changes nothing -- vcs columns included, same
        as every other field."""
        identity = "2" + "3" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        finish_snapshot = _vcs(commit="c" * 40)
        finish = _execution(identity, finished=True, started=started, vcs=finish_snapshot)
        store.record_session(
            finish, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        late_start = _start_only_execution(identity, started=started, vcs=_vcs(commit="d" * 40))
        store.record_session(
            late_start, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        stored = store.get_execution(identity)
        assert stored == finish
        assert stored is not None
        assert stored.vcs == finish_snapshot

    def test_vcs_none_normalizes_the_same_whether_absent_or_all_null(
        self, store: ExecutionStore
    ) -> None:
        """An absent `vcs` section and a section whose five value fields are
        all null both read back as `execution.vcs is None`, in every adapter --
        independently of the service's `_to_vcs_context` normalisation, which
        never runs here."""
        absent_id = "2" + "4" * 31
        all_null_id = "2" + "5" * 31
        absent = _execution(absent_id, vcs=None)
        all_null = _execution(
            all_null_id,
            vcs=VcsContext(
                commit=None,
                branch=None,
                commit_subject=None,
                commit_subject_truncated=False,
                dirty=None,
                root=None,
            ),
        )
        store.record_session(
            absent, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )
        store.record_session(
            all_null, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        stored_absent = store.get_execution(absent_id)
        stored_all_null = store.get_execution(all_null_id)
        assert stored_absent is not None
        assert stored_absent.vcs is None
        assert stored_all_null is not None
        assert stored_all_null.vcs is None

    # -- list_runs / get_run_detail --

    def test_timestamps_in_another_offset_order_by_the_instant_they_name(
        self, store: ExecutionStore
    ) -> None:
        """The port takes any aware `datetime`. 11:30+02:00 is 09:30 UTC --
        earlier than 10:00 UTC, though its ISO text sorts later -- so the
        10:00 run is the newer one everywhere order matters."""
        node_id = "t.py::test_x"
        newer = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        older = datetime(2026, 9, 1, 11, 30, 0, tzinfo=timezone(timedelta(hours=2)))
        store.record_session(
            _execution("a" * 32, started=newer),
            results=(_result(node_id),),
            received_at=newer,
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            _execution("b" * 32, started=older),
            results=(_result(node_id),),
            received_at=newer,
            project=DEFAULT_PROJECT,
        )

        runs = store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT).items
        history = store.list_history(
            node_id=node_id, limit=10, offset=0, project=DEFAULT_PROJECT
        ).items
        entry = store.get_catalogue_entry(node_id, project=DEFAULT_PROJECT)

        assert [run.execution.identity.value for run in runs] == ["a" * 32, "b" * 32]
        assert [item.run_id for item in history] == ["a" * 32, "b" * 32]
        assert runs[1].execution.started_at == older
        assert entry is not None
        assert entry.last_seen_at == newer
        assert entry.last_seen_run_id == "a" * 32

    def test_list_runs_orders_newest_first_with_total_tiebreak(self, store: ExecutionStore) -> None:
        """Two runs sharing one `started_at`; `id DESC` breaks the tie so the
        order is total, not merely partial -- a page boundary can never fall
        inside the tie group."""
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        store.record_session(
            _execution("a" * 32, started=started),
            results=(),
            received_at=started,
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            _execution("b" * 32, started=started),
            results=(),
            received_at=started,
            project=DEFAULT_PROJECT,
        )

        page = store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT)

        assert [entry.execution.identity.value for entry in page.items] == [
            "b" * 32,
            "a" * 32,
        ]

    def test_list_runs_after_a_key_starts_just_past_it(self, store: ExecutionStore) -> None:
        """A page asked for `after` a run's key holds only the runs past it
        in the newest-first order: a tie on `started_at` is broken by `id`,
        as the order itself breaks it, and a microsecond apart is apart."""
        base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        tied = base + timedelta(microseconds=1)
        for identity, started in (
            ("a" * 32, base),
            ("b" * 32, tied),
            ("c" * 32, tied),
            ("d" * 32, tied + timedelta(microseconds=1)),
        ):
            store.record_session(
                _execution(identity, started=started),
                results=(),
                received_at=base,
                project=DEFAULT_PROJECT,
            )

        page = store.list_runs(
            limit=10,
            offset=0,
            after=RunKey(started_at=tied, run_id="c" * 32),
            project=DEFAULT_PROJECT,
        )

        assert [entry.execution.identity.value for entry in page.items] == ["b" * 32, "a" * 32]
        assert page.has_more is False

    def test_list_runs_after_a_key_is_not_shifted_by_a_run_recorded_since(
        self, store: ExecutionStore
    ) -> None:
        """The page after a key holds the same runs whatever was recorded
        once the key was read. A newer run lands before the key, where an
        offset would have pushed a run already listed onto the next page."""
        base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        for i in range(4):
            store.record_session(
                _execution(f"{i:032x}", started=base + timedelta(seconds=i)),
                results=(),
                received_at=base,
                project=DEFAULT_PROJECT,
            )
        first = store.list_runs(limit=2, offset=0, project=DEFAULT_PROJECT)
        last = first.items[-1].execution
        store.record_session(
            _execution("f" * 32, started=base + timedelta(seconds=10)),
            results=(),
            received_at=base,
            project=DEFAULT_PROJECT,
        )

        after = RunKey(started_at=last.started_at, run_id=last.identity.value)
        second = store.list_runs(limit=2, offset=0, after=after, project=DEFAULT_PROJECT)

        assert [entry.execution.identity.value for entry in second.items] == [
            f"{1:032x}",
            f"{0:032x}",
        ]
        assert second.has_more is False

    def test_the_filtered_run_list_pages_after_a_key_too(self, store: ExecutionStore) -> None:
        """The metadata-filtered run list takes the same key, over the runs
        the filter keeps; the horizon still counts every run."""
        base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        for i in range(4):
            store.record_session(
                _execution(f"{i:032x}", started=base + timedelta(seconds=i)),
                results=(),
                received_at=base,
                metadata=RunMetadata(
                    entries=(
                        MetadataEntry(
                            key="bench",
                            value="lab" if i != 2 else "other",
                            source_file=None,
                            status="captured",
                            source="session",
                            declared=False,
                        ),
                    )
                ),
                project=DEFAULT_PROJECT,
            )

        page, predating = store.list_runs_with_metadata_horizon(
            filters=[("bench", "lab")],
            limit=10,
            offset=0,
            after=RunKey(started_at=base + timedelta(seconds=3), run_id=f"{3:032x}"),
            project=DEFAULT_PROJECT,
        )

        assert [entry.execution.identity.value for entry in page.items] == [
            f"{1:032x}",
            f"{0:032x}",
        ]
        assert predating == (0,)

    def test_list_runs_caps_at_200_items(self, store: ExecutionStore) -> None:
        """A list response never exceeds 200 items, even when the caller asks
        for more."""
        base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        for i in range(MAX_PAGE_ITEMS + 1):
            identity = f"{i:032x}"
            store.record_session(
                _execution(identity, started=base + timedelta(seconds=i)),
                results=(),
                received_at=base,
                project=DEFAULT_PROJECT,
            )

        page = store.list_runs(limit=MAX_PAGE_ITEMS * 5, offset=0, project=DEFAULT_PROJECT)

        assert len(page.items) == MAX_PAGE_ITEMS
        assert page.has_more is True

    def test_list_runs_has_more_distinguishes_exhaustion_from_truncation(
        self, store: ExecutionStore
    ) -> None:
        """The more-items flag distinguishes exhaustion (exactly 200 stored)
        from truncation (201 stored) -- both drawn from the same fetch, never a
        second query."""
        base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        for i in range(MAX_PAGE_ITEMS):
            identity = f"{i:032x}"
            store.record_session(
                _execution(identity, started=base + timedelta(seconds=i)),
                results=(),
                received_at=base,
                project=DEFAULT_PROJECT,
            )

        exhausted = store.list_runs(limit=MAX_PAGE_ITEMS, offset=0, project=DEFAULT_PROJECT)
        assert exhausted.has_more is False

        store.record_session(
            _execution(f"{MAX_PAGE_ITEMS:032x}", started=base + timedelta(seconds=MAX_PAGE_ITEMS)),
            results=(),
            received_at=base,
            project=DEFAULT_PROJECT,
        )

        truncated = store.list_runs(limit=MAX_PAGE_ITEMS, offset=0, project=DEFAULT_PROJECT)
        assert truncated.has_more is True

    def test_list_runs_honors_a_smaller_requested_page_size(self, store: ExecutionStore) -> None:
        """A caller-requested page size under the cap is honored, not silently
        rounded up to 200."""
        base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        for i in range(10):
            identity = f"{i:032x}"
            store.record_session(
                _execution(identity, started=base + timedelta(seconds=i)),
                results=(),
                received_at=base,
                project=DEFAULT_PROJECT,
            )

        page = store.list_runs(limit=3, offset=0, project=DEFAULT_PROJECT)

        assert len(page.items) == 3
        assert page.has_more is True

    def test_list_runs_includes_absent_repository_run_undistinguished(
        self, store: ExecutionStore
    ) -> None:
        """A run recorded outside a repository appears in the run list at its
        ordinary chronological position, with `vcs is None` and no other
        distinction from a run recorded inside one."""
        base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        store.record_session(
            _execution("a" * 32, started=base, vcs=_vcs()),
            results=(),
            received_at=base,
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            _execution("b" * 32, started=base + timedelta(seconds=1), vcs=None),
            results=(),
            received_at=base,
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            _execution("c" * 32, started=base + timedelta(seconds=2), vcs=_vcs()),
            results=(),
            received_at=base,
            project=DEFAULT_PROJECT,
        )

        page = store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT)

        assert [entry.execution.identity.value for entry in page.items] == [
            "c" * 32,
            "b" * 32,
            "a" * 32,
        ]
        by_id = {entry.execution.identity.value: entry for entry in page.items}
        assert by_id["b" * 32].vcs is None

    def test_list_runs_bounds_commit_subject_at_display_width(self, store: ExecutionStore) -> None:
        """The commit subject is bounded in list responses, and the SQL
        projection agrees with `project_vcs`."""
        subject = "x" * 200
        store.record_session(
            _execution(
                "a" * 32,
                vcs=_vcs(commit_subject=subject, commit_subject_truncated=False),
            ),
            results=(),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        page = store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT)

        entry = page.items[0]
        assert entry.vcs is not None
        assert entry.vcs.commit_subject == subject[:120]
        assert entry.vcs.commit_subject_truncated is True

    def test_list_runs_flags_capture_truncated_subject_even_when_short(
        self, store: ExecutionStore
    ) -> None:
        """A subject already truncated at capture keeps its flag in the list
        even when it is shorter than the display width."""
        store.record_session(
            _execution(
                "a" * 32,
                vcs=_vcs(commit_subject="short", commit_subject_truncated=True),
            ),
            results=(),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        page = store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT)

        entry = page.items[0]
        assert entry.vcs is not None
        assert entry.vcs.commit_subject == "short"
        assert entry.vcs.commit_subject_truncated is True

    def test_list_views_bound_text_holding_control_characters_like_any_other_text(
        self, store: ExecutionStore
    ) -> None:
        """A failure message can hold control characters -- a test of a
        binary protocol that puts raw bytes in its exception -- and a
        hand-written report can put one in a commit subject. The list must
        show the display width and flag the rest, not a prefix cut at a
        control character that claims to be the whole value. (U+0000 never
        reaches a store; the service replaces it.)"""
        message = "ValueError: bad frame header \x01\x02\x7f" + "x" * 300
        subject = "Fix\x1b" + "y" * 200
        vcs = _vcs(commit_subject=subject, commit_subject_truncated=False)
        failure = _failure(failure_message=message)
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        store.record_session(
            _execution("a" * 32, started=started, vcs=vcs),
            results=(_result("t.py::test_x", outcome="failed", failure=failure),),
            received_at=started,
            project=DEFAULT_PROJECT,
        )

        (result_entry,) = store.list_results("a" * 32, limit=10, offset=0).items
        (run_entry,) = store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT).items
        (history_entry,) = store.list_history(
            node_id="t.py::test_x", limit=10, offset=0, project=DEFAULT_PROJECT
        ).items

        assert result_entry.failure == project_failure(failure)
        assert result_entry.failure is not None
        assert result_entry.failure.failure_message == message[:LIST_FAILURE_MESSAGE_CHARS]
        assert result_entry.failure.failure_message_truncated is True
        assert run_entry.vcs == project_vcs(vcs)
        assert run_entry.vcs is not None
        assert run_entry.vcs.commit_subject == subject[:LIST_COMMIT_SUBJECT_CHARS]
        assert run_entry.vcs.commit_subject_truncated is True
        assert history_entry.vcs == run_entry.vcs

    def test_list_views_keep_empty_text_empty(self, store: ExecutionStore) -> None:
        """The plugin sends an empty commit subject for a commit whose
        message is empty, and any client can send an empty failure message.
        A list entry carries a value exactly when the full record does, so
        `""` lists as `""` -- and a result whose only evidence is that empty
        message still lists with a failure object."""
        vcs = _vcs(commit_subject="", commit_subject_truncated=False)
        failure = _failure(
            failure_type=None,
            failure_message="",
            failure_path=None,
            failure_lineno=None,
            failure_repr=None,
            traceback=None,
        )
        store.record_session(
            _execution("a" * 32, vcs=vcs),
            results=(_result("t.py::test_x", outcome="failed", failure=failure),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        (result_entry,) = store.list_results("a" * 32, limit=10, offset=0).items
        (run_entry,) = store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT).items
        (history_entry,) = store.list_history(
            node_id="t.py::test_x", limit=10, offset=0, project=DEFAULT_PROJECT
        ).items
        detail = store.get_run_detail("a" * 32)

        assert result_entry.failure == project_failure(failure)
        assert result_entry.failure is not None
        assert result_entry.failure.failure_message == ""
        assert run_entry.vcs == project_vcs(vcs)
        assert run_entry.vcs is not None
        assert run_entry.vcs.commit_subject == ""
        assert history_entry.vcs == run_entry.vcs
        assert detail is not None
        assert detail.execution.vcs is not None
        assert detail.execution.vcs.commit_subject == ""

    def test_list_views_bound_multibyte_text_by_characters(self, store: ExecutionStore) -> None:
        """The display width counts characters, whatever their UTF-8 length."""
        message = "é" * 250
        subject = "🙂" * 150
        vcs = _vcs(commit_subject=subject, commit_subject_truncated=False)
        failure = _failure(failure_message=message)
        store.record_session(
            _execution("a" * 32, vcs=vcs),
            results=(_result("t.py::test_x", outcome="failed", failure=failure),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        (result_entry,) = store.list_results("a" * 32, limit=10, offset=0).items
        (run_entry,) = store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT).items

        assert result_entry.failure == project_failure(failure)
        assert run_entry.vcs == project_vcs(vcs)

    def test_list_runs_null_subject_flag_is_false_not_null(self, store: ExecutionStore) -> None:
        """A run with `vcs_commit_subject IS NULL` reports
        `commit_subject_truncated is False`, never `None`."""
        store.record_session(
            _execution(
                "a" * 32,
                vcs=_vcs(commit_subject=None, commit_subject_truncated=False),
            ),
            results=(),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        page = store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT)

        entry = page.items[0]
        assert entry.vcs is not None
        assert entry.vcs.commit_subject is None
        assert entry.vcs.commit_subject_truncated is False

    def test_get_run_detail_returns_full_untruncated_subject(self, store: ExecutionStore) -> None:
        """`get_run_detail` is the lean list's complement -- the full record
        stays reachable at the whole stored subject, with
        `commit_subject_truncated` reflecting only capture-time truncation."""
        subject = "y" * 200
        execution = _execution(
            "a" * 32, vcs=_vcs(commit_subject=subject, commit_subject_truncated=False)
        )
        store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        detail = store.get_run_detail("a" * 32)

        assert detail is not None
        assert detail.execution.vcs is not None
        assert detail.execution.vcs.commit_subject == subject
        assert detail.execution.vcs.commit_subject_truncated is False

    def test_get_run_detail_returns_none_for_unknown_id(self, store: ExecutionStore) -> None:
        assert store.get_run_detail("f" * 32) is None

    # -- list_results / list_history --

    def test_list_history_orders_newest_first_with_full_vcs(self, store: ExecutionStore) -> None:
        """Executions return newest first, and every entry carries its full VCS
        context -- commit, branch, commit subject, truncation flag, dirty flag
        -- and its duration."""
        node_id = "t.py::test_recurring"
        older = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        newer = older + timedelta(hours=1)
        store.record_session(
            _execution("a" * 32, started=older, vcs=_vcs(branch="feature", dirty=True)),
            results=(_result(node_id, duration=0.5),),
            received_at=older,
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            _execution("b" * 32, started=newer, vcs=_vcs(branch="main", dirty=False)),
            results=(_result(node_id, duration=0.75),),
            received_at=newer,
            project=DEFAULT_PROJECT,
        )

        page = store.list_history(node_id=node_id, limit=10, offset=0, project=DEFAULT_PROJECT)

        assert [entry.run_id for entry in page.items] == ["b" * 32, "a" * 32]
        newest, oldest = page.items
        assert newest.vcs is not None
        assert newest.vcs.commit == _vcs().commit
        assert newest.vcs.branch == "main"
        assert newest.vcs.commit_subject == _vcs().commit_subject
        assert newest.vcs.commit_subject_truncated is True
        assert newest.vcs.dirty is False
        assert newest.duration == 0.75
        assert oldest.vcs is not None
        assert oldest.vcs.branch == "feature"
        assert oldest.vcs.dirty is True
        assert oldest.duration == 0.5

    def test_list_history_after_a_key_starts_just_past_it(self, store: ExecutionStore) -> None:
        """A test's history pages after a run's key the way the run list
        does, in the same newest-first order."""
        node_id = "t.py::test_recurring"
        base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            store.record_session(
                _execution(f"{i:032x}", started=base + timedelta(seconds=i)),
                results=(_result(node_id),),
                received_at=base,
                project=DEFAULT_PROJECT,
            )

        page = store.list_history(
            node_id=node_id,
            limit=10,
            offset=0,
            after=RunKey(started_at=base + timedelta(seconds=2), run_id=f"{2:032x}"),
            project=DEFAULT_PROJECT,
        )

        assert [entry.run_id for entry in page.items] == [f"{1:032x}", f"{0:032x}"]
        assert page.has_more is False

    def test_list_history_unknown_node_id_is_empty_not_error(self, store: ExecutionStore) -> None:
        """An unknown test identity yields an empty history, not an error."""
        page = store.list_history(
            node_id="t.py::test_never_ran", limit=10, offset=0, project=DEFAULT_PROJECT
        )

        assert page.items == ()
        assert page.has_more is False

    def test_list_history_null_vcs_entry_present_not_omitted(self, store: ExecutionStore) -> None:
        """An execution recorded outside a git repository is present in the
        history with a null VCS context, not omitted."""
        node_id = "t.py::test_no_repo"
        store.record_session(
            _execution("a" * 32, vcs=None),
            results=(_result(node_id),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        page = store.list_history(node_id=node_id, limit=10, offset=0, project=DEFAULT_PROJECT)

        assert len(page.items) == 1
        assert page.items[0].vcs is None

    def test_list_history_caps_and_reports_more_like_list_runs(self, store: ExecutionStore) -> None:
        """History pagination has the same 200/201 clamp and `has_more`
        transition as `list_runs`."""
        node_id = "t.py::test_hot_path"
        base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        for i in range(MAX_PAGE_ITEMS):
            identity = f"{i:032x}"
            store.record_session(
                _execution(identity, started=base + timedelta(seconds=i)),
                results=(_result(node_id),),
                received_at=base,
                project=DEFAULT_PROJECT,
            )

        exhausted = store.list_history(
            node_id=node_id, limit=MAX_PAGE_ITEMS, offset=0, project=DEFAULT_PROJECT
        )
        assert len(exhausted.items) == MAX_PAGE_ITEMS
        assert exhausted.has_more is False

        store.record_session(
            _execution(f"{MAX_PAGE_ITEMS:032x}", started=base + timedelta(seconds=MAX_PAGE_ITEMS)),
            results=(_result(node_id),),
            received_at=base,
            project=DEFAULT_PROJECT,
        )

        truncated = store.list_history(
            node_id=node_id, limit=MAX_PAGE_ITEMS, offset=0, project=DEFAULT_PROJECT
        )
        assert len(truncated.items) == MAX_PAGE_ITEMS
        assert truncated.has_more is True

    def test_list_results_paginates_a_runs_results(self, store: ExecutionStore) -> None:
        """`list_results` is the paginated sibling of `get_results` -- respects
        `limit`/`offset`/`has_more` over one run's results."""
        execution = _execution("a" * 32)
        results = tuple(_result(f"t.py::test_{i}") for i in range(5))
        store.record_session(
            execution,
            results=results,
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        page = store.list_results(execution.identity.value, limit=3, offset=0)
        assert len(page.items) == 3
        assert page.has_more is True

        next_page = store.list_results(execution.identity.value, limit=3, offset=3)
        assert len(next_page.items) == 2
        assert next_page.has_more is False

    def test_list_results_narrowed_to_outcomes_keeps_order_and_pages_over_them(
        self, store: ExecutionStore
    ) -> None:
        """The outcomes narrow the set before it is paged: `has_more` and
        `offset` count the matching results alone, which keep their stored
        order."""
        execution = _execution("a" * 32)
        outcomes = ("failed", "passed", "error", "passed", "xpassed", "skipped", "failed")
        store.record_session(
            execution,
            results=tuple(
                _result(f"t.py::test_{letter}", outcome=outcome)
                for letter, outcome in zip("gfedcba", outcomes)
            ),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )
        run_id = execution.identity.value
        not_passing = {"failed", "error", "xpassed"}

        first = store.list_results(run_id, limit=3, offset=0, outcomes=not_passing)
        rest = store.list_results(run_id, limit=3, offset=3, outcomes=not_passing)

        assert [entry.identity.node_id for entry in first.items] == [
            "t.py::test_g",
            "t.py::test_e",
            "t.py::test_c",
        ]
        assert first.has_more is True
        assert [(entry.identity.node_id, entry.outcome) for entry in rest.items] == [
            ("t.py::test_a", "failed")
        ]
        assert rest.has_more is False

    def test_list_results_narrowed_to_no_outcome_it_holds_is_empty(
        self, store: ExecutionStore
    ) -> None:
        """An empty collection keeps nothing, as does a word no result can
        hold; only `None` leaves the page unfiltered."""
        execution = _execution("a" * 32)
        store.record_session(
            execution,
            results=(_result("t.py::test_a"), _result("t.py::test_b", outcome="failed")),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )
        run_id = execution.identity.value

        for outcomes in ((), ("xfailed",), ("flaky",)):
            page = store.list_results(run_id, limit=10, offset=0, outcomes=outcomes)
            assert (page.items, page.has_more) == ((), False), outcomes
        assert len(store.list_results(run_id, limit=10, offset=0, outcomes=None).items) == 2

    def test_list_results_empty_for_a_run_with_no_results(self, store: ExecutionStore) -> None:
        execution = _execution("a" * 32)
        store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        page = store.list_results(execution.identity.value, limit=10, offset=0)

        assert page.items == ()
        assert page.has_more is False

    # -- list_results / get_result failure evidence --

    def test_list_results_projects_failure_evidence_via_failure_projection(
        self, store: ExecutionStore
    ) -> None:
        """A stored result with a >200-character message: the `list_results`
        entry's `failure` agrees with `project_failure`'s bounding and
        truncation-flag rule."""
        long_message = "E" * (LIST_FAILURE_MESSAGE_CHARS + 51)
        failure = _failure(failure_message=long_message, failure_message_truncated=False)
        execution = _execution("a" * 32)
        store.record_session(
            execution,
            results=(_result("t.py::test_failing", outcome="failed", failure=failure),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        page = store.list_results(execution.identity.value, limit=10, offset=0)

        entry = page.items[0]
        assert entry.failure == project_failure(failure)
        assert entry.failure is not None
        assert entry.failure.failure_message == long_message[:LIST_FAILURE_MESSAGE_CHARS]
        assert entry.failure.failure_message_truncated is True

    @pytest.mark.parametrize(
        "failure",
        [
            _failure(
                failure_type=None, failure_message=None, failure_path=None, failure_lineno=None
            ),
            _failure(
                failure_type=None,
                failure_message=None,
                failure_path=None,
                failure_lineno=None,
                failure_repr=None,
                traceback=None,
                skip_reason_truncated=True,
            ),
        ],
        ids=["repr-and-traceback-only", "dropped-skip-reason-only"],
    )
    def test_a_list_entry_carries_failure_data_exactly_when_the_full_record_does(
        self, store: ExecutionStore, failure: FailureEvidence
    ) -> None:
        """Evidence whose only content is outside the lean projection still
        lists as a failure object, so a client can tell from the list that
        the detail has something to show."""
        execution = _execution("a" * 32)
        store.record_session(
            execution,
            results=(_result("t.py::test_x", outcome="failed", failure=failure),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        (entry,) = store.list_results(execution.identity.value, limit=10, offset=0).items
        found = store.get_result(execution.identity.value, node_id="t.py::test_x")

        assert entry.failure is not None
        assert entry.failure == project_failure(failure)
        assert found is not None
        assert found.failure == failure

    def test_all_null_failure_evidence_reads_back_as_none_everywhere(
        self, store: ExecutionStore
    ) -> None:
        """Evidence with every field null or false is no evidence: every read
        path returns `failure is None`, however the caller spelled it."""
        empty = _failure(
            failure_type=None,
            failure_message=None,
            failure_path=None,
            failure_lineno=None,
            failure_repr=None,
            traceback=None,
        )
        execution = _execution("a" * 32)
        store.record_session(
            execution,
            results=(_result("t.py::test_x", failure=empty),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        found = store.get_result(execution.identity.value, node_id="t.py::test_x")
        (listed,) = store.get_results(execution.identity.value)
        (entry,) = store.list_results(execution.identity.value, limit=10, offset=0).items

        assert found is not None
        assert found.failure is None
        assert listed.failure is None
        assert entry.failure is None

    def test_a_run_whose_only_vcs_field_is_its_root_lists_with_an_empty_projection(
        self, store: ExecutionStore
    ) -> None:
        """A repository whose every git read but the root failed is still a
        repository: the list says so with an all-null projection rather than
        the `None` of a run recorded outside one, as the detail does."""
        root_only = _vcs(commit=None, branch=None, commit_subject=None, dirty=None)
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        store.record_session(
            _execution("a" * 32, started=started, vcs=root_only),
            results=(_result("t.py::test_x"),),
            received_at=started,
            project=DEFAULT_PROJECT,
        )

        (run_entry,) = store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT).items
        (history_entry,) = store.list_history(
            node_id="t.py::test_x", limit=10, offset=0, project=DEFAULT_PROJECT
        ).items
        detail = store.get_run_detail("a" * 32)

        assert detail is not None
        assert detail.execution.vcs == root_only
        assert run_entry.vcs == project_vcs(root_only)
        assert run_entry.vcs is not None
        assert history_entry.vcs == run_entry.vcs

    def test_get_result_returns_the_full_record_hit(self, store: ExecutionStore) -> None:
        """`get_result` returns the whole stored `Result`, `failure` and
        `captured` populated in full, unbounded."""
        execution = _execution("a" * 32)
        failure = _failure()
        captured = _captured(stdout="captured output", stderr="")
        store.record_session(
            execution,
            results=(
                _result("t.py::test_x", outcome="failed", failure=failure, captured=captured),
            ),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        found = store.get_result(execution.identity.value, node_id="t.py::test_x")

        assert found is not None
        assert found.identity.node_id == "t.py::test_x"
        assert found.failure == failure
        assert found.captured == captured

    def test_get_result_returns_none_for_unknown_node_id_miss(self, store: ExecutionStore) -> None:
        execution = _execution("a" * 32)
        store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        assert store.get_result(execution.identity.value, node_id="t.py::test_never_ran") is None

    def test_get_result_truncation_flag_travels_with_the_field(self, store: ExecutionStore) -> None:
        """A bounded field's truncation flag travels with it: `get_result`'s
        `traceback_truncated` is exactly what was stored, never dropped or
        defaulted."""
        execution = _execution("a" * 32)
        failure = _failure(traceback="a truncated traceback", traceback_truncated=True)
        store.record_session(
            execution,
            results=(_result("t.py::test_x", outcome="failed", failure=failure),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        found = store.get_result(execution.identity.value, node_id="t.py::test_x")

        assert found is not None
        assert found.failure is not None
        assert found.failure.traceback == "a truncated traceback"
        assert found.failure.traceback_truncated is True

    def test_a_result_without_evidence_reads_back_with_no_failure_and_no_output(
        self, store: ExecutionStore
    ) -> None:
        """A result stored with no failure evidence and nothing captured reads
        back as `failure is None` and an all-`None` `CapturedOutput` from both
        read paths -- never as a record whose fields all happen to be null."""
        execution = _execution("a" * 32)
        store.record_session(
            execution,
            results=(_result("t.py::test_x"),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        found = store.get_result(execution.identity.value, node_id="t.py::test_x")
        (listed,) = store.get_results(execution.identity.value)

        for result in (found, listed):
            assert result is not None
            assert result.failure is None
            assert result.captured == _captured()

    def test_captured_output_empty_versus_absent_round_trips_through_storage(
        self, store: ExecutionStore
    ) -> None:
        """`""` (captured, empty) and `None` (never captured) round-trip
        distinctly through storage, both adapters -- collapsing `""` to `None`
        (a truthy check on the string) would make the two indistinguishable."""
        execution = _execution("a" * 32)
        empty_captured = _captured(stdout="", stderr=None)
        store.record_session(
            execution,
            results=(_result("t.py::test_x", captured=empty_captured),),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        found = store.get_result(execution.identity.value, node_id="t.py::test_x")

        assert found is not None
        assert found.captured.stdout == ""
        assert found.captured.stdout_truncated is False
        assert found.captured.stderr is None

    # -- settings: namespaced persistence, create/replace/delete --

    def test_list_settings_is_empty_for_an_unknown_namespace(self, store: ExecutionStore) -> None:
        assert store.list_settings("test_sections", project=DEFAULT_PROJECT) == ()

    def test_upsert_setting_creates_a_new_pair(self, store: ExecutionStore) -> None:
        now = datetime.now(timezone.utc)

        created = store.upsert_setting(
            "test_sections",
            "Billing",
            value='{"prefix": "tests/billing/"}',
            updated_at=now,
            project=DEFAULT_PROJECT,
        )

        assert created is True
        settings = store.list_settings("test_sections", project=DEFAULT_PROJECT)
        assert len(settings) == 1
        assert settings[0] == ProjectSetting(
            project=DEFAULT_PROJECT,
            namespace="test_sections",
            key="Billing",
            value='{"prefix": "tests/billing/"}',
            updated_at=now,
        )

    def test_upsert_setting_on_an_existing_pair_replaces_not_duplicates(
        self, store: ExecutionStore
    ) -> None:
        first = datetime.now(timezone.utc)
        second = first + timedelta(seconds=1)
        store.upsert_setting(
            "test_sections", "Billing", value="a", updated_at=first, project=DEFAULT_PROJECT
        )

        replaced = store.upsert_setting(
            "test_sections", "Billing", value="b", updated_at=second, project=DEFAULT_PROJECT
        )

        assert replaced is False
        settings = store.list_settings("test_sections", project=DEFAULT_PROJECT)
        assert len(settings) == 1
        assert settings[0].value == "b"

    def test_delete_setting_then_a_later_read_reports_it_absent(
        self, store: ExecutionStore
    ) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting(
            "test_sections", "Billing", value="a", updated_at=now, project=DEFAULT_PROJECT
        )

        deleted = store.delete_setting("test_sections", "Billing", project=DEFAULT_PROJECT)

        assert deleted is True
        assert store.list_settings("test_sections", project=DEFAULT_PROJECT) == ()

    def test_delete_setting_for_an_absent_key_reports_false(self, store: ExecutionStore) -> None:
        assert (
            store.delete_setting("test_sections", "never-stored", project=DEFAULT_PROJECT) is False
        )

    def test_list_settings_orders_by_key(self, store: ExecutionStore) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting(
            "test_sections", "Checkout", value="b", updated_at=now, project=DEFAULT_PROJECT
        )
        store.upsert_setting(
            "test_sections", "Billing", value="a", updated_at=now, project=DEFAULT_PROJECT
        )

        settings = store.list_settings("test_sections", project=DEFAULT_PROJECT)

        assert [setting.key for setting in settings] == ["Billing", "Checkout"]

    def test_list_settings_only_returns_its_own_namespace(self, store: ExecutionStore) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting(
            "test_sections", "Billing", value="a", updated_at=now, project=DEFAULT_PROJECT
        )
        store.upsert_setting(
            "other_namespace", "Billing", value="b", updated_at=now, project=DEFAULT_PROJECT
        )

        settings = store.list_settings("test_sections", project=DEFAULT_PROJECT)

        assert len(settings) == 1
        assert settings[0].value == "a"

    def test_upsert_setting_refuses_a_new_key_at_max_keys_and_writes_nothing(
        self, store: ExecutionStore
    ) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting(
            "test_sections", "Billing", value="a", updated_at=now, project=DEFAULT_PROJECT
        )
        store.upsert_setting(
            "test_sections", "Checkout", value="b", updated_at=now, project=DEFAULT_PROJECT
        )

        with pytest.raises(NamespaceFullError):
            store.upsert_setting(
                "test_sections",
                "Accounts",
                value="c",
                updated_at=now,
                max_keys=2,
                project=DEFAULT_PROJECT,
            )

        assert [
            setting.key for setting in store.list_settings("test_sections", project=DEFAULT_PROJECT)
        ] == [
            "Billing",
            "Checkout",
        ]

    def test_upsert_setting_replaces_an_existing_key_at_max_keys(
        self, store: ExecutionStore
    ) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting(
            "test_sections", "Billing", value="a", updated_at=now, project=DEFAULT_PROJECT
        )

        created = store.upsert_setting(
            "test_sections",
            "Billing",
            value="b",
            updated_at=now,
            max_keys=1,
            project=DEFAULT_PROJECT,
        )

        assert created is False
        assert [
            setting.value
            for setting in store.list_settings("test_sections", project=DEFAULT_PROJECT)
        ] == ["b"]

    def test_max_keys_counts_only_the_keys_of_its_own_namespace(
        self, store: ExecutionStore
    ) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting(
            "other_namespace", "Billing", value="a", updated_at=now, project=DEFAULT_PROJECT
        )

        created = store.upsert_setting(
            "test_sections",
            "Billing",
            value="b",
            updated_at=now,
            max_keys=1,
            project=DEFAULT_PROJECT,
        )

        assert created is True

    def test_get_run_case_outcomes_is_empty_for_a_run_with_no_results(
        self, store: ExecutionStore
    ) -> None:
        execution = _execution("a" * 32)
        store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        assert store.get_run_case_outcomes(execution.identity.value) == ()

    def test_get_run_case_outcomes_pairs_file_path_with_outcome(
        self, store: ExecutionStore
    ) -> None:
        execution = _execution("b" * 32)
        results = (
            _result("t.py::test_a", outcome="passed"),
            _result("t.py::test_b", outcome="failed"),
        )
        store.record_session(
            execution,
            results=results,
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        outcomes = store.get_run_case_outcomes(execution.identity.value)

        assert sorted(outcomes) == sorted([("t.py", "passed"), ("t.py", "failed")])

    def test_get_run_case_outcomes_come_in_the_order_the_results_were_stored(
        self, store: ExecutionStore
    ) -> None:
        """Stored order is the order the plugin reported them, which a
        client draws the run in -- neither node-id nor file order, which an
        index on the run's results could otherwise hand back."""
        execution = _execution("c" * 32)
        results = (
            _result("z.py::test_z", outcome="failed"),
            _result("a.py::test_b", outcome="passed"),
            _result("m.py::test_a", outcome="skipped"),
            _result("a.py::test_a", outcome="error"),
        )
        store.record_session(
            execution,
            results=results,
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        assert store.get_run_case_outcomes(execution.identity.value) == (
            ("z.py", "failed"),
            ("a.py", "passed"),
            ("m.py", "skipped"),
            ("a.py", "error"),
        )

    # -- count_outcomes --

    def test_count_outcomes_of_no_ids_is_empty(self, store: ExecutionStore) -> None:
        assert store.count_outcomes([]) == {}

    def test_count_outcomes_leaves_out_an_unknown_id_and_a_run_without_results(
        self, store: ExecutionStore
    ) -> None:
        execution = _execution("a" * 32)
        store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        assert store.count_outcomes([execution.identity.value, "f" * 32]) == {}

    def test_count_outcomes_counts_each_outcome_of_one_run(self, store: ExecutionStore) -> None:
        execution = _execution("a" * 32)
        outcomes = ("passed", "failed", "passed", "xfailed", "passed", "error")
        store.record_session(
            execution,
            results=tuple(
                _result(f"t.py::test_{i}", outcome=outcome) for i, outcome in enumerate(outcomes)
            ),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )

        assert store.count_outcomes([execution.identity.value]) == {
            execution.identity.value: {"passed": 3, "failed": 1, "xfailed": 1, "error": 1}
        }

    def test_count_outcomes_takes_more_ids_than_one_statement_binds(
        self, store: ExecutionStore
    ) -> None:
        """A thousand unknown ids between two runs, the first and the last
        asked for: both are counted, wherever the ids are split."""
        first, last = _execution("a" * 32), _execution("b" * 32)
        for execution in (first, last):
            store.record_session(
                execution,
                results=(_result("t.py::test_a"),),
                received_at=datetime.now(timezone.utc),
                project=DEFAULT_PROJECT,
            )
        unknown = [f"{i:032x}" for i in range(1, 1001)]

        counts = store.count_outcomes([first.identity.value, *unknown, last.identity.value])

        assert counts == {
            first.identity.value: {"passed": 1},
            last.identity.value: {"passed": 1},
        }

    def test_count_outcomes_keeps_two_runs_apart(self, store: ExecutionStore) -> None:
        """Two runs of the same tests, in two projects, sharing an outcome,
        asked for together and with one id repeated: each has only its own
        results."""
        store.create_project("firmware", created_at=datetime.now(timezone.utc))
        first, second = _execution("a" * 32), _execution("b" * 32)
        store.record_session(
            first,
            results=(_result("t.py::test_a"), _result("t.py::test_b", outcome="failed")),
            received_at=datetime.now(timezone.utc),
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            second,
            results=(_result("t.py::test_a", outcome="skipped"), _result("t.py::test_b")),
            received_at=datetime.now(timezone.utc),
            project="firmware",
        )

        counts = store.count_outcomes(
            [second.identity.value, first.identity.value, second.identity.value]
        )

        assert counts == {
            first.identity.value: {"passed": 1, "failed": 1},
            second.identity.value: {"skipped": 1, "passed": 1},
        }

    def test_recording_metadata_persists_both_tables(
        self, store: ExecutionStore, stored_metadata: StoredMetadata
    ) -> None:
        """A metadata-carrying report writes one `run_metadata_file` row and
        one `run_metadata` row."""
        execution = _execution("1" * 32)
        metadata = RunMetadata(
            files=(
                MetadataFile(
                    source_file="config/firmware.yaml", content_type="yaml", status="captured"
                ),
            ),
            entries=(
                MetadataEntry(
                    key="firmware_version",
                    value="2.1",
                    source_file="config/firmware.yaml",
                    status="captured",
                ),
            ),
        )

        store.record_session(
            execution,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=metadata,
            project=DEFAULT_PROJECT,
        )

        stored = stored_metadata(execution.identity.value)
        assert set(stored.files) == set(metadata.files)
        assert set(stored.entries) == set(metadata.entries)

    def test_a_declared_but_dropped_file_and_key_still_record_a_row(
        self, store: ExecutionStore, stored_metadata: StoredMetadata
    ) -> None:
        """Absence is a row, not a missing row -- a file dropped for being too
        large and its key's `source_unavailable` entry both persist, with the
        entry's `value` NULL."""
        execution = _execution("2" * 32)
        metadata = RunMetadata(
            files=(
                MetadataFile(
                    source_file="build/manifest.json", content_type="json", status="too_large"
                ),
            ),
            entries=(
                MetadataEntry(
                    key="toolchain",
                    value=None,
                    source_file="build/manifest.json",
                    status="source_unavailable",
                ),
            ),
        )

        store.record_session(
            execution,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=metadata,
            project=DEFAULT_PROJECT,
        )

        stored = stored_metadata(execution.identity.value)
        assert set(stored.files) == set(metadata.files)
        assert set(stored.entries) == set(metadata.entries)
        assert stored.entries[0].value is None

    def test_replaying_metadata_with_a_different_value_does_not_backfill(
        self, store: ExecutionStore, stored_metadata: StoredMetadata
    ) -> None:
        """A stored value never changes after ingestion: a second
        `record_session` call for the same run and key with a different value
        leaves the first value on read."""
        execution = _execution("8" * 32)
        first = RunMetadata(
            files=(MetadataFile(source_file="a.yaml", content_type="yaml", status="captured"),),
            entries=(MetadataEntry(key="k", value="v1", source_file="a.yaml", status="captured"),),
        )
        second = RunMetadata(
            files=(MetadataFile(source_file="a.yaml", content_type="yaml", status="captured"),),
            entries=(MetadataEntry(key="k", value="v2", source_file="a.yaml", status="captured"),),
        )
        store.record_session(
            execution,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=first,
            project=DEFAULT_PROJECT,
        )

        store.record_session(
            execution,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=second,
            project=DEFAULT_PROJECT,
        )

        stored = set(stored_metadata(execution.identity.value).entries)
        assert stored == set(first.entries)
        assert stored != set(second.entries)

    def test_a_finish_only_session_records_the_same_rows_a_start_finish_pair_would(
        self, store: ExecutionStore, stored_metadata: StoredMetadata
    ) -> None:
        """Metadata is frozen once and sent unchanged on both writes. A
        start-write followed by a finish-write, both carrying the identical
        `RunMetadata`, leaves the same stored rows a single finish-only report
        would."""
        metadata = RunMetadata(
            files=(MetadataFile(source_file="a.yaml", content_type="yaml", status="captured"),),
            entries=(MetadataEntry(key="k", value="v", source_file="a.yaml", status="captured"),),
        )
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)

        pair_identity = "6" * 32
        start = _start_only_execution(pair_identity, started=started)
        store.record_session(
            start,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=metadata,
            project=DEFAULT_PROJECT,
        )
        finish = _execution(pair_identity, started=started)
        store.record_session(
            finish,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=metadata,
            project=DEFAULT_PROJECT,
        )

        finish_only_identity = "7" * 32
        finish_only = _execution(finish_only_identity, started=started)
        store.record_session(
            finish_only,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=metadata,
            project=DEFAULT_PROJECT,
        )

        pair = stored_metadata(pair_identity)
        single = stored_metadata(finish_only_identity)
        assert set(pair.files) == set(single.files) == set(metadata.files)
        assert set(pair.entries) == set(single.entries) == set(metadata.entries)

    def test_every_kind_of_key_round_trips_with_its_source_name_and_declaration(
        self, store: ExecutionStore, stored_metadata: StoredMetadata
    ) -> None:
        """A file key and the keys a session reported itself -- declared or
        not, with and without a value -- are stored side by side, each with
        where it came from, its display name and whether it was declared."""
        execution = _execution("3" * 32)
        metadata = RunMetadata(
            files=(MetadataFile(source_file="board.json", content_type="json", status="captured"),),
            entries=(
                MetadataEntry(
                    key="board.revision",
                    value="rev-b",
                    source_file="board.json",
                    status="captured",
                    name="Board revision",
                ),
                MetadataEntry(
                    key="fpga.firmware",
                    value="1.1.0",
                    source_file=None,
                    status="captured",
                    source="session",
                    name="FPGA firmware version",
                    declared=True,
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
                    key="fmc.hardware",
                    value=None,
                    source_file=None,
                    status="absent",
                    source="session",
                    name="FMC hardware version",
                    declared=True,
                ),
                MetadataEntry(
                    key="fpga.dna",
                    value=None,
                    source_file=None,
                    status="value_too_large",
                    source="session",
                    declared=False,
                ),
            ),
        )

        store.record_session(
            execution,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=metadata,
            project=DEFAULT_PROJECT,
        )

        stored = stored_metadata(execution.identity.value)
        assert set(stored.files) == set(metadata.files)
        assert set(stored.entries) == set(metadata.entries)

    def test_a_key_recorded_from_a_file_keeps_its_value_over_a_later_session_value(
        self, store: ExecutionStore, stored_metadata: StoredMetadata
    ) -> None:
        """A file's keys arrive with the start report and the session's own
        values only with the finish report; the first row for a key is the
        one kept, whichever source the later one comes from."""
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        identity = "4" * 32
        from_file = MetadataEntry(
            key="fpga.firmware", value="1.0.0", source_file="fw.json", status="captured"
        )
        store.record_session(
            _start_only_execution(identity, started=started),
            results=(),
            received_at=started,
            metadata=RunMetadata(
                files=(
                    MetadataFile(source_file="fw.json", content_type="json", status="captured"),
                ),
                entries=(from_file,),
            ),
            project=DEFAULT_PROJECT,
        )

        store.record_session(
            _execution(identity, started=started),
            results=(),
            received_at=started,
            metadata=RunMetadata(
                entries=(
                    MetadataEntry(
                        key="fpga.firmware",
                        value="1.1.0",
                        source_file=None,
                        status="captured",
                        source="session",
                        declared=False,
                    ),
                ),
            ),
            project=DEFAULT_PROJECT,
        )

        assert stored_metadata(identity).entries == (from_file,)

    def test_a_run_holds_at_most_the_entry_bound_of_keys_across_its_reports(
        self, store: ExecutionStore
    ) -> None:
        """Each report is bounded on its way in, but a run may be sent any
        number of them. The store keeps the first keys up to the bound and
        drops every new one after it; a key the run already holds takes no
        second place."""
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        identity = "9" * 32

        def _values(*keys: str, value: str = "v") -> RunMetadata:
            return RunMetadata(
                entries=tuple(
                    MetadataEntry(
                        key=key,
                        value=value,
                        source_file=None,
                        status="captured",
                        source="session",
                        declared=False,
                    )
                    for key in keys
                )
            )

        first = [f"a{index:03d}" for index in range(MAX_METADATA_ENTRIES - 1)]
        store.record_session(
            _start_only_execution(identity, started=started),
            results=(),
            received_at=started,
            metadata=_values(*first),
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            _execution(identity, started=started),
            results=(),
            received_at=started,
            metadata=_values(first[0], "b000", "b001", value="later"),
            project=DEFAULT_PROJECT,
        )

        stored = store.get_run_metadata(identity)
        assert stored is not None
        assert [(entry.key, entry.value) for entry in stored.entries] == [
            *((key, "v") for key in first),
            ("b000", "later"),
        ]

    def test_a_session_with_no_metadata_argument_persists_no_metadata_rows(
        self, store: ExecutionStore, stored_metadata: StoredMetadata
    ) -> None:
        """A caller that never passes `metadata=` (the empty default,
        `EMPTY_RUN_METADATA`) writes zero rows to either table."""
        execution = _execution("8" * 32)

        store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        assert stored_metadata(execution.identity.value) == RunMetadata()

    # -- list_runs_with_metadata_horizon --

    def test_the_horizon_of_a_key_never_declared_is_every_run(self, store: ExecutionStore) -> None:
        base = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            store.record_session(
                _execution(f"{i:032x}", started=base + timedelta(minutes=i)),
                results=(),
                received_at=base,
                project=DEFAULT_PROJECT,
            )

        page, predating = store.list_runs_with_metadata_horizon(
            filters=[("fw", "2.1")], limit=10, offset=0, project=DEFAULT_PROJECT
        )

        assert page.items == ()
        assert page.has_more is False
        assert predating == (3,)

    def test_the_horizon_counts_the_runs_started_before_the_key_was_first_declared(
        self, store: ExecutionStore
    ) -> None:
        """The first declaration is the earliest run holding a row for the
        key in any status: a value too large to capture still declares it,
        though it never matches the filter."""
        base = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)

        def _declaring(value: str | None, status: str) -> RunMetadata:
            return RunMetadata(
                files=(MetadataFile(source_file="m.json", content_type="json", status="captured"),),
                entries=(
                    MetadataEntry(key="fw", value=value, source_file="m.json", status=status),
                ),
            )

        for i in range(2):
            store.record_session(
                _execution(f"{i:032x}", started=base + timedelta(minutes=i)),
                results=(),
                received_at=base,
                project=DEFAULT_PROJECT,
            )
        store.record_session(
            _execution("a" * 32, started=base + timedelta(minutes=5)),
            results=(),
            received_at=base,
            metadata=_declaring(None, "value_too_large"),
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            _execution("b" * 32, started=base + timedelta(minutes=6)),
            results=(),
            received_at=base,
            metadata=_declaring("2.1", "captured"),
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            _execution("c" * 32, started=base + timedelta(minutes=7)),
            results=(),
            received_at=base,
            metadata=_declaring("2.1", "captured"),
            project=DEFAULT_PROJECT,
        )

        page, predating = store.list_runs_with_metadata_horizon(
            filters=[("fw", "2.1")], limit=1, offset=0, project=DEFAULT_PROJECT
        )

        assert [entry.execution.identity.value for entry in page.items] == ["c" * 32]
        assert page.has_more is True
        assert predating == (2,)

    def test_several_pairs_match_the_runs_holding_every_one_from_either_source(
        self, store: ExecutionStore
    ) -> None:
        """A run matches only when it holds each pair, whether the value was
        read from a file or reported by the session; each distinct key gets
        its own horizon, in the order the filter first names it."""
        base = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)

        def _holding(**values: str) -> RunMetadata:
            return RunMetadata(
                files=(MetadataFile(source_file="m.json", content_type="json", status="captured"),),
                entries=tuple(
                    MetadataEntry(key=key, value=value, source_file="m.json", status="captured")
                    if key == "fw"
                    else MetadataEntry(
                        key=key, value=value, source_file=None, status="captured", source="session"
                    )
                    for key, value in values.items()
                ),
            )

        runs = {
            "0" * 32: RunMetadata(),
            "1" * 32: _holding(fmc="5.2.0"),
            "2" * 32: _holding(fw="1.1.0", fmc="5.2.0"),
            "3" * 32: _holding(fw="1.1.0", fmc="5.3.0"),
            "4" * 32: _holding(fw="1.1.0", fmc="5.2.0", bench="lab-3"),
            "5" * 32: _holding(fw="1.0.0", fmc="5.2.0"),
        }
        for minute, (run_id, metadata) in enumerate(runs.items()):
            store.record_session(
                _execution(run_id, started=base + timedelta(minutes=minute)),
                results=(),
                received_at=base,
                metadata=metadata,
                project=DEFAULT_PROJECT,
            )

        page, predating = store.list_runs_with_metadata_horizon(
            filters=[("fw", "1.1.0"), ("fmc", "5.2.0"), ("fw", "1.1.0")],
            limit=10,
            offset=0,
            project=DEFAULT_PROJECT,
        )

        assert [entry.execution.identity.value for entry in page.items] == ["4" * 32, "2" * 32]
        assert page.has_more is False
        assert predating == (2, 1)

    def test_two_values_for_one_key_match_no_run(self, store: ExecutionStore) -> None:
        store.record_session(
            _execution("a" * 32),
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=RunMetadata(
                files=(MetadataFile(source_file="m.json", content_type="json", status="captured"),),
                entries=(
                    MetadataEntry(key="fw", value="2.1", source_file="m.json", status="captured"),
                ),
            ),
            project=DEFAULT_PROJECT,
        )

        page, predating = store.list_runs_with_metadata_horizon(
            filters=[("fw", "2.1"), ("fw", "2.2")], limit=10, offset=0, project=DEFAULT_PROJECT
        )

        assert page.items == ()
        assert predating == (0,)

    def test_no_pairs_narrow_nothing_and_count_nothing(self, store: ExecutionStore) -> None:
        base = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(2):
            store.record_session(
                _execution(f"{i:032x}", started=base + timedelta(minutes=i)),
                results=(),
                received_at=base,
                project=DEFAULT_PROJECT,
            )

        page, predating = store.list_runs_with_metadata_horizon(
            filters=[], limit=10, offset=0, project=DEFAULT_PROJECT
        )

        assert page == store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT)
        assert predating == ()

    # -- get_run_metadata --

    def test_get_run_metadata_is_none_for_an_unknown_run(self, store: ExecutionStore) -> None:
        assert store.get_run_metadata("0" * 32) is None

    def test_get_run_metadata_is_empty_for_a_run_that_reported_none(
        self, store: ExecutionStore
    ) -> None:
        execution = _execution("5" * 32)
        store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), project=DEFAULT_PROJECT
        )

        stored = store.get_run_metadata(execution.identity.value)

        assert stored == RunMetadata()

    def test_get_run_metadata_returns_the_runs_rows_whole_in_code_point_order(
        self, store: ExecutionStore
    ) -> None:
        """Every field of every row of that run and no other, whichever
        source it came from, ordered by key the same way on either adapter:
        by code point, so capitals before lower case and ASCII before
        anything else."""
        execution = _execution("6" * 32)
        other = _execution("7" * 32)
        entries = (
            MetadataEntry(
                key="zeta",
                value="z",
                source_file=None,
                status="captured",
                source="session",
                declared=False,
            ),
            MetadataEntry(
                key="fw",
                value="2.1",
                source_file="m.json",
                status="captured",
                name="Firmware version",
            ),
            MetadataEntry(
                key="é",
                value=None,
                source_file=None,
                status="absent",
                source="session",
                name="Accented",
                declared=True,
            ),
            MetadataEntry(key="Zeta", value=None, source_file="m.json", status="not_scalar"),
            MetadataEntry(
                key="\U0001f600",
                value=None,
                source_file=None,
                status="value_too_large",
                source="session",
                declared=False,
            ),
            MetadataEntry(key="a.b", value="1", source_file="m.json", status="captured"),
        )
        files = (MetadataFile(source_file="m.json", content_type="json", status="captured"),)
        store.record_session(
            execution,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=RunMetadata(files=files, entries=entries),
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            other,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=RunMetadata(
                files=files,
                entries=(
                    MetadataEntry(key="b", value="2", source_file="m.json", status="captured"),
                ),
            ),
            project=DEFAULT_PROJECT,
        )

        stored = store.get_run_metadata(execution.identity.value)

        assert stored is not None
        assert [entry.key for entry in stored.entries] == [
            "Zeta",
            "a.b",
            "fw",
            "zeta",
            "é",
            "\U0001f600",
        ]
        assert list(stored.entries) == sorted(entries, key=lambda entry: entry.key)

    def test_get_run_metadata_returns_the_runs_files_whole_in_code_point_order(
        self, store: ExecutionStore
    ) -> None:
        """Every declared file of that run and no other, with the status that
        says why its keys have no value, ordered by path the same way on
        either adapter: by code point, as the keys are."""
        execution = _execution("8" * 32)
        other = _execution("9" * 32)
        files = (
            MetadataFile(
                source_file="build/manifest.json", content_type="json", status="not_found"
            ),
            MetadataFile(source_file="é.yaml", content_type="yaml", status="malformed"),
            MetadataFile(source_file="B.yaml", content_type="yaml", status="captured"),
            MetadataFile(source_file="a.json", content_type="json", status="too_large"),
        )
        store.record_session(
            execution,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=RunMetadata(files=files),
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            other,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=RunMetadata(
                files=(MetadataFile(source_file="c.yaml", content_type="yaml", status="captured"),),
            ),
            project=DEFAULT_PROJECT,
        )

        stored = store.get_run_metadata(execution.identity.value)

        assert stored is not None
        assert [file.source_file for file in stored.files] == [
            "B.yaml",
            "a.json",
            "build/manifest.json",
            "é.yaml",
        ]
        assert list(stored.files) == sorted(files, key=lambda file: file.source_file)
        assert stored.entries == ()

    # --- Users, tokens and who recorded a run ---------------------------------

    def test_no_token_is_required_until_a_user_exists_and_then_always(
        self, store: ExecutionStore
    ) -> None:
        """Users are disabled, never deleted, so the first one closes the
        store for good: disabling every user leaves it closed."""
        assert store.access_required() is False

        store.create_user("alice", admin=True, created_at=_ACCESS_AT)
        store.update_user("alice", disabled=True)

        assert store.access_required() is True

    def test_a_created_user_reads_back_as_it_was_created(self, store: ExecutionStore) -> None:
        created = store.create_user("alice", admin=True, created_at=_ACCESS_AT)

        assert created == User(name="alice", admin=True, disabled=False, created_at=_ACCESS_AT)
        assert store.get_user("alice") == created
        assert store.get_user("bob") is None

    def test_users_list_in_code_point_order(self, store: ExecutionStore) -> None:
        for name in ("b", "a_3", "a.2", "a-1", "0"):
            store.create_user(name, admin=False, created_at=_ACCESS_AT)

        assert [user.name for user in store.list_users()] == ["0", "a-1", "a.2", "a_3", "b"]

    def test_a_taken_name_is_refused_and_the_user_left_as_it_was(
        self, store: ExecutionStore
    ) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)

        with pytest.raises(UserExistsError):
            store.create_user("alice", admin=True, created_at=_ACCESS_AT + timedelta(days=1))

        assert store.list_users() == (
            User(name="alice", admin=False, disabled=False, created_at=_ACCESS_AT),
        )

    def test_update_user_sets_only_what_it_is_given(self, store: ExecutionStore) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)

        promoted = store.update_user("alice", admin=True)
        disabled = store.update_user("alice", disabled=True)
        unchanged = store.update_user("alice")

        assert promoted is not None and (promoted.admin, promoted.disabled) == (True, False)
        assert disabled is not None and (disabled.admin, disabled.disabled) == (True, True)
        assert unchanged == disabled
        assert store.get_user("alice") == disabled

    def test_update_user_of_nobody_is_none(self, store: ExecutionStore) -> None:
        assert store.update_user("nobody", admin=True) is None
        assert store.list_users() == ()

    def test_a_token_reads_back_and_lists_oldest_first_by_user(self, store: ExecutionStore) -> None:
        store.create_user("alice", admin=True, created_at=_ACCESS_AT)
        store.create_user("bob", admin=False, created_at=_ACCESS_AT)

        first = store.create_token(
            "alice",
            digest=token_digest("first"),
            label="laptop",
            scopes=frozenset({READ_SCOPE, ADMIN_SCOPE}),
            created_at=_ACCESS_AT,
        )
        second = store.create_token(
            "bob",
            digest=token_digest("second"),
            label="",
            scopes=frozenset({RECORD_SCOPE}),
            created_at=_ACCESS_AT,
        )
        third = store.create_token(
            "alice",
            digest=token_digest("third"),
            label="ci — nightly",
            scopes=frozenset({READ_SCOPE, RECORD_SCOPE, ADMIN_SCOPE}),
            created_at=_ACCESS_AT,
        )

        assert first == Token(
            id=first.id,
            user="alice",
            label="laptop",
            scopes=frozenset({READ_SCOPE, ADMIN_SCOPE}),
            created_at=_ACCESS_AT,
            revoked_at=None,
        )
        assert first.id < second.id < third.id
        assert store.list_tokens() == (first, second, third)
        assert store.list_tokens(user="alice") == (first, third)
        assert store.list_tokens(user="carol") == ()

    def test_a_token_of_nobody_is_refused_and_nothing_stored(self, store: ExecutionStore) -> None:
        with pytest.raises(UnknownUserError):
            store.create_token(
                "nobody",
                digest=token_digest("t"),
                label="",
                scopes=frozenset({READ_SCOPE}),
                created_at=_ACCESS_AT,
            )

        assert store.list_tokens() == ()
        assert store.authenticate(token_digest("t"), now=_ACCESS_AT) is None

    def test_a_live_token_grants_its_scopes_as_its_user(self, store: ExecutionStore) -> None:
        """The grant names the token, and a made token has no end."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        _token(store, "alice", "earlier")
        made = _token(store, "alice", "secret", scopes={READ_SCOPE, RECORD_SCOPE})

        assert store.authenticate(token_digest("secret"), now=_ACCESS_AT) == Grant(
            user="alice",
            admin=False,
            scopes=frozenset({READ_SCOPE, RECORD_SCOPE}),
            token_id=made.id,
            expires_at=None,
        )
        assert store.authenticate(token_digest("other"), now=_ACCESS_AT) is None

    @pytest.mark.parametrize(
        "scopes",
        [
            {MANAGE_SCOPE},
            {READ_SCOPE, MANAGE_SCOPE},
            {RECORD_SCOPE, MANAGE_SCOPE, ADMIN_SCOPE},
            set(SCOPES),
        ],
        ids=["manage", "read-manage", "all-but-read", "every-scope"],
    )
    def test_the_manage_scope_is_held_and_granted_beside_any_other(
        self, store: ExecutionStore, scopes: set[str]
    ) -> None:
        """Each scope is stored on its own, so manage reads back, lists and
        is granted with whatever else the token holds -- or alone, which is
        still a scope held."""
        store.create_user("alice", admin=True, created_at=_ACCESS_AT)

        made = _token(store, "alice", "secret", scopes=scopes)

        assert made.scopes == frozenset(scopes)
        assert store.get_token(made.id) == made
        assert store.list_tokens(user="alice") == (made,)
        assert store.authenticate(token_digest("secret"), now=_ACCESS_AT) == Grant(
            user="alice", admin=True, scopes=frozenset(scopes), token_id=made.id, expires_at=None
        )

    def test_a_grant_carries_its_users_standing_now(self, store: ExecutionStore) -> None:
        """An admin token of a user who is no longer an admin still
        authenticates, but its grant says the user is not one, so the admin
        scope it holds allows nothing."""
        store.create_user("alice", admin=True, created_at=_ACCESS_AT)
        _token(store, "alice", "secret", scopes={ADMIN_SCOPE})
        store.update_user("alice", admin=False)

        grant = store.authenticate(token_digest("secret"), now=_ACCESS_AT)

        assert grant is not None
        assert grant.admin is False
        assert not grant.allows(ADMIN_SCOPE)

    def test_a_disabled_users_tokens_authenticate_nothing_until_enabled(
        self, store: ExecutionStore
    ) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        _token(store, "alice", "secret")

        store.update_user("alice", disabled=True)
        while_disabled = store.authenticate(token_digest("secret"), now=_ACCESS_AT)
        store.update_user("alice", disabled=False)

        assert while_disabled is None
        assert store.authenticate(token_digest("secret"), now=_ACCESS_AT) is not None

    def test_a_revoked_token_authenticates_nothing_and_keeps_its_first_revocation(
        self, store: ExecutionStore
    ) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        token = _token(store, "alice", "secret")
        revoked_at = _ACCESS_AT + timedelta(hours=1)

        revoked = store.revoke_token(token.id, revoked_at=revoked_at)
        again = store.revoke_token(token.id, revoked_at=revoked_at + timedelta(hours=1))

        assert (revoked, again) == (True, False)
        assert store.authenticate(token_digest("secret"), now=_ACCESS_AT) is None
        assert store.list_tokens() == (replace(token, revoked_at=revoked_at),)

    def test_revoking_as_a_user_reaches_only_that_users_tokens(self, store: ExecutionStore) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_user("bob", admin=False, created_at=_ACCESS_AT)
        token = _token(store, "alice", "secret")

        by_bob = store.revoke_token(token.id, revoked_at=_ACCESS_AT, user="bob")
        by_nobody = store.revoke_token(token.id, revoked_at=_ACCESS_AT, user="nobody")
        by_alice = store.revoke_token(token.id, revoked_at=_ACCESS_AT, user="alice")

        assert (by_bob, by_nobody, by_alice) == (False, False, True)

    def test_a_token_reads_back_by_its_id_revoked_or_not(self, store: ExecutionStore) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        live = _token(store, "alice", "live")
        revoked = _token(store, "alice", "revoked")
        revoked_at = _ACCESS_AT + timedelta(hours=1)
        store.revoke_token(revoked.id, revoked_at=revoked_at)
        store.revoke_token(revoked.id, revoked_at=revoked_at + timedelta(hours=1))

        assert store.get_token(live.id) == live
        assert store.get_token(revoked.id) == replace(revoked, revoked_at=revoked_at)
        assert store.get_token(revoked.id + 1) is None

    @pytest.mark.parametrize("token_id", [0, -1, 2**63, 2**64])
    def test_an_id_no_token_can_have_reads_back_as_none(
        self, store: ExecutionStore, token_id: int
    ) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        _token(store, "alice", "secret")

        assert store.get_token(token_id) is None

    @pytest.mark.parametrize("token_id", [0, -1, 2**63, 2**64])
    def test_revoking_an_id_no_token_can_have_is_false(
        self, store: ExecutionStore, token_id: int
    ) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        _token(store, "alice", "secret")

        assert store.revoke_token(token_id, revoked_at=_ACCESS_AT) is False
        assert store.authenticate(token_digest("secret"), now=_ACCESS_AT) is not None

    def test_a_run_names_the_user_whose_report_created_it(self, store: ExecutionStore) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        mine, anonymous = "1" * 32, "2" * 32
        store.record_session(
            _start_only_execution(mine),
            results=(),
            received_at=_ACCESS_AT,
            recorded_by="alice",
            project=DEFAULT_PROJECT,
        )
        store.record_session(
            _execution(anonymous), results=(), received_at=_ACCESS_AT, project=DEFAULT_PROJECT
        )

        mine_detail = store.get_run_detail(mine)
        anonymous_detail = store.get_run_detail(anonymous)
        listed = {
            entry.execution.identity.value: entry.recorded_by
            for entry in store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT).items
        }
        filtered, _horizon = store.list_runs_with_metadata_horizon(
            filters=(), limit=10, offset=0, project=DEFAULT_PROJECT
        )

        assert mine_detail is not None and mine_detail.recorded_by == "alice"
        assert anonymous_detail is not None and anonymous_detail.recorded_by is None
        assert listed == {mine: "alice", anonymous: None}
        assert {entry.recorded_by for entry in filtered.items} == {"alice", None}

    @pytest.mark.parametrize(
        ("creator", "sender"), [("alice", "bob"), ("alice", None), (None, "alice")]
    )
    def test_a_report_from_anyone_but_the_runs_creator_stores_nothing(
        self, store: ExecutionStore, creator: str | None, sender: str | None
    ) -> None:
        """Refused before anything is written: the run is not finished, and
        neither the report's results nor its metadata are kept."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_user("bob", admin=False, created_at=_ACCESS_AT)
        run_id = "3" * 32
        start = _start_only_execution(run_id)
        store.record_session(
            start, results=(), received_at=_ACCESS_AT, recorded_by=creator, project=DEFAULT_PROJECT
        )

        with pytest.raises(ForeignRunError):
            store.record_session(
                _execution(run_id),
                results=(_result("t.py::test_a"),),
                received_at=_ACCESS_AT,
                metadata=RunMetadata(
                    entries=(
                        MetadataEntry(
                            key="k",
                            value="v",
                            source_file=None,
                            status="captured",
                            source="session",
                        ),
                    )
                ),
                recorded_by=sender,
                project=DEFAULT_PROJECT,
            )

        detail = store.get_run_detail(run_id)
        assert detail is not None
        assert (detail.execution, detail.recorded_by) == (start, creator)
        assert store.get_results(run_id) == []
        assert store.get_run_metadata(run_id) == RunMetadata()
        assert store.get_catalogue_entry("t.py::test_a", project=DEFAULT_PROJECT) is None

    def test_another_users_replay_of_a_finished_run_is_refused_too(
        self, store: ExecutionStore
    ) -> None:
        """Whose run it is comes before whether it is finished, so nobody
        else learns a run is finished by getting its duplicate answer."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_user("bob", admin=False, created_at=_ACCESS_AT)
        execution = _execution("4" * 32)
        store.record_session(
            execution,
            results=(),
            received_at=_ACCESS_AT,
            recorded_by="alice",
            project=DEFAULT_PROJECT,
        )

        with pytest.raises(ForeignRunError):
            store.record_session(
                execution,
                results=(),
                received_at=_ACCESS_AT,
                recorded_by="bob",
                project=DEFAULT_PROJECT,
            )

        assert (
            store.record_session(
                execution,
                results=(),
                received_at=_ACCESS_AT,
                recorded_by="alice",
                project=DEFAULT_PROJECT,
            )
            is False
        )

    def test_the_creator_of_a_run_finishes_it(self, store: ExecutionStore) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        run_id = "5" * 32
        store.record_session(
            _start_only_execution(run_id),
            results=(),
            received_at=_ACCESS_AT,
            recorded_by="alice",
            project=DEFAULT_PROJECT,
        )

        created = store.record_session(
            _execution(run_id),
            results=(_result("t.py::test_a"),),
            received_at=_ACCESS_AT,
            recorded_by="alice",
            project=DEFAULT_PROJECT,
        )

        assert created is False
        assert store.get_execution(run_id) == _execution(run_id)
        assert len(store.get_results(run_id)) == 1

    # --- Projects ----------------------------------------------------------------

    def test_a_new_store_holds_the_default_project_alone(self, store: ExecutionStore) -> None:
        """Every database has `default` from creation, for the runs whose
        report names no project; nothing else exists until someone makes it."""
        projects = store.list_projects()

        assert [project.name for project in projects] == [DEFAULT_PROJECT]
        assert store.get_project(DEFAULT_PROJECT) == projects[0]

    def test_created_projects_read_back_and_list_in_code_point_order(
        self, store: ExecutionStore
    ) -> None:
        """Code point order, not a locale's: `-`, `.` and `_` sort by their
        code points, before every letter, whatever the database collates by."""
        created = [
            store.create_project(name, created_at=_ACCESS_AT + timedelta(minutes=i))
            for i, name in enumerate(("zeta", "b", "a_3", "a.2", "a-1", "0"))
        ]

        assert created[0] == Project(name="zeta", created_at=_ACCESS_AT)
        assert [store.get_project(project.name) for project in created] == created
        assert [project.name for project in store.list_projects()] == [
            "0",
            "a-1",
            "a.2",
            "a_3",
            "b",
            DEFAULT_PROJECT,
            "zeta",
        ]

    @pytest.mark.parametrize("name", [DEFAULT_PROJECT, "alpha"])
    def test_a_taken_project_name_is_refused_and_changes_nothing(
        self, store: ExecutionStore, name: str
    ) -> None:
        store.create_project("alpha", created_at=_ACCESS_AT)
        before = list(store.list_projects())

        with pytest.raises(ProjectExistsError):
            store.create_project(name, created_at=_ACCESS_AT + timedelta(days=1))

        assert list(store.list_projects()) == before

    @pytest.mark.parametrize("name", ["nope", "alpha\x00", "\x00", "Alpha"])
    def test_a_name_no_project_has_reads_back_as_none(
        self, store: ExecutionStore, name: str
    ) -> None:
        """A U+0000 names no project on any adapter, whatever it trails;
        names differ by case, so `Alpha` is not `alpha`."""
        store.create_project("alpha", created_at=_ACCESS_AT)

        assert store.get_project(name) is None

    @pytest.mark.parametrize("project", ["nope", "default\x00"])
    def test_a_report_into_an_unknown_project_is_refused_and_stores_nothing(
        self, store: ExecutionStore, project: str
    ) -> None:
        """Refused before anything is written, and the store never makes the
        project itself: only whoever manages the store does."""
        node_id = "t.py::test_a"

        with pytest.raises(UnknownProjectError):
            store.record_session(
                _execution("1" * 32),
                results=(_result(node_id),),
                received_at=_ACCESS_AT,
                metadata=_session_metadata(("k", "v")),
                project=project,
            )

        assert store.count_executions() == 0
        assert store.count_results() == 0
        assert store.get_run_detail("1" * 32) is None
        assert store.get_catalogue_entry(node_id, project=DEFAULT_PROJECT) is None
        assert store.get_catalogue_entry(node_id, project=project) is None
        assert [p.name for p in store.list_projects()] == [DEFAULT_PROJECT]

    def test_one_node_id_in_two_projects_is_two_catalogue_entries(
        self, store: ExecutionStore
    ) -> None:
        """Two projects never share a test, however alike their node ids: a
        newer run of the node id in one leaves the other's entry, identity
        and last run as they were."""
        store.create_project("a", created_at=_ACCESS_AT)
        store.create_project("b", created_at=_ACCESS_AT)
        node_id = "tests/a.py::T::test_x"
        in_a = _identified(node_id, "tests/a.py", "T")
        in_b = _identified(node_id, "b.py", None)
        run_a = _execution("a" * 32, started=_ACCESS_AT)
        run_b = _execution("b" * 32, started=_ACCESS_AT + timedelta(days=1))
        store.record_session(run_a, results=(in_a,), received_at=_ACCESS_AT, project="a")
        entry_of_a = store.get_catalogue_entry(node_id, project="a")

        store.record_session(run_b, results=(in_b,), received_at=_ACCESS_AT, project="b")

        entry_of_b = store.get_catalogue_entry(node_id, project="b")
        assert entry_of_a is not None and entry_of_b is not None
        assert store.get_catalogue_entry(node_id, project="a") == entry_of_a
        assert (entry_of_a.identity, entry_of_a.last_seen_run_id) == (in_a.identity, "a" * 32)
        assert (entry_of_b.identity, entry_of_b.last_seen_run_id) == (in_b.identity, "b" * 32)
        assert entry_of_b.first_seen_at == run_b.started_at
        assert store.get_catalogue_entry(node_id, project=DEFAULT_PROJECT) is None

    def test_a_projects_results_read_its_own_catalogue_row(self, store: ExecutionStore) -> None:
        """Recorded in A, then B, then A again: a result is attached to the
        catalogue row of its run's project, never to another project's row
        of the same node id, so A's runs read A's identity and A's history
        holds A's runs alone."""
        store.create_project("a", created_at=_ACCESS_AT)
        store.create_project("b", created_at=_ACCESS_AT)
        node_id = "tests/a.py::T::test_x"
        in_a = _identified(node_id, "tests/a.py", "T")
        in_b = _identified(node_id, "b.py", None)
        recorded = [
            ("1" * 32, "a", in_a, _ACCESS_AT),
            ("2" * 32, "b", in_b, _ACCESS_AT + timedelta(hours=1)),
            ("3" * 32, "a", in_a, _ACCESS_AT + timedelta(hours=2)),
        ]
        for run_id, project, result, started in recorded:
            store.record_session(
                _execution(run_id, started=started),
                results=(result,),
                received_at=started,
                project=project,
            )

        for run_id, _project, result, _started in recorded:
            found = store.get_result(run_id, node_id=node_id)
            (listed,) = store.list_results(run_id, limit=10, offset=0).items
            assert found is not None and found.identity == result.identity, run_id
            assert listed.identity == result.identity, run_id
            assert [r.identity for r in store.get_results(run_id)] == [result.identity]
        history_of_a = store.list_history(node_id=node_id, limit=10, offset=0, project="a")
        history_of_b = store.list_history(node_id=node_id, limit=10, offset=0, project="b")
        assert [entry.run_id for entry in history_of_a.items] == ["3" * 32, "1" * 32]
        assert [entry.run_id for entry in history_of_b.items] == ["2" * 32]
        entry_of_a = store.get_catalogue_entry(node_id, project="a")
        assert entry_of_a is not None
        assert (entry_of_a.first_seen_at, entry_of_a.last_seen_run_id) == (_ACCESS_AT, "3" * 32)

    def test_the_run_list_holds_its_own_projects_runs_alone(self, store: ExecutionStore) -> None:
        """With or without a cursor, and filtered or not; a cursor naming
        another project's run only bounds the order, as any key does."""
        store.create_project("a", created_at=_ACCESS_AT)
        store.create_project("b", created_at=_ACCESS_AT)
        runs = [("a0", "a"), ("b0", "b"), ("d0", DEFAULT_PROJECT), ("a1", "a"), ("b1", "b")]
        for i, (name, project) in enumerate(runs):
            store.record_session(
                _execution(name * 16, started=_ACCESS_AT + timedelta(minutes=i)),
                results=(),
                received_at=_ACCESS_AT,
                project=project,
            )

        def _ids(project: str, after: RunKey | None = None) -> list[str]:
            page = store.list_runs(limit=10, offset=0, after=after, project=project)
            filtered, _horizon = store.list_runs_with_metadata_horizon(
                filters=(), limit=10, offset=0, after=after, project=project
            )
            ids = [entry.execution.identity.value for entry in page.items]
            assert [entry.execution.identity.value for entry in filtered.items] == ids
            return ids

        after_b0 = RunKey(started_at=_ACCESS_AT + timedelta(minutes=1), run_id="b0" * 16)
        assert _ids("a") == ["a1" * 16, "a0" * 16]
        assert _ids("b") == ["b1" * 16, "b0" * 16]
        assert _ids(DEFAULT_PROJECT) == ["d0" * 16]
        assert _ids("a", after=after_b0) == ["a0" * 16]
        assert _ids("b", after=after_b0) == []

    @pytest.mark.parametrize("project", ["nope", "default\x00"])
    def test_a_project_that_does_not_exist_reads_empty(
        self, store: ExecutionStore, project: str
    ) -> None:
        node_id = "t.py::test_a"
        store.record_session(
            _execution("1" * 32),
            results=(_result(node_id),),
            received_at=_ACCESS_AT,
            metadata=_session_metadata(("fw", "2.1")),
            project=DEFAULT_PROJECT,
        )
        store.upsert_setting(
            "test_sections", "Billing", value="a", updated_at=_ACCESS_AT, project=DEFAULT_PROJECT
        )

        runs = store.list_runs(limit=10, offset=0, project=project)
        filtered, predating = store.list_runs_with_metadata_horizon(
            filters=[("fw", "2.1")], limit=10, offset=0, project=project
        )
        history = store.list_history(node_id=node_id, limit=10, offset=0, project=project)

        assert (runs.items, runs.has_more) == ((), False)
        assert (filtered.items, filtered.has_more, predating) == ((), False, (0,))
        assert (history.items, history.has_more) == ((), False)
        assert store.get_catalogue_entry(node_id, project=project) is None
        assert tuple(store.list_settings("test_sections", project=project)) == ()
        assert store.delete_setting("test_sections", "Billing", project=project) is False
        assert len(store.list_settings("test_sections", project=DEFAULT_PROJECT)) == 1

    def test_the_horizon_of_a_key_is_counted_within_the_project(
        self, store: ExecutionStore
    ) -> None:
        """B carries both keys before A exists in time: A's `fw` horizon
        counts A's runs before A first carried it, not B's older runs nor
        B's earlier declaration, and a key only B carries leaves A's count
        at A's run count."""
        store.create_project("a", created_at=_ACCESS_AT)
        store.create_project("b", created_at=_ACCESS_AT)
        runs = [
            ("b0", "b", None),
            ("b1", "b", _session_metadata(("fw", "2.1"), ("only_b", "x"))),
            ("b2", "b", None),
            ("a0", "a", None),
            ("a1", "a", None),
            ("a2", "a", _session_metadata(("fw", "2.1"))),
        ]
        for i, (name, project, metadata) in enumerate(runs):
            store.record_session(
                _execution(name * 16, started=_ACCESS_AT + timedelta(minutes=i)),
                results=(),
                received_at=_ACCESS_AT,
                metadata=metadata if metadata is not None else RunMetadata(),
                project=project,
            )

        def _horizon(project: str, key: str, value: str) -> tuple[list[str], tuple[int, ...]]:
            page, predating = store.list_runs_with_metadata_horizon(
                filters=[(key, value)], limit=10, offset=0, project=project
            )
            return [entry.execution.identity.value for entry in page.items], predating

        assert _horizon("a", "fw", "2.1") == (["a2" * 16], (2,))
        assert _horizon("a", "only_b", "x") == ([], (3,))
        assert _horizon("b", "fw", "2.1") == (["b1" * 16], (1,))

    @pytest.mark.parametrize("finished", [False, True], ids=["unfinished", "finished"])
    def test_a_report_naming_another_project_than_its_runs_stores_nothing(
        self, store: ExecutionStore, finished: bool
    ) -> None:
        """A run stays in the project that created it. A report naming
        another is refused before anything is written -- even for a
        finished run, whose replay would otherwise be answered as a
        duplicate, hiding that it went to the wrong project."""
        store.create_project("a", created_at=_ACCESS_AT)
        store.create_project("b", created_at=_ACCESS_AT)
        run_id = "6" * 32
        first = _execution(run_id) if finished else _start_only_execution(run_id)
        store.record_session(first, results=(), received_at=_ACCESS_AT, project="a")

        with pytest.raises(ProjectMismatchError):
            store.record_session(
                _execution(run_id),
                results=(_result("t.py::test_a"),),
                received_at=_ACCESS_AT + timedelta(hours=1),
                metadata=_session_metadata(("k", "v")),
                project="b",
            )

        detail = store.get_run_detail(run_id)
        assert detail is not None
        assert (detail.execution, detail.project) == (first, "a")
        assert detail.last_contact_at == _ACCESS_AT
        assert store.get_results(run_id) == []
        assert store.get_run_metadata(run_id) == RunMetadata()
        assert store.get_catalogue_entry("t.py::test_a", project="a") is None
        assert store.get_catalogue_entry("t.py::test_a", project="b") is None
        assert store.list_runs(limit=10, offset=0, project="b").items == ()

    def test_a_run_detail_names_the_project_that_created_it(self, store: ExecutionStore) -> None:
        store.create_project("a", created_at=_ACCESS_AT)
        run_id = "7" * 32
        store.record_session(
            _start_only_execution(run_id), results=(), received_at=_ACCESS_AT, project="a"
        )
        finished = store.record_session(
            _execution(run_id),
            results=(_result("t.py::test_a"),),
            received_at=_ACCESS_AT,
            project="a",
        )
        store.record_session(
            _execution("8" * 32), results=(), received_at=_ACCESS_AT, project=DEFAULT_PROJECT
        )

        in_a = store.get_run_detail(run_id)
        in_default = store.get_run_detail("8" * 32)

        assert finished is False
        assert in_a is not None and in_a.project == "a"
        assert in_a.execution == _execution(run_id)
        assert in_default is not None and in_default.project == DEFAULT_PROJECT

    def test_an_unknown_project_is_refused_before_another_user_and_that_before_a_mismatch(
        self, store: ExecutionStore
    ) -> None:
        """Each refusal is checked before the next: a report into no
        project says so whoever sends it, and another user's report learns
        nothing of which project the run is in."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_user("bob", admin=False, created_at=_ACCESS_AT)
        store.create_project("a", created_at=_ACCESS_AT)
        store.create_project("b", created_at=_ACCESS_AT)
        execution = _execution("9" * 32)
        store.record_session(
            execution, results=(), received_at=_ACCESS_AT, recorded_by="alice", project="a"
        )

        def _report(sender: str, project: str) -> bool:
            return store.record_session(
                execution,
                results=(_result("t.py::test_a"),),
                received_at=_ACCESS_AT,
                recorded_by=sender,
                project=project,
            )

        with pytest.raises(UnknownProjectError):
            _report("bob", "nope")
        with pytest.raises(UnknownProjectError):
            _report("alice", "nope")
        with pytest.raises(ForeignRunError):
            _report("bob", "b")
        with pytest.raises(ProjectMismatchError):
            _report("alice", "b")
        assert _report("alice", "a") is False
        assert store.get_results("9" * 32) == []

    def test_one_setting_key_in_two_projects_is_two_settings(self, store: ExecutionStore) -> None:
        store.create_project("a", created_at=_ACCESS_AT)
        later = _ACCESS_AT + timedelta(minutes=1)

        in_default = store.upsert_setting(
            "test_sections", "Billing", value="d", updated_at=_ACCESS_AT, project=DEFAULT_PROJECT
        )
        in_a = store.upsert_setting(
            "test_sections", "Billing", value="a", updated_at=later, project="a"
        )

        assert (in_default, in_a) == (True, True)
        assert tuple(store.list_settings("test_sections", project=DEFAULT_PROJECT)) == (
            ProjectSetting(DEFAULT_PROJECT, "test_sections", "Billing", "d", _ACCESS_AT),
        )
        assert tuple(store.list_settings("test_sections", project="a")) == (
            ProjectSetting("a", "test_sections", "Billing", "a", later),
        )

    def test_max_keys_counts_only_the_keys_of_its_own_project(self, store: ExecutionStore) -> None:
        store.create_project("a", created_at=_ACCESS_AT)
        for key in ("Billing", "Checkout"):
            store.upsert_setting(
                "test_sections", key, value="d", updated_at=_ACCESS_AT, project=DEFAULT_PROJECT
            )

        def _add(key: str) -> bool:
            return store.upsert_setting(
                "test_sections", key, value="a", updated_at=_ACCESS_AT, max_keys=2, project="a"
            )

        assert (_add("Accounts"), _add("Billing")) == (True, True)
        with pytest.raises(NamespaceFullError):
            _add("Checkout")
        assert [s.key for s in store.list_settings("test_sections", project="a")] == [
            "Accounts",
            "Billing",
        ]

    def test_deleting_a_setting_in_one_project_leaves_the_others(
        self, store: ExecutionStore
    ) -> None:
        store.create_project("a", created_at=_ACCESS_AT)
        for project in (DEFAULT_PROJECT, "a"):
            store.upsert_setting(
                "test_sections", "Billing", value=project, updated_at=_ACCESS_AT, project=project
            )

        deleted = store.delete_setting("test_sections", "Billing", project="a")
        again = store.delete_setting("test_sections", "Billing", project="a")

        assert (deleted, again) == (True, False)
        assert tuple(store.list_settings("test_sections", project="a")) == ()
        assert [s.value for s in store.list_settings("test_sections", project=DEFAULT_PROJECT)] == [
            DEFAULT_PROJECT
        ]

    @pytest.mark.parametrize("project", ["nope", "default\x00"])
    def test_a_setting_of_an_unknown_project_is_refused_and_stores_nothing(
        self, store: ExecutionStore, project: str
    ) -> None:
        with pytest.raises(UnknownProjectError):
            store.upsert_setting(
                "test_sections", "Billing", value="a", updated_at=_ACCESS_AT, project=project
            )

        assert tuple(store.list_settings("test_sections", project=project)) == ()
        assert tuple(store.list_settings("test_sections", project=DEFAULT_PROJECT)) == ()
        assert [p.name for p in store.list_projects()] == [DEFAULT_PROJECT]

    # --- The first admin, passwords and login tokens ------------------------

    def test_the_first_admin_is_created_once_and_never_again(self, store: ExecutionStore) -> None:
        """Of two servers starting on one new database, one creates it; the
        other, like every later start, finds a user and creates nothing --
        whatever name it asks for."""
        first = store.create_first_admin("admin", password_hash=_HASH, created_at=_ACCESS_AT)
        again = store.create_first_admin(
            "root", password_hash=_OTHER_HASH, created_at=_ACCESS_AT + timedelta(hours=1)
        )

        assert first == User(
            name="admin", admin=True, disabled=False, created_at=_ACCESS_AT, has_password=True
        )
        assert again is None
        assert store.list_users() == (first,)
        assert store.get_user("admin") == first
        assert store.get_password_hash("admin") == _HASH
        assert store.access_required() is True

    @pytest.mark.parametrize("disabled", [False, True], ids=["enabled", "disabled"])
    def test_no_first_admin_is_created_once_the_database_has_a_user(
        self, store: ExecutionStore, disabled: bool
    ) -> None:
        """Not even when every user is disabled: a restart never hands out a
        fresh admin, so a closed database stays closed."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.update_user("alice", disabled=disabled)

        created = store.create_first_admin("admin", password_hash=_HASH, created_at=_ACCESS_AT)

        assert created is None
        assert [user.name for user in store.list_users()] == ["alice"]
        assert store.get_password_hash("admin") is None

    def test_a_password_hash_is_handed_out_only_for_an_enabled_user_who_has_one(
        self, store: ExecutionStore
    ) -> None:
        """A disabled user cannot log in, so there is nothing to check their
        password against. No stored name holds U+0000, so one that does
        matches nobody, without the lookup failing."""
        _user(store, "alice")
        _user(store, "bob")
        store.update_user("bob", disabled=True)
        store.create_user("carol", admin=False, created_at=_ACCESS_AT)

        assert store.get_password_hash("alice") == _HASH
        assert store.get_password_hash("bob") is None
        assert store.get_password_hash("carol") is None
        assert store.get_password_hash("nobody") is None
        assert store.get_password_hash("alice\x00") is None

    def test_a_password_set_is_the_users_until_the_next_one(self, store: ExecutionStore) -> None:
        created = store.create_user("alice", admin=False, created_at=_ACCESS_AT)

        first = store.set_password("alice", password_hash=_HASH, changed_at=_ACCESS_AT)
        with_one = store.get_user("alice")
        promoted = store.update_user("alice", admin=True)
        second = store.set_password(
            "alice", password_hash=_OTHER_HASH, changed_at=_ACCESS_AT + timedelta(hours=1)
        )

        assert created.has_password is False
        assert (first, second) == (True, True)
        assert with_one == replace(created, has_password=True)
        assert promoted == replace(created, admin=True, has_password=True)
        assert store.list_users() == (promoted,)
        assert store.get_password_hash("alice") == _OTHER_HASH

    def test_a_password_is_replaced_only_while_it_is_still_the_one_checked(
        self, store: ExecutionStore
    ) -> None:
        """A change checked against the current password loses to any change
        made while it was checked, and then changes nothing: neither the
        hash nor the login tokens opened with the password that won."""
        _user(store, "alice")
        replaced = store.set_password(
            "alice", password_hash=_OTHER_HASH, changed_at=_ACCESS_AT, replacing=_HASH
        )
        login = _logged_in(store, "alice", "login", password_hash=_OTHER_HASH)

        stale = store.set_password(
            "alice",
            password_hash=_THIRD_HASH,
            changed_at=_ACCESS_AT + timedelta(hours=1),
            replacing=_HASH,
        )

        assert (replaced, stale) == (True, False)
        assert store.get_password_hash("alice") == _OTHER_HASH
        assert store.list_tokens() == (login,)

    def test_a_user_without_a_password_has_none_to_replace(self, store: ExecutionStore) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)

        replaced = store.set_password(
            "alice", password_hash=_HASH, changed_at=_ACCESS_AT, replacing=_OTHER_HASH
        )

        assert replaced is False
        assert store.get_password_hash("alice") is None
        user = store.get_user("alice")
        assert user is not None and user.has_password is False

    def test_a_disabled_users_password_is_set_but_never_replaced(
        self, store: ExecutionStore
    ) -> None:
        """A disabled user cannot change their own password, any more than
        they can log in; an admin still sets it, ready for when the user is
        enabled again."""
        _user(store, "alice")
        store.update_user("alice", disabled=True)

        replaced = store.set_password(
            "alice", password_hash=_OTHER_HASH, changed_at=_ACCESS_AT, replacing=_HASH
        )
        store.update_user("alice", disabled=False)
        kept = store.get_password_hash("alice")
        store.update_user("alice", disabled=True)
        set_while_disabled = store.set_password(
            "alice", password_hash=_THIRD_HASH, changed_at=_ACCESS_AT
        )
        store.update_user("alice", disabled=False)

        assert (replaced, kept) == (False, _HASH)
        assert set_while_disabled is True
        assert store.get_password_hash("alice") == _THIRD_HASH

    @pytest.mark.parametrize("checked", [False, True], ids=["set", "replaced"])
    def test_setting_a_password_revokes_that_users_login_tokens_alone(
        self, store: ExecutionStore, checked: bool
    ) -> None:
        """Whoever logged in with the old password must log in again; made
        tokens -- the plugin's, CI's -- carry on, as do other users' login
        tokens. One revoked before keeps its first revocation."""
        _user(store, "alice")
        _user(store, "bob")
        made = _token(store, "alice", "made")
        login = _logged_in(store, "alice", "login")
        revoked_before = _logged_in(store, "alice", "revoked")
        store.revoke_token(revoked_before.id, revoked_at=_ACCESS_AT)
        bobs = _logged_in(store, "bob", "bobs")
        changed_at = _ACCESS_AT + timedelta(hours=1)

        changed = store.set_password(
            "alice",
            password_hash=_OTHER_HASH,
            changed_at=changed_at,
            replacing=_HASH if checked else None,
        )

        assert changed is True
        assert store.list_tokens() == (
            made,
            replace(login, revoked_at=changed_at),
            replace(revoked_before, revoked_at=_ACCESS_AT),
            bobs,
        )
        assert store.authenticate(token_digest("login"), now=changed_at) is None
        assert store.authenticate(token_digest("made"), now=changed_at) is not None
        assert store.authenticate(token_digest("bobs"), now=changed_at) is not None

    @pytest.mark.parametrize("name", ["nobody", "alice\x00"], ids=["unknown", "nul"])
    def test_the_password_of_nobody_is_not_set(self, store: ExecutionStore, name: str) -> None:
        _user(store, "alice")

        set_ = store.set_password(name, password_hash=_OTHER_HASH, changed_at=_ACCESS_AT)
        replaced = store.set_password(
            name, password_hash=_OTHER_HASH, changed_at=_ACCESS_AT, replacing=_HASH
        )

        assert (set_, replaced) == (False, False)
        assert [user.name for user in store.list_users()] == ["alice"]
        assert store.get_password_hash("alice") == _HASH

    @pytest.mark.parametrize("admin", [False, True], ids=["user", "admin"])
    def test_a_login_token_reads_manages_and_administers_as_its_user_may_but_never_records(
        self, store: ExecutionStore, admin: bool
    ) -> None:
        """Recording takes a made token, so a leaked login cannot inject
        runs. Every login may manage, as far as the user's role in a project
        lets it, and only an admin's may administer. The expiry reads back
        to the microsecond, which is what `authenticate` compares."""
        _user(store, "alice", admin=admin)
        expires_at = _ACCESS_AT + LOGIN_TOKEN_LIFETIME + timedelta(microseconds=1)

        login = store.create_login_token(
            "alice",
            password_hash=_HASH,
            digest=token_digest("login"),
            created_at=_ACCESS_AT,
            expires_at=expires_at,
        )

        scopes = frozenset(
            {READ_SCOPE, MANAGE_SCOPE, ADMIN_SCOPE} if admin else {READ_SCOPE, MANAGE_SCOPE}
        )
        assert login is not None
        assert login == Token(
            id=login.id,
            user="alice",
            label=LOGIN_TOKEN_LABEL,
            scopes=scopes,
            created_at=_ACCESS_AT,
            revoked_at=None,
            expires_at=expires_at,
        )
        assert store.get_token(login.id) == login
        assert store.list_tokens(user="alice") == (login,)
        assert store.authenticate(token_digest("login"), now=_ACCESS_AT) == Grant(
            user="alice", admin=admin, scopes=scopes, token_id=login.id, expires_at=expires_at
        )

    def test_a_login_stores_nothing_unless_the_hash_it_checked_is_still_an_enabled_users(
        self, store: ExecutionStore
    ) -> None:
        """A login checks the password against the hash it read, then makes
        its token only while that hash is still the user's: a password set
        or a user disabled meanwhile wins, and the login stores nothing."""
        _user(store, "alice")
        store.set_password("alice", password_hash=_OTHER_HASH, changed_at=_ACCESS_AT)
        _user(store, "bob")
        store.update_user("bob", disabled=True)
        store.create_user("carol", admin=False, created_at=_ACCESS_AT)

        refused = {
            "changed since": _login(store, "alice", "1", password_hash=_HASH),
            "disabled": _login(store, "bob", "2", password_hash=_HASH),
            "no password": _login(store, "carol", "3", password_hash=_HASH),
            "unknown": _login(store, "nobody", "4", password_hash=_HASH),
            "nul": _login(store, "alice\x00", "5", password_hash=_OTHER_HASH),
        }

        assert refused == dict.fromkeys(refused)
        assert store.list_tokens() == ()

    def test_a_login_deletes_that_users_expired_login_tokens_alone(
        self, store: ExecutionStore
    ) -> None:
        """So the lists of tokens stay short. A login token expired by the
        moment of the new login goes, revoked or not; one still live stays,
        as do made tokens, which never expire, and every other user's."""
        _user(store, "alice")
        _user(store, "bob")
        now = _ACCESS_AT + timedelta(days=1)
        made = _token(store, "alice", "made")
        long_expired = _logged_in(store, "alice", "long", expires_at=now - timedelta(hours=1))
        just_expired = _logged_in(store, "alice", "just", expires_at=now)
        revoked = _logged_in(store, "alice", "revoked", expires_at=now - timedelta(hours=1))
        store.revoke_token(revoked.id, revoked_at=_ACCESS_AT)
        live = _logged_in(store, "alice", "live", expires_at=now + timedelta(microseconds=1))
        bobs = _logged_in(store, "bob", "bobs", expires_at=now - timedelta(hours=1))

        new = _logged_in(store, "alice", "new", created_at=now)

        assert store.list_tokens() == (made, live, bobs, new)
        for gone in (long_expired, just_expired, revoked):
            assert store.get_token(gone.id) is None
        # Asked before it expired: its row is gone, not merely expired.
        assert store.authenticate(token_digest("long"), now=_ACCESS_AT) is None

    def test_a_login_never_deletes_the_token_it_makes(self, store: ExecutionStore) -> None:
        """Whatever expiry it is given: a login token expiring the moment it
        is made is stored, and authenticates nothing."""
        _user(store, "alice")

        instant = _logged_in(store, "alice", "instant", expires_at=_ACCESS_AT)

        assert store.get_token(instant.id) == instant
        assert store.list_tokens() == (instant,)
        assert store.authenticate(token_digest("instant"), now=_ACCESS_AT) is None

    def test_a_refused_login_deletes_nothing(self, store: ExecutionStore) -> None:
        _user(store, "alice")
        expired = _logged_in(store, "alice", "expired", expires_at=_ACCESS_AT + timedelta(hours=1))

        refused = _login(
            store,
            "alice",
            "refused",
            password_hash=_OTHER_HASH,
            created_at=_ACCESS_AT + timedelta(days=1),
        )

        assert refused is None
        assert store.list_tokens() == (expired,)

    def test_a_token_id_is_never_handed_out_again_once_its_row_is_deleted(
        self, store: ExecutionStore
    ) -> None:
        """A token is revoked by its id, so an id handed out again would let
        a revocation meant for one token reach another."""
        _user(store, "alice")
        expired = _logged_in(store, "alice", "expired", expires_at=_ACCESS_AT + timedelta(hours=1))
        made = _token(store, "alice", "made")
        pruning = _logged_in(store, "alice", "pruning", created_at=_ACCESS_AT + timedelta(days=1))
        after = _token(store, "alice", "after")

        assert expired.id < made.id < pruning.id < after.id
        assert store.list_tokens() == (made, pruning, after)

    @pytest.mark.parametrize("offset_hours", [0, 2, -5], ids=["utc", "east", "west"])
    def test_a_login_token_authenticates_until_the_moment_it_expires(
        self, store: ExecutionStore, offset_hours: int
    ) -> None:
        """Up to the microsecond before `expires_at` and never from then on,
        whatever offset the moment asking is given in: what is compared is
        the instant."""
        _user(store, "alice")
        expires_at = _ACCESS_AT + LOGIN_TOKEN_LIFETIME
        login = _logged_in(store, "alice", "login", expires_at=expires_at)
        zone = timezone(timedelta(hours=offset_hours))

        def _grant(now: datetime) -> Grant | None:
            return store.authenticate(token_digest("login"), now=now.astimezone(zone))

        assert _grant(expires_at - timedelta(microseconds=1)) == Grant(
            user="alice",
            admin=False,
            scopes=frozenset({READ_SCOPE, MANAGE_SCOPE}),
            token_id=login.id,
            expires_at=expires_at,
        )
        assert _grant(expires_at) is None
        assert _grant(expires_at + timedelta(days=365)) is None

    def test_a_made_token_never_expires(self, store: ExecutionStore) -> None:
        """A made token -- the plugin's, CI's -- works until it is
        revoked."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        made = _token(store, "alice", "made")
        far_future = datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)

        assert made.expires_at is None
        assert store.get_token(made.id) == made
        assert store.authenticate(token_digest("made"), now=far_future) is not None

    # --- Project members -------------------------------------------------------

    def test_a_new_store_holds_no_member_rows(self, store: ExecutionStore) -> None:
        """Not in `default` either, whose every user is an editor without a
        row, nor for an admin, who acts as an owner everywhere: a store
        holds rows alone, and those two rules are `effective_role`'s."""
        store.create_user("alice", admin=True, created_at=_ACCESS_AT)
        store.create_project("a", created_at=_ACCESS_AT)

        for project in (DEFAULT_PROJECT, "a"):
            assert tuple(store.list_members(project=project)) == ()
            assert store.get_member_role("alice", project=project) is None
        assert tuple(store.list_memberships("alice")) == ()

    def test_set_member_adds_a_member_once_and_then_sets_their_role(
        self, store: ExecutionStore
    ) -> None:
        """Only the call that added the row says so, which is what tells
        adding a member from setting a member's role -- also when a later
        call sets the role the member already holds."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_project("a", created_at=_ACCESS_AT)

        added = store.set_member("alice", project="a", role=VIEWER_ROLE)
        as_added = store.get_member_role("alice", project="a")
        to_editor = store.set_member("alice", project="a", role=EDITOR_ROLE)
        as_editor = store.get_member_role("alice", project="a")
        to_owner = store.set_member("alice", project="a", role=OWNER_ROLE)
        as_owner = store.get_member_role("alice", project="a")
        again = store.set_member("alice", project="a", role=OWNER_ROLE)

        member = Membership(project="a", user="alice", role=OWNER_ROLE)
        assert (added, to_editor, to_owner, again) == (True, False, False, False)
        assert (as_added, as_editor, as_owner) == (VIEWER_ROLE, EDITOR_ROLE, OWNER_ROLE)
        assert tuple(store.list_members(project="a")) == (member,)
        assert tuple(store.list_memberships("alice")) == (member,)

    def test_an_unknown_project_is_refused_before_an_unknown_user_and_nothing_stored(
        self, store: ExecutionStore
    ) -> None:
        """Naming neither is answered with the project, and the store never
        makes the project or the user itself."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_project("a", created_at=_ACCESS_AT)

        with pytest.raises(UnknownProjectError):
            store.set_member("nobody", project="nope", role=EDITOR_ROLE)
        with pytest.raises(UnknownProjectError):
            store.set_member("alice", project="nope", role=EDITOR_ROLE)
        with pytest.raises(UnknownUserError):
            store.set_member("nobody", project="a", role=EDITOR_ROLE)

        assert tuple(store.list_members(project="a")) == ()
        assert tuple(store.list_members(project="nope")) == ()
        assert tuple(store.list_memberships("alice")) == ()
        assert tuple(store.list_memberships("nobody")) == ()
        assert [project.name for project in store.list_projects()] == ["a", DEFAULT_PROJECT]
        assert [user.name for user in store.list_users()] == ["alice"]

    @pytest.mark.parametrize(
        ("project", "role", "refusal"),
        [
            (DEFAULT_PROJECT, EDITOR_ROLE, "default"),
            ("a", "admin", "role"),
            ("a", "Owner", "role"),
            ("nope", "admin", "role"),
        ],
        ids=["default", "not-a-role", "capitalised", "unknown-project"],
    )
    def test_default_and_a_role_no_row_may_hold_are_refused_before_anything_is_looked_up(
        self, store: ExecutionStore, project: str, role: str, refusal: str
    ) -> None:
        """So an unknown user or project gets the same refusal, and nothing
        is stored."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_project("a", created_at=_ACCESS_AT)

        for user in ("alice", "nobody"):
            with pytest.raises(ValueError, match=refusal):
                store.set_member(user, project=project, role=role)

        assert store.get_member_role("alice", project=project) is None
        assert tuple(store.list_members(project=project)) == ()
        assert tuple(store.list_memberships("alice")) == ()

    def test_removing_a_member_removes_that_row_alone(self, store: ExecutionStore) -> None:
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_user("bob", admin=False, created_at=_ACCESS_AT)
        store.create_project("a", created_at=_ACCESS_AT)
        store.create_project("b", created_at=_ACCESS_AT)
        store.set_member("alice", project="a", role=OWNER_ROLE)
        store.set_member("bob", project="a", role=VIEWER_ROLE)
        store.set_member("alice", project="b", role=EDITOR_ROLE)

        removed = store.remove_member("alice", project="a")
        again = store.remove_member("alice", project="a")

        assert (removed, again) == (True, False)
        assert store.get_member_role("alice", project="a") is None
        assert tuple(store.list_members(project="a")) == (
            Membership(project="a", user="bob", role=VIEWER_ROLE),
        )
        assert tuple(store.list_memberships("alice")) == (
            Membership(project="b", user="alice", role=EDITOR_ROLE),
        )

    @pytest.mark.parametrize(
        ("user", "project"),
        [("bob", "a"), ("nobody", "a"), ("alice", "nope"), ("alice", DEFAULT_PROJECT)],
        ids=["not-a-member", "unknown-user", "unknown-project", "default"],
    )
    def test_removing_someone_who_is_no_member_is_false_and_changes_nothing(
        self, store: ExecutionStore, user: str, project: str
    ) -> None:
        """`default` included: it has no rows to remove, so there is nothing
        to refuse."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_user("bob", admin=False, created_at=_ACCESS_AT)
        store.create_project("a", created_at=_ACCESS_AT)
        store.set_member("alice", project="a", role=EDITOR_ROLE)

        assert store.remove_member(user, project=project) is False
        assert tuple(store.list_members(project="a")) == (
            Membership(project="a", user="alice", role=EDITOR_ROLE),
        )

    def test_members_and_memberships_list_in_code_point_order(self, store: ExecutionStore) -> None:
        """A project's rows by user and a user's by project, each holding
        its own rows alone. Code point order, not a locale's: `-`, `.` and
        `_` sort by their code points, whatever the database collates by."""
        role_of = {
            "b": VIEWER_ROLE,
            "a_3": EDITOR_ROLE,
            "a.2": OWNER_ROLE,
            "a-1": VIEWER_ROLE,
            "0": EDITOR_ROLE,
        }
        for name in role_of:
            store.create_user(name, admin=False, created_at=_ACCESS_AT)
            store.create_project(name, created_at=_ACCESS_AT)
        for name, role in role_of.items():
            store.set_member(name, project="b", role=role)
            store.set_member("b", project=name, role=role)

        in_order = ["0", "a-1", "a.2", "a_3", "b"]
        assert tuple(store.list_members(project="b")) == tuple(
            Membership(project="b", user=name, role=role_of[name]) for name in in_order
        )
        assert tuple(store.list_memberships("b")) == tuple(
            Membership(project=name, user="b", role=role_of[name]) for name in in_order
        )
        assert tuple(store.list_members(project="a-1")) == (
            Membership(project="a-1", user="b", role=VIEWER_ROLE),
        )
        assert tuple(store.list_memberships("a-1")) == (
            Membership(project="b", user="a-1", role=VIEWER_ROLE),
        )

    def test_a_value_holding_nul_names_no_member_on_any_call(self, store: ExecutionStore) -> None:
        """No stored name or role holds U+0000, so a lookup by one matches
        nothing, a removal removes nothing and an addition names no project,
        no user or no role -- without the call failing, as binding the value
        would -- and the one row stored is left as it was."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_project("a", created_at=_ACCESS_AT)
        store.set_member("alice", project="a", role=EDITOR_ROLE)
        nul = "\x00"

        assert store.get_member_role("alice" + nul, project="a") is None
        assert store.get_member_role("alice", project="a" + nul) is None
        assert tuple(store.list_members(project="a" + nul)) == ()
        assert tuple(store.list_memberships("alice" + nul)) == ()
        assert store.remove_member("alice" + nul, project="a") is False
        assert store.remove_member("alice", project="a" + nul) is False
        for project in ("a" + nul, DEFAULT_PROJECT + nul):
            with pytest.raises(UnknownProjectError):
                store.set_member("alice", project=project, role=EDITOR_ROLE)
            with pytest.raises(UnknownProjectError):
                store.set_member("alice" + nul, project=project, role=EDITOR_ROLE)
        with pytest.raises(UnknownUserError):
            store.set_member("alice" + nul, project="a", role=EDITOR_ROLE)
        with pytest.raises(ValueError, match="role"):
            store.set_member("alice", project="a", role=EDITOR_ROLE + nul)

        assert tuple(store.list_members(project="a")) == (
            Membership(project="a", user="alice", role=EDITOR_ROLE),
        )
        assert tuple(store.list_memberships("alice")) == (
            Membership(project="a", user="alice", role=EDITOR_ROLE),
        )

    def test_a_disabled_users_and_a_demoted_admins_rows_stay(self, store: ExecutionStore) -> None:
        """Users are disabled, never deleted, and neither that nor a change
        of standing touches a member row: a disabled user's rows count again
        once they are enabled, and a former admin's are what they act with."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_user("root", admin=True, created_at=_ACCESS_AT)
        store.create_project("a", created_at=_ACCESS_AT)
        store.set_member("alice", project="a", role=EDITOR_ROLE)
        store.set_member("root", project="a", role=VIEWER_ROLE)

        store.update_user("alice", disabled=True)
        store.update_user("root", admin=False)
        while_disabled = store.get_member_role("alice", project="a")
        store.update_user("alice", disabled=False)

        assert while_disabled == EDITOR_ROLE
        assert tuple(store.list_members(project="a")) == (
            Membership(project="a", user="alice", role=EDITOR_ROLE),
            Membership(project="a", user="root", role=VIEWER_ROLE),
        )
        assert store.get_member_role("root", project="a") == VIEWER_ROLE

    def test_a_disabled_user_or_an_admin_is_made_a_member_like_anyone(
        self, store: ExecutionStore
    ) -> None:
        """A disabled user's row grants nothing while their tokens
        authenticate nothing, and applies once they are enabled; an admin's
        is what they act with if they stop being one."""
        store.create_user("alice", admin=False, created_at=_ACCESS_AT)
        store.create_user("root", admin=True, created_at=_ACCESS_AT)
        store.create_project("a", created_at=_ACCESS_AT)
        store.update_user("alice", disabled=True)

        disabled_added = store.set_member("alice", project="a", role=OWNER_ROLE)
        admin_added = store.set_member("root", project="a", role=EDITOR_ROLE)

        assert (disabled_added, admin_added) == (True, True)
        assert tuple(store.list_members(project="a")) == (
            Membership(project="a", user="alice", role=OWNER_ROLE),
            Membership(project="a", user="root", role=EDITOR_ROLE),
        )

    # -- Comparisons ---------------------------------------------------------
    #
    # A run is compared once, by the report that gives it its exit status,
    # with the latest complete run of its project before it, on its branch
    # first. `_finish` records a run in one report, which is its finish.

    def test_the_first_finished_run_is_compared_with_nothing(self, store: ExecutionStore) -> None:
        _finish(store, _compared_run(1, 0), {"test_a": "failed"})

        detail = store.get_run_detail(_rid(1))
        (entry,) = store.list_runs(project=DEFAULT_PROJECT, limit=10, offset=0).items

        assert detail is not None
        assert detail.comparison == entry.comparison == Comparison(state="none", baseline=None)
        assert store.count_changes([_rid(1)]) == {}
        assert store.list_changes(_rid(1), limit=10, offset=0).items == ()
        assert store.get_run_changes(_rid(1)) is None

    def test_a_run_whose_first_report_is_its_finish_is_compared_with_its_branchs_last(
        self, store: ExecutionStore
    ) -> None:
        _finish(
            store,
            _compared_run(1, 0),
            {
                "test_same": "passed",
                "test_breaks": "passed",
                "test_heals": "failed",
                "test_stays": "error",
                "test_goes": "passed",
            },
        )
        _finish(
            store,
            _compared_run(2, 5, exit_status=1),
            {
                "test_arrives": "skipped",
                "test_stays": "failed",
                "test_heals": "xfailed",
                "test_breaks": "error",
                "test_same": "passed",
            },
        )

        detail = store.get_run_detail(_rid(2))
        entries = {
            entry.execution.identity.value: entry.comparison
            for entry in store.list_runs(project=DEFAULT_PROJECT, limit=10, offset=0).items
        }
        changes = store.list_changes(_rid(2), limit=10, offset=0)

        assert detail is not None
        baseline = BaselineRef(run_id=_rid(1), started_at=_at(0), branch="main")
        assert detail.comparison == Comparison(state="branch", baseline=baseline)
        assert entries[_rid(2)] == detail.comparison
        assert entries[_rid(1)] == Comparison(state="none", baseline=None)
        assert changes.has_more is False
        assert [
            (
                entry.identity.node_id,
                entry.change,
                entry.outcome,
                entry.was,
                entry.position,
                entry.streak,
            )
            for entry in changes.items
        ] == [
            ("t.py::test_breaks", "new_failure", "error", "passed", 3, None),
            ("t.py::test_stays", "still_failing", "failed", "error", 1, Streak(2, _rid(1))),
            ("t.py::test_heals", "fixed", "xfailed", "failed", 2, None),
            ("t.py::test_arrives", "new_test", "skipped", None, 0, None),
            ("t.py::test_goes", "removed", None, "passed", None, None),
        ]
        assert changes.items[0].duration == _result("t.py::x").duration
        assert changes.items[-1].duration is None
        assert changes.items[-1].identity == _result("t.py::test_goes").identity
        assert store.count_changes([_rid(1), _rid(2)]) == {
            _rid(2): {
                "new_failure": 1,
                "still_failing": 1,
                "fixed": 1,
                "new_test": 1,
                "removed": 1,
            }
        }

    def test_a_run_prefers_its_branch_over_a_newer_run_on_another(
        self, store: ExecutionStore
    ) -> None:
        _finish(store, _compared_run(1, 0, branch="main"), {"test_a": "passed"})
        _finish(store, _compared_run(2, 1, branch="feat/x"), {"test_a": "passed"})
        _finish(store, _compared_run(3, 2, branch="main"), {"test_a": "passed"})

        assert _comparison_of(store, _rid(3)) == ("branch", _rid(1))

    def test_a_branch_with_no_earlier_complete_run_falls_back_to_the_project(
        self, store: ExecutionStore
    ) -> None:
        _finish(store, _compared_run(1, 0, branch="main"), {"test_a": "passed"})
        # A run of the branch that stopped early is passed over.
        _finish(
            store,
            _compared_run(2, 1, branch="feat/x", exit_status=1, reason="stopped after 1 failure"),
            {"test_a": "failed"},
        )
        _finish(store, _compared_run(3, 2, branch="feat/x"), {"test_a": "passed"})

        detail = store.get_run_detail(_rid(3))

        assert _comparison_of(store, _rid(3)) == ("project", _rid(1))
        assert detail is not None
        assert detail.comparison.baseline == BaselineRef(
            run_id=_rid(1), started_at=_at(0), branch="main"
        )

    def test_a_run_without_a_branch_is_compared_with_the_projects_latest(
        self, store: ExecutionStore
    ) -> None:
        _finish(store, _compared_run(1, 0, branch="main"), {"test_a": "passed"})
        _finish(store, _compared_run(2, 1, branch=None), {"test_a": "passed"})
        _finish(store, _compared_run(3, 2, repository=False), {"test_a": "passed"})

        assert _comparison_of(store, _rid(2)) == ("project", _rid(1))
        assert _comparison_of(store, _rid(3)) == ("project", _rid(2))

    def test_two_runs_without_a_branch_never_share_one(self, store: ExecutionStore) -> None:
        """Detached checkouts would otherwise compare with each other: a
        run without a branch takes the project's latest, whatever its
        branch."""
        _finish(store, _compared_run(1, 0, branch=None), {"test_a": "passed"})
        _finish(store, _compared_run(2, 1, branch="main"), {"test_a": "passed"})
        _finish(store, _compared_run(3, 2, branch=None), {"test_a": "passed"})
        _finish(store, _compared_run(4, 3, branch=None), {"test_a": "passed"})

        assert _comparison_of(store, _rid(3)) == ("project", _rid(2))
        assert _comparison_of(store, _rid(4)) == ("project", _rid(3))

    @pytest.mark.parametrize(
        "kind",
        [
            "running",
            "interrupted",
            "internal-error",
            "stopped-early",
            "exit-2-finished",
            "usage-error",
            "no-tests",
            "another-project",
            "started-later",
        ],
    )
    def test_only_an_earlier_complete_run_of_the_project_is_ever_a_baseline(
        self, store: ExecutionStore, kind: str
    ) -> None:
        """Running and abandoned runs have no exit status; the others ended
        without a verdict on every test, belong elsewhere, or started after
        the run compared."""
        store.create_project("firmware", created_at=_at(0))
        _finish(store, _compared_run(1, 0), {"test_a": "passed"})
        project = "firmware" if kind == "another-project" else DEFAULT_PROJECT
        minutes = 9 if kind == "started-later" else 1
        passed_over = {
            "running": _start_of(_compared_run(2, minutes)),
            "interrupted": _compared_run(
                2, minutes, exit_status=2, finished=False, interrupted=True, reason="ctrl-c"
            ),
            "internal-error": _compared_run(2, minutes, exit_status=3, finished=False),
            "stopped-early": _compared_run(2, minutes, exit_status=1, reason="-x"),
            "exit-2-finished": _compared_run(2, minutes, exit_status=2),
            "usage-error": _compared_run(2, minutes, exit_status=4),
            "no-tests": _compared_run(2, minutes, exit_status=5),
            "another-project": _compared_run(2, minutes),
            "started-later": _compared_run(2, minutes),
        }[kind]
        _finish(store, passed_over, {"test_a": "passed"}, project=project)

        _finish(store, _compared_run(3, 5), {"test_a": "passed"})

        assert _comparison_of(store, _rid(3)) == ("branch", _rid(1))

    def test_a_tie_on_started_at_is_settled_by_id(self, store: ExecutionStore) -> None:
        """The run list's own order: of runs started together, the one with
        the smaller id is the earlier."""
        _finish(store, _compared_run(2, 0), {"test_a": "passed"})
        _finish(store, _compared_run(1, 0), {"test_a": "passed"})
        _finish(store, _compared_run(3, 0), {"test_a": "passed"})

        assert _comparison_of(store, _rid(1)) == ("none", None)
        assert _comparison_of(store, _rid(3)) == ("branch", _rid(2))

    def test_reports_that_give_no_exit_status_compare_nothing(self, store: ExecutionStore) -> None:
        _finish(store, _compared_run(1, 0), {"test_a": "passed", "test_b": "passed"})
        running = _compared_run(2, 1)
        store.record_session(
            _start_of(running), results=(), received_at=_at(1), project=DEFAULT_PROJECT
        )
        store.record_session(
            _start_of(running),
            results=_tests({"test_a": "failed"}),
            received_at=_at(1),
            project=DEFAULT_PROJECT,
        )

        detail = store.get_run_detail(_rid(2))

        assert detail is not None
        assert detail.comparison == Comparison(state="pending", baseline=None)
        assert store.count_changes([_rid(2)]) == {}
        assert store.list_changes(_rid(2), limit=10, offset=0).items == ()
        assert store.get_run_changes(_rid(2)) is None
        assert store.get_result_change(_rid(2), node_id="t.py::test_a") == ResultChange(
            position=0, change=None, was=None, streak=None
        )

    def test_a_finish_after_slices_compares_every_result_the_run_holds(
        self, store: ExecutionStore
    ) -> None:
        _finish(store, _compared_run(1, 0), {"test_a": "passed", "test_b": "passed"})
        run = _compared_run(2, 1, exit_status=1)
        for outcomes in ({"test_a": "failed"}, {"test_b": "failed"}):
            store.record_session(
                _start_of(run),
                results=_tests(outcomes),
                received_at=_at(1),
                project=DEFAULT_PROJECT,
            )
        store.record_session(
            run, results=_tests({"test_c": "passed"}), received_at=_at(2), project=DEFAULT_PROJECT
        )

        assert _changes(store, _rid(2)) == [
            ("test_a", "new_failure", 0),
            ("test_b", "new_failure", 1),
            ("test_c", "new_test", 2),
        ]

    def test_an_older_run_finishing_later_never_moves_a_comparison(
        self, store: ExecutionStore
    ) -> None:
        _finish(store, _compared_run(1, 0), {"test_a": "passed"})
        older = _compared_run(2, 1)
        store.record_session(
            _start_of(older), results=(), received_at=_at(1), project=DEFAULT_PROJECT
        )
        _finish(store, _compared_run(3, 2), {"test_a": "failed"})
        before = _changes(store, _rid(3))

        _finish(store, older, {"test_a": "failed"})

        assert _comparison_of(store, _rid(3)) == ("branch", _rid(1))
        assert _changes(store, _rid(3)) == before == [("test_a", "new_failure", 0)]
        assert _comparison_of(store, _rid(2)) == ("branch", _rid(1))

    def test_an_older_run_recorded_late_is_compared_with_the_run_before_it(
        self, store: ExecutionStore
    ) -> None:
        """The order `vantage push` delivers queued runs in: the late run is
        compared among the runs stored then, and the runs compared meanwhile
        keep their baselines."""
        _finish(store, _compared_run(1, 0), {"test_a": "passed"})
        _finish(store, _compared_run(3, 2), {"test_a": "failed"})

        _finish(store, _compared_run(2, 1), {"test_a": "error"})

        assert _comparison_of(store, _rid(2)) == ("branch", _rid(1))
        assert _comparison_of(store, _rid(3)) == ("branch", _rid(1))
        assert _changes(store, _rid(3)) == [("test_a", "new_failure", 0)]

    def test_a_replayed_finish_changes_no_comparison(self, store: ExecutionStore) -> None:
        _finish(store, _compared_run(1, 0), {"test_a": "passed"})
        _finish(store, _compared_run(2, 1), {"test_a": "failed"})

        replayed = _finish(store, _compared_run(2, 1), {"test_a": "passed", "test_b": "failed"})

        assert replayed is False
        assert _comparison_of(store, _rid(2)) == ("branch", _rid(1))
        assert _changes(store, _rid(2)) == [("test_a", "new_failure", 0)]

    @pytest.mark.parametrize(
        ("ending", "missing"),
        [
            ({"exit_status": 0}, "removed"),
            ({"exit_status": 1}, "removed"),
            ({"exit_status": 5}, "removed"),
            ({"exit_status": 1, "reason": "-x"}, "not_reached"),
            ({"exit_status": 2}, "not_reached"),
            ({"exit_status": 2, "finished": False, "interrupted": True}, "not_reached"),
            ({"exit_status": 3, "finished": False}, "not_reached"),
            ({"exit_status": 4}, "not_reached"),
        ],
        ids=["exit-0", "exit-1", "exit-5", "stopped-early", "exit-2", "ctrl-c", "exit-3", "exit-4"],
    )
    def test_the_tests_a_run_lacks_are_removed_only_when_it_ran_to_its_end(
        self, store: ExecutionStore, ending: dict[str, Any], missing: str
    ) -> None:
        _finish(store, _compared_run(1, 0), {"test_a": "passed", "test_b": "failed"})

        _finish(store, _compared_run(2, 1, **ending), {"test_a": "passed"})

        assert _changes(store, _rid(2)) == [("test_b", missing, None)]
        assert store.count_changes([_rid(2)]) == {_rid(2): {missing: 1}}
        (entry,) = store.list_changes(_rid(2), limit=10, offset=0).items
        assert (entry.outcome, entry.was, entry.position, entry.duration) == (
            None,
            "failed",
            None,
            None,
        )

    def test_a_streak_counts_the_failing_runs_along_the_chain_of_baselines(
        self, store: ExecutionStore
    ) -> None:
        """The first run was compared with nothing, so the second's streak
        starts there; a run on another branch between them is no link of
        the chain."""
        for run in range(1, 5):
            if run == 3:
                _finish(store, _compared_run(9, 3, branch="feat/x"), {"test_a": "passed"})
            _finish(store, _compared_run(run, run * 2, exit_status=1), {"test_a": "failed"})

        streaks = [
            store.get_result_change(_rid(run), node_id="t.py::test_a") for run in range(1, 5)
        ]

        assert [None if found is None else found.streak for found in streaks] == [
            None,
            Streak(2, _rid(1)),
            Streak(3, _rid(1)),
            Streak(4, _rid(1)),
        ]
        (entry,) = store.list_changes(_rid(4), limit=10, offset=0).items
        assert entry.streak == Streak(4, _rid(1))

    def test_a_streak_starts_at_the_run_the_test_newly_failed_in(
        self, store: ExecutionStore
    ) -> None:
        _finish(store, _compared_run(1, 0), {"test_a": "passed"})
        _finish(store, _compared_run(2, 1, exit_status=1), {"test_a": "failed"})
        _finish(store, _compared_run(3, 2, exit_status=1), {"test_a": "error"})

        assert _changes(store, _rid(2)) == [("test_a", "new_failure", 0)]
        found = store.get_result_change(_rid(3), node_id="t.py::test_a")
        assert found == ResultChange(
            position=0, change="still_failing", was="failed", streak=Streak(2, _rid(2))
        )

    def test_a_runs_changes_are_listed_in_queue_order_and_filtered(
        self, store: ExecutionStore
    ) -> None:
        _finish(
            store,
            _compared_run(1, 0),
            {f"test_{i}": "failed" if i % 3 == 0 else "passed" for i in range(12)},
        )
        # Every third test was failing: 0, 3, 6 and 9 now pass, 1 and 7
        # newly fail, the rest keep their outcome; 12 and 13 are new.
        _finish(
            store,
            _compared_run(2, 1, exit_status=1),
            {f"test_{i}": "failed" if i in (1, 7, 12) else "passed" for i in range(14)},
        )

        everything = store.list_changes(_rid(2), limit=10, offset=0)
        fixed = store.list_changes(_rid(2), limit=10, offset=0, changes=["fixed"])
        several = store.list_changes(
            _rid(2), limit=10, offset=0, changes=["new_test", "fixed", "sideways"]
        )

        assert _names(everything.items) == [
            ("test_1", "new_failure"),
            ("test_7", "new_failure"),
            ("test_12", "new_failure"),
            ("test_0", "fixed"),
            ("test_3", "fixed"),
            ("test_6", "fixed"),
            ("test_9", "fixed"),
            ("test_13", "new_test"),
        ]
        assert _names(fixed.items) == [
            ("test_0", "fixed"),
            ("test_3", "fixed"),
            ("test_6", "fixed"),
            ("test_9", "fixed"),
        ]
        assert _names(several.items) == _names(everything.items)[3:]
        assert store.list_changes(_rid(2), limit=10, offset=0, changes=[]).items == ()
        assert store.list_changes(_rid(2), limit=10, offset=0, changes=["sideways"]).items == ()
        paged = store.list_changes(_rid(2), limit=3, offset=2)
        assert _names(paged.items) == _names(everything.items)[2:5]
        assert paged.has_more is True
        last = store.list_changes(_rid(2), limit=3, offset=6, changes=None)
        assert (_names(last.items), last.has_more) == (_names(everything.items)[6:], False)

    def test_a_page_of_changes_is_clamped(self, store: ExecutionStore) -> None:
        _finish(store, _compared_run(1, 0), {})
        _finish(store, _compared_run(2, 1), {f"test_{i:03}": "passed" for i in range(205)})

        first = store.list_changes(_rid(2), limit=MAX_PAGE_ITEMS + 50, offset=0)
        rest = store.list_changes(_rid(2), limit=MAX_PAGE_ITEMS + 50, offset=MAX_PAGE_ITEMS)

        assert (len(first.items), first.has_more) == (MAX_PAGE_ITEMS, True)
        assert (len(rest.items), rest.has_more) == (5, False)
        assert [entry.position for entry in (*first.items, *rest.items)] == list(range(205))

    def test_change_counts_batch_every_run_asked_and_leave_out_the_rest(
        self, store: ExecutionStore
    ) -> None:
        _finish(store, _compared_run(1, 0), {"test_a": "passed", "test_b": "passed"})
        _finish(store, _compared_run(2, 1), {"test_a": "passed", "test_b": "passed"})
        _finish(store, _compared_run(3, 2, exit_status=1), {"test_a": "failed", "test_c": "passed"})
        unknown = [_rid(1000 + i) for i in range(600)]

        counts = store.count_changes([*unknown, _rid(1), _rid(2), _rid(3), _rid(3)])

        # The first was compared with nothing, the second changed nothing.
        assert counts == {_rid(3): {"new_failure": 1, "new_test": 1, "removed": 1}}
        tallies: dict[str, int] = {}
        for entry in store.list_changes(_rid(3), limit=10, offset=0).items:
            tallies[entry.change] = tallies.get(entry.change, 0) + 1
        assert counts[_rid(3)] == tallies
        assert store.count_changes([]) == {}

    def test_a_runs_changed_positions_align_with_its_outcomes(self, store: ExecutionStore) -> None:
        _finish(store, _compared_run(1, 0), {"test_a": "failed", "test_b": "passed"})
        _finish(store, _compared_run(2, 1), {"test_a": "passed", "test_b": "passed"})
        _finish(
            store,
            _compared_run(3, 2, exit_status=1),
            {"test_c": "error", "test_b": "passed", "test_a": "passed", "test_d": "failed"},
        )

        unchanged = store.get_run_changes(_rid(2))
        positions = store.get_run_changes(_rid(3))
        outcomes = store.get_run_case_outcomes(_rid(3))

        assert unchanged == ((0, "fixed"),)
        assert positions == ((0, "new_failure"), (3, "new_failure"))
        assert positions is not None
        assert [outcomes[position][1] for position, _change in positions] == ["error", "failed"]
        assert store.get_run_changes(_rid(1)) is None
        assert store.get_run_changes(_rid(99)) is None

    def test_a_results_change_names_its_position_in_the_run(self, store: ExecutionStore) -> None:
        _finish(store, _compared_run(1, 0), {"test_a": "passed", "test_b": "passed"})
        _finish(
            store,
            _compared_run(2, 1, exit_status=1),
            {"test_c": "passed", "test_b": "passed", "test_a": "failed"},
        )
        stored = [result.identity.node_id for result in store.get_results(_rid(2))]

        found = {node_id: store.get_result_change(_rid(2), node_id=node_id) for node_id in stored}

        assert {node_id: change.position for node_id, change in found.items() if change} == {
            node_id: position for position, node_id in enumerate(stored)
        }
        assert found["t.py::test_a"] == ResultChange(
            position=2, change="new_failure", was="passed", streak=None
        )
        assert found["t.py::test_b"] == ResultChange(position=1, change=None, was=None, streak=None)
        assert found["t.py::test_c"] == ResultChange(
            position=0, change="new_test", was=None, streak=None
        )
        assert store.get_result_change(_rid(2), node_id="t.py::test_gone") is None
        assert store.get_result_change(_rid(1), node_id="t.py::test_a") == ResultChange(
            position=0, change=None, was=None, streak=None
        )

    def test_a_tests_history_carries_each_results_change(self, store: ExecutionStore) -> None:
        _finish(store, _compared_run(1, 0), {"test_a": "passed"})
        _finish(store, _compared_run(2, 1, exit_status=1), {"test_a": "failed"})
        _finish(store, _compared_run(3, 2, exit_status=1), {"test_a": "failed"})
        _finish(store, _compared_run(4, 3), {"test_a": "passed"})
        _finish(store, _compared_run(5, 4), {"test_a": "passed"})

        history = store.list_history(
            project=DEFAULT_PROJECT, node_id="t.py::test_a", limit=10, offset=0
        )

        assert [(entry.run_id, entry.change) for entry in history.items] == [
            (_rid(5), None),
            (_rid(4), "fixed"),
            (_rid(3), "still_failing"),
            (_rid(2), "new_failure"),
            (_rid(1), None),
        ]


class LocalDatabaseContract:
    """The contract of an adapter whose databases pytest-vantage's local
    store can make -- SQLite's and the in-memory double's, never
    PostgreSQL's. Inherit it beside `ExecutionStoreContract` and override
    `local_store` with a fresh adapter instance opened as the local store
    opens one."""

    @pytest.fixture
    def local_store(self) -> ExecutionStore:
        raise NotImplementedError("subclasses must override the `local_store` fixture")

    def test_a_database_the_local_store_made_gets_no_first_admin(
        self, local_store: ExecutionStore
    ) -> None:
        """It holds only its owner's runs, so it is served open, as it
        always was, until its first user is added by hand."""
        created = local_store.create_first_admin(
            "admin", password_hash=_HASH, created_at=_ACCESS_AT
        )

        assert created is None
        assert local_store.list_users() == ()
        assert local_store.access_required() is False


_ACCESS_AT = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


def _session_metadata(*pairs: tuple[str, str]) -> RunMetadata:
    """A run's metadata holding each `(key, value)` as the session reported
    it."""
    return RunMetadata(
        entries=tuple(
            MetadataEntry(
                key=key, value=value, source_file=None, status="captured", source="session"
            )
            for key, value in pairs
        )
    )


def _identified(node_id: str, file_path: str, class_name: str | None) -> Result:
    """A result of `node_id` decomposed into `file_path` and `class_name`,
    so which catalogue row a result reads shows in its identity."""
    identity = CaseIdentity(
        node_id=node_id,
        file_path=file_path,
        class_name=class_name,
        function_name=node_id.rsplit("::", 1)[-1],
        param_id=None,
    )
    return replace(_result(node_id), identity=identity)


def _token(
    store: ExecutionStore,
    user: str,
    token: str,
    *,
    scopes: set[str] | None = None,
) -> Token:
    """A token of `user` whose text is `token`, holding `scopes` or read and
    record."""
    return store.create_token(
        user,
        digest=token_digest(token),
        label="",
        scopes=frozenset(scopes or {READ_SCOPE, RECORD_SCOPE}),
        created_at=_ACCESS_AT,
    )


def _stored_hash(mark: str) -> str:
    """A password hash as a store is given one, told apart from others by
    `mark` and shaped as `core/domain/passwords.py` makes one. A store keeps
    a hash as it is and never checks it, so nothing here is hashed."""
    return f"$scrypt$ln=4,r=8,p=1${mark * 22}${mark * 43}"


_HASH = _stored_hash("A")
_OTHER_HASH = _stored_hash("B")
_THIRD_HASH = _stored_hash("C")


def _user(store: ExecutionStore, name: str, *, admin: bool = False) -> None:
    """The user `name`, whose password's hash is `_HASH`."""
    store.create_user(name, admin=admin, created_at=_ACCESS_AT)
    store.set_password(name, password_hash=_HASH, changed_at=_ACCESS_AT)


def _login(
    store: ExecutionStore,
    user: str,
    token: str,
    *,
    password_hash: str = _HASH,
    created_at: datetime = _ACCESS_AT,
    expires_at: datetime | None = None,
) -> Token | None:
    """A login of `user` whose token's text is `token`, its password checked
    against `password_hash`, at `created_at`, expiring
    `LOGIN_TOKEN_LIFETIME` later unless `expires_at` says when; or None if
    the store refuses it."""
    return store.create_login_token(
        user,
        password_hash=password_hash,
        digest=token_digest(token),
        created_at=created_at,
        expires_at=created_at + LOGIN_TOKEN_LIFETIME if expires_at is None else expires_at,
    )


def _logged_in(
    store: ExecutionStore,
    user: str,
    token: str,
    *,
    password_hash: str = _HASH,
    created_at: datetime = _ACCESS_AT,
    expires_at: datetime | None = None,
) -> Token:
    """The token of a `_login` the store must accept."""
    login = _login(
        store,
        user,
        token,
        password_hash=password_hash,
        created_at=created_at,
        expires_at=expires_at,
    )
    assert login is not None
    return login


# The comparisons' runs start a minute apart from here.
_COMPARED_AT = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc)


def _rid(number: int) -> str:
    """A run id, the larger the later a tie on `started_at` puts it."""
    return f"{number:032x}"


def _at(minutes: int) -> datetime:
    return _COMPARED_AT + timedelta(minutes=minutes)


def _compared_run(
    number: int,
    minutes: int,
    *,
    branch: str | None = "main",
    repository: bool = True,
    exit_status: int = 0,
    finished: bool = True,
    interrupted: bool = False,
    reason: str | None = None,
) -> Execution:
    """Run `number`'s finish: started `minutes` in, on `branch` -- none on a
    detached HEAD, and no repository at all without `repository` -- ending
    as the rest say."""
    started = _at(minutes)
    return Execution(
        identity=Identity(_rid(number)),
        started_at=started,
        finished_at=started + timedelta(seconds=30) if finished else None,
        exit_status=exit_status,
        interrupted=interrupted,
        interrupt_reason=reason,
        vcs=_vcs(branch=branch) if repository else None,
    )


def _start_of(execution: Execution) -> Execution:
    """The report a session sends before it ends: no exit status."""
    return replace(
        execution, finished_at=None, exit_status=None, interrupted=False, interrupt_reason=None
    )


def _tests(outcomes: dict[str, str]) -> tuple[Result, ...]:
    """A result per test of `t.py`, in the order given."""
    return tuple(_result(f"t.py::{name}", outcome=outcome) for name, outcome in outcomes.items())


def _finish(
    store: ExecutionStore,
    execution: Execution,
    outcomes: dict[str, str],
    *,
    project: str = DEFAULT_PROJECT,
) -> bool:
    """Record `execution` in one report holding every result."""
    return store.record_session(
        execution, results=_tests(outcomes), received_at=execution.started_at, project=project
    )


def _comparison_of(store: ExecutionStore, run_id: str) -> tuple[str, str | None]:
    """A run's comparison state and its baseline's id."""
    detail = store.get_run_detail(run_id)
    assert detail is not None
    baseline = detail.comparison.baseline
    return detail.comparison.state, None if baseline is None else baseline.run_id


def _names(entries: Sequence[ChangeEntry]) -> list[tuple[str, str]]:
    return [(entry.identity.function_name, entry.change) for entry in entries]


def _changes(store: ExecutionStore, run_id: str) -> list[tuple[str, str, int | None]]:
    """Every change a run's comparison stored, with its position."""
    page = store.list_changes(run_id, limit=MAX_PAGE_ITEMS, offset=0)
    return [(entry.identity.function_name, entry.change, entry.position) for entry in page.items]


def check_comparison(store: ExecutionStore, run_id: str) -> str | None:
    """Assert that `run_id`'s stored comparison is right for the baseline
    it names -- an earlier complete run of its project, on its own branch
    when the state says so -- and that its changes are exactly what
    `classify` makes of the two runs' results; return the baseline's id.
    For the concurrency tests, where which earlier run a finish sees depends
    on what committed first."""
    detail = store.get_run_detail(run_id)
    assert detail is not None
    comparison = detail.comparison
    listed = store.list_changes(run_id, limit=MAX_PAGE_ITEMS, offset=0)
    assert listed.has_more is False
    if comparison.baseline is None:
        assert comparison.state == ("pending" if detail.execution.exit_status is None else "none")
        assert listed.items == ()
        return None
    baseline_id = comparison.baseline.run_id
    baseline = store.get_execution(baseline_id)
    assert baseline is not None
    assert is_baseline_candidate(baseline)
    assert (baseline.started_at, baseline_id) < (detail.execution.started_at, run_id)
    assert store.get_run_detail(baseline_id).project == detail.project  # type: ignore[union-attr]
    if comparison.state == "branch":
        assert baseline.vcs is not None
        assert detail.execution.vcs is not None
        assert baseline.vcs.branch == detail.execution.vcs.branch
    was = {result.identity.node_id: result.outcome for result in store.get_results(baseline_id)}
    expected: list[tuple[str, str]] = []
    held: set[str] = set()
    for result in store.get_results(run_id):
        held.add(result.identity.node_id)
        change = classify(result.outcome, was.get(result.identity.node_id))
        if change is not None:
            expected.append((result.identity.node_id, change))
    missing = "removed" if ran_to_its_end(detail.execution) else "not_reached"
    expected.extend((node_id, missing) for node_id in was if node_id not in held)
    assert sorted((entry.identity.node_id, entry.change) for entry in listed.items) == sorted(
        expected
    )
    return baseline_id
