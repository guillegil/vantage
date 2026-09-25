"""`vantage.service.metadata_parse`: the one place a *declared document* --
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
from vantage.core.domain.metadata import MAX_METADATA_VALUE_BYTES
from vantage.service.metadata_parse import parse


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


def test_json_top_level_that_is_not_an_object_yields_none() -> None:
    """A document that parses cleanly but has no top level to hold keys at
    all cannot yield any declared key -- treated the same as malformed,
    since nothing distinguishes "no keys reachable" from "wrong shape
    entirely" for a document this format was never going to satisfy."""
    result = parse("[1, 2, 3]", "json", ["anything"])

    assert result is None
