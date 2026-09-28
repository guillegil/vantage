"""Passwords (`core/domain/passwords.py`): the rule a password follows, the
one made for a first admin, and how one is stored and checked.

A stored hash carries its own cost, so most tests hash at the tiny cost of
`password_fixtures` and what they show holds at any cost. Only the tests of
the cost itself pay for a real hash, about 0.2 s each.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import re
import secrets
import string
from collections.abc import Callable
from typing import Any, NamedTuple

import pytest
from password_fixtures import CHEAP_COST, cheap_hash
from vantage.core.domain import passwords
from vantage.core.domain.passwords import (
    InvalidPasswordError,
    check_password,
    hash_password,
    new_password,
    verify_password,
)

# `cheap_passwords`: new hashes, and the stand-in, at a tiny cost.
pytest_plugins = ["password_fixtures"]

_PASSWORD = "correct horse battery staple"

_STORED = cheap_hash(_PASSWORD)

_RULE = "a password is 15 to 256 characters, with no control characters"


class _Call(NamedTuple):
    """What one `hashlib.scrypt` call was asked to compute with."""

    n: int
    r: int
    p: int
    salt: bytes


def _record_scrypt(monkeypatch: pytest.MonkeyPatch, answer: Callable[..., bytes]) -> list[_Call]:
    """Every `hashlib.scrypt` call from here on, each answered by `answer`."""
    calls: list[_Call] = []

    def record(password: bytes, **kwargs: Any) -> bytes:
        calls.append(_Call(kwargs["n"], kwargs["r"], kwargs["p"], kwargs["salt"]))
        return answer(password, **kwargs)

    monkeypatch.setattr(hashlib, "scrypt", record)
    return calls


@pytest.fixture
def scrypt_calls(monkeypatch: pytest.MonkeyPatch) -> list[_Call]:
    """Every `hashlib.scrypt` call the test makes, each still computed."""
    return _record_scrypt(monkeypatch, hashlib.scrypt)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii").rstrip("=")


_SALT = b"s" * 16
_KEY = b"k" * 32


def _phc(
    ln: int = 5, r: int = 8, p: int = 2, salt: str = _b64(_SALT), key: str = _b64(_KEY)
) -> str:
    """A string in the hash format, whatever it holds."""
    return f"$scrypt$ln={ln},r={r},p={p}${salt}${key}"


def _against(stored: str) -> _Call:
    """The call that checks a password against the well-formed hash `stored`."""
    match = re.fullmatch(r"\$scrypt\$ln=(\d+),r=(\d+),p=(\d+)\$([^$]+)\$[^$]+", stored)
    assert match is not None
    salt = match[4] + "=" * (-len(match[4]) % 4)
    return _Call(2 ** int(match[1]), int(match[2]), int(match[3]), base64.b64decode(salt))


def _against_the_stand_in() -> _Call:
    """The call a check with nothing to match makes."""
    cost, salt, _key = passwords._UNUSABLE
    return _Call(2**cost.ln, cost.r, cost.p, salt)


# --- Storing and checking -------------------------------------------------


@pytest.fixture(scope="module")
def hashed_at_the_current_cost() -> str:
    """One hash at the real cost, shared by the tests of that cost."""
    return hash_password(_PASSWORD)


def test_a_hash_is_an_88_character_phc_string_at_the_current_cost(
    hashed_at_the_current_cost: str,
) -> None:
    """scrypt with N = 2^15, r = 8, p = 3; a 16-byte salt and a 32-byte key
    in standard base64 without padding, 22 and 43 characters."""
    assert re.fullmatch(
        r"\$scrypt\$ln=15,r=8,p=3\$[A-Za-z0-9+/]{22}\$[A-Za-z0-9+/]{43}",
        hashed_at_the_current_cost,
    )
    assert len(hashed_at_the_current_cost) == 88


def test_a_password_verifies_against_its_hash_at_the_current_cost(
    hashed_at_the_current_cost: str, scrypt_calls: list[_Call]
) -> None:
    assert verify_password(_PASSWORD, hashed_at_the_current_cost) is True
    assert scrypt_calls == [_Call(2**15, 8, 3, _against(hashed_at_the_current_cost).salt)]


@pytest.mark.usefixtures("cheap_passwords")
def test_each_hash_of_a_password_has_its_own_salt_and_each_verifies() -> None:
    first, second = hash_password(_PASSWORD), hash_password(_PASSWORD)

    assert first != second
    assert verify_password(_PASSWORD, first) is True
    assert verify_password(_PASSWORD, second) is True


@pytest.mark.parametrize(
    "wrong",
    [
        pytest.param("correct horse battery stapler", id="another-password"),
        pytest.param("Correct horse battery staple", id="case"),
        pytest.param(" correct horse battery staple", id="leading-space"),
        pytest.param("correct horse battery staple ", id="trailing-space"),
        pytest.param("", id="empty"),
    ],
)
def test_a_wrong_password_fails_after_one_scrypt_against_the_stored_hash(
    wrong: str, scrypt_calls: list[_Call]
) -> None:
    """Normalisation folds no case and trims nothing: a password differing
    only in either is a wrong one."""
    assert verify_password(wrong, _STORED) is False
    assert scrypt_calls == [_against(_STORED)]


@pytest.mark.usefixtures("cheap_passwords")
@pytest.mark.parametrize(
    ("one_form", "other_form"),
    [
        pytest.param(
            "correct horse battery staple",
            "ｃｏｒｒｅｃｔ\u3000ｈｏｒｓｅ\u3000ｂａｔｔｅｒｙ\u3000ｓｔａｐｌｅ",
            id="full-width",
        ),
        pytest.param(
            "contrase\u00f1a del caf\u00e9 de m\u00fcnchen",
            "contrasen\u0303a del cafe\u0301 de mu\u0308nchen",
            id="composed-decomposed",
        ),
    ],
)
def test_a_password_typed_in_an_equivalent_form_matches_the_form_that_was_set(
    one_form: str, other_form: str
) -> None:
    """Another keyboard or input method sends the same password as other
    code points; either form may be the one set, and the other the one typed."""
    assert verify_password(other_form, hash_password(one_form)) is True
    assert verify_password(one_form, hash_password(other_form)) is True


def test_a_hash_made_at_another_cost_verifies_at_its_own(scrypt_calls: list[_Call]) -> None:
    """A hash carries its cost, so a later change of the cost leaves every
    stored hash verifying, each checked at the cost it was made at."""
    assert passwords._COST != CHEAP_COST

    assert verify_password(_PASSWORD, _STORED) is True
    assert scrypt_calls == [
        _Call(2**CHEAP_COST.ln, CHEAP_COST.r, CHEAP_COST.p, _against(_STORED).salt)
    ]


@pytest.mark.parametrize(
    "password",
    [
        pytest.param("short", id="too-short"),
        pytest.param("correct\thorse battery staple", id="control-character"),
    ],
)
def test_a_password_the_rule_refuses_still_verifies_against_its_hash(password: str) -> None:
    """The rule is applied when a password is set, never when one is
    checked, so a rule made stricter locks nobody out."""
    assert verify_password(password, cheap_hash(password)) is True


@pytest.mark.parametrize(
    ("ln", "r", "p"),
    [
        pytest.param(17, 8, 1, id="128-mib"),
        pytest.param(4, 8, 16, id="p-16"),
        pytest.param(1, 1, 1, id="least"),
    ],
)
def test_a_hash_at_the_bounds_is_checked_at_its_own_cost(
    monkeypatch: pytest.MonkeyPatch, ln: int, r: int, p: int
) -> None:
    """The bounds are inclusive. A stub stands in for scrypt, so the
    128 MiB case allocates nothing, and answers with the stored key: the
    check passes only if it was made against the stored hash."""
    calls = _record_scrypt(monkeypatch, lambda password, **kwargs: _KEY)

    assert verify_password(_PASSWORD, _phc(ln, r, p)) is True
    assert calls == [_Call(2**ln, r, p, _SALT)]


def test_a_hash_whose_parallel_blocks_weigh_most_still_verifies() -> None:
    """scrypt takes memory for its p blocks as well as its N: the most r and
    p the bounds allow, with the least N, needs more than the N blocks
    alone, and a memory bound that left the p blocks out would have
    OpenSSL refuse the right password."""
    salt = secrets.token_bytes(16)
    n, r, p = 2, 999, 16
    key = hashlib.scrypt(_PASSWORD.encode(), salt=salt, n=n, r=r, p=p, maxmem=2**27, dklen=32)
    stored = f"$scrypt$ln=1,r={r},p={p}${_b64(salt)}${_b64(key)}"

    assert verify_password(_PASSWORD, stored) is True


@pytest.mark.parametrize(
    "refusal",
    [
        pytest.param(ValueError("memory limit exceeded"), id="value-error"),
        pytest.param(MemoryError(), id="memory-error"),
    ],
)
def test_a_hash_scrypt_will_not_compute_fails_without_raising(
    monkeypatch: pytest.MonkeyPatch, refusal: Exception
) -> None:
    """A hash within the bounds can still ask for what the scrypt
    implementation will not do; the check fails as a wrong password does."""

    def refuse(password: bytes, **kwargs: Any) -> bytes:
        raise refusal

    calls = _record_scrypt(monkeypatch, refuse)

    assert verify_password(_PASSWORD, _phc()) is False
    assert calls == [_against(_phc())]


# --- Checks with nothing to match -----------------------------------------


@pytest.mark.usefixtures("cheap_passwords")
@pytest.mark.parametrize(
    "stored",
    [
        pytest.param(None, id="no-hash"),
        pytest.param("", id="empty"),
        pytest.param("not a hash", id="not-a-hash"),
        pytest.param("ab" * 32, id="sha256-hex"),
        pytest.param(f"$argon2id$v=19$m=65536,t=3,p=4${_b64(_SALT)}${_b64(_KEY)}", id="argon2"),
        pytest.param("$2b$12$" + "a" * 53, id="bcrypt"),
        pytest.param(_phc(key=_b64(_KEY)[:-1] + "."), id="adapted-base64"),
        pytest.param(_phc() + "\n", id="trailing-newline"),
        pytest.param(_phc(salt=_b64(b"s" * 15)), id="salt-15-bytes"),
        pytest.param(_phc(salt=_b64(b"s" * 17)), id="salt-17-bytes"),
        pytest.param(_phc(key=_b64(b"k" * 31)), id="key-31-bytes"),
        pytest.param(_phc(key=_b64(b"k" * 64)), id="key-64-bytes"),
        pytest.param(_phc(salt="A" * 21), id="salt-not-base64"),
        pytest.param(_phc(key="A" * 41), id="key-not-base64"),
        pytest.param(_phc(ln=17, r=9, p=1), id="over-128-mib"),
        pytest.param(_phc(ln=18, r=8, p=1), id="twice-128-mib"),
        pytest.param(_phc(ln=99), id="ln-99"),
        pytest.param(_phc(p=17), id="p-17"),
        pytest.param(_phc(ln=0), id="ln-0"),
        pytest.param(_phc(r=0), id="r-0"),
        pytest.param(_phc(p=0), id="p-0"),
    ],
)
def test_a_hash_it_will_not_check_fails_after_one_scrypt_against_the_stand_in(
    stored: str | None, scrypt_calls: list[_Call]
) -> None:
    """No hash, one in another format, one of the wrong shape, or one
    asking for more memory or parallelism than a check will spend: the
    password is checked against the stand-in hash all the same, so the
    answer takes as long as a wrong password's and nothing tells them apart."""
    assert verify_password(_PASSWORD, stored) is False
    assert scrypt_calls == [_against_the_stand_in()]


