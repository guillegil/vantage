// The pytest suite both servers record: one result of each outcome, and
// results whose names and text are hostile, so the client is seen to print
// them as text.
export const SUITE = `
import pytest


def test_passes():
    assert True


def test_passes_again():
    assert 1 + 1 == 2


def test_assert_fails():
    assert 3.38 == 3.3, "expected 3.3V, got 3.38V"


@pytest.fixture
def rig():
    raise RuntimeError("the rig did not answer")


def test_fixture_errors(rig):
    pass


def test_skipped():
    pytest.skip("no rig attached")


@pytest.mark.xfail(reason="known drift")
def test_expected_failure():
    assert False


@pytest.mark.xfail(reason="fixed upstream", strict=False)
def test_unexpected_pass():
    assert True


@pytest.mark.parametrize("payload", ["<img src=x onerror=window.__pwned=1>"])
def test_hostile_id(payload):
    assert payload


def test_hostile_output():
    print("</script><script>window.__pwned=1</script>")
    assert False, "</script><script>window.__pwned=1</script>"
`;
