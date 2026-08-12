from decimal import Decimal
from pathlib import Path


def test_raw_ratio_converts_to_percent_without_float() -> None:
    raw = Decimal("0.05")
    assert raw * Decimal("100") == Decimal("5.00")


def test_business_code_does_not_read_legacy_trader_stat_roi() -> None:
    app_root = Path(__file__).parents[2] / "app"
    offenders: list[str] = []
    for path in app_root.rglob("*.py"):
        text = path.read_text()
        if "TraderStat.roi_pct" in text or "stat.roi_pct" in text:
            offenders.append(str(path.relative_to(app_root)))
    assert offenders == []
