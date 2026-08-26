"""`resolve_failure_text_capture`: the opt-in's composition rule (design.md
D72, revised after Phase 9's RQ-25 measurement -- see spec.md's Measurements
paragraph and `docs/open-questions.md` OQ-11). Capture is now absent unless
requested: `--vantage-failure-text` is the only means by which an already-
activated session gains capture, and no committed configuration file MAY be
the means by which capture is enabled -- the same invariant RQ-2 already
holds for recording itself, restated here as a property rather than a case
list.
"""

from __future__ import annotations

import inspect
import itertools

import pytest
import pytest_vantage.config as config_module
from pytest_vantage.config import resolve_failure_text_capture

_BOOL_COMBINATIONS = list(itertools.product([False, True], repeat=2))


@pytest.mark.parametrize(("activated", "cli_opt_in"), _BOOL_COMBINATIONS)
def test_resolve_failure_text_capture_is_monotone_decreasing_in_activation(
    activated: bool, cli_opt_in: bool
) -> None:
    """design.md D72: for every one of the four input combinations,
    `resolve(...) <= activated` -- the opt-in cannot turn a session on when
    recording itself was never activated. Compared as `int` because
    `bool <= bool` already means this in Python, but the comparison is
    written this way to make the property itself, not an implicit
    truthiness coincidence, the thing under test."""
    resolved = resolve_failure_text_capture(activated=activated, cli_opt_in=cli_opt_in)

    assert int(resolved) <= int(activated)


@pytest.mark.parametrize("activated", [False, True])
def test_resolve_failure_text_capture_is_monotone_increasing_in_cli_opt_in(activated: bool) -> None:
    """design.md D72's revised polarity: unlike the old opt-out, which was
    monotone DECREASING in its narrowing sources, the opt-in is monotone
    INCREASING in `cli_opt_in` -- turning it on can only ever ADD capture,
    never remove it. `resolve(..., cli_opt_in=True) >=
    resolve(..., cli_opt_in=False)` for every fixed `activated`."""
    resolved_false = resolve_failure_text_capture(activated=activated, cli_opt_in=False)
    resolved_true = resolve_failure_text_capture(activated=activated, cli_opt_in=True)

    assert int(resolved_true) >= int(resolved_false)


def test_resolve_failure_text_capture_true_only_when_activated_and_cli_opt_in() -> None:
    """The exhaustive truth table: only `activated=True, cli_opt_in=True`
    resolves `True` -- capture is absent by default, not merely narrowable."""
    assert resolve_failure_text_capture(activated=False, cli_opt_in=False) is False
    assert resolve_failure_text_capture(activated=False, cli_opt_in=True) is False
    assert resolve_failure_text_capture(activated=True, cli_opt_in=False) is False
    assert resolve_failure_text_capture(activated=True, cli_opt_in=True) is True


def test_no_opt_in_anywhere_leaves_an_activated_session_without_capture() -> None:
    """The default state (design.md D72, revised for RQ-25): recording
    activated, the opt-in not given, capture stays absent."""
    assert resolve_failure_text_capture(activated=True, cli_opt_in=False) is False


def test_no_environment_variable_surface_exists_for_the_opt_in() -> None:
    """design.md D72: an environment variable is invisible in the command
    line RQ-11 records -- for an opt-in that means a run whose stored
    evidence appears with nothing in its own history to explain why it is
    present. No parameter for one exists on
    `resolve_failure_text_capture`'s signature -- and no ini parameter
    either, now that the capability spec's "no committed configuration file
    MAY be the means by which capture is enabled" requirement has removed
    that surface entirely, along with its `addini` registration and
    `config.getini` read in `plugin.py` -- and `pytest_vantage.config`'s
    source never references `os.environ` on the opt-in path (it does,
    deliberately, for `resolve_server_address`'s `VANTAGE_SERVER` -- a
    different surface, D6/D11, not this one)."""
    parameters = set(inspect.signature(resolve_failure_text_capture).parameters)

    assert parameters == {"activated", "cli_opt_in"}

    source = inspect.getsource(resolve_failure_text_capture)
    assert "os.environ" not in source
    assert not hasattr(config_module, "os"), (
        "pytest_vantage.config must not import os at all for the opt-in to have "
        "nowhere to reach an environment variable from"
    )
