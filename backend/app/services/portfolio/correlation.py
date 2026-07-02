import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.services.portfolio.types import ScoredCandidate

MIN_OVERLAPPING_DAYS = 10


@dataclass(frozen=True)
class CorrelationCheck:
    status: str
    max_abs_correlation: float | None
    peer_trader_id: int | None
    overlapping_days: int


def pearson_correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None

    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_deltas = [value - left_mean for value in left]
    right_deltas = [value - right_mean for value in right]
    numerator = sum(a * b for a, b in zip(left_deltas, right_deltas, strict=True))
    left_denominator = math.sqrt(sum(value * value for value in left_deltas))
    right_denominator = math.sqrt(sum(value * value for value in right_deltas))
    denominator = left_denominator * right_denominator
    if denominator == 0.0:
        return None
    return max(-1.0, min(1.0, numerator / denominator))


def daily_pnl_correlation(
    left: Mapping[str, float] | None,
    right: Mapping[str, float] | None,
    min_overlapping_days: int = MIN_OVERLAPPING_DAYS,
) -> tuple[float | None, int]:
    if not left or not right:
        return None, 0

    common_days = sorted(set(left).intersection(right))
    if len(common_days) < min_overlapping_days:
        return None, len(common_days)

    left_values = [left[day] for day in common_days]
    right_values = [right[day] for day in common_days]
    return pearson_correlation(left_values, right_values), len(common_days)


def max_correlation_to_selected(
    candidate: ScoredCandidate,
    selected: Sequence[ScoredCandidate],
    min_overlapping_days: int = MIN_OVERLAPPING_DAYS,
) -> CorrelationCheck:
    candidate_series = candidate.candidate.metrics.daily_pnl_by_day
    max_abs_correlation: float | None = None
    peer_trader_id: int | None = None
    max_overlap = 0

    for selected_candidate in selected:
        correlation, overlap = daily_pnl_correlation(
            candidate_series,
            selected_candidate.candidate.metrics.daily_pnl_by_day,
            min_overlapping_days=min_overlapping_days,
        )
        max_overlap = max(max_overlap, overlap)
        if correlation is None:
            continue
        abs_correlation = abs(correlation)
        if max_abs_correlation is None or abs_correlation > max_abs_correlation:
            max_abs_correlation = abs_correlation
            peer_trader_id = selected_candidate.candidate.trader_id

    if max_abs_correlation is None:
        return CorrelationCheck(
            status="unknown",
            max_abs_correlation=None,
            peer_trader_id=None,
            overlapping_days=max_overlap,
        )

    return CorrelationCheck(
        status="known",
        max_abs_correlation=round(max_abs_correlation, 6),
        peer_trader_id=peer_trader_id,
        overlapping_days=max_overlap,
    )
