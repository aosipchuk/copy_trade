from decimal import Decimal

from app.services.hyperliquid.models import Position, PositionLeverage
from app.services.signal_detector import SignalType, detect_changes


def _position(size: str) -> Position:
    return Position(
        coin="BTC",
        szi=Decimal(size),
        entryPx=Decimal("60000"),
        unrealizedPnl=Decimal("0"),
        leverage=PositionLeverage(type="cross", value=10),
    )


def test_empty_accepted_state_detects_open_after_explicit_zero_baseline() -> None:
    events = detect_changes([], [_position("0.01")])
    assert len(events) == 1
    assert events[0].signal_type == SignalType.OPEN


def test_small_increases_accumulate_against_last_accepted_state() -> None:
    accepted = [_position("1")]
    assert detect_changes(accepted, [_position("1.04")]) == []
    events = detect_changes(accepted, [_position("1.06")])
    assert len(events) == 1
    assert events[0].target_size == Decimal("1.06")
