"""The `vantage_metadata` fixture end to end: real inner pytest sessions
report what they ran against to a real `vantage` server, and every
assertion reads it back the way a user would, through
`GET /api/v1/runs/{run_id}/metadata` and the run list's metadata filter.

The server refuses a `metadata` section it does not understand whole, and
decides for itself which keys are declared, so only a session that crosses
the real boundary shows the two halves agree.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from vantage_test_server import VantageTestServer

_DECLARATION = "vantage-metadata.json"

_NAMES = {
    "fpga.firmware": "FPGA firmware version",
    "fpga.hardware": "FPGA hardware version",
    "fmc.hardware": "FMC hardware version",
    "fmc.pic.firmware": "FMC PIC firmware version",
}

# What the fixture exists for: what the session runs against is known only
# once a session fixture has connected to it.
_REPORTS_THE_BENCH = """
import pytest


@pytest.fixture(scope="session", autouse=True)
def dut(vantage_metadata):
    vantage_metadata.update({
        "fpga": {"firmware": "1.1.0", "hardware": "v0.5.3"},
        "fmc": {"hardware": "5.2.0", "pic": {"firmware": "2.0.0"}},
    })
    vantage_metadata["bench"] = "lab-3"
    yield


def test_one():
    assert True


def test_two():
    assert True
"""


def _declare(root: Path, document: dict[str, object]) -> None:
    (root / _DECLARATION).write_text(json.dumps({"version": 1, **document}))


def _declare_the_bench(root: Path) -> None:
    _declare(root, {"keys": {key: {"name": name} for key, name in _NAMES.items()}})


def _get(server: VantageTestServer, path: str, query: list[tuple[str, str]] | None = None) -> Any:
    url = f"{server.address}/api/v1{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 -- loopback test server
        return json.load(response)


def _only_run_id(server: VantageTestServer) -> str:
    (execution,) = server.executions()
    return execution.identity.value


def _metadata(server: VantageTestServer, run_id: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = _get(server, f"/runs/{run_id}/metadata")["items"]
    return items


def _by_key(server: VantageTestServer, run_id: str) -> dict[str, dict[str, object]]:
    return {str(item["key"]): item for item in _metadata(server, run_id)}


def _session_row(
    key: str, value: str | None, *, status: str = "captured", declared: bool = True
) -> dict[str, object]:
    return {
        "key": key,
        "name": _NAMES.get(key) if declared else None,
        "value": value,
        "status": status,
        "source": "session",
        "source_file": None,
        "declared": declared,
    }


def _vantage_warnings(output: str) -> list[str]:
    return [line for line in output.splitlines() if "VantageWarning:" in line]


def test_the_bench_a_session_reports_is_recorded_flat_under_its_declared_names(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    """Nested mappings arrive as dotted keys, each carrying the name the
    declaration gives it. A key the declaration does not name is still
    recorded, marked undeclared, and named in the one warning."""
    _declare_the_bench(pytester.path)
    pytester.makepyfile(test_bench=_REPORTS_THE_BENCH)

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=2)
    assert _metadata(vantage_server, _only_run_id(vantage_server)) == [
        _session_row("bench", "lab-3", declared=False),
        _session_row("fmc.hardware", "5.2.0"),
        _session_row("fmc.pic.firmware", "2.0.0"),
        _session_row("fpga.firmware", "1.1.0"),
        _session_row("fpga.hardware", "v0.5.3"),
    ]
    (warning,) = _vantage_warnings(result.stdout.str())
    assert warning.endswith("vantage: undeclared metadata keys: 'bench'")


def test_a_misspelt_key_is_recorded_undeclared_and_the_warning_suggests_the_declared_one(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    _declare_the_bench(pytester.path)
    pytester.makepyfile(
        test_bench=_REPORTS_THE_BENCH.replace('"firmware": "1.1.0"', '"firmwre": "1.1.0"')
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=2)
    stored = _by_key(vantage_server, _only_run_id(vantage_server))
    assert stored["fpga.firmwre"] == _session_row("fpga.firmwre", "1.1.0", declared=False)
    (warning,) = _vantage_warnings(result.stdout.str())
    assert warning.endswith(
        "vantage: undeclared metadata keys: 'fpga.firmwre' (did you mean 'fpga.firmware'?), 'bench'"
    )


def test_a_declared_key_nothing_reported_is_recorded_absent(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    """No FMC fitted this time: every key the declaration names still has a
    row, so "not reported" reads differently from "never declared"."""
    _declare_the_bench(pytester.path)
    pytester.makepyfile(
        test_bench="""
