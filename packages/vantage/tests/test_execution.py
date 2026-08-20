"""`Identity` validation and `Execution`'s frozen, nullable shape (design.md, Interfaces).

`VcsContext.merged_over` (design.md D48) is the in-memory mirror of the
storage adapters' per-column `COALESCE`: null -> value only, never value ->
null. It is proven here, independently of either storage adapter, before
Phase 4 wires it into `memory.py`'s conflict branch.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest
from vantage.core.domain.execution import Execution, Identity, VcsContext


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


def test_vcs_context_merged_over_none_previous_returns_self_unchanged() -> None:
    incoming = VcsContext(
        commit="a" * 40,
        branch="main",
        commit_subject="Fix the thing",
        commit_subject_truncated=False,
        dirty=True,
        root="/repo",
    )

    merged = incoming.merged_over(None)

    assert merged == incoming


def test_vcs_context_merged_over_a_partial_incoming_snapshot_keeps_prior_fields() -> None:
    """A detached HEAD / no-commits report: some fields null, others not.
    The previous, fuller snapshot must fill exactly the null fields -- never
    the other way around (design.md D48's own "must not clobber" wording).
    """
    previous = VcsContext(
        commit="a" * 40,
        branch="main",
        commit_subject="Old subject",
        commit_subject_truncated=False,
        dirty=False,
        root="/repo",
    )
    incoming = VcsContext(
        commit="b" * 40,  # non-null: this DOES overwrite
        branch=None,  # null: inherits from previous
        commit_subject=None,  # null: inherits from previous
        commit_subject_truncated=False,
        dirty=None,  # null: inherits from previous
        root=None,  # null: inherits from previous
    )

    merged = incoming.merged_over(previous)

    assert merged == VcsContext(
        commit="b" * 40,
        branch="main",
        commit_subject="Old subject",
        commit_subject_truncated=False,
        dirty=False,
        root="/repo",
    )


def test_vcs_context_merged_over_truncated_flag_travels_with_commit_subject() -> None:
    """The `CASE` in D48's SQL keys `vcs_commit_subject_truncated` to
    whether `excluded.vcs_commit_subject IS NOT NULL` -- the flag follows
    the subject it describes, not the other fields' own coalesce.
    """
    previous = VcsContext(
        commit="a" * 40,
        branch="main",
        commit_subject="A very long subject that got cut",
        commit_subject_truncated=True,
        dirty=False,
        root="/repo",
    )

    # incoming has no subject at all: the flag must come FROM previous too,
    # travelling with the subject it was cut for -- not left at its own
    # (default, non-truncated) value.
    incoming_without_subject = VcsContext(
        commit=None,
        branch=None,
        commit_subject=None,
        commit_subject_truncated=False,
        dirty=None,
        root=None,
    )

    merged = incoming_without_subject.merged_over(previous)

    assert merged.commit_subject == "A very long subject that got cut"
    assert merged.commit_subject_truncated is True

    # incoming HAS a (short, untruncated) subject: the flag follows THAT
    # subject, even though previous's subject was truncated.
    incoming_with_subject = VcsContext(
        commit=None,
        branch=None,
        commit_subject="Short",
        commit_subject_truncated=False,
        dirty=None,
        root=None,
    )

    merged_with_subject = incoming_with_subject.merged_over(previous)

    assert merged_with_subject.commit_subject == "Short"
    assert merged_with_subject.commit_subject_truncated is False


def test_execution_vcs_defaults_to_none_for_every_existing_construction_site() -> None:
    """`Execution.vcs` is appended with a default so every pre-existing
    construction site -- three test doubles broke this way in Phase 2 --
    keeps working unmodified (design.md D48, tasks.md 3.6)."""
    execution = Execution(
        identity=Identity("a" * 32),
        started_at=datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc),
        finished_at=None,
        exit_status=None,
        interrupted=False,
        interrupt_reason=None,
    )

    assert execution.vcs is None
