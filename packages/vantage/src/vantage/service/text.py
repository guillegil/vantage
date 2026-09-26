"""Client text as every store can hold it.

PostgreSQL's `text` cannot hold U+0000 at all, and no UTF-8 encoder accepts
a lone surrogate. Text that is going to be stored has both replaced by
U+FFFD as the request body is decoded, before anything is validated, so
every adapter stores the same text and none refuses it.

A value a route only looks up with -- a node id, a metadata filter, a
section name to delete -- is never rewritten into something else to look
for. Nothing stored holds U+0000, so a value holding one matches nothing,
and the route answers it without asking the store.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

NUL = "\x00"
REPLACEMENT_CHARACTER = "�"

_NUL_OR_LONE_SURROGATE = re.compile("[\x00\ud800-\udfff]")


def without_nul(text: str) -> str:
    """`text` with every U+0000 replaced by U+FFFD."""
    return text.replace(NUL, REPLACEMENT_CHARACTER) if NUL in text else text


def _without_nul_or_lone_surrogate(text: str) -> str:
    # ASCII holds no surrogate, and the check is far cheaper than the regex.
    if text.isascii():
        return without_nul(text)
    return _NUL_OR_LONE_SURROGATE.sub(REPLACEMENT_CHARACTER, text)


def storable_json(payload: Any, *, replace_lone_surrogates: bool) -> Any:
    """`payload`, parsed JSON, with every U+0000 in its keys and string
    values replaced by U+FFFD, and every lone surrogate too when asked.

    The walk is iterative because `json.loads` accepts nesting deeper than
    Python's recursion limit. Valid surrogate pairs were already combined by
    `json.loads`, so any surrogate left is a lone one.
    """
    clean: Callable[[str], str] = (
        _without_nul_or_lone_surrogate if replace_lone_surrogates else without_nul
    )
    if isinstance(payload, str):
        return clean(payload)
    pending: list[Any] = [payload]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if any(clean(key) != key for key in node):
                cleaned = {clean(key): value for key, value in node.items()}
                node.clear()
                node.update(cleaned)
            for key, value in node.items():
                if isinstance(value, str):
                    node[key] = clean(value)
                elif isinstance(value, (dict, list)):
                    pending.append(value)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                if isinstance(value, str):
                    node[index] = clean(value)
                elif isinstance(value, (dict, list)):
                    pending.append(value)
    return payload


__all__ = ["NUL", "REPLACEMENT_CHARACTER", "storable_json", "without_nul"]
