"""Parse a *declared document* named by a metadata section (design.md D97,
ADR-0017 C4). **The only module in this project that imports `yaml`** --
`deptry` and the RQ-24/RQ-26 architecture tests can state where the
dependency lives precisely because nothing else reaches it.

This is not the declaration parser. `pytest_vantage.metadata.read_declaration`
parses the plugin's OWN file (`vantage-metadata.json`) and may reject it
outright, because refusing it captures nothing -- the same posture the
flag-absent path already has. A *declared document* -- the file a
declaration names, read and shipped by the plugin, parsed here on the server
-- is different: **it MUST NOT fail the run's ingestion.** A parse failure
degrades to "this file contributed no keys," and every failure this module
detects becomes exactly one thing: `None` for the whole document, or a
per-key `KeyResult` whose status is never `"captured"`. Nothing here ever
raises past its own boundary; `parse()`'s only exit is a return value.

**YAML is parsed with `yaml.compose()`, never `yaml.safe_load()` or
`yaml.load()`.** This is a security decision, not a style preference, and it
buys three separate properties:

1. **No Python object is ever constructed.** `compose()` builds PyYAML's
   node graph and stops -- it never calls a tag's constructor. The
   `!!python/object/apply` class of remote code execution is not merely
   unreached here, it is unreachable: nothing this module does could ever
   invoke it, with or without a hostile document.
2. **Non-scalar values fall out for free.** A `SequenceNode` or
   `MappingNode` is simply not a `ScalarNode` -- classifying a declared
   key's value as scalar-or-not needs no separate type check beyond asking
   which `Node` subclass the walk produced.
3. **The alias-expansion bomb is defused.** `yaml.safe_load()` offers no
   depth or expansion limit, and a few hundred bytes of nested YAML aliases
   expands to gigabytes during construction -- because `safe_load` builds
   the aliased Python objects out fully, once per reference. A node graph
   instead *shares* aliases: the same `Node` object is referenced wherever
   an alias points at it, never duplicated, so `compose()`'s work is
   bounded by the size of the *source text*, not by how many times an alias
   is referenced. The 8 KiB per-file read bound (`pytest_vantage.metadata`)
   therefore actually bounds the work here; against `safe_load` it would
   not, because expansion is exponential in source size, independent of
   that bound.

A `ScalarNode.value` is always the raw literal text as written in the
source -- YAML never resolves it to a typed Python value here, so
`firmware_version: 2.1` and `firmware_version: "2.1"` both come back as the
string `"2.1"`. That is what D91 means by "comparison is string equality...
the declaration names keys, not types": there is no type to lose, because
no type was ever constructed.

JSON is parsed with stdlib `json.loads`, which has neither an
object-construction hazard nor an alias hazard -- but deep nesting raises
**`RecursionError`, not `JSONDecodeError`**, because `json`'s decoder is a
recursive-descent parser and Python's default recursion limit is far below
what 8 KiB of nested brackets can produce. Both exceptions are caught here,
alongside `yaml.YAMLError`, and all three collapse to the same `None`.

A document whose top level is not a mapping (JSON: not a `dict`; YAML: not
a `MappingNode`) cannot hold any declared key at all, so it is treated the
same as a malformed document -- `None` -- rather than a well-formed
document with zero keys. `compose()` returning `None` for a genuinely empty
document falls into this same branch, since `None` is not a `MappingNode`
either.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

import yaml
from yaml.nodes import MappingNode, ScalarNode

from vantage.core.domain.metadata import MAX_METADATA_VALUE_BYTES

_ADMISSIBLE_CONTENT_TYPES = frozenset({"json", "yaml"})
"""`sdd-verify` SUGGESTION-2: narrower than `routes/runs.py`'s
`_KNOWN_METADATA_CONTENT_TYPES` (which also admits `"toml"`) on purpose --
this is load-bearing, not an oversight to reconcile. Storage must match
`schema.sql`'s CHECK, which already includes `toml` for a future slice;
this parser supports exactly two formats today and routes `toml` to
`"malformed"` like any other unsupported type. Widening this set to match
the other would silently start attempting to parse a format `parse()` has
no branch for."""


@dataclass(frozen=True, slots=True)
class KeyResult:
    """The outcome for one declared key against one parsed document
    (design.md D97 classes 8-10). `value` is `None` whenever `status` is
    not `"captured"` -- the same "declared-but-dropped is a row" contract
    D95 states for the storage side."""

    status: str
    value: str | None


def parse(content: str, content_type: str, keys: Sequence[str]) -> dict[str, KeyResult] | None:
    """Parse `content` as `content_type` and classify every name in `keys`.

    Returns `None` when the document itself could not be used at all --
    a parser exception (`json.JSONDecodeError`, `yaml.YAMLError`,
    `RecursionError`), an unsupported `content_type`, or a top level that
    is not a mapping. That `None` is design.md D97 class 7, `"malformed"`;
    the caller marks the whole file `malformed` and every one of its
    declared keys `source_unavailable`.

    Otherwise returns exactly one `KeyResult` per entry of `keys`, in no
    particular order requirement -- `absent` (class 8), `not_scalar`
    (class 9), `value_too_large` (class 10), or `captured`.
    """
    if content_type not in _ADMISSIBLE_CONTENT_TYPES:
        return None
    try:
        document = _parse_json(content) if content_type == "json" else _parse_yaml(content)
    except (json.JSONDecodeError, yaml.YAMLError, RecursionError):
        return None
    if document is None:
        return None
    return {key: _classify(document, key) for key in keys}


def _parse_json(content: str) -> dict[str, str | None] | None:
    """Return a mapping of top-level key name to its stringified scalar
    value (`None` for a non-scalar value), or `None` if the top level is
    not a JSON object. May raise `json.JSONDecodeError` or `RecursionError`
    -- both are caught by `parse`, never here."""
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        return None
    result: dict[str, str | None] = {}
    for key, value in parsed.items():
        if isinstance(value, (dict, list)):
            result[key] = None
        elif isinstance(value, str):
            result[key] = value
        elif isinstance(value, bool):
            result[key] = "true" if value else "false"
        elif value is None:
            result[key] = "null"
        else:
            result[key] = json.dumps(value)
    return result


def _parse_yaml(content: str) -> dict[str, str | None] | None:
    """The YAML half of `_parse_json`: walk the top-level `MappingNode`'s
    pairs, taking only `ScalarNode` values. May raise `yaml.YAMLError` or
    `RecursionError` -- both are caught by `parse`, never here."""
    root = yaml.compose(content)
    if not isinstance(root, MappingNode):
        return None
    result: dict[str, str | None] = {}
    for key_node, value_node in root.value:
        if not isinstance(key_node, ScalarNode):
            continue
        result[key_node.value] = value_node.value if isinstance(value_node, ScalarNode) else None
    return result


def _classify(document: dict[str, str | None], key: str) -> KeyResult:
    if key not in document:
        return KeyResult(status="absent", value=None)
    value = document[key]
    if value is None:
        return KeyResult(status="not_scalar", value=None)
    if len(value.encode("utf-8")) > MAX_METADATA_VALUE_BYTES:
        return KeyResult(status="value_too_large", value=None)
    return KeyResult(status="captured", value=value)


__all__ = ["KeyResult", "parse"]
