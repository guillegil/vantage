"""Parse a *declared document*: a file named by a metadata declaration, read
and shipped by the plugin, and parsed here on the server. This is the only
module in the project that imports `yaml`.

This is not the declaration parser. `pytest_vantage.metadata.read_declaration`
parses the plugin's own `vantage-metadata.json` and may reject it outright. A
declared document, by contrast, must never fail ingestion of the run: every
failure becomes either `None` for the whole document or a per-key `KeyResult`
whose status is not `"captured"`. `parse()` never raises, whatever the
content.

YAML is parsed with `yaml.compose()`, never `yaml.safe_load()` or
`yaml.load()`:

1. No Python object is ever constructed. `compose()` builds the node graph and
   never calls a tag's constructor, so `!!python/object/apply` and similar
   tags cannot execute anything.
2. Non-scalar values are recognised by node type: a `SequenceNode` or
   `MappingNode` is simply not a `ScalarNode`.
3. Alias bombs stay harmless. The node graph shares an aliased node instead
   of copying it, and merge keys (`<<`), which `compose()` leaves unresolved,
   are resolved by `_mapping_view` without copying either, so the work is
   bounded by the size of the source text.

Values are compared as strings; no type is ever constructed that could be
lost. A `ScalarNode.value` is the raw literal text, so `firmware_version: 2.1`
and `firmware_version: "2.1"` both yield the string `"2.1"`, and a JSON number
keeps its literal text the same way: `2.10` stays `"2.10"`.

Everything a parser raises on unusable input collapses to `None`:
`json.loads` raises `JSONDecodeError`, and `RecursionError` on deeply nested
input; PyYAML raises `yaml.YAMLError`; and a lone surrogate, which has no
UTF-8 form and so cannot be stored, raises `UnicodeDecodeError` from
`_well_formed`. A document whose top level is not a mapping (including an
empty YAML document, for which `compose()` returns `None`) cannot hold a
declared key and is also `None`. One leading byte order mark is ignored in
either format.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from vantage.core.domain.metadata import MAX_METADATA_VALUE_BYTES

_ADMISSIBLE_CONTENT_TYPES = frozenset({"json", "yaml"})
"""Deliberately narrower than `routes/runs.py`'s
`_KNOWN_METADATA_CONTENT_TYPES`, which also admits `"toml"` to match the
`schema.sql` CHECK. This parser has no TOML branch, so `toml` is treated as
malformed; adding it here would make `parse()` hand TOML to the YAML parser."""

_MERGE_TAG = "tag:yaml.org,2002:merge"
"""The tag `compose()` resolves a plain `<<` key to. A quoted `'<<'` keeps
the string tag and stays an ordinary key."""


@dataclass(frozen=True, slots=True)
class KeyResult:
    """The outcome for one declared key against one parsed document.
    `value` is `None` whenever `status` is not `"captured"`; the key is still
    reported, so a declared-but-dropped key is stored with its status."""

    status: str
    value: str | None


def parse(content: str, content_type: str, keys: Sequence[str]) -> dict[str, KeyResult] | None:
    """Parse `content` as `content_type` and classify every name in `keys`.

    Returns `None` when the document cannot be used at all: a parse failure
    (see the module docstring), an unsupported `content_type`, or a top
    level that is not a mapping. The caller then marks the whole file
    `malformed` and each of its declared keys `source_unavailable`.

    Otherwise returns one `KeyResult` per entry of `keys`, with status
    `absent`, `not_scalar`, `value_too_large` or `captured`.
    """
    if content_type not in _ADMISSIBLE_CONTENT_TYPES:
        return None
    try:
        document = _parse_json(content) if content_type == "json" else _parse_yaml(content)
    except (ValueError, yaml.YAMLError, RecursionError):
        return None
    if document is None:
        return None
    return {key: _classify(document, key) for key in keys}


def _well_formed(text: str) -> str:
    """`text` with each surrogate pair combined into the character it
    encodes; raises `UnicodeDecodeError` on a lone surrogate.

    PyYAML turns every `\\uXXXX` escape into its own code point, so a
    non-BMP character escaped as a pair -- what a JSON serialiser writes --
    arrives as two surrogates. `json.loads` combines pairs but passes a lone
    one through.
    """
    if text.isascii():
        return text
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le")


def _parse_json(content: str) -> dict[str, str | None] | None:
    """Return a mapping of top-level key name to its scalar value as text
    (`None` for a non-scalar value), or `None` if the top level is not a
    JSON object.

    `json.loads` only skips a byte order mark in bytes, so one leading BOM
    is removed here. Every number reaches the hooks as its literal text and
    is never converted, which also keeps an integer literal past the
    interpreter's digit limit from raising.
    """
    parsed = json.loads(
        content.removeprefix("\ufeff"), parse_int=str, parse_float=str, parse_constant=str
    )
    if not isinstance(parsed, dict):
        return None
    result: dict[str, str | None] = {}
    for key, value in parsed.items():
        if isinstance(value, (dict, list)):
            result[_well_formed(key)] = None
        elif isinstance(value, str):
            result[_well_formed(key)] = _well_formed(value)
        elif isinstance(value, bool):
            result[_well_formed(key)] = "true" if value else "false"
        else:
            result[_well_formed(key)] = "null"
    return result


def _parse_yaml(content: str) -> dict[str, str | None] | None:
    """The YAML half of `_parse_json`: the top-level mapping's keys, merge
    keys resolved, taking only `ScalarNode` values."""
    root = yaml.compose(content)
    if not isinstance(root, MappingNode):
        return None
    return {
        _well_formed(key): None if value is None else _well_formed(value)
        for key, value in _mapping_view(root, {}, set()).items()
    }


def _mapping_view(
    node: MappingNode, memo: dict[int, dict[str, str | None]], active: set[int]
) -> dict[str, str | None]:
    """`node`'s keys as `safe_load` would see them: an explicit key beats a
    merged one, and in a sequence merge the earlier source wins.

    Never `SafeConstructor.flatten_mapping`: it copies the merged pairs into
    every mapping, which doubles per level on `<<: [*a, *a]` chains, and
    recurses forever on an anchor that merges itself. Each mapping's view is
    built once (`memo`), and a mapping already being built (`active`)
    contributes nothing to itself. A merge value that is not a mapping is
    ignored rather than failing the whole document.
    """
    known = memo.get(id(node))
    if known is not None:
        return known
    if id(node) in active:
        return {}
    active.add(id(node))
    merged: dict[str, str | None] = {}
    explicit: dict[str, str | None] = {}
    for key_node, value_node in node.value:
        if key_node.tag == _MERGE_TAG:
            sources = value_node.value if isinstance(value_node, SequenceNode) else [value_node]
            for source in sources:
                if isinstance(source, MappingNode):
                    for key, value in _mapping_view(source, memo, active).items():
                        merged.setdefault(key, value)
            continue
        if isinstance(key_node, ScalarNode):
            explicit[key_node.value] = (
                value_node.value if isinstance(value_node, ScalarNode) else None
            )
    active.discard(id(node))
    view = {**merged, **explicit}
    memo[id(node)] = view
    return view


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
