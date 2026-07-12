"""
Concrete rupee/share translation of already-decided guidance percentages
(Layer 9, display-only).

Pure computation on numbers that already exist — target_allocation_change
and specific_instruments already carry percentages; this module never
invents a position, a target, or an instrument. It only answers "what does
this percentage mean in rupees, and how many shares is that" using the
same portfolio-value convention already shown elsewhere in the app
(cost-basis sum) and real, already-stored market prices — never a forward
projection (2026-07-12 finding: founder asked for direct, concrete action,
not just percentages).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.validation.entity_symbol_map import resolve_entity
from src.validation.market_data import price_on

ILLUSTRATIVE_PORTFOLIO_VALUE = 100_000.0


def _rupee_amount(pct: float | None, portfolio_value: float) -> float | None:
    if pct is None:
        return None
    return round(pct / 100 * portfolio_value, 2)


def _share_estimate(entity: str, rupee_amount: float, geography: str | None) -> dict[str, Any] | None:
    """
    Approximate share count for a real, resolvable instrument — omitted
    entirely (never fabricated) when the entity doesn't resolve to a
    tradeable ticker or no recent price exists. Index/sector/macro entities
    (e.g. 'nifty50', 'indian_banking') never resolve here by design — they
    aren't directly purchasable, so no share count should be implied.
    """
    resolved = resolve_entity(entity, geography)
    if resolved is None:
        return None
    fetch_symbol, source = resolved
    if source == "manual":
        return None  # macro/rate series — not a tradeable instrument

    price = price_on(fetch_symbol, date.today(), max_lookback_days=30)
    if price is None or price <= 0:
        return None
    return {"symbol": fetch_symbol, "price": price, "approx_shares": round(rupee_amount / price, 2)}


def compute_action_amounts(
    guidance: dict[str, Any], portfolio_value: float, geography: str | None = None
) -> dict[str, Any]:
    """
    Translate a guidance row's percentages into concrete amounts.

    Returns:
        {
          "theme_rupee_change": float | None,     # (to_pct - from_pct)/100 * portfolio_value
          "instruments": [                         # one entry per specific_instruments row
            {"entity": str, "target_pct": float, "rupee_amount": float,
             "share_estimate": {...} | None},
            ...
          ],
          "illustrative": {                        # same math on a round 1,00,000 example
            "theme_rupee_change": float | None,
            "instruments": [...],
          },
        }
    """
    change = guidance.get("target_allocation_change") or {}
    from_pct, to_pct = change.get("from_pct"), change.get("to_pct")
    theme_pct_delta = None
    if from_pct is not None and to_pct is not None:
        theme_pct_delta = to_pct - from_pct

    instruments_raw = guidance.get("specific_instruments") or []

    def build_instruments(value: float) -> list[dict[str, Any]]:
        out = []
        for inst in instruments_raw:
            entity = inst.get("entity")
            pct = inst.get("target_pct")
            if not entity or pct is None:
                continue
            rupee = _rupee_amount(pct, value)
            out.append({
                "entity": entity,
                "target_pct": pct,
                "rupee_amount": rupee,
                "share_estimate": _share_estimate(entity, rupee, geography) if rupee else None,
            })
        return out

    return {
        "theme_rupee_change": _rupee_amount(theme_pct_delta, portfolio_value) if theme_pct_delta is not None else None,
        "instruments": build_instruments(portfolio_value),
        "illustrative": {
            "theme_rupee_change": _rupee_amount(theme_pct_delta, ILLUSTRATIVE_PORTFOLIO_VALUE) if theme_pct_delta is not None else None,
            "instruments": build_instruments(ILLUSTRATIVE_PORTFOLIO_VALUE),
        },
    }
