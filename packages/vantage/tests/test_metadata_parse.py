"""`vantage.ingestion.metadata_parse`: the one place a *declared document* --
not the declaration itself -- is parsed. A malformed declared document never
raises; it degrades to a per-key or per-document status.

Parsing YAML with `yaml.compose()` rather than `safe_load()`/`load()` is
tested three ways: `!!python/object/apply` executes nothing, non-scalar
values are recognised by node type, and an alias-expansion bomb is never
expanded.
"""

from __future__ import annotations

import json
import time

import pytest
import yaml
from vantage.core.domain.metadata import MAX_METADATA_VALUE_BYTES, METADATA_CONTENT_TYPES
from vantage.ingestion.metadata_parse import KeyResult, parse


def test_json_malformed_document_yields_none() -> None:
    """A parser exception never propagates -- it becomes `None`, the
    module's "this file contributed no keys" marker."""
    result = parse('{"unterminated": ', "json", ["unterminated"])

    assert result is None


def test_yaml_malformed_document_yields_none() -> None:
    """A YAML parser exception becomes `None` too."""
    result = parse("key: [unclosed", "yaml", ["key"])

    assert result is None


def test_json_deep_nesting_raises_recursion_error_not_json_decode_error_and_yields_none() -> None:
    """A sufficiently deep JSON array overflows `json.loads`'s recursive
    descent parser with `RecursionError`, never `JSONDecodeError` -- both
    must be caught, or this document crashes ingestion instead of degrading.

    CPython's C-accelerated scanner survives 8,192 levels at the default
    recursion limit and only overflows from about 10,000, so the depth here
    is 20,000, and the bare `json.loads` call is asserted to raise first."""
    deeply_nested = "[" * 20_000 + "]" * 20_000
    with pytest.raises(RecursionError):
        # Confirms the raw stdlib call really does raise RecursionError, not
        # JSONDecodeError, for this input -- a handler that only catches
        # JSONDecodeError would let this propagate uncaught.
        json.loads(deeply_nested)

    result = parse(deeply_nested, "json", ["anything"])

    assert result is None


def test_yaml_alias_expansion_bomb_completes_quickly_and_does_not_expand() -> None:
    """`yaml.compose()` builds a node graph that shares aliases instead of
    expanding them, so a few hundred bytes of nested aliases must parse in
    well under a second."""
    bomb = "a0: &a0 [x, x, x, x, x, x, x, x, x, x]\n"
    for i in range(1, 12):
        bomb += f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}, *a{i - 1}, *a{i - 1}, *a{i - 1}]\n"
    bomb += "top: *a11\n"

    started = time.monotonic()
    result = parse(bomb, "yaml", ["top"])
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert result is not None
    # `top` resolves to a sequence (non-scalar), never constructed as a
    # Python list -- proving the bomb was never expanded into one.
    assert result["top"].status == "not_scalar"


