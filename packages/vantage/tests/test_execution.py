"""`Identity` validation and `Execution`'s frozen, nullable shape (design.md, Interfaces)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest
from vantage.core.domain.execution import Execution, Identity


def test_identity_accepts_32_lowercase_hex_characters() -> None:
    identity = Identity("a" * 32)

    assert identity.value == "a" * 32


@pytest.mark.parametrize(
    "bad_value",
    [
        "too-short",
        "A" * 32,  # uppercase is rejected
        "g" * 32,  # not a hex digit
        "a" * 31,  # one char short
        "a" * 33,  # one char over
        "",
    ],
)
def test_identity_rejects_anything_but_32_lowercase_hex_characters(bad_value: str) -> None:
    with pytest.raises(ValueError, match="32 lowercase hex"):
        Identity(bad_value)


def test_execution_finished_at_is_nullable_for_an_interrupted_session() -> None:
    execution = Execution(
        identity=Identity("a" * 32),
        started_at=datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc),
        finished_at=None,
        exit_status=2,
        interrupted=True,
        interrupt_reason="ctrl-c",
    )

    assert execution.finished_at is None


def test_execution_finished_at_is_set_for_an_orderly_session() -> None:
    started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 8, 15, 9, 0, 5, tzinfo=timezone.utc)
    execution = Execution(
        identity=Identity("b" * 32),
        started_at=started,
        finished_at=finished,
        exit_status=0,
        interrupted=False,
        interrupt_reason=None,
    )

    assert execution.finished_at == finished


def test_execution_is_frozen() -> None:
    execution = Execution(
        identity=Identity("a" * 32),
        started_at=datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 15, 9, 0, 5, tzinfo=timezone.utc),
        exit_status=0,
        interrupted=False,
        interrupt_reason=None,
    )

    with pytest.raises(FrozenInstanceError):
        execution.exit_status = 1  # type: ignore[misc]
