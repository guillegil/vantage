"""The page cursor the run list and a test's history hand out: a `RunKey`
as an opaque token.

A client passes `next_cursor` back as `cursor` and never reads it, so its
spelling is the server's to change. It carries the key's `started_at` to the
microsecond, in UTC, and its run id, joined by `|` and base64url-encoded
without padding. The timestamp must survive the round trip exactly: a cursor
a microsecond off would start the next page one run early or late.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone

from vantage.core.domain.execution import IDENTITY_PATTERN
from vantage.core.ports.storage import RunKey

MAX_CURSOR_CHARS = 128
"""Longer than any cursor `encode_cursor` makes (88 characters): a longer
value was never handed out, and is refused before it is decoded."""

_SEPARATOR = "|"

_IDENTITY_RE = re.compile(IDENTITY_PATTERN)


def encode_cursor(key: RunKey) -> str:
    """The cursor naming `key`: the page after it starts just past it."""
    started_at = key.started_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    raw = f"{started_at}{_SEPARATOR}{key.run_id}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> RunKey | None:
    """The key `cursor` names, or None when `encode_cursor` could not have
    made it."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True).decode("ascii")
        started_text, separator, run_id = raw.partition(_SEPARATOR)
        started_at = datetime.fromisoformat(started_text)
    except ValueError:  # binascii.Error and UnicodeDecodeError are ValueErrors
        return None
    if not separator or started_at.tzinfo is None or not _IDENTITY_RE.fullmatch(run_id):
        return None
    return RunKey(started_at=started_at, run_id=run_id)


__all__ = ["MAX_CURSOR_CHARS", "decode_cursor", "encode_cursor"]
