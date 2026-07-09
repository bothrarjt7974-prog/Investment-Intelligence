"""
Deterministic eligibility gates for guidance generation (Layer 9).

Same "code does mechanics, LLM does meaning" split as the fundamentals
hard veto (src/fundamentals/data.py::check_balance_sheet_red_flag): every
gate here is a mechanical check on data already in the database, evaluated
BEFORE any Opus call — a theme that fails any gate is refused with a
specific reason and never reaches the (expensive, highest-stakes) guidance
generation prompt at all.

No LLM calls anywhere in this module.
"""

from __future__ import annotations

from typing import Any

from src.db.queries import fetch_active_guidance

ELIGIBLE_CONVICTION_LEVELS = ("medium", "high")
ELIGIBLE_FUNDAMENTALS_VERDICTS = ("pass", "downgrade")

# Index / macro / commodity / FX cluster keys where company-level
# fundamentals categorically don't exist — a balance-sheet check on
# nifty50 or crude oil is meaningless, not failed. For these entities
# (and ONLY these), not_applicable/insufficient_data fundamentals
# verdicts don't block guidance; the guidance record carries an explicit
# note instead (founder policy decision, 2026-07-09 decision log).
# Sector themes (indian_banking, us_tech_ai...) are NOT here — they have
# constituent stocks and must still earn pass/downgrade.
INDEX_MACRO_ENTITIES = frozenset(
    {
        "nifty50",
        "nifty500",
        "indian_smallcap",
        "indian_midcap",
        "indian_equity_market",
        "sp500",
        "nasdaq",
        "dow",
        "qqq",
        "us_equities",
        "us_smallcap",
        "broad_equity",
        "inr_usd",
        "dxy",
        "eur_usd",
        "jpy_usd",
        "gold",
        "silver",
        "crude_oil",
        "crypto_sector",
        "BTC",
        "ETH",
        "global_liquidity",
        "us_treasuries",
        "fed_rate",
        "rbi_rate",
        "india_inflation",
        "us_inflation",
        "india_gdp",
        "us_gdp",
    }
)


def fundamentals_not_applicable_but_eligible(theme: dict[str, Any]) -> bool:
    """True when this theme's fundamentals verdict doesn't block guidance
    because the entity is index/macro-level (no company fundamentals
    exist to check)."""
    return (
        theme.get("entity_cluster_key") in INDEX_MACRO_ENTITIES
        and theme.get("fundamentals_verdict") in ("not_applicable", "insufficient_data")
    )

# Simple percentage-of-portfolio heuristic (v1 — not LLM-judged): if the
# theme's entity/instruments already represent this much of the
# portfolio's value, treat it as "already aligned" rather than generating
# an increase-exposure recommendation nobody asked for.
ALREADY_ALIGNED_THRESHOLD_PCT = 15.0


def check_theme_eligible(
    theme: dict[str, Any], matched_strategy: dict[str, Any] | None
) -> tuple[bool, str | None]:
    """
    Gate a theme against the deterministic eligibility conditions. Returns
    (True, None) if eligible, or (False, reason) naming the specific gate
    that failed.
    """
    conviction = theme.get("conviction_level")
    if conviction not in ELIGIBLE_CONVICTION_LEVELS:
        return False, (
            f"theme conviction is '{conviction}', requires medium or high "
            f"(convergence across independent experts, not a single voice)"
        )

    fundamentals_verdict = theme.get("fundamentals_verdict")
    if fundamentals_verdict not in ELIGIBLE_FUNDAMENTALS_VERDICTS and not fundamentals_not_applicable_but_eligible(theme):
        return False, (
            f"theme fundamentals_verdict is '{fundamentals_verdict}', "
            f"requires pass or downgrade (or an index/macro entity where "
            f"company fundamentals don't apply)"
        )

    if matched_strategy is None:
        return False, "no matched execution strategy found for this theme"

    return True, None


def check_already_aligned(theme: dict[str, Any], holdings: list[dict[str, Any]]) -> bool:
    """
    Simple percentage-of-portfolio heuristic: is the theme's entity (or
    any of its named instruments) already a large enough share of the
    portfolio's value that further increase-exposure guidance isn't
    warranted? Uses cost_basis * quantity as a value proxy — no live price
    lookup, deliberately simple for v1.
    """
    if not holdings:
        return False

    target_entities = set(theme.get("instruments_mentioned") or [])
    entity_cluster_key = theme.get("entity_cluster_key")
    if entity_cluster_key:
        target_entities.add(entity_cluster_key)

    total_value = sum(h["quantity"] * (h.get("cost_basis") or 0) for h in holdings)
    if total_value <= 0:
        return False

    target_value = sum(
        h["quantity"] * (h.get("cost_basis") or 0)
        for h in holdings
        if h.get("entity") in target_entities
    )

    return (target_value / total_value * 100) >= ALREADY_ALIGNED_THRESHOLD_PCT


def check_duplicate_guidance(theme_id: str, portfolio_id: str) -> bool:
    """True if active/executing guidance already exists for this theme+portfolio pair."""
    return bool(fetch_active_guidance(theme_id, portfolio_id))
