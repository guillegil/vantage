"""Recording to a server that has users: the token comes from
`VANTAGE_TOKEN` alone, goes with every report and heartbeat, never shows in
what the plugin prints, and a server's refusal of it reads as what to fix.

End to end against a real server (`vantage_server`) whose store holds a
user, plus the transport against a server that answers only refusals, and
the outbox, which keeps a run the token could not deliver for a sender
with another.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from loopback_server import LoopbackServer
from pytest_vantage.config import ReportSettings, VantageConfigError, resolve_token
from pytest_vantage.outbox import Outbox, outbox_path, send_queued, worth_retrying
from pytest_vantage.transport import AccessRefusedError, send, send_heartbeat
from starlette.types import Receive, Scope, Send
from vantage.core.domain.access import READ_SCOPE, RECORD_SCOPE
from vantage_test_server import VantageTestServer

pytestmark = pytest.mark.usefixtures("git_confined_to_basetemp")

_PASSING_TEST = "def test_passes():\n    pass\n"

_RUN = "b" * 32


def _report(run_id: str = _RUN) -> dict[str, object]:
    return {
        "run": {
            "id": run_id,
            "started_at": "2026-09-27T12:00:00+00:00",
            "finished_at": "2026-09-27T12:00:05+00:00",
            "exit_status": 0,
            "interrupted": False,
            "interrupt_reason": None,
        }
    }


# --- The setting -------------------------------------------------------------------


def test_an_unset_or_empty_vantage_token_is_no_token() -> None:
    assert resolve_token(None) is None
    assert resolve_token("") is None
    assert resolve_token("vantage_x-Y_z.0~") == "vantage_x-Y_z.0~"


@pytest.mark.parametrize("token", ["two words", "café", "x" * 513, "tab\there"])
def test_a_vantage_token_that_could_never_authenticate_is_refused_unquoted(token: str) -> None:
    with pytest.raises(VantageConfigError) as refused:
        resolve_token(token)

    assert str(refused.value) == (
        "VANTAGE_TOKEN is not a token: a token is at most 512 printable ASCII characters, "
        "with no spaces"
    )


def test_the_settings_never_show_their_token() -> None:
    settings = ReportSettings(address="http://127.0.0.1:1", timeout=1.0, token="vantage_secret")

    assert "vantage_secret" not in repr(settings)


def test_a_session_with_a_bad_vantage_token_is_a_usage_error_that_does_not_quote_it(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VANTAGE_TOKEN", "s3cret value")
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess("--vantage", "--vantage-server=http://127.0.0.1:1")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "VANTAGE_TOKEN is not a token" in result.stderr.str()
    assert "s3cret" not in result.stdout.str() + result.stderr.str()


def test_vantage_token_is_not_read_without_vantage(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a recording session reads the token, as it reads the address."""
    monkeypatch.setenv("VANTAGE_TOKEN", "not a token")
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess()

    result.assert_outcomes(passed=1, warnings=0)


# --- A session against a server with users ----------------------------------------


def _session(pytester: pytest.Pytester, server: VantageTestServer, *extra: str) -> pytest.RunResult:
    pytester.makepyfile(test_sample=_PASSING_TEST)
    return pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}", *extra)


