"""RQ-22's uniform 64 KiB bound, first written here (design.md D49).

The server owns this in one pure helper reached from
`service/routes/runs.py`. `errors.py::safe_segment` does not apply -- that
allow-lists CLIENT-CHOSEN text for echoing back; a commit subject is stored
data, never echoed (ADR-8 owns output encoding, not this module).

**The bound is on UTF-8 BYTES, cut at a character boundary.** `value[:65536]`
slices Python characters, not bytes, and can store twice the intended amount
for two-byte characters. The correct sequence: encode to UTF-8, slice the
BYTES at the boundary, decode with ``errors="ignore"`` so a multi-byte
character left straddling the cut is dropped whole, never stored mangled.

The project's first truncation writer -- built so RQ-22 can adopt
`MAX_TEXT_FIELD_BYTES`/`truncate` unchanged when it lands its own.
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
