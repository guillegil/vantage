"""Table-driven proof of `derive_presentation`'s precedence (design.md D34,
RQ-44).

Stdlib `datetime` only, no I/O -- `derive_presentation` is a pure function
with no caller yet (design.md D34), so nothing here needs a server, a
socket, or a temporary file.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from vantage.core.domain.execution import Execution, Identity
from vantage.core.domain.liveness import PRESENTATIONS, derive_presentation

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


@pytest.mark.req(id="RQ-44")
def test_a_finished_run_derives_as_finished_no_matter_how_stale() -> None:
    execution = _execution(finished=True, exit_status=0)
    now = _STARTED + timedelta(days=1)  # far past any grace period

    presentation = derive_presentation(execution, last_contact_at=_STARTED, now=now, grace=_GRACE)

    assert presentation == "finished"


@pytest.mark.req(id="RQ-44")
def test_an_interrupted_run_derives_as_interrupted_before_the_clock_is_consulted() -> None:
    """RQ-44.3: checked before the clock, and not shadowed by rule 1, because
    a Ctrl-C run carries `finished_at is None`."""
    execution = _execution(interrupted=True, exit_status=2)
    now = _STARTED + timedelta(days=1)

    presentation = derive_presentation(execution, last_contact_at=_STARTED, now=now, grace=_GRACE)

    assert presentation == "interrupted"


@pytest.mark.req(id="RQ-44")
def test_a_run_past_its_grace_period_with_no_finish_or_interrupt_derives_as_abandoned() -> None:
    execution = _execution()
    last_contact_at = _STARTED + timedelta(minutes=5)
    now = last_contact_at + _GRACE + timedelta(seconds=1)

    presentation = derive_presentation(
        execution, last_contact_at=last_contact_at, now=now, grace=_GRACE
    )

    assert presentation == "abandoned"


@pytest.mark.req(id="RQ-44")
def test_a_run_inside_its_grace_period_derives_as_running() -> None:
    execution = _execution()
    last_contact_at = _STARTED + timedelta(minutes=5)
    now = last_contact_at + _GRACE - timedelta(seconds=1)

    presentation = derive_presentation(
        execution, last_contact_at=last_contact_at, now=now, grace=_GRACE
    )

    assert presentation == "running"


@pytest.mark.req(id="RQ-44")
def test_a_null_last_contact_falls_back_to_started_at() -> None:
    """D27's defensive branch -- unreachable for any row this code writes,
    kept for a row written by another adapter or edited by hand."""
    execution = _execution()

    fresh = derive_presentation(
        execution, last_contact_at=None, now=_STARTED + _GRACE - timedelta(seconds=1), grace=_GRACE
    )
    stale = derive_presentation(
        execution, last_contact_at=None, now=_STARTED + _GRACE + timedelta(seconds=1), grace=_GRACE
    )

    assert fresh == "running"
    assert stale == "abandoned"


@pytest.mark.req(id="RQ-44")
def test_grace_is_measured_from_last_contact_not_from_start() -> None:
    """The 'Grace is measured from last contact, not start' scenario: a run
    active well past its original start, with contact inside the configured
    grace period, still derives as running."""
    execution = _execution()
    last_contact_at = _STARTED + timedelta(hours=3)
    now = last_contact_at + _GRACE - timedelta(seconds=1)

    presentation = derive_presentation(
        execution, last_contact_at=last_contact_at, now=now, grace=_GRACE
    )

    assert presentation == "running"


@pytest.mark.req(id="RQ-44")
def test_derive_presentation_returns_a_plain_str_never_an_enum() -> None:
    """design.md D34's correction: `PRESENTATIONS` is a module-level
    `frozenset`, never an `Enum` -- `class X(str, Enum)` formats differently
    between Python 3.10 and 3.13 (measured), and `vantage.core.domain.result`
    already established this precedent for `OUTCOMES`."""
    presentation = derive_presentation(
        _execution(finished=True, exit_status=0),
        last_contact_at=_STARTED,
        now=_STARTED,
        grace=_GRACE,
    )

    assert type(presentation) is str
    assert presentation in PRESENTATIONS


@pytest.mark.req(id="RQ-44")
def test_a_recorded_exit_status_is_never_abandoned_however_stale() -> None:
    """A run that reported is never abandoned, and `interrupted` alone does
    not say whether it reported.

    pytest's `INTERNAL_ERROR` (exit status 3) is the case that proves it: the
    recorder leaves `finished_at` null for it and sets `interrupted` only for
    status 2, so a session that DID send a finish report used to derive
    identically to one that was killed and never reported at all. The
    requirement's own rationale is that a report arriving is what rules
    abandonment out. Found by review, 2026-08-19.
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