@pytest.mark.usefixtures("cheap_passwords")
@pytest.mark.parametrize(
    "password",
    [
        pytest.param(_PASSWORD + "\ud800", id="high"),
        pytest.param("\udfff" + _PASSWORD, id="low"),
        pytest.param("\ud83d\ude00", id="split-pair"),
    ],
)
def test_a_password_with_a_lone_surrogate_fails_after_one_scrypt_against_the_stand_in(
    password: str, scrypt_calls: list[_Call]
) -> None:
    """A lone surrogate, which a command line hands over for a byte its
    locale cannot decode, has no UTF-8 form to hash: the check fails like a
    wrong password, instead of raising."""
    assert verify_password(password, _STORED) is False
    assert scrypt_calls == [_against_the_stand_in()]


@pytest.mark.parametrize(
    ("password", "stored", "stand_in_made_from"),
    [
        pytest.param(_PASSWORD, None, _PASSWORD, id="no-hash"),
        pytest.param(_PASSWORD, "not a hash", _PASSWORD, id="not-a-hash"),
        pytest.param(_PASSWORD + "\ud800", _STORED, "", id="lone-surrogate"),
    ],
)
def test_a_check_against_the_stand_in_fails_even_if_its_key_matched(
    monkeypatch: pytest.MonkeyPatch, password: str, stored: str | None, stand_in_made_from: str
) -> None:
    """The stand-in's key is random bytes that no password hashes to, and
    the answer does not rest on that alone: a stand-in whose key is the
    very one the check computes still fails it."""
    salt = secrets.token_bytes(16)
    key = passwords._scrypt(passwords._encode(stand_in_made_from), salt, CHEAP_COST)
    monkeypatch.setattr(passwords, "_UNUSABLE", (CHEAP_COST, salt, key))

    assert verify_password(password, stored) is False


