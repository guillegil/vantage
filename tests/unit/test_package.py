"""Smoke test: the vantage package is importable and exposes a version."""


def test_vantage_importable() -> None:
    import vantage

    assert vantage.__version__ == "0.1.0"
