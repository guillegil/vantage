"""`derive_presentation`'s precedence.

Stdlib `datetime` only, no I/O -- `derive_presentation` is a pure function,
so nothing here needs a server, a socket, or a temporary file.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vantage.core.domain.execution import Execution, Identity
from vantage.core.domain.liveness import derive_presentation

_STARTED = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
_GRACE = timedelta(minutes=15)


def _execution(
    *, finished: bool = False, interrupted: bool = False, exit_status: int | None = None
) -> Execution:
    return Execution(
        identity=Identity("a" * 32),
        started_at=_STARTED,
        finished_at=_STARTED + timedelta(seconds=5) if finished else None,
        exit_status=exit_status,
        interrupted=interrupted,
        interrupt_reason="ctrl-c" if interrupted else None,
    )


def test_a_finished_run_derives_as_finished_no_matter_how_stale() -> None:
    execution = _execution(finished=True, exit_status=0)
    now = _STARTED + timedelta(days=1)  # far past any grace period

    presentation = derive_presentation(execution, last_contact_at=_STARTED, now=now, grace=_GRACE)

    assert presentation == "finished"


def test_an_interrupted_run_derives_as_interrupted_before_the_clock_is_consulted() -> None:
    """Checked before the clock, and not shadowed by the finished rule,
    because a Ctrl-C run carries `finished_at is None`."""
    execution = _execution(interrupted=True, exit_status=2)
    now = _STARTED + timedelta(days=1)

    presentation = derive_presentation(execution, last_contact_at=_STARTED, now=now, grace=_GRACE)

    assert presentation == "interrupted"


def test_a_run_past_its_grace_period_with_no_finish_or_interrupt_derives_as_abandoned() -> None:
    execution = _execution()
    last_contact_at = _STARTED + timedelta(minutes=5)
    now = last_contact_at + _GRACE + timedelta(seconds=1)

    presentation = derive_presentation(
        execution, last_contact_at=last_contact_at, now=now, grace=_GRACE
    )

    assert presentation == "abandoned"


def test_a_run_inside_its_grace_period_derives_as_running() -> None:
    execution = _execution()
    last_contact_at = _STARTED + timedelta(minutes=5)
    now = last_contact_at + _GRACE - timedelta(seconds=1)

    presentation = derive_presentation(
        execution, last_contact_at=last_contact_at, now=now, grace=_GRACE
    )

    assert presentation == "running"


def test_a_null_last_contact_falls_back_to_started_at() -> None:
    """A defensive branch -- unreachable for any row this code writes, kept
    for a row written by another adapter or edited by hand."""
    execution = _execution()

    fresh = derive_presentation(
        execution, last_contact_at=None, now=_STARTED + _GRACE - timedelta(seconds=1), grace=_GRACE
    )
    stale = derive_presentation(
        execution, last_contact_at=None, now=_STARTED + _GRACE + timedelta(seconds=1), grace=_GRACE
    )

    assert fresh == "running"
    assert stale == "abandoned"


def test_grace_is_measured_from_last_contact_not_from_start() -> None:
    """A run active well past its original start, with contact inside the
    configured grace period, still derives as running."""
    execution = _execution()
    last_contact_at = _STARTED + timedelta(hours=3)
    now = last_contact_at + _GRACE - timedelta(seconds=1)

    presentation = derive_presentation(
        execution, last_contact_at=last_contact_at, now=now, grace=_GRACE
    )

    assert presentation == "running"


def test_a_recorded_exit_status_is_never_abandoned_however_stale() -> None:
    """A run that reported is never abandoned, and `interrupted` alone does
    not say whether it reported.

    pytest's `INTERNAL_ERROR` (exit status 3) is the case that proves it: the
    recorder leaves `finished_at` null for it and sets `interrupted` only for
    status 2, yet a finish report did arrive, which rules abandonment out.
    """
    started = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    long_after = started + timedelta(hours=1)
    grace = timedelta(minutes=15)

    internal_error = Execution(
        identity=Identity("a" * 32),
        started_at=started,
        finished_at=None,
        exit_status=3,
        interrupted=False,
        interrupt_reason=None,
    )
    never_reported = Execution(
        identity=Identity("b" * 32),
        started_at=started,
        finished_at=None,
        exit_status=None,
        interrupted=False,
        interrupt_reason=None,
    )

    assert (
        derive_presentation(internal_error, last_contact_at=started, now=long_after, grace=grace)
        == "interrupted"
    )
    # The control: without an exit status nothing arrived, and staleness wins.
    assert (
        derive_presentation(never_reported, last_contact_at=started, now=long_after, grace=grace)
        == "abandoned"
    )
