import pytest

from app.services.copy_engine.market_identity import allowed_market, market_id


def test_default_and_hip3_same_ticker_are_distinct() -> None:
    default = market_id("", "XYZ")
    hip3 = market_id("dex", "dex:XYZ")
    assert default.key != hip3.key
    assert allowed_market(["XYZ"], default)
    assert not allowed_market(["XYZ"], hip3)
    assert allowed_market(["dex:XYZ"], hip3)


def test_mismatched_prefix_is_rejected() -> None:
    with pytest.raises(ValueError):
        market_id("other", "dex:XYZ")
