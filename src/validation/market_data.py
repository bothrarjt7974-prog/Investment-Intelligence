"""
Market data ingestion and price lookups for claim validation.

Backfills and maintains daily OHLC snapshots in `market_data` for every
entity in ENTITY_SYMBOL_MAP, and exposes the price-lookup helpers the
validator uses to compute claim outcomes. yfinance is the sole automated
source; a handful of macro rates with no free API come from manual CSVs
in data/manual/.

No LLM calls anywhere in this module — pure deterministic data plumbing.
"""

from __future__ import annotations

import argparse
import random
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pendulum
import yfinance as yf
from loguru import logger

from src.db.queries import (
    fetch_earliest_snapshot_date,
    fetch_latest_market_price,
    fetch_latest_snapshot_date,
    fetch_market_price_series,
    symbol_has_market_data,
    upsert_many,
)
from src.validation.entity_symbol_map import ENTITY_SYMBOL_MAP

BATCH_SIZE = 500
ON_CONFLICT = "symbol,snapshot_date"

# Manual CSV files mapped to the canonical entity name they populate.
# Decoupled on purpose: file names describe the underlying data (repo rate,
# CPI print), symbol names match what claims/ENTITY_SYMBOL_MAP call the
# entity (rbi_rate, india_inflation, ...).
MANUAL_CSV_SYMBOLS: dict[str, str] = {
    "data/manual/rbi_repo.csv": "rbi_rate",
    "data/manual/fed_rate.csv": "fed_rate",
    "data/manual/india_cpi.csv": "india_inflation",
    "data/manual/us_cpi.csv": "us_inflation",
}


def _to_date_str(value: str | date | datetime) -> str:
    """Normalize a date-ish value to an ISO date string."""
    if isinstance(value, str):
        return pendulum.parse(value).to_date_string()
    if isinstance(value, datetime):
        return pendulum.instance(value).to_date_string()
    return value.isoformat()


def _rows_from_history(
    history: pd.DataFrame, symbol: str, fetch_symbol: str
) -> list[dict[str, Any]]:
    """Convert a yfinance history DataFrame into market_data row dicts."""
    rows: list[dict[str, Any]] = []
    for idx, record in history.iterrows():
        # yfinance indexes by Timestamp; some symbols (futures/FX) carry tz info.
        snapshot_date = idx.date().isoformat() if hasattr(idx, "date") else str(idx)
        close = record.get("Close")
        if close is None or pd.isna(close):
            continue
        rows.append(
            {
                "symbol": symbol,
                "fetch_symbol": fetch_symbol,
                "snapshot_date": snapshot_date,
                "open_price": _none_if_nan(record.get("Open")),
                "high_price": _none_if_nan(record.get("High")),
                "low_price": _none_if_nan(record.get("Low")),
                "close_price": float(close),
                "volume": _int_if_not_nan(record.get("Volume")),
                "source": "yfinance",
            }
        )
    return rows


