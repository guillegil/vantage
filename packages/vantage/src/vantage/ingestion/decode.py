"""Strict JSON decoding of a complete body, with its text made storable.

Used for every body the server takes -- a session report, a section
upsert, and the users and tokens routes' bodies -- once `service/body.py`
has read it under its size cap, and for
every report the local store takes, encoded the way the plugin sends one.
It blocks for as long as the body is large, so the server calls it in the
threadpool.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

from vantage.ingestion.errors import InvalidJsonError
from vantage.ingestion.text import storable_json


def _refuse_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not JSON")


def decode_json(body: bytes, *, replace_lone_surrogates: bool = False) -> Any:
    """Parse `body` as strict UTF-8 JSON, or raise `InvalidJsonError`.

    Every U+0000 in a key or string value comes back as U+FFFD, and so does
    every lone surrogate when `replace_lone_surrogates` is set; a caller
    that leaves them refuses the text they are in itself.

    Decoding first, strictly, refuses UTF-8-encoded surrogates, which
    `json.loads(bytes)` would accept. One leading byte order mark is
    skipped: it is still UTF-8, and `json.loads` of the bytes skips it too.
    `NaN`, `Infinity` and `-Infinity` are refused: `json.loads` accepts them,
    but they are not JSON, and a value no JSON response can carry would read
    back as `null`. Every parse failure is the same client error:
    `json.loads` raises `JSONDecodeError`, `UnicodeDecodeError` and the
    integer digit limit's plain `ValueError`, and `RecursionError` for deep
    nesting.
    """
    try:
        payload = json.loads(body.decode("utf-8-sig"), parse_constant=_refuse_constant)
    except (ValueError, RecursionError) as exc:
        raise InvalidJsonError() from exc
    return storable_json(payload, replace_lone_surrogates=replace_lone_surrogates)


__all__ = ["decode_json"]
