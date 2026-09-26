"""What a recorded session sends of its metadata: the `vantage_metadata`
fixture's values, the declaration's keys and files, and how xdist workers
hand their values to the controller.

Real inner pytest sessions against the real `vantage_server`, which answers
the reachability check and the capability probe, with every report
captured on its way out rather than stored: the assertions are about the
wire, and what the server keeps of it is the server's own tests' concern.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_vantage import budget, recorder, vcs
from pytest_vantage.boundary import VantageWarning
from pytest_vantage.metadata import SessionValue
from pytest_vantage.recorder import _WORKER_METADATA_KEY, Recorder, WorkerMetadataRelay
from pytest_vantage.session_metadata import relay
from vantage_test_server import VantageTestServer

_DECLARATION = "vantage-metadata.json"

# The example the fixture exists for: what the session ran against is known
# only once a session fixture has connected to it.
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

_BENCH_VALUES = [
    {"key": "fpga.firmware", "value": "1.1.0", "status": "captured"},
    {"key": "fpga.hardware", "value": "v0.5.3", "status": "captured"},
    {"key": "fmc.hardware", "value": "5.2.0", "status": "captured"},
    {"key": "fmc.pic.firmware", "value": "2.0.0", "status": "captured"},
    {"key": "bench", "value": "lab-3", "status": "captured"},
]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Every report the session sends, in order, captured instead of sent.
    Patched on the module imported at collection: `pytester` restores
    `sys.modules` after each in-process run, so a dotted path could reach
    a copy the plugin no longer uses."""
    reports: list[dict[str, object]] = []
    monkeypatch.setattr(
        recorder, "send", lambda address, report, *, timeout: reports.append(report)
    )
    return reports


def _declare(pytester: pytest.Pytester, document: dict[str, object]) -> None:
    (pytester.path / _DECLARATION).write_text(json.dumps({"version": 1, **document}))


def _metadata_of(report: dict[str, object]) -> dict[str, object]:
    section = report["metadata"]
    assert isinstance(section, dict)
    return section


# --- The fixture -------------------------------------------------------------


def test_the_fixtures_values_go_in_the_last_report_only(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, sent: list[dict[str, object]]
) -> None:
    """With no declaration, the start report has nothing to say about
    metadata; the finish report carries every value, flattened, in the
    order first set."""
    pytester.makepyfile(test_bench=_REPORTS_THE_BENCH)

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=2)
    assert "VantageWarning" not in result.stdout.str()
    start, finish = sent
    assert "metadata" not in start
    assert _metadata_of(finish) == {"declaration": None, "files": [], "values": _BENCH_VALUES}


def test_a_key_or_value_that_cannot_be_recorded_warns_and_costs_only_itself(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, sent: list[dict[str, object]]
) -> None:
    pytester.makepyfile(
        test_bench="""
import pytest


class Broken:
    def __str__(self):
        raise RuntimeError("no text")


@pytest.fixture(scope="session", autouse=True)
def dut(vantage_metadata):
    vantage_metadata["fpga"] = {"firmware": 1.10, "dna": Broken(), "bad\\nkey": 1}
    vantage_metadata["serial"] = "s" * 2000


def test_it(vantage_metadata):
    assert vantage_metadata["fpga.firmware"] == "1.1"
"""
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    output = result.stdout.str()
    assert output.count("VantageWarning:") == 2
    assert "ignoring the metadata key 'fpga.dna': its value cannot be turned into text" in output
    assert "ignoring the metadata key 'fpga.bad\\nkey': a key must not contain a control" in output
    assert _metadata_of(sent[-1])["values"] == [
        {"key": "fpga.firmware", "value": "1.1", "status": "captured"},
        {"key": "serial", "value": None, "status": "value_too_large"},
    ]


def test_a_failure_planning_the_values_costs_the_run_only_its_values(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    sent: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args: object) -> None:
        raise RuntimeError("synthetic planning failure")

    monkeypatch.setattr(recorder, "plan_values", _raise)
    pytester.makepyfile(test_bench=_REPORTS_THE_BENCH)

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=2)
    output = result.stdout.str()
    assert output.count("VantageWarning:") == 1
    assert (
        "vantage: error while reporting metadata: synthetic planning failure, the session's "
        "values will not be sent"
    ) in output
    start, finish = sent
    assert finish["run"]["exit_status"] == 0  # type: ignore[index]
    assert "metadata" not in finish


def test_an_unrecorded_session_hands_out_a_mapping_that_sends_nothing(
    pytester: pytest.Pytester, sent: list[dict[str, object]]
) -> None:
    """Recording asked for, but nothing listens: the fixture still works,
    and the only warning is the one saying the session is not recorded.
    Raised from `pytest_configure`, it escapes an in-process run."""
    pytester.makepyfile(test_bench=_REPORTS_THE_BENCH)

    with pytest.warns(VantageWarning) as warned:
        result = pytester.runpytest("--vantage", "--vantage-server=http://127.0.0.1:1")

    result.assert_outcomes(passed=2)
    assert "VantageWarning" not in result.stdout.str()
    assert sent == []
    assert [str(w.message) for w in warned] == [
        "vantage: cannot reach http://127.0.0.1:1, this session will not be recorded"
    ]


