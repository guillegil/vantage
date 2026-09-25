"""`truncate()` -- the uniform 64 KiB bound on stored text. The bound is on
UTF-8 bytes, cut at a character boundary: `value[:65536]` counts characters,
not bytes, and can store four times the intended amount.
"""

from __future__ import annotations

import pytest
from vantage.service.truncation import MAX_TEXT_FIELD_BYTES, truncate

# The plugin's commit subject cap (`pytest_vantage.vcs._MAX_SUBJECT_BYTES`),
# which sits above the server's bound. Reproduced as a literal because it is
# private to the plugin.
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
    """'ñ' is 2 UTF-8 bytes. Constructed so the boundary falls MID-character:
    65535 ASCII bytes then one straddling 2-byte character. A raw byte slice
    at 65536 would keep only its first, incomplete byte; the correct result
    drops the whole character instead of storing it mangled.
    """
    subject = ("a" * (MAX_TEXT_FIELD_BYTES - 1)) + "ñ"

    value, truncated = truncate(subject)

    assert value is not None
    assert len(value.encode("utf-8")) <= MAX_TEXT_FIELD_BYTES
    assert value == "a" * (MAX_TEXT_FIELD_BYTES - 1)
    assert truncated is True


@pytest.mark.parametrize(
    "length",
    [MAX_TEXT_FIELD_BYTES + 1, _PLUGIN_CAP_BYTES],
    ids=["just_over_server_bound", "at_plugin_cap"],
)
def test_truncate_sets_the_flag_for_any_subject_the_plugin_can_ever_send(length: int) -> None:
    """Every length the plugin can send above the server's bound, up to the
    plugin's own cap, sets the truncation flag. A server bound raised to
    meet the plugin's cap would store those subjects unflagged.
    """
    subject = "a" * length

    _value, truncated = truncate(subject)

    assert truncated is True
