"""RQ-22's uniform 64 KiB bound, first written here (design.md D49).

**Where**: the server owns this, in one pure helper reached from
`service/routes/runs.py`. `errors.py::safe_segment` is not applied here --
that is an allow-list for echoing CLIENT-CHOSEN text back in a response
body; a commit subject is stored data, the same class as
`interrupt_reason`, and it is never echoed in an acknowledgement or a
rejection (ADR-8 owns output encoding, not this module).

**The bound is on UTF-8 BYTES, cut at a character boundary.** RQ-22 says 64
KiB, a byte quantity. `value[:65536]` slices Python characters, not bytes --
for a string of two-byte characters that stores twice the intended amount.
The only correct sequence is: encode to UTF-8, slice the BYTES at the
boundary, then decode with ``errors="ignore"`` so a multi-byte character
left straddling the cut is dropped whole rather than stored mangled.

This is the project's first truncation implementation
(``rg _truncated packages/vantage/src`` finds the six `vcs_*` columns and no
writer before this change) -- built so RQ-22 can adopt
`MAX_TEXT_FIELD_BYTES`/`truncate` unchanged when it lands its own writer.
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
