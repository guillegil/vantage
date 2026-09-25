"""`--vantage-metadata` end to end: a real declaration and real JSON and YAML
files, recorded by a real `vantage` server and read back through its
`metadata_key`/`metadata_value` run filter.

The server refuses a metadata section with an unknown field, so a field
renamed on either side of the wire loses the whole session. Only a test
that crosses the real boundary sees that.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

import pytest
from vantage_test_server import VantageTestServer, vantage_server  # noqa: F401 -- fixture


def _runs_matching(server: VantageTestServer, key: str, value: str) -> list[str]:
    query = urllib.parse.urlencode({"metadata_key": key, "metadata_value": value})
    url = f"{server.address}/api/v1/runs?{query}"
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 -- loopback test server
        body = json.load(response)
    return [item["id"] for item in body["items"]]


def test_declared_values_are_recorded_and_filter_the_run_list(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    (pytester.path / "board.json").write_text('{"board_revision": "rev-b", "ignored": 1}')
    (pytester.path / "firmware.yaml").write_text("firmware_version: '2.1.0'\n")
    declaration = {
        "version": 1,
        "files": [
            {"path": "board.json", "format": "json", "keys": ["board_revision"]},
            {"path": "firmware.yaml", "format": "yaml", "keys": ["firmware_version"]},
        ],
    }
    (pytester.path / "vantage-metadata.json").write_text(json.dumps(declaration))
    pytester.makepyfile(test_sample="def test_passes():\n    assert True\n")

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-metadata"
    )

    result.assert_outcomes(passed=1)
    (execution,) = vantage_server.executions()
    run_id = execution.identity.value
    assert _runs_matching(vantage_server, "board_revision", "rev-b") == [run_id]
    assert _runs_matching(vantage_server, "firmware_version", "2.1.0") == [run_id]
    assert _runs_matching(vantage_server, "board_revision", "rev-a") == []
