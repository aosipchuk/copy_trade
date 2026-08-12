from decimal import Decimal

from app.services.copy_engine.target_calculator import TargetInput, calculate_targets


def test_demo_uses_target_state_not_repeated_order_accumulation() -> None:
    kwargs = {
        "items": [TargetInput("", "BTC", Decimal("1"), Decimal("100"), 3)],
        "sizing_mode": "fixed_ratio",
        "copy_ratio_pct": Decimal("10"),
        "max_allocation_usd": Decimal("1000"),
        "max_per_coin_usd": None,
        "equity_usd": Decimal("1000"),
    }
    first = calculate_targets(**kwargs)[0]
    repeated = calculate_targets(**kwargs)[0]
    assert first.target_size == repeated.target_size == Decimal("0.100")
