"""
Track B validator: strategy backtesting against historical market data.

For each quality-passed, in-scope strategy, detect every historical window
when its trigger condition held (src/validation/trigger_vocabulary.py),
simulate the strategy mechanically in each window, and aggregate the
results into a backtest_record. Follows the same CLI/loop/logging pattern
as src/validation/validator.py (Track A).

No LLM calls — pure deterministic price-data comparison, same principle as
Track A. Known v1 limitations (documented here and in the decision log):

  - Exit rule is time-based only. exit_conditions free text ("trim if the
    sector runs 30% above trend") is never parsed into a programmatic exit
    signal — trigger detection is already the hard part of Track B: every
    simulated window holds for exactly holding_horizon_months (or
    DEFAULT_BACKTEST_HORIZON_MONTHS if unstated), then exits.
  - Benchmark is a single buy-and-hold CAGR over the instrument's full
    available history, scaled to the strategy's horizon — not a sampled
    distribution of random entry points.
  - valuation_extreme_cheap/valuation_extreme_rich triggers have no
    detector (see trigger_vocabulary.py) — those strategies are marked
    insufficient_history, never scored with a faked proxy.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from typing import Any

import pendulum
from loguru import logger

from src.config.tuning import param
from src.db.queries import (
    fetch_backtestable_strategies,
    fetch_insufficient_history_strategies,
    update_by_id,
)
from src.extraction.normalization import normalize_entity
from src.validation.entity_symbol_map import resolve_entity
from src.validation.market_data import price_on, price_series
from src.validation.trigger_vocabulary import TRIGGER_DETECTORS

# Plan §8: strategies with fewer than 3 historical trigger windows are
# flagged insufficient_history rather than scored — never extrapolate from
# one data point.
MIN_TRIGGER_WINDOWS = param("backtest.min_trigger_windows")

# Used when a strategy states no holding_horizon_months — matches the
# "medium" bucket median already used for claims (normalization.py).
DEFAULT_BACKTEST_HORIZON_MONTHS = param("backtest.default_horizon_months")

PRICE_LOOKUP_MAX_LOOKBACK_DAYS = 7


# A full run (no --max-* flag) means everything pending. The old `or 100`
# default silently capped nightly runs; 1000 is PostgREST's per-request ceiling.
FULL_RUN_LIMIT = 1000

def _add_months(d: date, months: int) -> date:
    """Same 30.44-days/month approximation as normalization.compute_target_date."""
    return d + timedelta(days=int(months * 30.44))


def _today_str() -> str:
    return pendulum.now("UTC").to_date_string()


def compute_window_return(symbol: str, start: date, end: date) -> float | None:
    """Percentage return from entry (start) to exit (end). None if either price is missing."""
    entry = price_on(symbol, start, max_lookback_days=PRICE_LOOKUP_MAX_LOOKBACK_DAYS)
    exit_price = price_on(symbol, end, max_lookback_days=PRICE_LOOKUP_MAX_LOOKBACK_DAYS)
    if entry is None or exit_price is None or entry == 0:
        return None
    return (exit_price - entry) / entry * 100


def compute_window_drawdown(symbol: str, start: date, end: date) -> float | None:
    """Max peak-to-trough decline (%) within [start, end]. None if no price series."""
    series = price_series(symbol, start.isoformat(), end.isoformat())
    if not series:
        return None

    peak = series[0][1]
    worst = 0.0
    for _, price in series:
        if price > peak:
            peak = price
        drawdown_pct = (price - peak) / peak * 100 if peak else 0.0
        if drawdown_pct < worst:
            worst = drawdown_pct
    return worst


def compute_benchmark_return(symbol: str, horizon_months: int) -> float | None:
    """
    Buy-and-hold CAGR over the instrument's full available price history,
    scaled to horizon_months — the "what if I'd just held this instead"
    comparison for the strategy's average trigger-window return.
    """
    full_series = price_series(symbol, "2010-01-01", _today_str())
    if len(full_series) < 2:
        return None

    first_date, first_price = full_series[0]
    last_date, last_price = full_series[-1]
    days = (last_date - first_date).days
    if days <= 0 or first_price <= 0:
        return None

    cagr = (last_price / first_price) ** (365.25 / days) - 1
    return ((1 + cagr) ** (horizon_months / 12) - 1) * 100


def simulate_strategy(strategy: dict[str, Any]) -> dict[str, Any]:
    """
    Backtest one strategy. Returns the update dict to write to the
    strategies row (backtest_status, backtest_record).
    """
    # Canonicalized first: strategies stored before an alias existed
    # (nifty_50, CRYPTO, nifty_it) otherwise never resolve to a series.
    entity_class = normalize_entity(strategy.get("entity_class"))
    resolved = resolve_entity(entity_class, geography=None) if entity_class else None
    if resolved is None:
        return {
            "backtest_status": "insufficient_history",
            "backtest_record": {
                "reason": UNRESOLVABLE_REASON,
                "entity_class": entity_class,
            },
        }

    fetch_symbol, _source = resolved
    # market_data rows are keyed by the canonical entity name, same
    # convention as validator.py's Track A lookup — resolve_entity is only
    # consulted to confirm the entity is mappable and to get fetch_symbol's
    # .NS suffix as the geography signal below.
    symbol = entity_class
    is_india_linked = bool(fetch_symbol) and fetch_symbol.endswith(".NS")

    trigger_conditions = strategy.get("trigger_conditions")
    trigger_type = (
        trigger_conditions.get("type") if isinstance(trigger_conditions, dict) else None
    )
    detector = TRIGGER_DETECTORS.get(trigger_type) if trigger_type else None
    if detector is None:
        return {
            "backtest_status": "insufficient_history",
            "backtest_record": {
                "reason": f"no detector registered for trigger type {trigger_type!r}",
                "trigger_type": trigger_type,
            },
        }

    horizon_months = strategy.get("holding_horizon_months") or DEFAULT_BACKTEST_HORIZON_MONTHS
    window_starts = detector(symbol, is_india_linked)

    windows: list[dict[str, Any]] = []
    for start in window_starts:
        end = _add_months(start, horizon_months)
        return_pct = compute_window_return(symbol, start, end)
        if return_pct is None:
            continue
        windows.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "return_pct": return_pct,
                "drawdown_pct": compute_window_drawdown(symbol, start, end),
            }
        )

    if len(windows) < MIN_TRIGGER_WINDOWS:
        return {
            "backtest_status": "insufficient_history",
            "backtest_record": {
                "reason": (
                    f"only {len(windows)} valid trigger window(s) found "
                    f"(need >= {MIN_TRIGGER_WINDOWS})"
                ),
                "trigger_type": trigger_type,
                "windows_found": len(windows),
            },
        }

    num_windows = len(windows)
    win_rate = sum(1 for w in windows if w["return_pct"] > 0) / num_windows
    avg_return_pct = sum(w["return_pct"] for w in windows) / num_windows
    drawdowns = [w["drawdown_pct"] for w in windows if w["drawdown_pct"] is not None]
    worst_drawdown_pct = min(drawdowns) if drawdowns else None
    benchmark_return_pct = compute_benchmark_return(symbol, horizon_months)
    outperformance_pct = (
        avg_return_pct - benchmark_return_pct if benchmark_return_pct is not None else None
    )

    return {
        "backtest_status": "complete",
        "backtest_record": {
            "trigger_type": trigger_type,
            "num_windows": num_windows,
            "win_rate": win_rate,
            "avg_return_pct": avg_return_pct,
            "worst_drawdown_pct": worst_drawdown_pct,
            "benchmark_return_pct": benchmark_return_pct,
            "outperformance_pct": outperformance_pct,
            "holding_horizon_months_used": horizon_months,
            "windows": windows,
        },
    }


UNRESOLVABLE_REASON = "entity_class not resolvable to a data source"


def requeue_newly_resolvable() -> int:
    """
    Reset insufficient_history strategies to pending when the only reason
    was an unresolvable entity_class that now resolves (alias or symbol-map
    growth). Other reasons (no trigger detector, too few windows) are left
    alone — nothing about them changes between runs.
    """
    requeued = 0
    for strategy in fetch_insufficient_history_strategies():
        reason = (strategy.get("backtest_record") or {}).get("reason")
        if reason != UNRESOLVABLE_REASON:
            continue
        canonical = normalize_entity(strategy.get("entity_class"))
        if canonical and resolve_entity(canonical, geography=None) is not None:
            update_by_id("strategies", strategy["id"], {"backtest_status": "pending"})
            requeued += 1
    if requeued:
        logger.info(f"Requeued {requeued} strategies whose entity_class now resolves")
    return requeued


def run_backtesting(max_strategies: int | None = None) -> dict[str, int]:
    """
    Run Track B backtesting for strategies where quality_verdict IN
    ('pass', 'partial'), scope='in_scope', and backtest_status='pending'.
    """
    requeue_newly_resolvable()
    strategies = fetch_backtestable_strategies(limit=max_strategies or FULL_RUN_LIMIT)
    logger.info(f"Found {len(strategies)} backtestable strategies")

    counts = {"complete": 0, "insufficient_history": 0, "error": 0, "total": len(strategies)}

    for index, strategy in enumerate(strategies, start=1):
        strategy_id = strategy.get("id")
        try:
            update_fields = simulate_strategy(strategy)
            update_by_id("strategies", strategy_id, update_fields)
            status = update_fields.get("backtest_status", "error")
            if status in counts:
                counts[status] += 1
            else:
                counts["error"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to backtest strategy {strategy_id}: {exc}")
            counts["error"] += 1

        if index % 25 == 0 or index == len(strategies):
            logger.info(f"Backtested {index}/{len(strategies)} strategies")

    logger.info(
        f"Backtesting complete: complete={counts['complete']} "
        f"insufficient_history={counts['insufficient_history']} "
        f"errors={counts['error']} (of {counts['total']})"
    )

    return counts


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for strategy backtesting."""
    parser = argparse.ArgumentParser(
        description="Backtest quality-passed strategies against historical market data"
    )
    parser.add_argument(
        "--max-strategies",
        type=int,
        default=None,
        help="Limit how many strategies to backtest for a test run",
    )
    args = parser.parse_args(argv)

    counts = run_backtesting(max_strategies=args.max_strategies)
    print(
        f"Backtested {counts['total']} strategies: "
        f"{counts['complete']} complete, "
        f"{counts['insufficient_history']} insufficient_history, "
        f"{counts['error']} errors"
    )


if __name__ == "__main__":
    main()
