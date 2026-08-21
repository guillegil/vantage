"""Shared contract for any `ExecutionStore` implementation (RQ-30).

Never collected directly by pytest -- it is not named ``test_*`` -- and never
shipped in a wheel: the ``pytest`` import here would break ``vantage.core``'s
stdlib-only rule (RQ-26) if this module lived inside the package instead of
alongside the tests. Each adapter's own ``test_*_store.py`` subclasses
``ExecutionStoreContract``, provides a ``store`` fixture, and inherits every
test method unchanged -- that is what proves the core suite "passes
unchanged" against a second adapter (RQ-30.1). ``vantage.storage`` implements
this now (``test_memory_store.py``); ``vantage.storage.sqlite_store`` does
the same in PR5.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.result import CaseIdentity, Result
from vantage.core.ports.storage import MAX_PAGE_ITEMS, ExecutionStore


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
    """The shape a start-write reports (design.md D25/D32): `exit_status` is
    the only field never sent, `interrupted`/`interrupt_reason` are their
    defaults because nothing is yet known about how the session will end."""
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
    # `True` by default, deliberately. With `False`, both branches of the
    # conflict clause's `CASE WHEN excluded.vcs_commit_subject IS NOT NULL`
    # return the same value, so replacing the whole CASE with a bare
    # `excluded.vcs_commit_subject_truncated` left every test green while
    # producing a subject stored whole and flagged as untruncated -- a false
    # zero the server cannot detect. Found by mutation, 2026-08-20.
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
    )


class ExecutionStoreContract:
    """Inherit this and override the `store` fixture with a fresh adapter instance."""

    @pytest.fixture
    def store(self) -> ExecutionStore:
        raise NotImplementedError("subclasses must override the `store` fixture")

    @pytest.mark.req(id="RQ-30")
    def test_first_write_creates_a_row(self, store: ExecutionStore) -> None:
        execution = _execution("a" * 32)

        created = store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc)
        )

        assert created is True
        assert store.count_executions() == 1

    @pytest.mark.req(id="RQ-30")
    def test_replaying_the_same_id_reports_no_new_row(self, store: ExecutionStore) -> None:
        execution = _execution("b" * 32)
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

        created_again = store.record_session(
            execution, results=(), received_at=datetime.now(timezone.utc)
        )

        assert created_again is False
        assert store.count_executions() == 1

    @pytest.mark.req(id="RQ-30")
    def test_get_execution_returns_what_was_stored(self, store: ExecutionStore) -> None:
        execution = _execution("c" * 32, finished=False)
        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

        found = store.get_execution(execution.identity.value)

        assert found == execution

    @pytest.mark.req(id="RQ-30")
    def test_get_execution_returns_none_for_an_unknown_id(self, store: ExecutionStore) -> None:
        assert store.get_execution("d" * 32) is None

    @pytest.mark.req(id="RQ-30")
    def test_recording_a_session_with_results_persists_both(self, store: ExecutionStore) -> None:
        execution = _execution("e" * 32)
        results = (_result("t.py::test_a"), _result("t.py::test_b"))

        created = store.record_session(
            execution, results=results, received_at=datetime.now(timezone.utc)
        )

        assert created is True
        assert store.count_results() == 2

    @pytest.mark.req(id="RQ-41")
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

    @pytest.mark.req(id="RQ-3")
    def test_finish_after_start_applies_in_full(self, store: ExecutionStore) -> None:
        """design.md D25, task 1.1: a finish-write following an accepted
        start-write for the same run id applies in full -- `exit_status`
        goes NULL -> int, and the finish's other fields and results land."""
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

    @pytest.mark.req(id="RQ-3")
    def test_reordered_start_after_finish_never_nulls_the_recorded_finish(
        self, store: ExecutionStore
    ) -> None:
        """design.md D25, task 1.2: `run-recording`'s 'A reordered
        start-write never nulls a recorded finish' -- the finish, its exit
        fields and its result rows survive a start-write arriving late."""
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

    @pytest.mark.req(id="RQ-41")
    def test_replayed_finish_is_a_no_op_first_finish_wins(self, store: ExecutionStore) -> None:
        """design.md D25, task 1.3: finish-after-finish (replay) is a no-op
        -- the first accepted finish wins, unchanged semantics (RQ-41),
        now expressed through the same discriminator as every other case."""
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

    @pytest.mark.req(id="RQ-30")
    def test_duplicate_start_after_start_is_a_no_op(self, store: ExecutionStore) -> None:
        """design.md D25, task 1.4: a second start-write for the same run id
        changes nothing -- `excluded.exit_status IS NULL` never satisfies
        the conflict `WHERE`, regardless of what `run.exit_status` holds."""
        identity = "1" + "3" * 31
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        first_start = _start_only_execution(identity, started=started)
        store.record_session(first_start, results=(), received_at=datetime.now(timezone.utc))

        second_start = _start_only_execution(identity, started=started + timedelta(seconds=5))
        store.record_session(second_start, results=(), received_at=datetime.now(timezone.utc))

        stored = store.get_execution(identity)
        assert stored == first_start

    @pytest.mark.req(id="RQ-30")
    def test_created_is_true_only_on_a_true_first_insert(self, store: ExecutionStore) -> None:
        """design.md D26, task 1.5: `record_session` returns True only for a
        true first insert. A finish applied over an existing start-only row,
        and a true duplicate, both return False -- `rowcount` can no longer
        answer this under `DO UPDATE`, so the adapter must probe first."""
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

    @pytest.mark.req(id="RQ-5")
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

    @pytest.mark.req(id="RQ-9")
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

    @pytest.mark.req(id="RQ-13")
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

    @pytest.mark.req(id="RQ-13")
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

    @pytest.mark.req(id="RQ-13")
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

    @pytest.mark.req(id="RQ-44")
    def test_touch_last_contact_is_monotonic_and_reports_unknown_runs(
        self, store: ExecutionStore
    ) -> None:
        """design.md D33, task 4.1: `touch_last_contact` advances on a known
        run (returns True); a second, earlier-or-equal contact leaves it
        unchanged (returns False, the monotonic guard); an unknown execution
        id also returns False -- the route disambiguates the two `False`
        cases itself, by calling `get_execution` (D33), not this method."""
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

        # A heartbeat touches the contact clock and NOTHING else. Without
        # this read-back the assertions above are satisfied by the return
        # value alone, and an adapter whose UPDATE also fabricated a
        # `finished_at` would pass every one of them -- verified by
        # mutation, which left the whole suite green. The store is the only
        # place that can say so, because the route never reads these fields
        # back and `Execution` is what the client reported, not what was
        # stored beside it.
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
        """design.md D48, task 4.1: a start-write carries a vcs snapshot; the
        finish-write that follows carries no `vcs` section at all
        (`vcs=None`). The conflict branch's per-column `COALESCE` (SQL) /
        `merged_over` (memory) must leave the previously-recorded vcs values
        untouched -- a report with no vcs data is not a report that nulls
        the vcs it does not carry."""
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
        """The case that discriminates a per-FIELD merge from a per-OBJECT one.

        Every other vcs contract test hands both writes the same snapshot or
        no snapshot at all, and under either of those a whole-object swap and
        a per-column `COALESCE` agree. So `merged_over` shipped correct but
        unguarded: reverting it to
        `stored.vcs if execution.vcs is None else execution.vcs` left all 326
        tests green while the two adapters silently diverged. Found by
        mutation, 2026-08-20.

        The asymmetry is not contrived. A detached HEAD yields a snapshot with
        a null branch, and a repository with no commits yields one with a null
        commit and a null subject -- so a report whose snapshot is a strict
        subset of an earlier one is what git itself produces.
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

    def test_identical_vcs_snapshots_across_start_and_finish_apply_once(
        self, store: ExecutionStore
    ) -> None:
        """design.md D47, task 4.2: both reports of one session carry the
        identical vcs snapshot, so the upsert is idempotent -- the finish's
        (identical) snapshot applying over the start's own is a no-op, not a
        divergence."""
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
        """design.md D48, task 4.3: a start-write arriving after the finish
        fails the row-level `exit_status` guard and changes nothing -- vcs
        columns included, same as every other field."""
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
        """design.md D48, task 4.5: an absent `vcs` section and a section
        whose five value fields are all null both read back as
        `execution.vcs is None`, in every adapter -- proven independently of
        `_to_execution`'s own normalisation (which never runs in this
        contract test) so the two normalisation rules cannot drift apart."""
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

    @pytest.mark.req(id="RQ-23")
    def test_absent_repository_run_is_retrievable_in_storage(self, store: ExecutionStore) -> None:
        """design.md D48, task 4.9. Storage-level regression guard, kept
        alongside the route-level demonstration `read-api` now provides
        (`test_list_runs_includes_absent_repository_run_undistinguished`,
        `test_absent_repository_run_appears_in_list_undistinguished`): a
        deferral this docstring used to name -- "awaiting `read-api`", "not
        claimed as met until a run list exists" -- is retired, because a run
        list exists now. Verified here at the storage level via
        `count_executions` and `get_execution`; renamed in verify round 1
        (SUGGESTION-3) after its "pending a run list" name outlived the
        deferral it described."""
        identity = "2" + "6" * 31
        execution = _execution(identity, vcs=None)

        store.record_session(execution, results=(), received_at=datetime.now(timezone.utc))

        assert store.count_executions() == 1
        found = store.get_execution(identity)
        assert found is not None
        assert found.vcs is None

    # -- list_runs / get_run_detail (read-api Phase 2, design.md D57-D61) --

    def test_list_runs_orders_newest_first_with_total_tiebreak(self, store: ExecutionStore) -> None:
        """design.md D61: two runs sharing one `started_at`; `id DESC`
        breaks the tie so the order is total, not merely partial -- a page
        boundary can never fall inside the tie group."""
        started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
        store.record_session(_execution("a" * 32, started=started), results=(), received_at=started)
        store.record_session(_execution("b" * 32, started=started), results=(), received_at=started)

        page = store.list_runs(limit=10, offset=0)

        assert [entry.execution.identity.value for entry in page.items] == [
            "b" * 32,
            "a" * 32,
        ]

    def test_list_runs_caps_at_200_items(self, store: ExecutionStore) -> None:
        """history-read-api -> Bounded pagination: a list response never
        exceeds 200 items, even when the caller asks for more."""
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
        """history-read-api -> Bounded pagination: the more-items flag
        distinguishes exhaustion (exactly 200 stored) from truncation (201
        stored) -- both drawn from the same fetch, never a second query."""
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
        """history-read-api -> Bounded pagination: a caller-requested page
        size under the cap is honored, not silently rounded up to 200."""
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
        """version-control-context -> Absent repository: a run recorded
        outside a repository appears in the run list at its ordinary
        chronological position, `vcs is None`, no positional distinction
        from a run recorded inside one -- the criterion `version-control-
        context` deferred to this change by name."""
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
        """history-read-api -> Lean list projections: the commit subject is
        bounded in list responses -- proves the SQL projection agrees with
        `project_vcs` (design.md D57, D60)."""
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
        """history-read-api -> Lean list projections: the truncation flag
        never surfaces independently of its subject -- the other input to
        the disjunction, at adapter level (design.md D60)."""
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

    def test_list_runs_null_subject_flag_is_false_not_null(self, store: ExecutionStore) -> None:
        """design.md D60's `COALESCE` edge case: a run with
        `vcs_commit_subject IS NULL` reports `commit_subject_truncated is
        False`, never `None`."""
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
        """design.md D58, D59: `get_run_detail` is the lean-list rule's
        complement -- the full record stays reachable at the whole stored
        subject, with `commit_subject_truncated` reflecting only
        capture-time truncation, unchanged meaning."""
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

    # -- list_results / list_history (read-api Phase 3, design.md D57-D63) --

    def test_list_history_orders_newest_first_with_full_vcs(self, store: ExecutionStore) -> None:
        """history-read-api -> Test history: executions return newest first,
        and every entry carries its full VCS context -- commit, branch,
        commit subject, truncation flag, dirty flag -- and its duration."""
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
        """history-read-api -> Test history: an unknown test identity yields
        an empty history, not an error."""
        page = store.list_history(node_id="t.py::test_never_ran", limit=10, offset=0)

        assert page.items == ()
        assert page.has_more is False

    def test_list_history_null_vcs_entry_present_not_omitted(self, store: ExecutionStore) -> None:
        """history-read-api -> Test history: an execution recorded outside a
        git repository is present in the history with a null VCS context,
        not omitted."""
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
        """history-read-api -> Bounded pagination, reused for the history
        endpoint: the same 200/201 clamp and `has_more` transition as
        `list_runs`."""
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
        """design.md D57: `list_results` is the paginated sibling of
        `get_results` -- respects `limit`/`offset`/`has_more` over one run's
        results."""
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