import pytest


@pytest.fixture(scope="session", autouse=True)
def dut(vantage_metadata):
    vantage_metadata["fpga"] = {"firmware": "1.1.0", "hardware": "v0.5.3"}


def test_it():
    assert True
"""
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    assert "VantageWarning" not in result.stdout.str()
    assert _metadata(vantage_server, _only_run_id(vantage_server)) == [
        _session_row("fmc.hardware", None, status="absent"),
        _session_row("fmc.pic.firmware", None, status="absent"),
        _session_row("fpga.firmware", "1.1.0"),
        _session_row("fpga.hardware", "v0.5.3"),
    ]


def test_a_value_over_the_byte_bound_is_recorded_too_large_without_it(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    """The bound is 1,024 bytes of UTF-8, not characters: 512 two-byte
    characters fit, 513 do not."""
    pytester.makepyfile(
        test_bench="""
def test_it(vantage_metadata):
    vantage_metadata["fits"] = "\\u00e9" * 512
    vantage_metadata["dna"] = "\\u00e9" * 513
"""
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    assert "VantageWarning" not in result.stdout.str()
    stored = _by_key(vantage_server, _only_run_id(vantage_server))
    assert (stored["fits"]["status"], stored["fits"]["value"]) == ("captured", "é" * 512)
    assert (stored["dna"]["status"], stored["dna"]["value"]) == ("value_too_large", None)


def test_values_are_recorded_as_text(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    """Lists and tuples as compact JSON, booleans in lower case, numbers as
    Python prints them, and `None` as a key with no value."""
    pytester.makepyfile(
        test_bench="""
def test_it(vantage_metadata):
    vantage_metadata.update({
        "fmc": {"fitted": True, "slots": ["a", 2, None]},
        "fpga": {"secure_boot": False, "rails": (3.3, 1.8)},
        "retries": 3,
        "clock_mhz": 1.10,
        "pic": None,
    })
"""
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    stored = _by_key(vantage_server, _only_run_id(vantage_server))
    assert {key: (item["status"], item["value"]) for key, item in stored.items()} == {
        "fmc.fitted": ("captured", "true"),
        "fmc.slots": ("captured", '["a",2,null]'),
        "fpga.secure_boot": ("captured", "false"),
        "fpga.rails": ("captured", "[3.3,1.8]"),
        "retries": ("captured", "3"),
        "clock_mhz": ("captured", "1.1"),
        "pic": ("absent", None),
    }


def test_without_vantage_the_fixture_accepts_everything_and_sends_nothing(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    """A server address alone records nothing, so a project that reports
    metadata runs the same with the plugin inactive: no request, no
    warning, not even for a key that could not be recorded."""
    _declare_the_bench(pytester.path)
    pytester.makepyfile(
        test_bench=_REPORTS_THE_BENCH
        + """

def test_the_values_read_back(vantage_metadata):
    vantage_metadata[""] = "no key"
    assert vantage_metadata["fmc.pic.firmware"] == "2.0.0"
"""
    )

    result = pytester.runpytest(f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=3)
    assert "VantageWarning" not in result.stdout.str()
    assert vantage_server.requests == []


# Every worker runs the session fixture: `rig` differs between them, and
# `fpga.firmware` does not. The controller's conftest notes the order in
# which the workers' values reach it.
_REPORTS_PER_WORKER = """
import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def dut(vantage_metadata):
    worker = os.environ["PYTEST_XDIST_WORKER"]
    vantage_metadata["fpga"] = {"firmware": "1.1.0"}
    vantage_metadata["rig"] = worker
    vantage_metadata[f"seen.{worker}"] = True


def test_one():
    assert True


def test_two():
    assert True
"""

_NOTES_WORKERS_DOWN = """
from pathlib import Path


def pytest_testnodedown(node, error):
    with (Path(__file__).parent / "workers-down.txt").open("a") as notes:
        notes.write(node.gateway.id + "\\n")
