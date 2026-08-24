"""`resolve_failure_text_capture`: the opt-out's composition rule (design.md
D72). A configuration value MAY narrow what an already-activated session
records; no committed configuration file MAY be the means by which capture
is enabled -- the same invariant RQ-2 already holds for recording itself,
restated here as a property rather than a case list.
"""

from __future__ import annotations

import inspect
import itertools

import pytest
import pytest_vantage.config as config_module
from pytest_vantage.config import resolve_failure_text_capture

_BOOL_COMBINATIONS = list(itertools.product([False, True], repeat=3))


@pytest.mark.parametrize(("activated", "cli_opt_out", "ini_opt_out"), _BOOL_COMBINATIONS)
def test_resolve_failure_text_capture_is_monotone_decreasing(
    activated: bool, cli_opt_out: bool, ini_opt_out: bool
) -> None:
    """design.md D72: for every one of the eight input combinations,
    `resolve(...) <= activated` -- no source can turn a `False` into a
    `True`. Compared as `int` because `bool <= bool` already means this in
    Python, but the comparison is written this way to make the property
    itself, not an implicit truthiness coincidence, the thing under test."""
    resolved = resolve_failure_text_capture(
        activated=activated, cli_opt_out=cli_opt_out, ini_opt_out=ini_opt_out
    )

    assert int(resolved) <= int(activated)


def test_resolve_failure_text_capture_true_only_when_activated_and_neither_opt_out() -> None:
    """Triangulates the property test above with the one concrete positive
    case: activation with no opt-out anywhere resolves `True`."""
    assert (
        resolve_failure_text_capture(activated=True, cli_opt_out=False, ini_opt_out=False) is True
    )


@pytest.mark.parametrize(
    ("cli_opt_out", "ini_opt_out"), [(True, False), (False, True), (True, True)]
)
def test_resolve_failure_text_capture_either_opt_out_narrows_an_activated_session(
    cli_opt_out: bool, ini_opt_out: bool
) -> None:
    """Either opt-out alone, or both together, narrows an activated session
    to `False` -- the conjunction is not "CLI wins", it is AND."""
    assert (
        resolve_failure_text_capture(
            activated=True, cli_opt_out=cli_opt_out, ini_opt_out=ini_opt_out
        )
        is False
    )


def test_no_environment_variable_surface_exists_for_the_opt_out() -> None:
    """design.md D72: an environment variable is invisible in the command
    line RQ-11 records -- for an opt-out that means a run whose stored
    evidence is missing with nothing in its own history to explain why. No
    parameter for one exists on `resolve_failure_text_capture`'s signature,
    and `pytest_vantage.config`'s source never references `os.environ` on
    the opt-out path (it does, deliberately, for `resolve_server_address`'s
    `VANTAGE_SERVER` -- a different surface, D6/D11, not this one)."""
    parameters = set(inspect.signature(resolve_failure_text_capture).parameters)

    assert parameters == {"activated", "cli_opt_out", "ini_opt_out"}

    source = inspect.getsource(resolve_failure_text_capture)
    assert "os.environ" not in source
    assert not hasattr(config_module, "os"), (
        "pytest_vantage.config must not import os at all for the opt-out to have "
        "nowhere to reach an environment variable from"
    )
