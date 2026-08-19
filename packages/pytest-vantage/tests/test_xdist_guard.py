"""design.md D2: the xdist guard is the FIRST statement in ``pytest_configure``.

Under xdist every worker re-runs ``pytest_configure`` as a full pytest
session of its own -- unguarded, ``-n 4`` would leave four workers' recorders
plus the controller's, breaking RQ-1's "exactly one run entry" (RQ-27's
xdist half of the process-integration threat matrix). The discriminator is
whether the config object carries a ``workerinput`` attribute, and the
return must happen before the activation check and before anything could
register a recorder or open a socket -- not after.

Pure unit test: a config double stands in for a real ``pytest.Config``, no
subprocess or real xdist session needed. ``getoption`` raises if called at
all, which is the strongest way to prove ``pytest_configure`` returned
before touching activation.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_vantage.plugin import pytest_configure


class _WorkerConfigDouble:
    """A ``pytest.Config`` stand-in carrying xdist's ``workerinput`` marker.

    ``getoption`` raises: if ``pytest_configure`` reaches it at all after
    seeing ``workerinput``, the guard did not return first.
    """

    workerinput: dict[str, Any] = {}

    def getoption(self, name: str, default: object = None) -> object:
        raise AssertionError(
            f"pytest_configure must return before reading option {name!r} "
            "when config carries workerinput"
        )


class _ControllerConfigDouble:
    """The non-worker counterpart: no ``workerinput``, so the activation
    check must run -- triangulates that the guard is scoped to xdist workers
    only, not swallowing every invocation.
    """

    def __init__(self) -> None:
        self.options_read: list[str] = []

    def getoption(self, name: str, default: object = None) -> object:
        self.options_read.append(name)
        return False


@pytest.mark.req(id="RQ-1")
@pytest.mark.req(id="RQ-27")
def test_worker_input_returns_before_registration() -> None:
    config = _WorkerConfigDouble()
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config


@pytest.mark.req(id="RQ-1")
@pytest.mark.req(id="RQ-27")
def test_no_worker_input_still_runs_the_activation_check() -> None:
    config = _ControllerConfigDouble()
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert config.options_read == ["vantage"]