"""


def test_values_from_xdist_workers_arrive_and_the_first_of_two_differing_is_kept(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    """Session fixtures run on each worker, never on the controller that
    reports. `--dist each` so that both workers run them."""
    pytest.importorskip("xdist")
    pytester.makepyfile(test_workers=_REPORTS_PER_WORKER)
    pytester.makeconftest(_NOTES_WORKERS_DOWN)

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}", "-n", "2", "--dist", "each"
    )

    result.assert_outcomes(passed=4)
    first_down = (pytester.path / "workers-down.txt").read_text().split()[0]
    stored = _by_key(vantage_server, _only_run_id(vantage_server))
    assert {key: item["value"] for key, item in stored.items()} == {
        "fpga.firmware": "1.1.0",
        "rig": first_down,
        "seen.gw0": "true",
        "seen.gw1": "true",
    }
    (warning,) = _vantage_warnings(result.stdout.str())
    assert warning.endswith(
        "vantage: xdist workers reported different values for the metadata keys 'rig', "
        "the first value received is kept"
    )


def test_a_key_read_from_a_declared_file_keeps_the_files_value(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    """The file's value reaches the server first, in the start report, and
    the declaration's name goes with it; the session's value for the same
    key is not recorded, and one warning says so."""
    (pytester.path / "settings.json").write_text('{"region": "eu-central-1"}')
    _declare(
        pytester.path,
        {
            "keys": {"region": {"name": "Deployment region"}},
            "files": [{"path": "settings.json", "format": "json", "keys": ["region"]}],
        },
    )
    pytester.makepyfile(
        test_region="""
def test_it(vantage_metadata):
    vantage_metadata["region"] = "eu-west-1"
"""
    )

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-metadata"
    )

    result.assert_outcomes(passed=1)
    assert _metadata(vantage_server, _only_run_id(vantage_server)) == [
        {
            "key": "region",
            "name": "Deployment region",
            "value": "eu-central-1",
            "status": "captured",
            "source": "file",
            "source_file": "settings.json",
            "declared": True,
        }
    ]
    (warning,) = _vantage_warnings(result.stdout.str())
    assert warning.endswith(
        "vantage: the metadata keys 'region' are read from declared files and also reported "
        "by the session, the file values are kept"
    )


_REPORTS_BENCH_JSON = """
import json

import pytest


@pytest.fixture(scope="session", autouse=True)
def dut(vantage_metadata, pytestconfig):
    vantage_metadata.update(json.loads((pytestconfig.rootpath / "bench.json").read_text()))


def test_it():
    assert True
"""


def test_runs_are_filtered_by_several_keys_at_once(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    """A run matches only when it holds every pair. Each filtered key has
    its own horizon: the runs recorded before any run reported it, which
    hold no value for it and so can never match."""
    pytester.makepyfile(test_bench=_REPORTS_BENCH_JSON)
    benches: list[dict[str, dict[str, str]]] = [
        {},
        {"fpga": {"firmware": "1.1.0"}},
        {"fpga": {"firmware": "1.1.0"}, "fmc": {"hardware": "5.2.0"}},
        {"fpga": {"firmware": "1.1.0"}, "fmc": {"hardware": "5.1.0"}},
        {"fpga": {"firmware": "1.0.0"}, "fmc": {"hardware": "5.2.0"}},
    ]
    run_ids: list[str] = []
    for bench in benches:
        (pytester.path / "bench.json").write_text(json.dumps(bench))
        pytester.runpytest(
            "--vantage", f"--vantage-server={vantage_server.address}"
        ).assert_outcomes(passed=1)
        run_ids.append(vantage_server.executions()[0].identity.value)
    assert len(set(run_ids)) == len(benches)

    both = _get(
        vantage_server,
        "/projects/default/runs",
        [
            ("metadata_key", "fpga.firmware"),
            ("metadata_value", "1.1.0"),
            ("metadata_key", "fmc.hardware"),
            ("metadata_value", "5.2.0"),
        ],
    )
    firmware_only = _get(
        vantage_server,
        "/projects/default/runs",
        [("metadata_key", "fpga.firmware"), ("metadata_value", "1.1.0")],
    )

    assert [item["id"] for item in both["items"]] == [run_ids[2]]
    assert both["metadata_horizon"] == [
        {"key": "fpga.firmware", "predating": 1},
        {"key": "fmc.hardware", "predating": 2},
    ]
    assert [item["id"] for item in firmware_only["items"]] == run_ids[3:0:-1]
    assert firmware_only["metadata_horizon"] == [{"key": "fpga.firmware", "predating": 1}]