# --- The declaration's keys -------------------------------------------------


def test_declared_keys_go_in_every_report_and_unreported_ones_are_absent(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, sent: list[dict[str, object]]
) -> None:
    """Under `--vantage` alone the declaration is read for its keys. A key
    it declares that nothing reported is sent `absent`; one reported that
    it does not declare is still sent, and named in one warning at the end
    of the session, with the declared key it most likely meant."""
    keys = {
        "fpga.firmware": {"name": "FPGA firmware version"},
        "fpga.hardware": {"name": "FPGA hardware version"},
        "fmc.hardware": {"name": "FMC hardware version"},
        "fmc.pic.firmware": {"name": "FMC PIC firmware version"},
        "rack": {"name": None},
    }
    _declare(pytester, {"keys": keys})
    misspelt = _REPORTS_THE_BENCH.replace('"firmware": "1.1.0"', '"firmwre": "1.1.0"')
    pytester.makepyfile(test_bench=misspelt)

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=2)
    start, finish = sent
    assert _metadata_of(start) == {"declaration": _DECLARATION, "keys": keys, "files": []}
    assert _metadata_of(finish) == {
        "declaration": _DECLARATION,
        "keys": keys,
        "files": [],
        "values": [
            {"key": "fpga.firmwre", "value": "1.1.0", "status": "captured"},
            *_BENCH_VALUES[1:],
            {"key": "fpga.firmware", "value": None, "status": "absent"},
            {"key": "rack", "value": None, "status": "absent"},
        ],
    }
    output = result.stdout.str()
    assert output.count("VantageWarning:") == 1
    assert (
        "vantage: undeclared metadata keys: 'fpga.firmwre' (did you mean 'fpga.firmware'?), 'bench'"
    ) in output


def test_a_value_for_a_key_of_a_file_not_read_is_sent_as_declared(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, sent: list[dict[str, object]]
) -> None:
    """Without `--vantage-metadata` the file is never opened, and not in
    the report to declare its key, so the report declares the key it sends
    a value for."""
    (pytester.path / "settings.json").write_text('{"region": "from-the-file"}')
    _declare(
        pytester,
        {"files": [{"path": "settings.json", "format": "json", "keys": ["region", "zone"]}]},
    )
    pytester.makepyfile(
        test_region="""
def test_it(vantage_metadata):
    vantage_metadata["region"] = "eu-west-1"
"""
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1)
    assert "VantageWarning" not in result.stdout.str()
    start, finish = sent
    assert "metadata" not in start
    assert _metadata_of(finish) == {
        "declaration": _DECLARATION,
        "keys": {"region": {"name": None}},
        "files": [],
        "values": [{"key": "region", "value": "eu-west-1", "status": "captured"}],
    }


def test_a_key_read_from_a_declared_file_keeps_the_files_value(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, sent: list[dict[str, object]]
) -> None:
    """With `--vantage-metadata` the file's row reaches the server first,
    in the start report; the session's value for the same key is not sent,
    and one warning says so."""
    (pytester.path / "settings.json").write_text('{"region": "from-the-file"}')
    _declare(
        pytester,
        {
            "keys": {"region": {"name": "Deployment region"}},
            "files": [{"path": "settings.json", "format": "json", "keys": ["region"]}],
        },
    )
    pytester.makepyfile(
        test_region="""
def test_it(vantage_metadata):
    vantage_metadata["region"] = "eu-west-1"
    vantage_metadata["bench"] = "lab-3"
"""
    )

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-metadata"
    )

    result.assert_outcomes(passed=1)
    files = [
        {
            "path": "settings.json",
            "format": "json",
            "status": "captured",
            "keys": ["region"],
            "content": '{"region": "from-the-file"}',
        }
    ]
    keys = {"region": {"name": "Deployment region"}}
    start, finish = sent
    assert _metadata_of(start) == {"declaration": _DECLARATION, "keys": keys, "files": files}
    assert _metadata_of(finish) == {
        "declaration": _DECLARATION,
        "keys": keys,
        "files": files,
        "values": [{"key": "bench", "value": "lab-3", "status": "captured"}],
    }
    output = result.stdout.str()
    assert (
        "vantage: the metadata keys 'region' are read from declared files and also reported "
        "by the session, the file values are kept"
    ) in output
    assert "vantage: undeclared metadata keys: 'bench'" in output


# --- A report split in several ------------------------------------------------


