"""`truncate()` -- RQ-22's uniform 64 KiB bound, its first writer
(design.md D49). The bound is on UTF-8 BYTES, cut at a character boundary:
`value[:65536]` counts characters, not bytes, and can store four times the
intended amount for a wide-character-heavy string.
"""

from __future__ import annotations

import pytest
from vantage.service.truncation import MAX_TEXT_FIELD_BYTES, truncate

# design.md D49: the plugin's own cap is `64 * 1024 + 1024` -- a size guard
# that must sit ABOVE the server's semantic bound so a subject the plugin
# lets through is never already short enough to hide from the server. This
# module never imports pytest_vantage (RQ-24/RQ-26: `vantage.service` does
# not depend on the plugin distribution), so the plugin's constant is
# reproduced here as a literal for the window it describes.
_PLUGIN_CAP_BYTES = 64 * 1024 + 1024


def test_truncate_returns_none_and_false_for_none() -> None:
    value, truncated = truncate(None)

    assert value is None
    assert truncated is False


def test_truncate_stores_a_small_subject_whole_with_flag_false() -> None:
    subject = "a" * 1024  # 1 KiB, well under the bound

    value, truncated = truncate(subject)

    assert value == subject
    assert truncated is False


def test_truncate_cuts_a_large_subject_to_at_most_the_byte_bound_with_flag_true() -> None:
    subject = "a" * (100 * 1024)  # 100 KiB of single-byte ASCII characters

    value, truncated = truncate(subject)

    assert value is not None
    assert len(value.encode("utf-8")) <= MAX_TEXT_FIELD_BYTES
    assert truncated is True


def test_truncate_never_splits_a_multi_byte_character_on_the_boundary() -> None:
    """A UTF-8-encoded 'n with tilde' is 2 bytes. Constructed so the boundary
    falls in the MIDDLE of the character: 65535 ASCII bytes, then one 2-byte
    character straddling byte 65536. Slicing raw bytes at 65536 would keep
    only the character's first (invalid, incomplete) byte; the correct
    result drops the whole character rather than storing a mangled one.
    """
    subject = ("a" * (MAX_TEXT_FIELD_BYTES - 1)) + "ñ"  # "ñ", 2 bytes

    value, truncated = truncate(subject)

    assert value is not None
    encoded = value.encode("utf-8")
    assert len(encoded) <= MAX_TEXT_FIELD_BYTES
    # The straddling character was dropped whole, not split: what remains
    # decodes cleanly and is exactly the ASCII run, nothing more.
    assert value == "a" * (MAX_TEXT_FIELD_BYTES - 1)
    assert truncated is True


@pytest.mark.parametrize(
    "length",
    [
        MAX_TEXT_FIELD_BYTES + 1,  # just over the server's own bound
        _PLUGIN_CAP_BYTES,  # exactly at the plugin's promised cap
    ],
)
def test_truncate_sets_the_flag_for_any_subject_the_plugin_can_ever_send(length: int) -> None:
    """design.md D49's false-zero defect, asserted directly: the plugin's
    cap sits ABOVE the server's bound, so every subject length the plugin
    can possibly let through -- from one byte over the server's bound up to
    the plugin's own cap -- must still trip the server's truncation flag. A
    server bound wrongly raised to meet or exceed the plugin's cap would
    fail this test for the smaller of the two parametrised lengths.
    """
    subject = "a" * length

    _value, truncated = truncate(subject)

    assert truncated is True
