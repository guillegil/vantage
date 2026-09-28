"""`cheap_passwords`: password hashing at a tiny scrypt cost, so a test that
sets, checks or logs in with many passwords does not spend 0.2 s on each.

A plugin module, as `store_fixtures.py` is: a test module loads it with
``pytest_plugins = ["password_fixtures"]``.

Only the cost of new hashes changes, and that of the hash a check with
nothing to match is made against. A stored hash carries its own cost, so
a hash made at the real cost still verifies, and one made here verifies
once the fixture is gone. `test_passwords.py` checks the real cost.

PYTEST_DONT_REWRITE: a test module that lists this plugin may also import
`cheap_hash` from it, which loads it before pytest could rewrite it, and it
holds no assert to rewrite.
"""

from __future__ import annotations

import secrets

import pytest
from vantage.core.domain import passwords

CHEAP_COST = passwords._Cost(ln=4, r=8, p=1)


@pytest.fixture
def cheap_passwords(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "_COST", CHEAP_COST)
    monkeypatch.setattr(
        passwords, "_UNUSABLE", (CHEAP_COST, secrets.token_bytes(16), secrets.token_bytes(32))
    )


def cheap_hash(password: str) -> str:
    """`password`'s hash at `CHEAP_COST`, whatever the fixture's state."""
    salt = secrets.token_bytes(16)
    return passwords._format(
        CHEAP_COST, salt, passwords._scrypt(passwords._encode(password), salt, CHEAP_COST)
    )