def test_only_the_last_of_several_finish_reports_carries_the_values(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    sent: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every report carries the declared keys; the values ride in the last
    alone, so a report lost on the way never leaves a finished run without
    them. Values too large to share the last report with results get one of
    their own, and every report still fits the cap."""
    cap = 12_000
    monkeypatch.setattr(budget, "_REPORT_BYTES_CAP", cap)
    names = [*(f"dump.{i}" for i in range(5)), "bench"]
    _declare(pytester, {"keys": dict.fromkeys(names, {})})
    pytester.makepyfile(
        test_many="""
import pytest


@pytest.fixture(scope="session", autouse=True)
def dut(vantage_metadata):
    vantage_metadata.update({f"dump.{i}": "d" * 1000 for i in range(5)})
    vantage_metadata["bench"] = "lab-3"


@pytest.mark.parametrize("n", range(40), ids=lambda n: f"{n:02d}-" + "x" * 150)
def test_p(n):
    assert True
"""
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=40)
    assert "VantageWarning" not in result.stdout.str()
    declared = {
        "declaration": _DECLARATION,
        "keys": dict.fromkeys(names, {"name": None}),
        "files": [],
    }
    start, *finish_parts = sent
    *earlier, last = finish_parts
    assert earlier
    assert all(len(json.dumps(report).encode()) <= cap for report in finish_parts)
    assert [
        entry["node_id"]
        for report in finish_parts
        for entry in report["results"]  # type: ignore[attr-defined]
    ] == [f"test_many.py::test_p[{n:02d}-{'x' * 150}]" for n in range(40)]
    assert all(_metadata_of(report) == declared for report in [start, *earlier])
    values = _metadata_of(last).pop("values")
    assert _metadata_of(last) == declared
    assert isinstance(values, list)
    assert [value["key"] for value in values] == names


# --- xdist ----------------------------------------------------------------------

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


def test_xdist_workers_hand_their_values_to_the_controller(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, sent: list[dict[str, object]]
) -> None:
    """Session fixtures run on each worker, never on the controller that
    reports. Each worker's values arrive merged; a key two workers set
    alike is sent once, and one they disagree on keeps the first value
    received, with one warning. In-process, so the controller's reports
    can be captured; the workers are real processes."""
    pytest.importorskip("xdist")
    pytester.makepyfile(test_workers=_REPORTS_PER_WORKER)

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "-n", "2", "--dist", "each"
    )

    result.assert_outcomes(passed=4)
    values = _metadata_of(sent[-1])["values"]
    assert isinstance(values, list)
    first_worker = values[1]["value"]
    other_worker = {"gw0": "gw1", "gw1": "gw0"}[first_worker]
    assert values == [
        {"key": "fpga.firmware", "value": "1.1.0", "status": "captured"},
        {"key": "rig", "value": first_worker, "status": "captured"},
        {"key": f"seen.{first_worker}", "value": "true", "status": "captured"},
        {"key": f"seen.{other_worker}", "value": "true", "status": "captured"},
    ]
    output = result.stdout.str()
    assert output.count("VantageWarning:") == 1
    assert (
        "vantage: xdist workers reported different values for the metadata keys 'rig', "
        "the first value received is kept"
    ) in output


def test_a_worker_hands_over_its_values_as_its_session_finishes() -> None:
    config = SimpleNamespace(workeroutput={})
    worker = WorkerMetadataRelay(config)  # type: ignore[arg-type]
    worker.pytest_sessionfinish()
    assert config.workeroutput == {}

    worker.session_metadata["fpga"] = {"firmware": "1.1.0"}
    worker.session_metadata["dna"] = "d" * 2000
    worker.pytest_sessionfinish()

    assert config.workeroutput == {
        _WORKER_METADATA_KEY: [
            ["fpga.firmware", "1.1.0", "captured"],
            ["dna", None, "value_too_large"],
        ]
    }


def test_a_worker_reported_twice_is_merged_once(
    tmp_path: Path,
    sent: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    recwarn: pytest.WarningsRecorder,
) -> None:
    """xdist reports an interrupted worker down twice, with the same
    output; that is not two workers disagreeing."""
    monkeypatch.setattr(vcs, "capture", lambda rootpath: vcs.VcsSnapshot())
    config = SimpleNamespace(rootpath=str(tmp_path))
    controller = Recorder(config, "http://127.0.0.1:1", 1.0)  # type: ignore[arg-type]
    entries = relay([SessionValue("rig", "gw0", "captured")])
    node = SimpleNamespace(workeroutput={"exitstatus": 2, _WORKER_METADATA_KEY: entries})

    controller.pytest_testnodedown(node=node, error=None)
    controller.pytest_testnodedown(node=node, error="keyboard-interrupt")
    controller.pytest_sessionfinish(
        session=SimpleNamespace(shouldfail=False, shouldstop=False),  # type: ignore[arg-type]
        exitstatus=2,
    )

    assert not any(issubclass(w.category, VantageWarning) for w in recwarn.list)
    assert _metadata_of(sent[-1])["values"] == [
        {"key": "rig", "value": "gw0", "status": "captured"}
    ]
