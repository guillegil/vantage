"""E2E catalogue survival (RQ-13, design.md D20) through a real pytest
session and a real server. The pure-function/contract layer already proves
this at the `ExecutionStore` level (`vantage_port_contract.py`, Phase 3);
this file proves the same invariant survives the real hook wiring -- the
plugin decides what goes on the wire, not the store, and nothing before
Phase 9 exercised that decision end to end.
"""

from __future__ import annotations

import pytest
from vantage_test_server import VantageTestServer, vantage_server  # noqa: F401 -- fixture

_TWO_TESTS = "def test_stable():\n    assert True\n\n\ndef test_removable():\n    assert True\n"
_ONE_TEST = "def test_stable():\n    assert True\n"

_NODE_ID = "test_catalogue.py::test_removable"


@pytest.mark.req(id="RQ-13")
def test_deleting_and_readding_a_test_preserves_and_advances_the_catalogue_entry(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """RQ-13.1: deleting `test_removable` from the file and re-running
    leaves its catalogue entry byte-for-byte unchanged -- not merely
    similar, since a report that omits a node id must never touch that
    node id's row at all (D20). RQ-13.2: adding it back reuses the SAME
    entry (`first_seen_at` untouched) and advances `last_seen_at` and
    `last_seen_run_id` to the new run.
    """
    address_args = ("--vantage", f"--vantage-server={vantage_server.address}")

    pytester.makepyfile(test_catalogue=_TWO_TESTS)
    pytester.runpytest_subprocess(*address_args)
    entry_after_first_run = vantage_server.catalogue_entry(_NODE_ID)
    assert entry_after_first_run is not None

    pytester.makepyfile(test_catalogue=_ONE_TEST)
    pytester.runpytest_subprocess(*address_args)
    entry_after_deletion = vantage_server.catalogue_entry(_NODE_ID)
    assert entry_after_deletion == entry_after_first_run

    pytester.makepyfile(test_catalogue=_TWO_TESTS)
    pytester.runpytest_subprocess(*address_args)
    entry_after_readd = vantage_server.catalogue_entry(_NODE_ID)
    assert entry_after_readd is not None
    assert entry_after_readd.first_seen_at == entry_after_first_run.first_seen_at
    assert entry_after_readd.last_seen_at > entry_after_first_run.last_seen_at
    assert entry_after_readd.last_seen_run_id != entry_after_first_run.last_seen_run_id
