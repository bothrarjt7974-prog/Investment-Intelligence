"""
Trigger vocabulary — the curated mapping from a strategy's stated trigger
type to a programmatic historical-window detector.

Lives in code, not a DB table (technical_execution_plan_v3.docx §8's tech
stack section says "lives in the database, versioned," but this project
already has the equivalent precedent for Track A: ENTITY_SYMBOL_MAP in
entity_symbol_map.py is a Python dict, not a table, because the actual
logic is code either way — a trigger type is meaningless without its
detector function, and functions can't live in a DB row).

Each detector has signature `(symbol: str, is_india_linked: bool) ->
list[date]` and returns the START date of every historical window where
the trigger condition held. `is_india_linked` lets rate/currency detectors
pick the right macro series without strategies needing a geography column
of their own — resolved once by the caller (src/validation/backtest.py)
from the entity's fetch_symbol (.NS suffix), since canonical entity names
alone don't self-identify as Indian or not.

Only 5 of the 7 trigger types the strategy_extraction_v2 prompt can emit
are registered here. valuation_extreme_cheap/valuation_extreme_rich need
historical PE/valuation data, which isn't ingested anywhere (Layer 6
fundamentals, not built) — deliberately absent rather than faked with a
technical proxy standing in for a fundamentals concept the expert didn't
actually state. A strategy with one of those two trigger types is marked
insufficient_history by backtest.py, which checks TRIGGER_DETECTORS
membership.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable
from datetime import date, timedelta

import pendulum

from src.validation.market_data import price_series

# Plan §8: "Scans historical market data (2010 onward where available)".
EARLIEST_HISTORY_DATE = "2010-01-01"

DEFAULT_DRAWDOWN_THRESHOLD_PCT = -20.0
DEFAULT_CURRENCY_MOVE_THRESHOLD_PCT = 5.0
CURRENCY_MOVE_WINDOW_DAYS = 90

# Two windows from the same episode (same drawdown, same rate cycle, same
# currency move) must not each register as a separate trigger — a strategy
# backtested on "every day of the 2020 crash" would wildly overcount one
# real historical event. A new trigger only fires if it's at least this
# many days after the last one recorded by the same detector.
DEFAULT_EPISODE_GAP_DAYS = 90

# always_on has no real trigger — it's a standing rule. Sample the full
# available history at this stride so it still gets backtested across many
# entry points instead of just one.
ALWAYS_ON_STRIDE_DAYS = 90


def _today_str() -> str:
    return pendulum.now("UTC").to_date_string()


def _dedup_windows(dates: list[date], min_gap_days: int = DEFAULT_EPISODE_GAP_DAYS) -> list[date]:
    """Keep only the first trigger date per episode (see module docstring)."""
    if not dates:
        return []
    ordered = sorted(dates)
    kept = [ordered[0]]
    for d in ordered[1:]:
        if (d - kept[-1]).days >= min_gap_days:
            kept.append(d)
    return kept


def _detect_consecutive_direction_cycle(
    rate_series: list[tuple[date, float]], direction: int
) -> list[date]:
    """
    direction=-1 detects rate cuts, direction=+1 detects rate hikes.

    A "cycle" (plan §8's example: "RBI repo rate reduced in 2 consecutive
    policy meetings") fires at the date of the 2nd (and every subsequent)
    consecutive same-direction move — reset the streak on any move the
    other way or a hold.
    """
    windows: list[date] = []
    consecutive = 0
    for i in range(1, len(rate_series)):
        _, prev_val = rate_series[i - 1]
        cur_date, cur_val = rate_series[i]
        diff = cur_val - prev_val
        moved = (direction == -1 and diff < 0) or (direction == 1 and diff > 0)
        if moved:
            consecutive += 1
            if consecutive >= 2:
                windows.append(cur_date)
        else:
            consecutive = 0
    return windows


def detect_rate_cut_cycle(symbol: str, is_india_linked: bool) -> list[date]:
    """RBI/Fed repo rate reduced in 2+ consecutive readings."""
    rate_symbol = "rbi_rate" if is_india_linked else "fed_rate"
    series = price_series(rate_symbol, EARLIEST_HISTORY_DATE, _today_str())
    return _dedup_windows(_detect_consecutive_direction_cycle(series, direction=-1))


def detect_rate_hike_cycle(symbol: str, is_india_linked: bool) -> list[date]:
    """RBI/Fed repo rate raised in 2+ consecutive readings."""
    rate_symbol = "rbi_rate" if is_india_linked else "fed_rate"
    series = price_series(rate_symbol, EARLIEST_HISTORY_DATE, _today_str())
    return _dedup_windows(_detect_consecutive_direction_cycle(series, direction=1))


def detect_drawdown_threshold(symbol: str, is_india_linked: bool) -> list[date]:
    """
    Every date the instrument's trailing-peak drawdown first crosses below
    DEFAULT_DRAWDOWN_THRESHOLD_PCT (-20%). `in_drawdown` tracks whether
    we're still inside the same episode so a multi-month crash registers
    once, not on every day it stays below threshold.
    """
    series = price_series(symbol, EARLIEST_HISTORY_DATE, _today_str())
    if len(series) < 2:
        return []

    windows: list[date] = []
    peak = series[0][1]
    in_drawdown = False
    for d, price in series:
        if price > peak:
            peak = price
        drawdown_pct = (price - peak) / peak * 100 if peak else 0.0
        crossed = drawdown_pct <= DEFAULT_DRAWDOWN_THRESHOLD_PCT
        if crossed and not in_drawdown:
            windows.append(d)
            in_drawdown = True
        elif not crossed:
            in_drawdown = False
    return _dedup_windows(windows)


def detect_currency_regime(symbol: str, is_india_linked: bool) -> list[date]:
    """
    A sustained >5% move (either direction) in INR/USD over a trailing
    90-day span. v1 hardcodes inr_usd regardless of the strategy's own
    entity_class or is_india_linked — it's the one FX pair we have real
    reasoning to trust for this (India-focused) expert's content. Revisit
    when non-India strategies with other currency pairs appear.
    """
    series = price_series("inr_usd", EARLIEST_HISTORY_DATE, _today_str())
    if len(series) < 2:
        return []

    dates = [d for d, _ in series]
    prices = [p for _, p in series]
    windows: list[date] = []
    for i, d in enumerate(dates):
        cutoff = d - timedelta(days=CURRENCY_MOVE_WINDOW_DAYS)
        idx = bisect.bisect_right(dates, cutoff) - 1
        if idx < 0:
            continue
        prior_price = prices[idx]
        if not prior_price:
            continue
        move_pct = (prices[i] - prior_price) / prior_price * 100
        if abs(move_pct) >= DEFAULT_CURRENCY_MOVE_THRESHOLD_PCT:
            windows.append(d)
    return _dedup_windows(windows)


def detect_always_on(symbol: str, is_india_linked: bool) -> list[date]:
    """
    Standing rule with no real trigger — sample the full available price
    history for `symbol` at a fixed stride so it still gets backtested
    across many entry points.
    """
    series = price_series(symbol, EARLIEST_HISTORY_DATE, _today_str())
    if not series:
        return []

    first_date, last_date = series[0][0], series[-1][0]
    windows: list[date] = []
    current = first_date
    while current <= last_date:
        windows.append(current)
        current = current + timedelta(days=ALWAYS_ON_STRIDE_DAYS)
    return windows


TRIGGER_DETECTORS: dict[str, Callable[[str, bool], list[date]]] = {
    "rate_cut_cycle": detect_rate_cut_cycle,
    "rate_hike_cycle": detect_rate_hike_cycle,
    "drawdown_threshold": detect_drawdown_threshold,
    "currency_regime": detect_currency_regime,
    "always_on": detect_always_on,
}
