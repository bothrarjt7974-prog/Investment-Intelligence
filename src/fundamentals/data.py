"""
Fundamentals data — instrument resolution, snapshot fetching, and
sector-relative valuation for Layer 7 (fundamentals sanity check).

No LLM calls anywhere in this module — pure deterministic data plumbing,
same principle as src/validation/market_data.py. Current-snapshot only
(trailing PE, P/B, debt/equity, current ratio, earnings/revenue growth):
yfinance's free tier doesn't expose the historical PE time series the
plan's "vs 5/10yr own average" check would need, so valuation is checked
sector-relative instead (see 2026-07-06 decision log entry) — and NSE
sector indices don't expose PE via yfinance either (empirically verified:
^NSEBANK, ^CNXIT, ^NSEI all return None), hence the curated peer-ticker
basket below rather than a single index lookup.
"""

from __future__ import annotations

from typing import Any

import yfinance as yf
from loguru import logger

from src.config.tuning import param
from src.extraction.normalization import NOT_TICKERS, TICKER_PATTERN
from src.validation.entity_symbol_map import PRIVATE_ENTITIES, resolve_entity

# Canonical sector entity -> representative tickers already known in
# ENTITY_SYMBOL_MAP. v1 seed list covering the sectors that map already
# defines — grow over time as themes surface sectors not covered here,
# same curation spirit as ENTITY_SYMBOL_MAP itself.
SECTOR_PEER_TICKERS: dict[str, list[str]] = {
    "indian_banking": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK"],
    "indian_it": ["TCS", "INFY", "WIPRO"],
    "indian_auto": ["MARUTI"],
    "indian_fmcg": ["HINDUNILVR", "ITC"],
    "indian_pharma": ["SUNPHARMA"],
    "indian_energy": ["ONGC", "NTPC", "POWERGRID"],
    "indian_metals": ["COALINDIA"],
    "us_tech": ["AAPL", "MSFT", "GOOGL"],
    "us_software": ["MSFT"],
    "us_consumer": ["AMZN"],
}

FUNDAMENTALS_FIELDS = (
    "trailingPE",
    "priceToBook",
    "debtToEquity",
    "currentRatio",
    "earningsGrowth",
    "revenueGrowth",
    "recommendationKey",
)

# Balance sheet hard-veto thresholds — v1 placeholders, documented for
# calibration like every other threshold in this codebase. Both conditions
# must be present AND both must fail; missing data never triggers a veto
# (absence of data isn't evidence of a problem).
BALANCE_SHEET_MAX_DEBT_TO_EQUITY = param("fundamentals.max_debt_to_equity")
BALANCE_SHEET_MIN_CURRENT_RATIO = param("fundamentals.min_current_ratio")

# Plan §10: "Above 1.5x historical average is a red flag" — reused here as
# 1.5x sector-peer average, the closest computable analogue (see decision log).
SECTOR_RELATIVE_PE_RED_FLAG = param("fundamentals.sector_pe_red_flag_ratio")

# --- Index-level valuation check (2026-07-09, closes the "index themes
# bypass fundamentals" gap for equity indices) ---
# Indices themselves expose no PE via yfinance (verified: ^NSEI/^GSPC
# return None), but their tracker ETFs DO (verified: SPY 26.8, QQQ 31.6,
# NIFTYBEES 21.5 on 2026-07-09). Each entity maps to (proxy_etf,
# fair_band_low, fair_band_high) — trailing-PE bands are v1 placeholders
# anchored to long-run medians, documented for calibration like every
# other threshold here. Verdict is deterministic: within/below band →
# pass, above band → downgrade (rich valuations warrant caution, not a
# veto — there is no balance-sheet-disaster analog for an index, so
# reject is never produced here).
INDEX_PE_PROXIES: dict[str, tuple[str, float, float]] = {
    "sp500": ("SPY", 16.0, 24.0),
    "us_equities": ("SPY", 16.0, 24.0),
    "nasdaq": ("QQQ", 20.0, 30.0),
    "qqq": ("QQQ", 20.0, 30.0),
    "dow": ("DIA", 15.0, 22.0),
    "us_smallcap": ("IWM", 14.0, 22.0),
    "nifty50": ("NIFTYBEES.NS", 17.0, 24.0),
    "indian_equity_market": ("NIFTYBEES.NS", 17.0, 24.0),
    "broad_equity": ("NIFTYBEES.NS", 17.0, 24.0),
}


def _looks_like_ticker(entity: str | None) -> bool:
    """Same signal normalize_entity uses to distinguish tickers (RELIANCE,
    AAPL) from sector/index/macro canonical names (indian_banking, nifty50,
    gold) — uppercase, ticker-shaped, not in the explicit non-ticker set."""
    if not entity:
        return False
    return bool(TICKER_PATTERN.match(entity)) and entity.isupper() and entity not in NOT_TICKERS


