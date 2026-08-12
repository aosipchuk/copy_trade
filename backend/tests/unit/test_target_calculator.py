from decimal import Decimal

from app.services.copy_engine.target_calculator import TargetInput, calculate_targets


def test_fixed_ratio_preserves_short_sign() -> None:
    result = calculate_targets(
        [TargetInput("", "BTC", Decimal("-1"), Decimal("100"), 3)],
        sizing_mode="fixed_ratio",
        copy_ratio_pct=Decimal("25"),
        max_allocation_usd=Decimal("1000"),
        max_per_coin_usd=None,
        equity_usd=None,
    )
    assert result[0].target_size == Decimal("-0.250")


def test_final_targets_scale_to_aggregate_cap() -> None:
    result = calculate_targets(
        [
            TargetInput("", "A", Decimal("1"), Decimal("100"), 3),
            TargetInput("dex", "dex:B", Decimal("1"), Decimal("100"), 3),
        ],
        sizing_mode="fixed_usd",
        copy_ratio_pct=Decimal("100"),
        max_allocation_usd=Decimal("100"),
        max_per_coin_usd=None,
        equity_usd=None,
    )
    assert sum(item.target_notional_usd for item in result) <= Decimal("100")
    assert [item.target_size for item in result] == [Decimal("0.500"), Decimal("0.500")]


def test_per_coin_cap_is_applied_before_portfolio_cap() -> None:
    result = calculate_targets(
        [TargetInput("", "BTC", Decimal("10"), Decimal("100"), 2)],
        sizing_mode="fixed_ratio",
        copy_ratio_pct=Decimal("100"),
        max_allocation_usd=Decimal("1000"),
        max_per_coin_usd=Decimal("50"),
        equity_usd=None,
    )
    assert result[0].target_notional_usd == Decimal("50.00")
