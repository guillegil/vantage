"""Parse a *declared document*: a file named by a metadata declaration, read
and shipped by the plugin, and parsed here on the server. This is the only
module in the project that imports `yaml`.

This is not the declaration parser. `pytest_vantage.metadata.read_declaration`
parses the plugin's own `vantage-metadata.json` and may reject it outright. A
declared document, by contrast, must never fail ingestion of the run: every
failure becomes either `None` for the whole document or a per-key `KeyResult`
whose status is not `"captured"`. `parse()` never raises.

YAML is parsed with `yaml.compose()`, never `yaml.safe_load()` or
`yaml.load()`:

1. No Python object is ever constructed. `compose()` builds the node graph and
   never calls a tag's constructor, so `!!python/object/apply` and similar
   tags cannot execute anything.
2. Non-scalar values are recognised by node type: a `SequenceNode` or
   `MappingNode` is simply not a `ScalarNode`.
3. Alias bombs stay harmless. The node graph shares an aliased node instead
   of copying it, and only top-level scalars are read, so the work is bounded
   by the size of the source text.

A `ScalarNode.value` is the raw literal text, so `firmware_version: 2.1` and
`firmware_version: "2.1"` both yield the string `"2.1"`. Values are compared
as strings; no type is ever constructed that could be lost.

`json.loads` raises `RecursionError`, not `JSONDecodeError`, on deeply nested
input. Both are caught, alongside `yaml.YAMLError`, and collapse to `None`. A
document whose top level is not a mapping (including an empty YAML document,
for which `compose()` returns `None`) cannot hold a declared key and is also
`None`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

import yaml
from yaml.nodes import MappingNode, ScalarNode

from vantage.core.domain.metadata import MAX_METADATA_VALUE_BYTES

_ADMISSIBLE_CONTENT_TYPES = frozenset({"json", "yaml"})
"""Deliberately narrower than `routes/runs.py`'s
`_KNOWN_METADATA_CONTENT_TYPES`, which also admits `"toml"` to match the
`schema.sql` CHECK. This parser has no TOML branch, so `toml` is treated as
malformed; adding it here would make `parse()` hand TOML to the YAML parser."""


@dataclass(frozen=True, slots=True)
class KeyResult:
    """The outcome for one declared key against one parsed document.
    `value` is `None` whenever `status` is not `"captured"`; the key is still
    reported, so a declared-but-dropped key is stored with its status."""

    status: str
    value: str | None


def parse(content: str, content_type: str, keys: Sequence[str]) -> dict[str, KeyResult] | None:
    """Parse `content` as `content_type` and classify every name in `keys`.

    Returns `None` when the document cannot be used at all: a parser
    exception (`json.JSONDecodeError`, `yaml.YAMLError`, `RecursionError`),
    an unsupported `content_type`, or a top level that is not a mapping. The
    caller then marks the whole file `malformed` and each of its declared
    keys `source_unavailable`.

    Otherwise returns one `KeyResult` per entry of `keys`, with status
    `absent`, `not_scalar`, `value_too_large` or `captured`.
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
