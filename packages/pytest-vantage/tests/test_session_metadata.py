"""`pytest_vantage.session_metadata`: the mapping the `vantage_metadata`
fixture hands out, the xdist relay of what workers reported, and the plan
for what the run's last report sends.

Pure unit tests; what a real session sends is in
`test_metadata_reporting.py`.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pytest_vantage import budget, metadata, session_metadata
from pytest_vantage.metadata import (
    CapturedFile,
    DeclaredKey,
    MetadataSection,
    SessionValue,
)
from pytest_vantage.session_metadata import (
    ReportedValues,
    SessionMetadata,
    plan_values,
    plan_warnings,
    relay,
    unrelay,
)


def _recording() -> tuple[SessionMetadata, list[str]]:
    warned: list[str] = []
    return SessionMetadata(warn=warned.append), warned


class _Version:
    """Stands in for a version object a device driver returns."""

    def __str__(self) -> str:
        return "1.2.3"


class _Unprintable:
    def __str__(self) -> str:
        raise RuntimeError("no text for you")


# --- the mapping ---------------------------------------------------------------


def test_nested_dicts_are_flattened_under_their_key_in_the_order_set() -> None:
    store, warned = _recording()

    store.update(
        {
            "fpga": {"firmware": "1.1.0", "hardware": "v0.5.3"},
            "fmc": {"hardware": "5.2.0", "pic": {"firmware": "2.0.0"}},
        }
    )
    store["bench"] = "lab-3"

    assert list(store.items()) == [
        ("fpga.firmware", "1.1.0"),
        ("fpga.hardware", "v0.5.3"),
        ("fmc.hardware", "5.2.0"),
        ("fmc.pic.firmware", "2.0.0"),
        ("bench", "lab-3"),
    ]
    assert warned == []


def test_a_key_set_again_is_replaced_where_it_was_first_set() -> None:
    """Setting a mapping sets its leaves one by one: a leaf it names is
    replaced, and a key it does not mention stays."""
    store = SessionMetadata()
    store["fpga"] = {"firmware": "1.0.0", "hardware": "v0.5.3"}
    store["bench"] = "lab-3"

    store["fpga"] = {"firmware": "1.1.0"}
    store["fpga.hardware"] = "v0.6.0"

    assert list(store.items()) == [
        ("fpga.firmware", "1.1.0"),
        ("fpga.hardware", "v0.6.0"),
        ("bench", "lab-3"),
    ]


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("1.1.0", "1.1.0"),
        (True, "true"),
        (False, "false"),
        (3, "3"),
        (1.10, "1.1"),
        ([1, "a", None, True], '[1,"a",null,true]'),
        (("é", {"k": 2}), '["é",{"k":2}]'),
        ([_Version()], '["1.2.3"]'),
        (None, None),
        (_Version(), "1.2.3"),
    ],
    ids=[
        "str",
        "true",
        "false",
        "int",
        "float",
        "list",
        "tuple",
        "list-of-objects",
        "none",
        "object",
    ],
)
def test_a_value_is_stored_as_text_when_set(value: object, text: str | None) -> None:
    store, warned = _recording()

    store["k"] = value

    assert store["k"] == text
    assert warned == []


@pytest.mark.parametrize(
    ("key", "named", "reason"),
    [
        (3, "'3'", "a key must be a string"),
        ("", "''", "a key must not be empty"),
        ("k" * 1025, repr("k" * 80 + "..."), "a key must be at most 1024 characters"),
        ("fpga\nfirmware", repr("fpga\nfirmware"), "a key must not contain a control character"),
        ("fpga\x7ffirmware", repr("fpga\x7ffirmware"), "control character"),
        ("fpga\x85firmware", repr("fpga\x85firmware"), "control character"),
    ],
    ids=["not-a-string", "empty", "too-long", "newline", "delete", "c1-control"],
)
def test_a_key_that_cannot_be_recorded_is_skipped_with_one_warning_naming_it(
    key: object, named: str, reason: str
) -> None:
    store, warned = _recording()

    store[key] = "x"  # type: ignore[index]
    store["kept"] = "y"

    assert dict(store) == {"kept": "y"}
    (warning,) = warned
    assert warning.startswith(f"vantage: ignoring the metadata key {named}: ")
    assert reason in warning


def test_a_key_at_the_length_bound_is_recorded() -> None:
    store, warned = _recording()

    store["k" * 1024] = "x"

    assert list(store) == ["k" * 1024]
    assert warned == []


def test_a_bad_key_inside_a_mapping_costs_that_key_alone() -> None:
    store, warned = _recording()

    store["fpga"] = {1: "x", "firmware": "1.1.0", "bad\tkey": "y"}

    assert dict(store) == {"fpga.firmware": "1.1.0"}
    assert [message.split(": ")[1] for message in warned] == [
        "ignoring the metadata key 'fpga.1'",
        "ignoring the metadata key 'fpga.bad\\tkey'",
    ]


def _circular_list() -> list[object]:
    value: list[object] = []
    value.append(value)
    return value


@pytest.mark.parametrize(
    ("make_value", "error"),
    [
        (_Unprintable, "RuntimeError"),
        (lambda: [_Unprintable()], "RuntimeError"),
        (_circular_list, "ValueError"),
        (lambda: [{(1, 2): "a tuple is no JSON key"}], "TypeError"),
    ],
    ids=["str-raises", "json-default-raises", "circular-list", "unencodable-key"],
)
def test_a_value_that_cannot_be_turned_into_text_is_skipped_with_one_warning(
    make_value: Callable[[], object], error: str
) -> None:
    store, warned = _recording()

    store.update({"bad": make_value(), "good": "1"})

    assert dict(store) == {"good": "1"}
    assert warned == [
        f"vantage: ignoring the metadata key 'bad': its value cannot be turned into text ({error})"
    ]


def test_a_mapping_that_contains_itself_is_walked_once() -> None:
    store, warned = _recording()
    value: dict[str, object] = {"ok": "1"}
    value["again"] = value

    store["loop"] = value

    assert dict(store) == {"loop.ok": "1"}
    assert warned == ["vantage: ignoring the metadata key 'loop.again': its value contains itself"]


def test_nesting_deeper_than_the_recursion_limit_never_raises() -> None:
    """The flattened key outgrows its bound long before the interpreter's
    recursion limit would; the whole branch is skipped with one warning."""
    store, warned = _recording()
    deep: dict[str, object] = {"leaf": "x"}
    for _ in range(5_000):
        deep = {"a": deep}

    store["deep"] = deep
    store["kept"] = "y"

    assert dict(store) == {"kept": "y"}
    (warning,) = warned
    assert "at most 1024 characters" in warning


def test_an_update_that_is_not_pairs_is_skipped_with_one_warning() -> None:
    store, warned = _recording()

    store.update(5)
    store.update([("fpga", {"firmware": "1.1.0"})], bench="lab-3")

    assert dict(store) == {"fpga.firmware": "1.1.0", "bench": "lab-3"}
    (warning,) = warned
    assert "neither a mapping nor pairs" in warning


def test_a_mapping_nobody_records_accepts_everything_silently() -> None:
    """The mapping a session that records nothing gets: the same values,
    and no warning to act on."""
    store = SessionMetadata()

    store[3] = "x"  # type: ignore[index]
    store["fpga"] = {"firmware": 1.10}

    assert dict(store) == {"fpga.firmware": "1.1"}


def test_it_is_an_ordinary_mapping() -> None:
    store = SessionMetadata()
    store["a"] = "1"
    store["b"] = None

    assert len(store) == 2
    assert "b" in store
    assert store.get("missing") is None
    with pytest.raises(KeyError):
        store["missing"]
    del store["a"]
    assert store.pop("b") is None
    assert dict(store) == {}


def test_entries_carry_the_status_each_value_is_sent_with() -> None:
    """Measured in UTF-8 bytes, a lone surrogate as the three the server's
    replacement character costs."""
    store = SessionMetadata()
    store["none"] = None
    store["at-bound"] = "é" * 512
    store["over-bound"] = "é" * 512 + "x"
    store["surrogates"] = "\udc80" * 342

    assert store.entries() == [
        SessionValue("none", None, "absent"),
        SessionValue("at-bound", "é" * 512, "captured"),
        SessionValue("over-bound", None, "value_too_large"),
        SessionValue("surrogates", None, "value_too_large"),
    ]


# --- the xdist relay --------------------------------------------------------


def test_entries_survive_the_relay_and_anything_else_is_dropped() -> None:
    entries = [
        SessionValue("fpga.firmware", "1.1.0", "captured"),
        SessionValue("bench", None, "absent"),
    ]

    relayed = relay(entries)

    assert unrelay(relayed) == entries
    assert unrelay(None) == []
    assert unrelay([*relayed, ["k", 1, "captured"], ["k", "v", "bogus"], ["k"], "k"]) == entries


def test_the_first_value_received_for_a_key_is_kept_and_a_different_one_noted() -> None:
    merged = ReportedValues()

    merged.add([SessionValue("a", "1", "captured"), SessionValue("rig", "gw0", "captured")])
    merged.add([SessionValue("rig", "gw1", "captured"), SessionValue("a", "1", "captured")])
    merged.add([SessionValue("rig", "gw2", "captured"), SessionValue("b", None, "absent")])

    assert merged.entries() == [
        SessionValue("a", "1", "captured"),
        SessionValue("rig", "gw0", "captured"),
        SessionValue("b", None, "absent"),
    ]
    assert merged.conflicting == ["rig"]


# --- the plan for the last report -------------------------------------------


def _values(*pairs: tuple[str, str | None]) -> list[SessionValue]:
    return [metadata.session_value(key, text) for key, text in pairs]


def _declared(
    *keys: str,
    files: tuple[CapturedFile, ...] = (),
    unread_file_keys: tuple[str, ...] = (),
) -> MetadataSection:
    return MetadataSection(
        declaration=metadata.DECLARATION_FILENAME,
        files=files,
        keys=tuple(DeclaredKey(key) for key in keys),
        unread_file_keys=unread_file_keys,
    )


def _read_file(*keys: str) -> CapturedFile:
    return CapturedFile("settings.json", "json", "captured", keys, "{}")


def test_without_a_declaration_every_value_is_sent_and_nothing_warns() -> None:
    reported = _values(("fpga.firmware", "1.1.0"), ("bench", None))

    plan = plan_values(reported, None)

    assert plan.values == tuple(reported)
    assert plan_warnings(plan) == []


def test_a_declared_key_nothing_reported_is_sent_absent_after_the_reported_ones() -> None:
    reported = _values(("bench", "lab-3"), ("fpga.firmware", "1.1.0"), ("fmc.hardware", None))

    plan = plan_values(reported, _declared("fpga.firmware", "fmc.hardware", "fmc.pic.firmware"))

    assert plan.values == (
        *reported,
        SessionValue("fmc.pic.firmware", None, "absent"),
    )
    assert plan.undeclared == ("bench",)


def test_undeclared_keys_warn_once_suggesting_the_closest_declared_key() -> None:
    reported = _values(("fpga.firmwre", "1.1.0"), ("bench", "lab-3"))

    plan = plan_values(reported, _declared("fpga.firmware", "fpga.hardware"))

    assert plan_warnings(plan) == [
        "vantage: undeclared metadata keys: 'fpga.firmwre' (did you mean 'fpga.firmware'?), 'bench'"
    ]


def test_no_close_match_is_looked_for_among_long_keys() -> None:
    """Comparing two keys takes time quadratic in their length, and two
    hundred long keys against two hundred more would hold the session's end
    for many seconds."""
    declared = "x" * 200
    plan = plan_values(_values(("x" * 199 + "y", "1")), _declared(declared))

    assert plan_warnings(plan) == [f"vantage: undeclared metadata keys: {'x' * 199 + 'y'!r}"]


def test_a_key_a_read_file_supplies_keeps_the_files_row() -> None:
    """The file's row reaches the server first, in the start report, and
    counts against the cap; the session's value is not sent, and a
    declared key the file supplies is not reported absent either."""
    section = _declared("region", "bench", files=(_read_file("region", "zone"),))
    reported = _values(("zone", "b"), ("fpga.firmware", "1.1.0"))

    plan = plan_values(reported, section)

    assert plan.values == (
        SessionValue("fpga.firmware", "1.1.0", "captured"),
        SessionValue("bench", None, "absent"),
    )
    assert plan.shadowed == ("zone",)
    assert plan.undeclared == ("fpga.firmware",)
    assert plan_warnings(plan)[0] == (
        "vantage: the metadata keys 'zone' are read from declared files and also "
        "reported by the session, the file values are kept"
    )


def test_a_value_for_a_key_of_an_unread_file_is_sent_and_declared_in_keys() -> None:
    """Without `--vantage-metadata` the files are not in the report to
    declare their keys, so the report declares the ones it sends a value
    for. A key the `keys` section names needs no second declaration, and
    without a value it is absent like any other."""
    section = _declared("region", "bench", unread_file_keys=("region", "zone", "rack"))
    reported = _values(("zone", "b"), ("region", "eu-west-1"))

    plan = plan_values(reported, section)

    assert plan.values == (
        SessionValue("zone", "b", "captured"),
        SessionValue("region", "eu-west-1", "captured"),
        SessionValue("bench", None, "absent"),
    )
    assert plan.named_keys == ("zone",)
    assert plan.shadowed == ()
    assert plan.undeclared == ()


def test_rows_past_the_cap_are_left_out_file_keys_first_then_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(metadata, "MAX_METADATA_ENTRIES", 3)
    section = _declared("declared", files=(_read_file("region"),))
    reported = _values(("a", "1"), ("b", "2"), ("c", "3"))

    plan = plan_values(reported, section)

    assert [entry.key for entry in plan.values] == ["a", "b"]
    assert plan.capped == 2
    assert (
        "vantage: 2 metadata key(s) left out: a run records at most 3, those read from "
        "declared files first"
    ) in plan_warnings(plan)


def test_values_past_the_budget_are_left_out_with_one_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one_entry = len('{"key": "a", "value": "1", "status": "captured"}, ')
    monkeypatch.setattr(budget, "MAX_METADATA_VALUES_BYTES", 2 * one_entry)

    plan = plan_values(_values(("a", "1"), ("b", "2"), ("c", "3")), None)

    assert [entry.key for entry in plan.values] == ["a", "b"]
    assert plan.over_budget == 1
    assert plan_warnings(plan) == [
        f"vantage: 1 metadata key(s) left out: the values reported exceed the "
        f"{2 * one_entry} bytes a run's last report holds"
    ]


def test_every_value_at_its_bounds_fits_the_budget_in_plain_text() -> None:
    """Only escape-heavy text can reach the budget."""
    reported = [
        SessionValue(f"{i:03d}" + "k" * 1021, "v" * 1024, "captured")
        for i in range(metadata.MAX_METADATA_ENTRIES)
    ]

    plan = plan_values(reported, None)

    assert plan.values == tuple(reported)
    assert session_metadata.plan_warnings(plan) == []


def test_workers_that_disagree_warn_once_naming_the_keys() -> None:
    assert plan_warnings(plan_values([], None), ["rig", "bench"]) == [
        "vantage: xdist workers reported different values for the metadata keys 'rig', "
        "'bench', the first value received is kept"
    ]