def resolve_target_instruments(theme: dict[str, Any]) -> list[str]:
    """
    Resolve which instrument(s) a theme's fundamentals check should run
    against, in priority order:

      1. theme.instruments_mentioned (specific tickers theme_synthesis
         already extracted from the underlying claims' reasoning)
      2. entity_cluster_key itself, if it's ticker-shaped (the theme is
         already about one specific stock)
      3. [] — a sector/index/macro-level theme with no named instrument.
         Fundamentals checking a sector in the abstract isn't meaningful
         without a constituent mapping; the caller marks this
         'not_applicable' rather than fabricating an opinion about
         instruments nobody named.
    """
    # Private companies (OpenAI, SpaceX...) have no filings/price series —
    # excluded up front so a theme about them lands on 'not_applicable'
    # instead of chasing tickers that don't exist.
    instruments_mentioned = [
        i for i in (theme.get("instruments_mentioned") or []) if i not in PRIVATE_ENTITIES
    ]
    resolved = [i for i in instruments_mentioned if _looks_like_ticker(i)]
    if resolved:
        return resolved

    entity_cluster_key = theme.get("entity_cluster_key")
    if _looks_like_ticker(entity_cluster_key) and entity_cluster_key not in PRIVATE_ENTITIES:
        return [entity_cluster_key]

    return []


def fetch_fundamentals_snapshot(canonical_symbol: str) -> dict[str, Any] | None:
    """
    Fetch a current fundamentals snapshot for one canonical entity via
    yfinance. Returns None on any resolution/fetch failure — never crash
    the batch on one bad ticker, same resilience pattern as
    market_data.backfill_symbol.
    """
    resolved = resolve_entity(canonical_symbol, geography=None)
    if resolved is None:
        logger.info(f"'{canonical_symbol}' not resolvable to a data source for fundamentals")
        return None

    fetch_symbol, source = resolved
    if source != "yfinance" or not fetch_symbol:
        return None

    try:
        info = yf.Ticker(fetch_symbol).info
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Fundamentals fetch failed for '{canonical_symbol}' ({fetch_symbol}): {exc}")
        return None

    snapshot = {field: info.get(field) for field in FUNDAMENTALS_FIELDS}
    if all(value is None for value in snapshot.values()):
        return None
    return snapshot


def fetch_index_valuation(entity_cluster_key: str | None) -> dict[str, Any] | None:
    """
    Index-level valuation snapshot via the entity's tracker-ETF proxy.
    Returns {"proxy", "trailing_pe", "band_low", "band_high", "position"}
    (position: 'below' | 'within' | 'above') or None when the entity has
    no proxy mapping or the fetch fails — caller falls back to the
    existing not_applicable path.
    """
    if not entity_cluster_key or entity_cluster_key not in INDEX_PE_PROXIES:
        return None

    proxy, band_low, band_high = INDEX_PE_PROXIES[entity_cluster_key]
    try:
        trailing_pe = yf.Ticker(proxy).info.get("trailingPE")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Index valuation fetch failed for '{entity_cluster_key}' ({proxy}): {exc}")
        return None

    if trailing_pe is None:
        return None

    if trailing_pe < band_low:
        position = "below"
    elif trailing_pe > band_high:
        position = "above"
    else:
        position = "within"

    return {
        "proxy": proxy,
        "trailing_pe": round(float(trailing_pe), 2),
        "band_low": band_low,
        "band_high": band_high,
        "position": position,
    }


def compute_sector_relative_pe(entity_cluster_key: str | None, trailing_pe: float | None) -> float | None:
    """
    Ratio of an instrument's trailing PE to its sector's peer-basket
    average PE. Only meaningful when entity_cluster_key is itself a known
    sector in SECTOR_PEER_TICKERS — an individually-named instrument
    (resolved via instruments_mentioned with no sector context) returns
    None here; the LLM sees the raw PE without a ratio in that case.
    """
    if trailing_pe is None or not entity_cluster_key:
        return None

    peers = SECTOR_PEER_TICKERS.get(entity_cluster_key)
    if not peers:
        return None

    peer_pes = []
    for peer in peers:
        snapshot = fetch_fundamentals_snapshot(peer)
        if snapshot and snapshot.get("trailingPE") is not None:
            peer_pes.append(snapshot["trailingPE"])

    if len(peer_pes) < 2:
        return None

    peer_average = sum(peer_pes) / len(peer_pes)
    if not peer_average:
        return None
    return trailing_pe / peer_average


def check_balance_sheet_red_flag(snapshot: dict[str, Any]) -> bool:
    """
    Deterministic hard veto: plan §10 says deteriorating balance sheets
    are rejected "regardless of expert enthusiasm" — a mechanical rule,
    not an LLM judgment call. Both debt/equity and current ratio must be
    present AND both must fail; missing data never triggers a false veto.
    """
    debt_to_equity = snapshot.get("debtToEquity")
    current_ratio = snapshot.get("currentRatio")
    if debt_to_equity is None or current_ratio is None:
        return False
    return debt_to_equity > BALANCE_SHEET_MAX_DEBT_TO_EQUITY and current_ratio < BALANCE_SHEET_MIN_CURRENT_RATIO