def test_the_stand_in_hash_is_a_well_formed_hash_at_the_current_cost() -> None:
    """A check with nothing to match costs what a real check costs because
    the stand-in it is made against is a hash like any other, at the cost
    a new hash gets."""
    cost, salt, key = passwords._UNUSABLE

    assert cost == passwords._COST
    assert passwords._parse(passwords._format(cost, salt, key)) == passwords._UNUSABLE


def test_importing_the_module_runs_no_scrypt(scrypt_calls: list[_Call]) -> None:
    """The stand-in is made of random bytes, not by hashing, so a process
    that never checks a password never pays for one. A fresh copy of the
    module is loaded, leaving the one every other module holds alone."""
    assert passwords.__file__ is not None
    spec = importlib.util.spec_from_file_location("_passwords_afresh", passwords.__file__)
    assert spec is not None
    assert spec.loader is not None
    fresh = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(fresh)

    assert fresh._UNUSABLE[0] == fresh._COST
    assert scrypt_calls == []


# --- The rule -------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        pytest.param("p" * 15, id="15"),
        pytest.param("p" * 256, id="256"),
        pytest.param("\ufb03" + "p" * 12, id="13-normalising-to-15"),
        pytest.param("e\u0301" + "p" * 255, id="257-normalising-to-256"),
    ],
)
def test_a_password_of_15_to_256_characters_once_normalised_is_accepted_as_given(
    password: str,
) -> None:
    """Characters are counted after NFKC -- a ligature becomes several, a
    decomposed accent one -- and the password comes back as given."""
    assert check_password(password) == password


