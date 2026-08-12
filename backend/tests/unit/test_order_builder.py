from decimal import Decimal

from app.services.copy_engine.order_builder import delta_to_order


def test_open_uses_signed_delta() -> None:
    order = delta_to_order(
        coin="BTC",
        asset_index=0,
        before_size=Decimal("0"),
        target_size=Decimal("0.01"),
        mid_price=Decimal("50000"),
        sz_decimals=3,
    )
    assert order is not None
    assert order.is_buy is True
    assert order.reduce_only is False
    assert order.size == Decimal("0.010")


def test_reduction_is_reduce_only() -> None:
    order = delta_to_order(
        coin="BTC",
        asset_index=0,
        before_size=Decimal("0.02"),
        target_size=Decimal("0.01"),
        mid_price=Decimal("50000"),
        sz_decimals=3,
    )
    assert order is not None
    assert order.is_buy is False
    assert order.reduce_only is True
    assert order.size == Decimal("0.010")


def test_flip_only_closes_to_zero_first() -> None:
    order = delta_to_order(
        coin="BTC",
        asset_index=0,
        before_size=Decimal("0.02"),
        target_size=Decimal("-0.03"),
        mid_price=Decimal("50000"),
        sz_decimals=3,
    )
    assert order is not None
    assert order.reduce_only is True
    assert order.size == Decimal("0.020")


def test_dust_is_not_sent() -> None:
    order = delta_to_order(
        coin="newdex:TINY",
        asset_index=110000,
        before_size=Decimal("0"),
        target_size=Decimal("0.001"),
        mid_price=Decimal("1"),
        sz_decimals=3,
    )
    assert order is None