def test_a_session_with_a_token_is_recorded_as_its_user(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = vantage_server.token("alice", RECORD_SCOPE)
    monkeypatch.setenv("VANTAGE_TOKEN", token)

    result = _session(pytester, vantage_server)

    result.assert_outcomes(passed=1, warnings=0)
    (execution,) = vantage_server.executions()
    assert execution.exit_status == 0
    assert vantage_server.recorded_by(execution.identity.value) == "alice"
    # The start report and the finish, both taken.
    assert vantage_server.requests.count(("POST", "/api/v1/runs")) == 2
    assert token not in result.stdout.str() + result.stderr.str()


def test_a_session_without_a_token_says_the_server_needs_one_and_its_tests_still_pass(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    vantage_server.token("alice")

    result = _session(pytester, vantage_server)

    result.assert_outcomes(passed=1, warnings=2)
    needs_one = (
        "HTTP 401: the server requires a token: set VANTAGE_TOKEN to one with the record scope"
    )
    result.stdout.fnmatch_lines(
        [
            f"*vantage: error while reporting session liveness: {needs_one}",
            f"*vantage: error while reporting: {needs_one}",
        ]
    )
    assert vantage_server.executions() == []


@pytest.mark.parametrize(
    ("scopes", "reason"),
    [
        (
            [READ_SCOPE],
            "HTTP 403: the token in VANTAGE_TOKEN does not grant the record scope",
        ),
        (
            None,
            "HTTP 401: the server does not accept the token in VANTAGE_TOKEN: it is unknown "
            "or revoked, or its user is disabled",
        ),
    ],
    ids=["read-only", "unknown"],
)
def test_a_token_the_server_refuses_says_why_without_showing_it(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
    scopes: list[str] | None,
    reason: str,
) -> None:
    token = vantage_server.token("alice", *(scopes or [RECORD_SCOPE]))
    if scopes is None:
        token = f"{token[:-4]}abcd"
    monkeypatch.setenv("VANTAGE_TOKEN", token)

    result = _session(pytester, vantage_server)

    result.assert_outcomes(passed=1, warnings=2)
    result.stdout.fnmatch_lines([f"*vantage: error while reporting: {reason}"])
    assert token not in result.stdout.str() + result.stderr.str()
    assert vantage_server.executions() == []


def test_a_token_sent_to_a_server_without_users_is_refused(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token meant for another server, or a server whose users are not
    set up yet: recording without it would hide the mistake."""
    monkeypatch.setenv("VANTAGE_TOKEN", "vantage_meant_for_another_server")

    result = _session(pytester, vantage_server)

    result.assert_outcomes(passed=1, warnings=2)
    result.stdout.fnmatch_lines(["*does not accept the token in VANTAGE_TOKEN*"])


# --- The transport -------------------------------------------------------------------


class _Refusing(LoopbackServer):
    """Answers every request with `status` and `body`, and keeps the
    `Authorization` header of each. The request's body is read first: a
    server that answers and hangs up while the client is still sending can
    reset the connection, and the client then sees no answer at all."""

    def __init__(self, status: int, body: bytes) -> None:
        self.authorizations: list[str | None] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                return
            while (await receive()).get("more_body", False):
                pass
            headers = dict(scope["headers"])
            authorization = headers.get(b"authorization")
            self.authorizations.append(None if authorization is None else authorization.decode())
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})

        super().__init__(app)


def _refusal(error: str) -> bytes:
    return json.dumps({"error": error, "detail": "", "fields": []}).encode()


@pytest.mark.parametrize(
    ("status", "error", "token", "reason"),
    [
        (
            401,
            "unauthenticated",
            None,
            "the server requires a token: set VANTAGE_TOKEN to one with the record scope",
        ),
        (
            401,
            "unauthenticated",
            "vantage_t",
            "the server does not accept the token in VANTAGE_TOKEN: it is unknown or revoked, "
            "or its user is disabled",
        ),
        (
            403,
            "insufficient_scope",
            "vantage_t",
            "the token in VANTAGE_TOKEN does not grant the record scope",
        ),
        (409, "foreign_run", "vantage_t", "the run was recorded by another user"),
    ],
)
def test_a_vantage_servers_refusal_of_the_sender_says_what_to_fix(
    status: int, error: str, token: str | None, reason: str
) -> None:
    server = _Refusing(status, _refusal(error))
    server.start()
    try:
        with pytest.raises(AccessRefusedError) as refused:
            send(server.address, _report(), timeout=5.0, token=token)
        with pytest.raises(AccessRefusedError):
            send_heartbeat(server.address, _RUN, timeout=5.0, token=token)
    finally:
        server.stop()

    assert refused.value.code == status
    assert str(refused.value) == f"HTTP {status}: {reason}"
    expected = None if token is None else f"Bearer {token}"
    assert server.authorizations == [expected, expected]


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, b"<html>Basic realm</html>"),
        (403, _refusal("not_vantage")),
        (409, b""),
        (401, _refusal("insufficient_scope")),
    ],
)
def test_a_refusal_no_vantage_server_gave_stays_a_plain_http_error(
    status: int, body: bytes
) -> None:
    """A proxy in front of the server says nothing about the token."""
    server = _Refusing(status, body)
    server.start()
    try:
        with pytest.raises(Exception) as refused:
            send(server.address, _report(), timeout=5.0, token="vantage_t")
    finally:
        server.stop()

    assert type(refused.value).__name__ == "HTTPError"
    assert getattr(refused.value, "code", None) == status


def test_a_token_is_never_sent_to_the_capability_probe(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server answers the probe without one, so it carries none."""
    server = _Refusing(200, b'{"session_lifecycle": false}')
    server.start()
    monkeypatch.setenv("VANTAGE_TOKEN", "vantage_t")
    pytester.makepyfile(test_sample=_PASSING_TEST)
    try:
        pytester.runpytest_subprocess("--vantage", f"--vantage-server={server.address}")
    finally:
        server.stop()

    probe, *reports = server.authorizations
    assert probe is None
    assert reports and all(sent == "Bearer vantage_t" for sent in reports)


# --- The outbox ----------------------------------------------------------------------


def test_a_refused_token_is_worth_retrying_with_another_and_another_users_run_is_not() -> None:
    def refusal(status: int) -> AccessRefusedError:
        import urllib.error

        plain = urllib.error.HTTPError("http://x", status, "no", {}, None)  # type: ignore[arg-type]
        return AccessRefusedError(plain, "why")

    assert worth_retrying(refusal(401)) is True
    assert worth_retrying(refusal(403)) is True
    assert worth_retrying(refusal(409)) is False


def test_a_queued_run_waits_for_a_token_and_goes_with_one(
    tmp_path: Path, vantage_server: VantageTestServer
) -> None:
    """Sent without a token, or with one the server refuses, the run stays
    queued and sending stops; sent with one it accepts, it is recorded as
    that token's user. The outbox itself never holds a token."""
    token = vantage_server.token("alice", RECORD_SCOPE)
    database = tmp_path / "vantage.db"
    with Outbox(outbox_path(database)) as box:
        box.enqueue(vantage_server.address, _RUN, [_report()])

        without = send_queued(box, vantage_server.address, timeout=5.0, budget=math.inf)
        refused = send_queued(
            box, vantage_server.address, timeout=5.0, budget=math.inf, token="vantage_nope"
        )
        taken = send_queued(box, vantage_server.address, timeout=5.0, budget=math.inf, token=token)

    assert (without.sent, without.waiting, without.dropped) == (0, 1, ())
    assert without.stopped is not None and "HTTP 401: the server requires a token" in (
        without.stopped
    )
    assert (refused.sent, refused.waiting) == (0, 1)
    assert (taken.sent, taken.waiting) == (1, 0)
    assert vantage_server.recorded_by(_RUN) == "alice"
    assert token.encode() not in outbox_path(database).read_bytes()


def test_a_queued_run_of_another_user_is_dropped(
    tmp_path: Path, vantage_server: VantageTestServer
) -> None:
    alices = vantage_server.token("alice", RECORD_SCOPE)
    bobs = vantage_server.token("bob", RECORD_SCOPE)
    send(vantage_server.address, _report(), timeout=5.0, token=alices)
    with Outbox(outbox_path(tmp_path / "vantage.db")) as box:
        box.enqueue(vantage_server.address, _RUN, [_report()])

        summary = send_queued(box, vantage_server.address, timeout=5.0, budget=math.inf, token=bobs)

    assert (summary.sent, summary.dropped, summary.waiting) == (0, (_RUN,), 0)
    assert vantage_server.recorded_by(_RUN) == "alice"
