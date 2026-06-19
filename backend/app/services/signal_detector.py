from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.services.hyperliquid.models import Position

_UPDATE_THRESHOLD = Decimal("0.05")  # 5% size change triggers UPDATE signal


class SignalType(StrEnum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    UPDATE = "UPDATE"


@dataclass(frozen=True)
class SignalEvent:
    signal_type: SignalType
    coin: str
    side: str | None
    size: Decimal | None
    entry_price: Decimal | None
    leverage: float | None


def detect_changes(
    prev: list[Position],
    curr: list[Position],
) -> list[SignalEvent]:
    """
    Compare two position snapshots and return detected trading signals.

    Rules:
    - A (coin, side) pair that appears in curr but not prev → OPEN
    - A (coin, side) pair that disappears → CLOSE
    - Same pair with |Δsize| / prev_size > 5% → UPDATE
    """
    prev_map: dict[tuple[str, str], Position] = {(p.coin, p.side): p for p in prev}
    curr_map: dict[tuple[str, str], Position] = {(p.coin, p.side): p for p in curr}

    signals: list[SignalEvent] = []

    for key, pos in curr_map.items():
        if key not in prev_map:
            signals.append(
                SignalEvent(
                    signal_type=SignalType.OPEN,
                    coin=pos.coin,
                    side=pos.side,
                    size=pos.abs_size,
                    entry_price=pos.entry_px,
                    leverage=float(pos.leverage.value),
                )
            )
        else:
            prev_pos = prev_map[key]
            if prev_pos.abs_size > Decimal("0"):
                change_ratio = abs(pos.abs_size - prev_pos.abs_size) / prev_pos.abs_size
                if change_ratio >= _UPDATE_THRESHOLD:
                    signals.append(
                        SignalEvent(
                            signal_type=SignalType.UPDATE,
                            coin=pos.coin,
                            side=pos.side,
                            size=pos.abs_size,
                            entry_price=pos.entry_px,
                            leverage=float(pos.leverage.value),
                        )
                    )

    for key, pos in prev_map.items():
        if key not in curr_map:
            signals.append(
                SignalEvent(
                    signal_type=SignalType.CLOSE,
                    coin=pos.coin,
                    side=pos.side,
                    size=None,
                    entry_price=None,
                    leverage=None,
                )
            )

    return signals
