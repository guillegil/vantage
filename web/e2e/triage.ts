// The pytest suite the closed server records into the project triage, once
// per round, so its runs hold every change a comparison records and every
// state it can be in. TRIAGE_ROUND picks the round:
//
// - a: test_breaks and test_goes_away pass; test_stays_failing and
//   test_gets_fixed fail.
// - b: test_breaks and the new test_arrives_failing fail, test_gets_fixed
//   passes, test_arrives is new and passes, test_goes_away is gone, and
//   test_stops_the_session and test_after_the_stop are new and pass.
// - c: as b, but test_stops_the_session ends the session with pytest.exit
//   before test_after_the_stop.
// - killed: the process dies at its first test, after the start report and
//   before any result, as a session killed outright does.
export const TRIAGE = `
import os

import pytest

ROUND = os.environ["TRIAGE_ROUND"]

if ROUND == "killed":

    def test_dies():
        os._exit(0)


def test_breaks():
    assert ROUND == "a", "broke after the first round"


if ROUND == "a":

    def test_goes_away():
        pass


def test_stays_failing():
    assert False, "never worked"


def test_gets_fixed():
    assert ROUND != "a", "fixed after the first round"


if ROUND != "a":

    def test_arrives_failing():
        assert False, "arrived broken"

    def test_arrives():
        pass

    def test_stops_the_session():
        if ROUND == "c":
            pytest.exit("stopped on purpose")

    def test_after_the_stop():
        pass
`;
