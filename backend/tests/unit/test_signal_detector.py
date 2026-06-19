from decimal import Decimal

from app.services.hyperliquid.models import Position, PositionLeverage
from app.services.signal_detector import SignalType, detect_changes


def _pos(coin: str, szi: str, entry_px: str = "60000") -> Position:
    return Position(
        coin=coin,
        szi=Decimal(szi),
        entryPx=Decimal(entry_px),
        unrealizedPnl=Decimal("0"),
        leverage=PositionLeverage(type="cross", value=10),
    )


class TestDetectChanges:
    def test_open_signal_when_new_position_appears(self) -> None:
        prev: list[Position] = []
        curr = [_pos("BTC", "0.01")]

        signals = detect_changes(prev, curr)

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.OPEN
        assert signals[0].coin == "BTC"
        assert signals[0].side == "long"
        assert signals[0].size == Decimal("0.01")

    def test_close_signal_when_position_disappears(self) -> None:
        prev = [_pos("ETH", "1.0")]
        curr: list[Position] = []

        signals = detect_changes(prev, curr)

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.CLOSE
        assert signals[0].coin == "ETH"
        assert signals[0].size is None

    def test_update_signal_when_size_changes_above_threshold(self) -> None:
        prev = [_pos("SOL", "10.0")]
        curr = [_pos("SOL", "11.0")]  # +10% change

        signals = detect_changes(prev, curr)

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.UPDATE
        assert signals[0].size == Decimal("11.0")

    def test_no_signal_when_size_change_below_threshold(self) -> None:
        prev = [_pos("SOL", "10.0")]
        curr = [_pos("SOL", "10.4")]  # +4% — below 5% threshold

        signals = detect_changes(prev, curr)

        assert len(signals) == 0

    def test_no_signal_when_position_unchanged(self) -> None:
        prev = [_pos("BTC", "0.5")]
        curr = [_pos("BTC", "0.5")]

        assert detect_changes(prev, curr) == []

    def test_open_and_close_simultaneously(self) -> None:
        prev = [_pos("BTC", "0.1")]
        curr = [_pos("ETH", "2.0")]

        signals = detect_changes(prev, curr)
        types = {s.signal_type for s in signals}

        assert SignalType.OPEN in types
        assert SignalType.CLOSE in types

    def test_side_change_treated_as_close_then_open(self) -> None:
        prev = [_pos("BTC", "0.01")]  # long
        curr = [_pos("BTC", "-0.01")]  # short — different (coin, side) key

        signals = detect_changes(prev, curr)
        types = {s.signal_type for s in signals}

        assert SignalType.CLOSE in types  # old long closed
        assert SignalType.OPEN in types  # new short opened

    def test_empty_snapshots_produce_no_signals(self) -> None:
        assert detect_changes([], []) == []
