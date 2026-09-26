"""`Identity` validation."""

from __future__ import annotations

import pytest
from vantage.core.domain.execution import Identity


def test_identity_accepts_32_lowercase_hex_characters() -> None:
    identity = Identity("a" * 32)

    assert identity.value == "a" * 32


@pytest.mark.parametrize(
    "bad_value",
    [
        "too-short",
        "A" * 32,  # uppercase is rejected
        "g" * 32,  # not a hex digit
        "a" * 31,  # one char short
        "a" * 33,  # one char over
        "",
    ],
)
def test_identity_rejects_anything_but_32_lowercase_hex_characters(bad_value: str) -> None:
    with pytest.raises(ValueError, match="32 lowercase hex"):
        Identity(bad_value)
