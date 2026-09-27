"""Shared contract for any `ExecutionStore` implementation.

Never collected directly by pytest -- it is not named ``test_*`` -- and kept
beside the tests rather than in the package, because ``vantage.core`` must not
import pytest. Each adapter's ``test_*_store.py`` subclasses
``ExecutionStoreContract``, provides ``store`` and ``stored_metadata``
fixtures, and inherits every test unchanged, so both adapters are held to the
same behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.metadata import MAX_METADATA_ENTRIES
from vantage.core.domain.projection import (
    LIST_COMMIT_SUBJECT_CHARS,
    LIST_FAILURE_MESSAGE_CHARS,
    project_failure,
    project_vcs,
)
from vantage.core.domain.result import CapturedOutput, CaseIdentity, FailureEvidence, Result
from vantage.core.ports.storage import (
    MAX_PAGE_ITEMS,
    ExecutionStore,
    MetadataEntry,
    MetadataFile,
    NamespaceFullError,
    RunMetadata,
    UserSetting,
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
            execution, results=(), received_at=datetime.now(timezone.utc)
        )

        assert created is True
        assert store.count_executions() == 1

    def test_replaying_the_same_id_reports_no_new_row(self, store: ExecutionStore) -> None:
        execution = _execution("b" * 32)
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

        created_again = store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc)
        )

        assert created_again is False
        assert store.count_executions() == 1

    def test_get_execution_returns_what_was_stored(self, store: ExecutionStore) -> None:
        execution = _execution("c" * 32, finished=False)
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

        found = store.get_execution(execution.identity.value)

        assert found == execution

    def test_get_execution_returns_none_for_an_unknown_id(self, store: ExecutionStore) -> None:
        assert store.get_execution("d" * 32) is None

    def test_recording_a_session_with_results_persists_both(self, store: ExecutionStore) -> None:
        execution = _execution("e" * 32)
        results = (_result("t.py::test_a"), _result("t.py::test_b"))

        created = store.record_session(
            execution, results=results, received_at=datetime.now(timezone.utc)
        )

        assert created is True
        assert store.count_results() == 2

    def test_replaying_the_same_report_does_not_duplicate_results(
        self, store: ExecutionStore
    ) -> None:
        execution = _execution("f" * 32)
        results = (_result("t.py::test_a"), _result("t.py::test_b"))
        store.record_session(execution, results=results, received_at=datetime.now(timezone.utc))

        replayed = store.record_session(
            execution, results=results, received_at=datetime.now(timezone.utc)
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
        store.record_session(start, results=(), received_at=datetime.now(timezone.utc))

        finish = _execution(identity, finished=True, started=started)
        results = (_result("t.py::test_a"),)
        store.record_session(finish, results=results, received_at=datetime.now(timezone.utc))

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
        store.record_session(finish, results=results, received_at=datetime.now(timezone.utc))

        late_start = _start_only_execution(identity, started=started)
        store.record_session(late_start, results=(), received_at=datetime.now(timezone.utc))

        stored = store.get_execution(identity)
        assert stored == finish
        assert store.count_results() == 1

    def test_replayed_finish_is_a_no_op_first_finish_wins(self, store: ExecutionStore) -> None:
        """A second finish for the same run is a no-op -- the first accepted
        finish wins."""
        identity = "1" + "2" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        first_finish = _execution(identity, finished=True, started=started)
        store.record_session(first_finish, results=(), received_at=datetime.now(timezone.utc))

        second_finish = Execution(
            identity=Identity(identity),
            started_at=started,
            finished_at=started + timedelta(seconds=104),
            exit_status=1,
            interrupted=True,
            interrupt_reason="a-different-reason",
        )
        store.record_session(second_finish, results=(), received_at=datetime.now(timezone.utc))

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
        store.record_session(start, results=(), received_at=started)
        store.record_session(start, results=(_result("t.py::test_a"),), received_at=started)

        store.record_session(
            _execution(identity, started=started),
            results=(_result("t.py::test_b"),),
            received_at=started,
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
        store.record_session(finish, results=(_result("t.py::test_a"),), received_at=started)

        store.record_session(finish, results=(_result("t.py::test_b"),), received_at=started)
        store.record_session(
            _start_only_execution(identity, started=started),
            results=(_result("t.py::test_c"),),
            received_at=started,
        )

        stored = [result.identity.node_id for result in store.get_results(identity)]
        assert stored == ["t.py::test_a"]
        assert store.get_catalogue_entry("t.py::test_b") is None
        assert store.get_catalogue_entry("t.py::test_c") is None

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
        store.record_session(finish, results=(), received_at=started, metadata=first)

        store.record_session(finish, results=(), received_at=started, metadata=later)

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
        store.record_session(first_start, results=(), received_at=datetime.now(timezone.utc))

        second_start = _start_only_execution(identity, started=started + timedelta(seconds=5))
        store.record_session(second_start, results=(), received_at=datetime.now(timezone.utc))

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
            start, results=(), received_at=datetime.now(timezone.utc)
        )
        assert created_by_start is True

        finish = _execution(identity, finished=True, started=started)
        created_by_finish = store.record_session(
            finish, results=(), received_at=datetime.now(timezone.utc)
        )
        assert created_by_finish is False

        created_by_duplicate = store.record_session(
            finish, results=(), received_at=datetime.now(timezone.utc)
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
        )

        entry_after_first = store.get_catalogue_entry(node_id)
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
        )

        entry_after_second = store.get_catalogue_entry(node_id)
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
        )

        older_execution = _execution(
            "7" + "0" * 31, started=later_execution.started_at - timedelta(days=1)
        )
        store.record_session(
            older_execution,
            results=(_result(node_id),),
            received_at=datetime.now(timezone.utc),
        )

        entry = store.get_catalogue_entry(node_id)
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
            short_job, results=(_result(node_id),), received_at=datetime.now(timezone.utc)
        )
        store.record_session(
            long_job, results=(_result(node_id),), received_at=datetime.now(timezone.utc)
        )

        entry = store.get_catalogue_entry(node_id)

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
        )
        entry_before = store.get_catalogue_entry(stable_node_id)
        assert entry_before is not None

        second_execution = _execution("9" + "2" * 31)
        store.record_session(
            second_execution,
            results=(_result(other_node_id),),
            received_at=datetime.now(timezone.utc),
        )

        entry_after = store.get_catalogue_entry(stable_node_id)
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
            store.record_session(execution, results=(result,), received_at=started)

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
        store.record_session(start, results=(), received_at=started)

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
        store.record_session(start, results=(), received_at=datetime.now(timezone.utc))

        finish = _execution(identity, finished=True, started=started, vcs=None)
        store.record_session(finish, results=(), received_at=datetime.now(timezone.utc))

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
        )

        # A strict subset: only the commit survives, as a detached-HEAD or
        # unborn-branch read would produce.
        partial = _vcs(branch=None, commit_subject=None, dirty=None, root=None)
        store.record_session(
            _execution(identity, finished=True, started=started, vcs=partial),
            results=(),
            received_at=datetime.now(timezone.utc),
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
        store.record_session(start, results=(), received_at=datetime.now(timezone.utc))

        finish = _execution(identity, finished=True, started=started, vcs=snapshot)
        store.record_session(finish, results=(), received_at=datetime.now(timezone.utc))

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
        store.record_session(finish, results=(), received_at=datetime.now(timezone.utc))

        late_start = _start_only_execution(identity, started=started, vcs=_vcs(commit="d" * 40))
        store.record_session(late_start, results=(), received_at=datetime.now(timezone.utc))

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
        store.record_session(absent, results=(), received_at=datetime.now(timezone.utc))
        store.record_session(all_null, results=(), received_at=datetime.now(timezone.utc))

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
            _execution("a" * 32, started=newer), results=(_result(node_id),), received_at=newer
        )
        store.record_session(
            _execution("b" * 32, started=older), results=(_result(node_id),), received_at=newer
        )

        runs = store.list_runs(limit=10, offset=0).items
        history = store.list_history(node_id=node_id, limit=10, offset=0).items
        entry = store.get_catalogue_entry(node_id)

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
        store.record_session(_execution("a" * 32, started=started), results=(), received_at=started)
        store.record_session(_execution("b" * 32, started=started), results=(), received_at=started)

        page = store.list_runs(limit=10, offset=0)

        assert [entry.execution.identity.value for entry in page.items] == [
            "b" * 32,
            "a" * 32,
        ]

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
            )

        page = store.list_runs(limit=MAX_PAGE_ITEMS * 5, offset=0)

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
            )

        exhausted = store.list_runs(limit=MAX_PAGE_ITEMS, offset=0)
        assert exhausted.has_more is False

        store.record_session(
            _execution(f"{MAX_PAGE_ITEMS:032x}", started=base + timedelta(seconds=MAX_PAGE_ITEMS)),
            results=(),
            received_at=base,
        )

        truncated = store.list_runs(limit=MAX_PAGE_ITEMS, offset=0)
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
            )

        page = store.list_runs(limit=3, offset=0)

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
        )
        store.record_session(
            _execution("b" * 32, started=base + timedelta(seconds=1), vcs=None),
            results=(),
            received_at=base,
        )
        store.record_session(
            _execution("c" * 32, started=base + timedelta(seconds=2), vcs=_vcs()),
            results=(),
            received_at=base,
        )

        page = store.list_runs(limit=10, offset=0)

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
        )

        page = store.list_runs(limit=10, offset=0)

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
        )

        page = store.list_runs(limit=10, offset=0)

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
        )

        (result_entry,) = store.list_results("a" * 32, limit=10, offset=0).items
        (run_entry,) = store.list_runs(limit=10, offset=0).items
        (history_entry,) = store.list_history(node_id="t.py::test_x", limit=10, offset=0).items

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
        )

        (result_entry,) = store.list_results("a" * 32, limit=10, offset=0).items
        (run_entry,) = store.list_runs(limit=10, offset=0).items
        (history_entry,) = store.list_history(node_id="t.py::test_x", limit=10, offset=0).items
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
        )

        (result_entry,) = store.list_results("a" * 32, limit=10, offset=0).items
        (run_entry,) = store.list_runs(limit=10, offset=0).items

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
        )

        page = store.list_runs(limit=10, offset=0)

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
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

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
        )
        store.record_session(
            _execution("b" * 32, started=newer, vcs=_vcs(branch="main", dirty=False)),
            results=(_result(node_id, duration=0.75),),
            received_at=newer,
        )

        page = store.list_history(node_id=node_id, limit=10, offset=0)

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

    def test_list_history_unknown_node_id_is_empty_not_error(self, store: ExecutionStore) -> None:
        """An unknown test identity yields an empty history, not an error."""
        page = store.list_history(node_id="t.py::test_never_ran", limit=10, offset=0)

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
        )

        page = store.list_history(node_id=node_id, limit=10, offset=0)

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
            )

        exhausted = store.list_history(node_id=node_id, limit=MAX_PAGE_ITEMS, offset=0)
        assert len(exhausted.items) == MAX_PAGE_ITEMS
        assert exhausted.has_more is False

        store.record_session(
            _execution(f"{MAX_PAGE_ITEMS:032x}", started=base + timedelta(seconds=MAX_PAGE_ITEMS)),
            results=(_result(node_id),),
            received_at=base,
        )

        truncated = store.list_history(node_id=node_id, limit=MAX_PAGE_ITEMS, offset=0)
        assert len(truncated.items) == MAX_PAGE_ITEMS
        assert truncated.has_more is True

    def test_list_results_paginates_a_runs_results(self, store: ExecutionStore) -> None:
        """`list_results` is the paginated sibling of `get_results` -- respects
        `limit`/`offset`/`has_more` over one run's results."""
        execution = _execution("a" * 32)
        results = tuple(_result(f"t.py::test_{i}") for i in range(5))
        store.record_session(execution, results=results, received_at=datetime.now(timezone.utc))

        page = store.list_results(execution.identity.value, limit=3, offset=0)
        assert len(page.items) == 3
        assert page.has_more is True

        next_page = store.list_results(execution.identity.value, limit=3, offset=3)
        assert len(next_page.items) == 2
        assert next_page.has_more is False

    def test_list_results_empty_for_a_run_with_no_results(self, store: ExecutionStore) -> None:
        execution = _execution("a" * 32)
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

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
        )

        (run_entry,) = store.list_runs(limit=10, offset=0).items
        (history_entry,) = store.list_history(node_id="t.py::test_x", limit=10, offset=0).items
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
        )

        found = store.get_result(execution.identity.value, node_id="t.py::test_x")

        assert found is not None
        assert found.identity.node_id == "t.py::test_x"
        assert found.failure == failure
        assert found.captured == captured

    def test_get_result_returns_none_for_unknown_node_id_miss(self, store: ExecutionStore) -> None:
        execution = _execution("a" * 32)
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

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
        )

        found = store.get_result(execution.identity.value, node_id="t.py::test_x")

        assert found is not None
        assert found.captured.stdout == ""
        assert found.captured.stdout_truncated is False
        assert found.captured.stderr is None

    # -- settings: namespaced persistence, create/replace/delete --

    def test_list_settings_is_empty_for_an_unknown_namespace(self, store: ExecutionStore) -> None:
        assert store.list_settings("test_sections") == ()

    def test_upsert_setting_creates_a_new_pair(self, store: ExecutionStore) -> None:
        now = datetime.now(timezone.utc)

        created = store.upsert_setting(
            "test_sections", "Billing", value='{"prefix": "tests/billing/"}', updated_at=now
        )

        assert created is True
        settings = store.list_settings("test_sections")
        assert len(settings) == 1
        assert settings[0] == UserSetting(
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
        store.upsert_setting("test_sections", "Billing", value="a", updated_at=first)

        replaced = store.upsert_setting("test_sections", "Billing", value="b", updated_at=second)

        assert replaced is False
        settings = store.list_settings("test_sections")
        assert len(settings) == 1
        assert settings[0].value == "b"

    def test_delete_setting_then_a_later_read_reports_it_absent(
        self, store: ExecutionStore
    ) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting("test_sections", "Billing", value="a", updated_at=now)

        deleted = store.delete_setting("test_sections", "Billing")

        assert deleted is True
        assert store.list_settings("test_sections") == ()

    def test_delete_setting_for_an_absent_key_reports_false(self, store: ExecutionStore) -> None:
        assert store.delete_setting("test_sections", "never-stored") is False

    def test_list_settings_orders_by_key(self, store: ExecutionStore) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting("test_sections", "Checkout", value="b", updated_at=now)
        store.upsert_setting("test_sections", "Billing", value="a", updated_at=now)

        settings = store.list_settings("test_sections")

        assert [setting.key for setting in settings] == ["Billing", "Checkout"]

    def test_list_settings_only_returns_its_own_namespace(self, store: ExecutionStore) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting("test_sections", "Billing", value="a", updated_at=now)
        store.upsert_setting("other_namespace", "Billing", value="b", updated_at=now)

        settings = store.list_settings("test_sections")

        assert len(settings) == 1
        assert settings[0].value == "a"

    def test_upsert_setting_refuses_a_new_key_at_max_keys_and_writes_nothing(
        self, store: ExecutionStore
    ) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting("test_sections", "Billing", value="a", updated_at=now)
        store.upsert_setting("test_sections", "Checkout", value="b", updated_at=now)

        with pytest.raises(NamespaceFullError):
            store.upsert_setting("test_sections", "Accounts", value="c", updated_at=now, max_keys=2)

        assert [setting.key for setting in store.list_settings("test_sections")] == [
            "Billing",
            "Checkout",
        ]

    def test_upsert_setting_replaces_an_existing_key_at_max_keys(
        self, store: ExecutionStore
    ) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting("test_sections", "Billing", value="a", updated_at=now)

        created = store.upsert_setting(
            "test_sections", "Billing", value="b", updated_at=now, max_keys=1
        )

        assert created is False
        assert [setting.value for setting in store.list_settings("test_sections")] == ["b"]

    def test_max_keys_counts_only_the_keys_of_its_own_namespace(
        self, store: ExecutionStore
    ) -> None:
        now = datetime.now(timezone.utc)
        store.upsert_setting("other_namespace", "Billing", value="a", updated_at=now)

        created = store.upsert_setting(
            "test_sections", "Billing", value="b", updated_at=now, max_keys=1
        )

        assert created is True

    def test_get_run_case_outcomes_is_empty_for_a_run_with_no_results(
        self, store: ExecutionStore
    ) -> None:
        execution = _execution("a" * 32)
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

        assert store.get_run_case_outcomes(execution.identity.value) == ()

    def test_get_run_case_outcomes_pairs_file_path_with_outcome(
        self, store: ExecutionStore
    ) -> None:
        execution = _execution("b" * 32)
        results = (
            _result("t.py::test_a", outcome="passed"),
            _result("t.py::test_b", outcome="failed"),
        )
        store.record_session(execution, results=results, received_at=datetime.now(timezone.utc))

        outcomes = store.get_run_case_outcomes(execution.identity.value)

        assert sorted(outcomes) == sorted([("t.py", "passed"), ("t.py", "failed")])

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
            execution, results=(), received_at=datetime.now(timezone.utc), metadata=metadata
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
            execution, results=(), received_at=datetime.now(timezone.utc), metadata=metadata
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
            execution, results=(), received_at=datetime.now(timezone.utc), metadata=first
        )

        store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc), metadata=second
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
            start, results=(), received_at=datetime.now(timezone.utc), metadata=metadata
        )
        finish = _execution(pair_identity, started=started)
        store.record_session(
            finish, results=(), received_at=datetime.now(timezone.utc), metadata=metadata
        )

        finish_only_identity = "7" * 32
        finish_only = _execution(finish_only_identity, started=started)
        store.record_session(
            finish_only, results=(), received_at=datetime.now(timezone.utc), metadata=metadata
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
            execution, results=(), received_at=datetime.now(timezone.utc), metadata=metadata
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
        )
        store.record_session(
            _execution(identity, started=started),
            results=(),
            received_at=started,
            metadata=_values(first[0], "b000", "b001", value="later"),
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

        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

        assert stored_metadata(execution.identity.value) == RunMetadata()

    # -- list_runs_with_metadata_horizon --

    def test_the_horizon_of_a_key_never_declared_is_every_run(self, store: ExecutionStore) -> None:
        base = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            store.record_session(
                _execution(f"{i:032x}", started=base + timedelta(minutes=i)),
                results=(),
                received_at=base,
            )

        page, predating = store.list_runs_with_metadata_horizon(
            filters=[("fw", "2.1")], limit=10, offset=0
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
            )
        store.record_session(
            _execution("a" * 32, started=base + timedelta(minutes=5)),
            results=(),
            received_at=base,
            metadata=_declaring(None, "value_too_large"),
        )
        store.record_session(
            _execution("b" * 32, started=base + timedelta(minutes=6)),
            results=(),
            received_at=base,
            metadata=_declaring("2.1", "captured"),
        )
        store.record_session(
            _execution("c" * 32, started=base + timedelta(minutes=7)),
            results=(),
            received_at=base,
            metadata=_declaring("2.1", "captured"),
        )

        page, predating = store.list_runs_with_metadata_horizon(
            filters=[("fw", "2.1")], limit=1, offset=0
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
            )

        page, predating = store.list_runs_with_metadata_horizon(
            filters=[("fw", "1.1.0"), ("fmc", "5.2.0"), ("fw", "1.1.0")], limit=10, offset=0
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
        )

        page, predating = store.list_runs_with_metadata_horizon(
            filters=[("fw", "2.1"), ("fw", "2.2")], limit=10, offset=0
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
            )

        page, predating = store.list_runs_with_metadata_horizon(filters=[], limit=10, offset=0)

        assert page == store.list_runs(limit=10, offset=0)
        assert predating == ()

    # -- get_run_metadata --

    def test_get_run_metadata_is_none_for_an_unknown_run(self, store: ExecutionStore) -> None:
        assert store.get_run_metadata("0" * 32) is None

    def test_get_run_metadata_is_empty_for_a_run_that_reported_none(
        self, store: ExecutionStore
    ) -> None:
        execution = _execution("5" * 32)
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

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
        )
        store.record_session(
            other,
            results=(),
            received_at=datetime.now(timezone.utc),
            metadata=RunMetadata(
                files=(MetadataFile(source_file="c.yaml", content_type="yaml", status="captured"),),
            ),
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
