"""User passwords: the rule a password follows, the one the server makes
for its first admin, and how one is stored and checked.

A password is stored only as a scrypt hash in PHC string form,
`$scrypt$ln=15,r=8,p=3$<salt>$<key>`, which carries its own cost, so a
later cost change needs no schema change and leaves every stored hash
verifying. Unlike a token, a password is chosen by a person and may be
guessed, so each check is made deliberately expensive: about 0.2 s and
32 MiB, which is what bounds how fast anyone can guess.

Both sides of a comparison are NFKC-normalised first, so a password typed
on another keyboard or input method -- composed or decomposed accents,
full-width forms -- matches the one that was set.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import unicodedata
from typing import NamedTuple

from vantage.core.domain.access import plain_character

PASSWORD_MIN_CHARS = 15
PASSWORD_MAX_CHARS = 256
"""Keeps a current and a new password inside one request body however their
characters are spelt: decomposed, or as escaped surrogate pairs."""

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"  # noqa: S105
"""What `new_password` draws from: letters and digits without the ones
that read alike (0/O, 1/l/I) and nothing a terminal or a shell treats
specially, so a password copied from a log line pastes whole."""

_NEW_PASSWORD_CHARS = 24

_SALT_BYTES = 16
_KEY_BYTES = 32

# The most memory one check may take, whatever a stored hash asks for: a
# hash is data, and it may have been written by hand.
_MAX_MEMORY_BYTES = 128 * 2**20
_MAX_PARALLELISM = 16


class _Cost(NamedTuple):
    ln: int
    r: int
    p: int


_COST = _Cost(ln=15, r=8, p=3)
"""N = 2^15 and r = 8 take 32 MiB; p = 3 triples the work without taking
more memory, so a check costs a guesser what N = 2^17 would at a quarter
of the memory."""

# What a check with nothing to match is made against: the current cost and
# a random salt and key. The key is not the output of scrypt, so no
# password matches it, and making it runs no scrypt.
_UNUSABLE = (_COST, secrets.token_bytes(_SALT_BYTES), secrets.token_bytes(_KEY_BYTES))

_PHC_RE = re.compile(
    r"\A\$scrypt\$ln=([0-9]{1,2}),r=([0-9]{1,3}),p=([0-9]{1,3})"
    r"\$([A-Za-z0-9+/]{1,64})\$([A-Za-z0-9+/]{1,128})\Z"
)


class InvalidPasswordError(ValueError):
    """A password too short or too long, or holding a control character.
    Its message states the rule and never holds the password."""


def check_password(password: str) -> str:
    """`password`, if it can be set: `PASSWORD_MIN_CHARS` to
    `PASSWORD_MAX_CHARS` characters once normalised, none of them a control
    character or a lone surrogate. Spaces and every other character are
    kept as they are; nothing is trimmed. Checked when a password is set,
    never when one is checked, so a rule change locks nobody out."""
    normalised = unicodedata.normalize("NFKC", password)
    if not (
        PASSWORD_MIN_CHARS <= len(normalised) <= PASSWORD_MAX_CHARS
        and all(map(plain_character, normalised))
    ):
        raise InvalidPasswordError(
            f"a password is {PASSWORD_MIN_CHARS} to {PASSWORD_MAX_CHARS} characters, "
            "with no control characters"
        )
    return password


def new_password() -> str:
    """A fresh random password of about 140 bits, which follows the rule."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(_NEW_PASSWORD_CHARS))


def hash_password(password: str) -> str:
    """What the store keeps of `password`: its scrypt hash at the current
    cost, with a fresh salt, as a PHC string."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _scrypt(_encode(password), salt, _COST)
    return _format(_COST, salt, key)


def verify_password(password: str, stored: str | None) -> bool:
    """Whether `password` matches the hash `stored`. Never raises.

    Does the same work whether or not there is anything to match: with no
    hash, one it cannot read, one that asks for more than it will spend, or
    a password UTF-8 cannot encode, it checks against a hash that matches
    nothing, so a caller cannot tell those cases from a wrong password by
    how long the answer took."""
    parsed = _parse(stored) if stored is not None else None
    try:
        encoded = _encode(password)
    except UnicodeEncodeError:
        encoded, parsed = b"", None
    usable = parsed is not None
    cost, salt, key = parsed if parsed is not None else _UNUSABLE
    try:
        computed = _scrypt(encoded, salt, cost)
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(computed, key) and usable


def _encode(password: str) -> bytes:
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def _scrypt(password: bytes, salt: bytes, cost: _Cost) -> bytes:
    # OpenSSL's default memory bound, 32 MiB, refuses N = 2^15 with r = 8
    # by a hair; the bound here is what the parameters need. `_parse` has
    # already refused any whose N blocks need more than `_MAX_MEMORY_BYTES`,
    # and the p + 2 blocks besides, with p at most 16, are a few MiB more.
    return hashlib.scrypt(
        password,
        salt=salt,
        n=2**cost.ln,
        r=cost.r,
        p=cost.p,
        maxmem=_memory(cost) + 2**20,
        dklen=_KEY_BYTES,
    )


def _memory(cost: _Cost) -> int:
    """What scrypt takes at `cost`, as OpenSSL counts it: 128 * r bytes for
    each of N + p + 2 blocks."""
    blocks: int = 2**cost.ln + cost.p + 2
    return 128 * cost.r * blocks


def _format(cost: _Cost, salt: bytes, key: bytes) -> str:
    return f"$scrypt$ln={cost.ln},r={cost.r},p={cost.p}${_b64(salt)}${_b64(key)}"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes | None:
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
    except ValueError:
        return None


def _parse(stored: str) -> tuple[_Cost, bytes, bytes] | None:
    """The cost, salt and key of `stored`, or None if it is not a hash
    this module would check: another format, a salt or key of another
    length, or a cost beyond the bounds."""
    match = _PHC_RE.match(stored)
    if match is None:
        return None
    cost = _Cost(ln=int(match[1]), r=int(match[2]), p=int(match[3]))
    if not (
        1 <= cost.ln
        and 1 <= cost.r
        and 1 <= cost.p <= _MAX_PARALLELISM
        and 128 * cost.r * 2**cost.ln <= _MAX_MEMORY_BYTES
    ):
        return None
    salt, key = _unb64(match[4]), _unb64(match[5])
    if salt is None or key is None or len(salt) != _SALT_BYTES or len(key) != _KEY_BYTES:
        return None
    return cost, salt, key