@pytest.mark.parametrize(
    "password",
    [
        pytest.param("", id="empty"),
        pytest.param("p" * 14, id="14"),
        pytest.param("p" * 257, id="257"),
        pytest.param("e\u0301" * 2 + "p" * 12, id="16-normalising-to-14"),
        pytest.param("\ufb03" + "p" * 254, id="255-normalising-to-257"),
    ],
)
def test_a_password_of_under_15_or_over_256_characters_once_normalised_is_refused(
    password: str,
) -> None:
    with pytest.raises(InvalidPasswordError):
        check_password(password)


@pytest.mark.parametrize(
    "character",
    [
        pytest.param("\x00", id="nul"),
        pytest.param("\t", id="tab"),
        pytest.param("\n", id="line-feed"),
        pytest.param("\r", id="carriage-return"),
        pytest.param("\x1b", id="escape"),
        pytest.param("\x1f", id="last-c0"),
        pytest.param("\x7f", id="delete"),
        pytest.param("\x80", id="first-c1"),
        pytest.param("\x85", id="next-line"),
        pytest.param("\x9f", id="last-c1"),
        pytest.param("\ud800", id="high-surrogate"),
        pytest.param("\udfff", id="low-surrogate"),
    ],
)
def test_a_password_holding_a_control_character_or_a_lone_surrogate_is_refused(
    character: str,
) -> None:
    with pytest.raises(InvalidPasswordError):
        check_password(f"correct horse{character}battery staple")


@pytest.mark.parametrize(
    "character",
    [
        pytest.param(" ", id="space"),
        pytest.param("~", id="tilde"),
        pytest.param("\xa0", id="no-break-space"),
        pytest.param("\u00e9", id="accented"),
        pytest.param("\ud7ff", id="before-surrogates"),
        pytest.param("\ue000", id="after-surrogates"),
        pytest.param("\U0001f511", id="beyond-the-bmp"),
    ],
)
def test_every_other_character_is_accepted_as_given(character: str) -> None:
    password = f"correct horse{character}battery staple"

    assert check_password(password) == password


def test_spaces_count_and_nothing_is_trimmed() -> None:
    """Fifteen characters with a space at each end: trimmed, they would be
    thirteen and refused."""
    password = " " + "p" * 13 + " "

    assert check_password(password) == password


@pytest.mark.parametrize(
    "password",
    [
        pytest.param("hunter2-hunter", id="too-short"),
        pytest.param("hunter2-" * 33, id="too-long"),
        pytest.param("hunter2-\x00-hunter2-hunter2", id="control-character"),
    ],
)
def test_a_refusal_states_the_rule_and_holds_nothing_of_the_password(password: str) -> None:
    with pytest.raises(InvalidPasswordError) as refused:
        check_password(password)

    assert refused.value.args == (_RULE,)
    assert "hunter2" not in repr(refused.value)


# --- A new password -------------------------------------------------------


_UNAMBIGUOUS = set(string.ascii_letters + string.digits) - set("0O1lI")


def test_a_new_password_is_24_characters_without_ones_that_read_alike() -> None:
    """Letters and digits but 0/O and 1/l/I, and every one of those drawn
    in 200 passwords, which misses one only with odds of about 1 in 10^35."""
    drawn = [new_password() for _ in range(200)]

    assert {len(password) for password in drawn} == {24}
    assert set("".join(drawn)) == _UNAMBIGUOUS


def test_a_new_password_follows_the_rule_and_differs_each_time() -> None:
    drawn = [new_password() for _ in range(200)]

    assert all(check_password(password) == password for password in drawn)
    assert len(set(drawn)) == len(drawn)


def test_a_new_password_is_drawn_from_the_system_random_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every character comes from `secrets`, never from a seeded generator
    whose output could be replayed."""
    monkeypatch.setattr(secrets, "choice", lambda alphabet: alphabet[-1])

    assert new_password() == "9" * 24
