"""`resolve_failure_text_capture`: the opt-in's composition rule. Capture is
absent unless requested: `--vantage-failure-text` is the only means by which
an already-activated session gains capture, and no committed configuration
file can enable it -- the same invariant `--vantage` holds for recording
itself.
"""

from __future__ import annotations

import inspect
import itertools

import pytest
import pytest_vantage.config as config_module
from pytest_vantage.config import resolve_failure_text_capture, resolve_metadata_capture

_BOOL_COMBINATIONS = list(itertools.product([False, True], repeat=2))


@pytest.mark.parametrize(("activated", "cli_opt_in"), _BOOL_COMBINATIONS)
def test_resolve_failure_text_capture_is_monotone_decreasing_in_activation(
    activated: bool, cli_opt_in: bool
) -> None:
    """For every input combination, `resolve(...) <= activated` -- the
    opt-in cannot turn a session on when recording itself was never
    activated. Compared as `int` because
    `bool <= bool` already means this in Python, but the comparison is
    written this way to make the property itself, not an implicit
    truthiness coincidence, the thing under test."""
    resolved = resolve_failure_text_capture(activated=activated, cli_opt_in=cli_opt_in)

    assert int(resolved) <= int(activated)


@pytest.mark.parametrize("activated", [False, True])
def test_resolve_failure_text_capture_is_monotone_increasing_in_cli_opt_in(activated: bool) -> None:
    """The opt-in is monotone INCREASING in `cli_opt_in` -- turning it on can
    only ever ADD capture, never remove it. `resolve(..., cli_opt_in=True)
    >= resolve(..., cli_opt_in=False)` for every fixed `activated`."""
    resolved_false = resolve_failure_text_capture(activated=activated, cli_opt_in=False)
    resolved_true = resolve_failure_text_capture(activated=activated, cli_opt_in=True)

    assert int(resolved_true) >= int(resolved_false)


def test_resolve_failure_text_capture_true_only_when_activated_and_cli_opt_in() -> None:
    """The exhaustive truth table: only `activated=True, cli_opt_in=True`
    resolves `True` -- capture is absent by default."""
    assert resolve_failure_text_capture(activated=False, cli_opt_in=False) is False
    assert resolve_failure_text_capture(activated=False, cli_opt_in=True) is False
    assert resolve_failure_text_capture(activated=True, cli_opt_in=False) is False
    assert resolve_failure_text_capture(activated=True, cli_opt_in=True) is True


def test_no_opt_in_anywhere_leaves_an_activated_session_without_capture() -> None:
    """The default state: recording activated, the opt-in not given, capture
    stays absent."""
    assert resolve_failure_text_capture(activated=True, cli_opt_in=False) is False


def test_no_environment_variable_surface_exists_for_the_opt_in() -> None:
    """An environment variable is invisible in the invocation -- for an
    opt-in that means a run whose stored evidence appears with nothing in
    its own history to explain why. `resolve_failure_text_capture`'s
    signature has no environment parameter and no ini parameter, and
    `pytest_vantage.config` does not import `os` at all (the server
    address's `VANTAGE_SERVER` is read in `plugin.py`, a different
    surface)."""
    parameters = set(inspect.signature(resolve_failure_text_capture).parameters)

    assert parameters == {"activated", "cli_opt_in"}

    source = inspect.getsource(resolve_failure_text_capture)
    assert "os.environ" not in source
    assert not hasattr(config_module, "os"), (
        "pytest_vantage.config must not import os at all for the opt-in to have "
        "nowhere to reach an environment variable from"
    )


# --- `resolve_metadata_capture` ----------------------------------------------
#
# The same monotone conjunction `resolve_failure_text_capture` proved above,
# for the metadata capture opt-in. The signature carries no ini parameter and
# no environment parameter, by construction -- a committed configuration
# file can never enable capture.


@pytest.mark.parametrize(("activated", "cli_opt_in"), _BOOL_COMBINATIONS)
def test_resolve_metadata_capture_is_monotone_decreasing_in_activation(
    activated: bool, cli_opt_in: bool
) -> None:
    """For every input combination, `resolve(...) <= activated` -- the
    metadata opt-in cannot turn a session on when recording itself was never
    activated."""
    resolved = resolve_metadata_capture(activated=activated, cli_opt_in=cli_opt_in)

    assert int(resolved) <= int(activated)


@pytest.mark.parametrize("activated", [False, True])
def test_resolve_metadata_capture_is_monotone_increasing_in_cli_opt_in(activated: bool) -> None:
    """The opt-in is monotone INCREASING in `cli_opt_in` -- turning it on can
    only ever ADD capture, never remove it. `resolve(..., cli_opt_in=True)
    >= resolve(..., cli_opt_in=False)` for every fixed `activated`."""
    resolved_false = resolve_metadata_capture(activated=activated, cli_opt_in=False)
    resolved_true = resolve_metadata_capture(activated=activated, cli_opt_in=True)

    assert int(resolved_true) >= int(resolved_false)


def test_resolve_metadata_capture_true_only_when_activated_and_cli_opt_in() -> None:
    """The exhaustive truth table: only `activated=True, cli_opt_in=True`
    resolves `True` -- capture is absent by default."""
    assert resolve_metadata_capture(activated=False, cli_opt_in=False) is False
    assert resolve_metadata_capture(activated=False, cli_opt_in=True) is False
    assert resolve_metadata_capture(activated=True, cli_opt_in=False) is False
    assert resolve_metadata_capture(activated=True, cli_opt_in=True) is True


def test_no_opt_in_anywhere_leaves_an_activated_session_without_metadata_capture() -> None:
    """The default state: recording activated, the metadata opt-in not
    given, capture stays absent."""
    assert resolve_metadata_capture(activated=True, cli_opt_in=False) is False


def test_no_environment_variable_surface_exists_for_the_metadata_opt_in() -> None:
    """No ini parameter, no environment-variable parameter -- the invocation
    flag is the only means, by construction, as for
    `resolve_failure_text_capture` above."""
    parameters = set(inspect.signature(resolve_metadata_capture).parameters)

    assert parameters == {"activated", "cli_opt_in"}

    source = inspect.getsource(resolve_metadata_capture)
    assert "os.environ" not in source