def _none_if_nan(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


def _int_if_not_nan(value: Any) -> int | None:
    """Volume is a bigint column — yfinance/pandas hands it back as float64."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return int(value)


def _upsert_in_batches(rows: list[dict[str, Any]]) -> int:
    """Upsert rows into market_data BATCH_SIZE at a time. Returns count written."""
    written = 0
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i : i + BATCH_SIZE]
        upsert_many("market_data", chunk, on_conflict=ON_CONFLICT)
        written += len(chunk)
    return written


def backfill_symbol(
    symbol: str, fetch_symbol: str, start_date: str
) -> tuple[int, str | None]:
    """
    Download and upsert full daily history for one symbol.

    Returns (rows_written, error). error is None on success — callers should
    never let one bad symbol crash the whole backfill.
    """
    try:
        ticker = yf.Ticker(fetch_symbol)
        history = ticker.history(start=start_date, interval="1d")
        if history.empty:
            return 0, "no data returned"
        rows = _rows_from_history(history, symbol, fetch_symbol)
        if not rows:
            return 0, "no usable rows (all-NaN close)"
        written = _upsert_in_batches(rows)
        return written, None
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def ensure_symbol_backfilled(
    symbol: str,
    fetch_symbol: str,
    start_date: str = "2019-01-01",
    needed_from: str | None = None,
) -> bool:
    """
    On-demand single-symbol backfill for entities resolved via
    entity_symbol_map's geography fallback (e.g. a ticker not explicitly in
    ENTITY_SYMBOL_MAP, appended with .NS) — these are skipped by the bulk
    backfill_all() loop, which only iterates the explicit map, so without
    this they resolve a valid-looking symbol and then silently have no
    price data to validate against.

    No-ops (cheap) if the symbol already has rows. Returns True if data is
    available after this call — either it already was, or the on-demand
    fetch found real history. False means yfinance genuinely has nothing
    for this ticker (wrong/delisted/renamed — the caller should still fall
    back to data_unavailable, and the "no data returned" log is exactly the
    signal that this entity needs a curated map correction, as ZOMATO.NS
    and TATAMOTORS.NS did).

    `needed_from` (a claim's date) makes "has rows" mean "has rows going
    back far enough". Without it, a symbol that only ever received a recent
    incremental update looked covered while every older claim on it had no
    starting price — 67 mapped symbols were in exactly that state on
    2026-10-05.
    """
    if needed_from is None:
        if symbol_has_market_data(symbol):
            return True
    else:
        earliest = fetch_earliest_snapshot_date(symbol)
        if earliest is not None and earliest <= needed_from:
            return True

    logger.info(f"'{symbol}' has no market_data yet — on-demand backfill from {fetch_symbol}")
    written, error = backfill_symbol(symbol, fetch_symbol, start_date)
    if error:
        logger.warning(
            f"On-demand backfill failed for '{symbol}' ({fetch_symbol}): {error}. "
            f"If this ticker is real, verify it manually; if renamed/delisted, "
            f"add a corrected explicit entry to ENTITY_SYMBOL_MAP."
        )
        return False
    return written > 0


def backfill_all(
    start_date: str = "2019-01-01", symbols: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """
    Backfill daily OHLC for every yfinance-sourced entity in ENTITY_SYMBOL_MAP.

    `symbols` restricts to a subset of canonical entity names — used for the
    small smoke test before committing to the full ~70-100 symbol backfill.
    Returns a per-symbol summary dict for logging/inspection.
    """
    universe = {
        k: v
        for k, v in ENTITY_SYMBOL_MAP.items()
        if v[1] == "yfinance" and (symbols is None or k in symbols)
    }
    logger.info(f"Backfilling {len(universe)} symbols from {start_date}")

    results: dict[str, dict[str, Any]] = {}
    for i, (symbol, (fetch_symbol, _source)) in enumerate(universe.items(), start=1):
        logger.info(f"[{i}/{len(universe)}] {symbol} ({fetch_symbol})")
        written, error = backfill_symbol(symbol, fetch_symbol, start_date)
        results[symbol] = {"fetch_symbol": fetch_symbol, "rows": written, "error": error}
        if error:
            logger.warning(f"{symbol} ({fetch_symbol}): {error}")
        else:
            logger.info(f"{symbol} ({fetch_symbol}): {written} rows")

        if i < len(universe):
            time.sleep(random.uniform(1, 2))

    total_rows = sum(r["rows"] for r in results.values())
    failed = [s for s, r in results.items() if r["error"]]
    logger.info(
        f"Backfill complete: {total_rows} rows across {len(universe) - len(failed)} "
        f"symbols, {len(failed)} failed: {failed}"
    )
    return results


def daily_update() -> dict[str, dict[str, Any]]:
    """
    Fetch every yfinance symbol from just before the newest stored row.

    Resumes from the latest snapshot_date (minus a 7-day overlap) rather than
    a fixed "last 7 days": when the cron stopped for three months (Jul-Oct
    2026), a fixed window left a permanent hole that silently broke every
    claim validation and backtest landing in it. Manual CSV entities are not
    touched here — those are updated by re-running --load-manual when new
    official data is published.
    """
    week_ago = pendulum.now("UTC").subtract(days=7)
    latest = fetch_latest_snapshot_date()
    start = week_ago if latest is None else min(week_ago, pendulum.parse(latest).subtract(days=7))
    return backfill_all(start_date=start.to_date_string())


def load_manual_csv(path: str, symbol: str) -> int:
    """
    Load a manual CSV (columns: date, value) into market_data as close_price.

    Used for macro series with no free automated API (RBI/Fed rates, CPI).
    """
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Manual CSV not found: {path}")

    df = pd.read_csv(csv_path, comment="#")
    rows = [
        {
            "symbol": symbol,
            "fetch_symbol": None,
            "snapshot_date": _to_date_str(row["date"]),
            "close_price": float(row["value"]),
            "source": "manual",
        }
        for _, row in df.iterrows()
    ]
    written = _upsert_in_batches(rows)
    logger.info(f"Loaded {written} rows for '{symbol}' from {path}")
    return written


def load_all_manual_csvs() -> dict[str, int]:
    """Load every manual CSV in MANUAL_CSV_SYMBOLS. Skips files that don't exist yet."""
    results: dict[str, int] = {}
    for path, symbol in MANUAL_CSV_SYMBOLS.items():
        if not Path(path).exists():
            logger.warning(f"Manual CSV missing, skipping: {path}")
            continue
        results[symbol] = load_manual_csv(path, symbol)
    return results


# =====================================================================
# Price lookup helpers — used by src/validation/validator.py
# =====================================================================


def price_on(
    symbol: str, target_date: str | date | datetime, max_lookback_days: int = 7
) -> float | None:
    """
    Closing price on `target_date`, or the nearest prior trading day within
    `max_lookback_days`. Handles weekends and market holidays (Indian and US)
    by simply looking backward for the most recent snapshot — no calendar
    logic needed since we only ever have rows for days markets were open.
    """
    target = _to_date_str(target_date)
    earliest = pendulum.parse(target).subtract(days=max_lookback_days).to_date_string()
    row = fetch_latest_market_price(symbol, on_or_before=target, earliest=earliest)
    if row is None or row.get("close_price") is None:
        return None
    return float(row["close_price"])


def price_series(
    symbol: str, start: str | date | datetime, end: str | date | datetime
) -> list[tuple[date, float]]:
    """Ordered (date, close) pairs for `symbol` between start and end, inclusive."""
    rows = fetch_market_price_series(symbol, _to_date_str(start), _to_date_str(end))
    return [
        (pendulum.parse(r["snapshot_date"]).date(), float(r["close_price"]))
        for r in rows
        if r.get("close_price") is not None
    ]


def return_between(
    symbol: str, start: str | date | datetime, end: str | date | datetime
) -> float | None:
    """Percentage return from `start` to `end` using nearest-prior-day closes."""
    price_start = price_on(symbol, start)
    price_end = price_on(symbol, end)
    if price_start is None or price_end is None or price_start == 0:
        return None
    return (price_end - price_start) / price_start * 100


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: python -m src.validation.market_data --backfill|--daily|--load-manual"""
    parser = argparse.ArgumentParser(description="Market data ingestion for claim validation")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backfill", action="store_true", help="Full historical backfill")
    group.add_argument("--daily", action="store_true", help="Last-7-days incremental update")
    group.add_argument("--load-manual", action="store_true", help="Load manual CSV tables")
    parser.add_argument(
        "--start-date", default="2019-01-01", help="Backfill start date (--backfill only)"
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated canonical entity names to restrict --backfill to (smoke test)",
    )
    args = parser.parse_args(argv)

    if args.backfill:
        symbols = args.symbols.split(",") if args.symbols else None
        results = backfill_all(start_date=args.start_date, symbols=symbols)
        total = sum(r["rows"] for r in results.values())
        print(f"Backfilled {total} rows across {len(results)} symbols")
    elif args.daily:
        results = daily_update()
        total = sum(r["rows"] for r in results.values())
        print(f"Daily update: {total} rows across {len(results)} symbols")
    elif args.load_manual:
        results = load_all_manual_csvs()
        print(f"Loaded manual CSVs: {results}")


if __name__ == "__main__":
    main()
