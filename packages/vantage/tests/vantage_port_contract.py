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
from vantage.core.domain.execution import Execution, Identity
from vantage.core.domain.result import CaseIdentity, Result
from vantage.core.ports.storage import ExecutionStore


def _execution(hex_id: str, *, finished: bool = True, started: datetime | None = None) -> Execution:
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
    )


def _start_only_execution(hex_id: str, *, started: datetime | None = None) -> Execution:
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
