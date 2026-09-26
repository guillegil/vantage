"""The uniform 64 KiB bound on stored text fields.

Applied by `ingestion/conversion.py` to the commit subject, the interrupt
reason, the failure evidence text and captured output. It only bounds size:
the values are stored, not echoed back, so output encoding is the reader's
concern.

**The bound is on UTF-8 bytes, cut at a character boundary.**
`value[:65536]` slices characters, not bytes, and can store up to four times
the intended amount. Instead: encode, slice the bytes, and decode with
``errors="ignore"`` so a multi-byte character straddling the cut is dropped
whole rather than stored mangled.
"""

from __future__ import annotations

MAX_TEXT_FIELD_BYTES = 64 * 1024


def truncate(value: str | None) -> tuple[str | None, bool]:
    """Cut ``value`` to at most `MAX_TEXT_FIELD_BYTES` of UTF-8.

    Returns ``(None, False)`` for `None` -- there is nothing to truncate.
    Returns ``(value, False)`` unchanged when it already fits. Otherwise
    returns the largest whole-character UTF-8 prefix that fits, with the
    flag `True`.
    """
    if value is None:
        return None, False
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_TEXT_FIELD_BYTES:
        return value, False
    truncated = encoded[:MAX_TEXT_FIELD_BYTES].decode("utf-8", errors="ignore")
    return truncated, True


__all__ = ["MAX_TEXT_FIELD_BYTES", "truncate"]
