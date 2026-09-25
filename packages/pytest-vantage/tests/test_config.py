"""`resolve_failure_text_capture` and `resolve_metadata_capture`: the
opt-ins' composition rule. Capture is absent unless requested, and a capture
flag only ever widens an already-activated session -- the same invariant
`--vantage` holds for recording itself.
"""

from __future__ import annotations

from pytest_vantage.config import resolve_failure_text_capture, resolve_metadata_capture


def test_resolve_failure_text_capture_true_only_when_activated_and_cli_opt_in() -> None:
    """The exhaustive truth table: only `activated=True, cli_opt_in=True`
    resolves `True` -- capture is absent by default."""
    assert resolve_failure_text_capture(activated=False, cli_opt_in=False) is False
    assert resolve_failure_text_capture(activated=False, cli_opt_in=True) is False
    assert resolve_failure_text_capture(activated=True, cli_opt_in=False) is False
    assert resolve_failure_text_capture(activated=True, cli_opt_in=True) is True


def test_resolve_metadata_capture_true_only_when_activated_and_cli_opt_in() -> None:
    """The exhaustive truth table: only `activated=True, cli_opt_in=True`
    resolves `True` -- capture is absent by default."""
    assert resolve_metadata_capture(activated=False, cli_opt_in=False) is False
    assert resolve_metadata_capture(activated=False, cli_opt_in=True) is False
    assert resolve_metadata_capture(activated=True, cli_opt_in=False) is False
    assert resolve_metadata_capture(activated=True, cli_opt_in=True) is True