def test_yaml_python_object_apply_document_yields_none_and_executes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canonical `PyYAML` RCE proof-of-concept. `yaml.compose()` never
    calls a constructor, so `os.system` must never run, and the module's own
    shape check (top level must be a mapping to have keys at all) turns this
    into `None` rather than a well-formed document with a `keys` field."""
    calls: list[str] = []
    monkeypatch.setattr("os.system", lambda command: calls.append(command))

    document = '!!python/object/apply:os.system ["echo pwned"]'
    result = parse(document, "yaml", ["anything"])

    assert result is None
    assert calls == []


def test_declared_key_absent_from_a_well_formed_document_is_marked_absent() -> None:
    """A declared key missing from a well-formed document is marked
    `absent`, with no value."""
    result = parse('{"firmware_version": "2.1"}', "json", ["board_revision"])

    assert result is not None
    assert result["board_revision"].status == "absent"
    assert result["board_revision"].value is None


def test_declared_key_present_but_non_scalar_is_marked_not_scalar_never_serialized() -> None:
    """A non-scalar declared value is marked `not_scalar`, never
    serialized into a string."""
    result = parse('{"toolchain": ["gcc", "12.2"]}', "json", ["toolchain"])

    assert result is not None
    assert result["toolchain"].status == "not_scalar"
    assert result["toolchain"].value is None


def test_yaml_declared_key_present_but_non_scalar_is_marked_not_scalar() -> None:
    """In YAML a mapping value is non-scalar too, and `compose()` needs no
    dedicated check to see that -- a `MappingNode` is simply not a
    `ScalarNode`."""
    result = parse("build:\n  toolchain: gcc\n  version: 12.2\n", "yaml", ["build"])

    assert result is not None
    assert result["build"].status == "not_scalar"
    assert result["build"].value is None


def test_declared_value_over_the_per_value_bound_is_dropped_whole_marked_value_too_large() -> None:
    """An oversized value is dropped whole and marked `value_too_large`,
    never truncated -- the result carries no partial value."""
    oversized = "x" * (MAX_METADATA_VALUE_BYTES + 1)
    result = parse(json.dumps({"board_revision": oversized}), "json", ["board_revision"])

    assert result is not None
    assert result["board_revision"].status == "value_too_large"
    assert result["board_revision"].value is None


def test_declared_value_within_the_per_value_bound_is_captured_whole() -> None:
    """A value exactly at the bound is captured unchanged."""
    at_bound = "y" * MAX_METADATA_VALUE_BYTES
    result = parse(json.dumps({"board_revision": at_bound}), "json", ["board_revision"])

    assert result is not None
    assert result["board_revision"].status == "captured"
    assert result["board_revision"].value == at_bound


def test_yaml_declared_value_within_bound_is_captured_as_literal_text() -> None:
    """A YAML declared document is parsed. `ScalarNode.value` is always the
    raw literal text, never a type-resolved Python object -- so a quoted
    string and an unquoted one both come back exactly as written."""
    result = parse(
        'firmware_version: "2.1"\nboard_revision: C\n',
        "yaml",
        ["firmware_version", "board_revision"],
    )

    assert result is not None
    assert result["firmware_version"].status == "captured"
    assert result["firmware_version"].value == "2.1"
    assert result["board_revision"].status == "captured"
    assert result["board_revision"].value == "C"


def test_unsupported_content_type_is_treated_the_same_as_malformed() -> None:
    """A content type the parser does not support is treated as
    malformed."""
    result = parse('{"k": "v"}', "toml", ["k"])

    assert result is None


_DOCUMENT_PER_FORMAT = {"json": '{"k": "v"}', "yaml": "k: v\n"}


def test_every_storable_content_type_has_a_parser() -> None:
    """`parse` hands every storable format but json to the YAML parser, so a
    format added to `METADATA_CONTENT_TYPES` alone would be parsed as YAML.
    Adding one here, with a sample document, is what makes that a choice."""
    assert set(_DOCUMENT_PER_FORMAT) == METADATA_CONTENT_TYPES
    for content_type, content in _DOCUMENT_PER_FORMAT.items():
        assert parse(content, content_type, ["k"]) == {"k": KeyResult(status="captured", value="v")}


def test_json_top_level_that_is_not_an_object_yields_none() -> None:
    """A document that parses cleanly but has no top level to hold keys at
    all cannot yield any declared key -- treated the same as malformed,
    since nothing distinguishes "no keys reachable" from "wrong shape
    entirely" for a document this format was never going to satisfy."""
    result = parse("[1, 2, 3]", "json", ["anything"])

    assert result is None


# --- documents that must degrade, never raise --------------------------------


def test_yaml_escaped_surrogate_pair_is_combined_into_one_character() -> None:
    """PyYAML turns each `\\uXXXX` escape into its own code point, so a
    non-BMP character written by a JSON serialiser -- valid YAML -- arrives
    as two surrogates. They are combined, as a JSON reader would."""
    content = json.dumps({"release_name": "Rocket \U0001f680"})

    result = parse(content, "yaml", ["release_name"])

    assert result == {"release_name": KeyResult(status="captured", value="Rocket \U0001f680")}


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        ('release_name: "x\\udc80"\n', "yaml"),
        ('{"release_name": "x\\ud800"}', "json"),
        ('{"x\\ud800": "2.1"}', "json"),
    ],
    ids=["yaml_value", "json_value", "json_key"],
)
def test_a_lone_surrogate_escape_makes_the_document_malformed(
    content: str, content_type: str
) -> None:
    """A lone surrogate has no UTF-8 form, so the text cannot be stored; the
    document is unusable and degrades to `None` rather than raising."""
    assert parse(content, content_type, ["release_name"]) is None


def test_json_integer_beyond_the_digit_limit_is_classified_not_raised() -> None:
    """Converting a number literal over 4,300 digits to `int` raises a plain
    `ValueError`; the literal text is kept instead, so it is only a value
    too large to store, and the file's other keys are unaffected."""
    long_literal = "1" + "0" * 4999
    with pytest.raises(ValueError, match="4300"):
        json.loads(long_literal)

    result = parse(
        f'{{"build": {long_literal}, "firmware_version": "2.1"}}',
        "json",
        ["build", "firmware_version"],
    )

    assert result == {
        "build": KeyResult(status="value_too_large", value=None),
        "firmware_version": KeyResult(status="captured", value="2.1"),
    }


# --- JSON numbers keep their literal text ------------------------------------


