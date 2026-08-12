from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.services.hyperliquid.models import Position

_UPDATE_THRESHOLD = Decimal("0.05")


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
    previous_size: Decimal
    target_size: Decimal
    delta_size: Decimal
    dex: str = ""


def _position_map(positions: list[Position]) -> dict[str, Position]:
    return {position.coin: position for position in positions}


def detect_changes(
    accepted: list[Position],
    observed: list[Position],
    *,
    dex: str = "",
) -> list[SignalEvent]:
    """Compare the last accepted signed state with a valid observed snapshot."""
    accepted_map = _position_map(accepted)
    observed_map = _position_map(observed)
    events: list[SignalEvent] = []

    for coin in sorted(accepted_map.keys() | observed_map.keys()):
        previous = accepted_map.get(coin)
        target = observed_map.get(coin)
        previous_size = previous.szi if previous else Decimal("0")
        target_size = target.szi if target else Decimal("0")
        if previous_size == target_size:
            continue

        signal_type: SignalType | None
        if previous_size == 0 and target_size != 0:
            signal_type = SignalType.OPEN
        elif previous_size != 0 and target_size == 0:
            signal_type = SignalType.CLOSE
        elif previous_size * target_size < 0 or abs(target_size) < abs(previous_size):
            signal_type = SignalType.UPDATE
        else:
            change_ratio = abs(target_size - previous_size) / abs(previous_size)
            signal_type = (
                SignalType.UPDATE if change_ratio >= _UPDATE_THRESHOLD else None
            )
        if signal_type is None:
            continue

        reference = target or previous
        events.append(
            SignalEvent(
                signal_type=signal_type,
                coin=coin,
                side=(
                    "long"
                    if target_size > 0
                    else (
                        "short"
                        if target_size < 0
                        else reference.side if reference else None
                    )
                ),
                size=abs(target_size) if target_size else None,
                entry_price=target.entry_px if target else None,
                leverage=(float(target.leverage.value) if target is not None else None),
                previous_size=previous_size,
                target_size=target_size,
                delta_size=target_size - previous_size,
                dex=dex,
            )
        )

    return events