@pytest.mark.parametrize(
    "literal",
    ["2.10", "3.140", "1e3", "1E3", "0.10000000000000000001", "1e400", "-0", "NaN", "-Infinity"],
)
def test_json_declared_number_is_captured_as_literal_text(literal: str) -> None:
    """Values are compared as strings, so a JSON number must keep the text
    the file holds -- `2.10` is not `2.1` -- exactly as YAML does."""
    json_result = parse(f'{{"firmware_version": {literal}}}', "json", ["firmware_version"])
    yaml_result = parse(f"firmware_version: {literal}\n", "yaml", ["firmware_version"])

    assert json_result == {"firmware_version": KeyResult(status="captured", value=literal)}
    assert json_result == yaml_result


# --- YAML merge keys ---------------------------------------------------------


def _captured(result: dict[str, KeyResult] | None, key: str) -> str | None:
    assert result is not None
    assert result[key].status == "captured"
    return result[key].value


def test_yaml_key_pulled_in_through_a_merge_is_captured() -> None:
    """`<<: *anchor` is how a defaults/overrides config shares keys; every
    YAML loader a user would check with resolves it."""
    content = 'defaults: &d {firmware_version: "2.1"}\n<<: *d\nboard_revision: C\n'

    result = parse(content, "yaml", ["firmware_version", "board_revision"])

    assert _captured(result, "firmware_version") == "2.1"
    assert _captured(result, "board_revision") == "C"


def test_yaml_merge_precedence_matches_safe_load() -> None:
    """An explicit key beats a merged one, and in a sequence merge the
    earlier source wins."""
    content = (
        "a: &a {board_revision: A, toolchain: gcc}\n"
        "b: &b {board_revision: B, toolchain: clang, os: linux}\n"
        "<<: [*a, *b]\n"
        "toolchain: icc\n"
    )
    keys = ["board_revision", "toolchain", "os"]
    expected = yaml.safe_load(content)

    result = parse(content, "yaml", keys)

    assert {key: _captured(result, key) for key in keys} == {key: expected[key] for key in keys}


@pytest.mark.parametrize(
    "content",
    [
        "a: &x {c: '2'}\n<<: *x\n<<: {c: '3'}\n",
        "a: &x {c: '2'}\nb: &y {c: '4'}\n<<: [*x, *y]\n<<: {c: '3'}\n",
        "a: &x {c: '2'}\nb: &y {c: '4', d: '5'}\n<<: {c: '3'}\n<<: [*x, *y]\n",
    ],
    ids=["mapping-then-mapping", "sequence-then-mapping", "mapping-then-sequence"],
)
def test_yaml_a_later_merge_key_beats_an_earlier_one_as_in_safe_load(content: str) -> None:
    """Two `<<` keys in one mapping: `safe_load` merges them in order, so
    the later one's value wins, below the explicit keys. A declared key
    must be recorded with the value any YAML tool shows for the document."""
    expected = yaml.safe_load(content)

    result = parse(content, "yaml", ["c", "d"])

    assert _captured(result, "c") == expected["c"]
    assert result is not None
    assert result["d"].value == expected.get("d")


def test_yaml_nested_merge_bomb_completes_quickly() -> None:
    """Each mapping's merged view is built once, so a chain of mappings that
    each merge the previous one twice stays linear instead of doubling per
    level the way copying the merged pairs would."""
    bomb = "a0: &a0 {firmware_version: '2.1'}\n"
    for i in range(1, 61):
        bomb += f"a{i}: &a{i} {{<<: [*a{i - 1}, *a{i - 1}]}}\n"
    bomb += "<<: *a60\n"

    started = time.monotonic()
    result = parse(bomb, "yaml", ["firmware_version"])
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert _captured(result, "firmware_version") == "2.1"


def test_yaml_self_referencing_merge_terminates() -> None:
    result = parse("x: &a {k: v, <<: *a}\n<<: *a\n", "yaml", ["k"])

    assert _captured(result, "k") == "v"


def test_yaml_quoted_merge_key_is_an_ordinary_key() -> None:
    """Only a plain `<<` is a merge; a quoted one is a key like any other."""
    result = parse("'<<': literal\n", "yaml", ["<<"])

    assert _captured(result, "<<") == "literal"


# --- byte order mark ---------------------------------------------------------


@pytest.mark.parametrize("content_type", ["json", "yaml"])
def test_a_leading_byte_order_mark_is_ignored(content_type: str) -> None:
    """Editors on Windows save UTF-8 with a BOM, and the plugin ships the
    decoded text as it is. YAML skips it; JSON must too."""
    result = parse('\ufeff{"firmware_version": "2.1"}', content_type, ["firmware_version"])

    assert result == {"firmware_version": KeyResult(status="captured", value="2.1")}


def test_a_byte_order_mark_after_the_start_is_still_malformed() -> None:
    assert parse(' \ufeff{"firmware_version": "2.1"}', "json", ["firmware_version"]) is None
