"""
Deterministic sizing gate for guidance generation (Layer 9).

The guidance prompt instructs Opus to trace every percentage to the
matched strategy's allocation_rule — but many legitimate strategies
(entry methodologies like DCA, risk rules, exit disciplines) carry no
allocation percentage at all. When that happens the model has nothing to
trace to, and "never let the LLM invent position sizes" (a hard product
rule) is unenforceable by prompt alone. This module is the code-side
enforcement: percentages are either validated against the strategy's own
rule or clamped to a risk-profile cap, and instruments the model
introduced out of thin air reject the whole response.

Cap table values are founder-reviewed policy (see decision log,
2026-07-08) — conservative first-position sizing for a long-horizon
retail portfolio, NOT an optimization output.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from src.config.tuning import param

# Max size for a single theme position (to_pct) and for any single
# instrument within it, as % of total portfolio, by user risk profile.
RISK_PROFILE_CAPS: dict[str, dict[str, float]] = {
    "conservative": {
        "max_position_pct": param("sizing.conservative_max_position_pct"),
        "max_instrument_pct": param("sizing.conservative_max_instrument_pct"),
    },
    "moderate": {
        "max_position_pct": param("sizing.moderate_max_position_pct"),
        "max_instrument_pct": param("sizing.moderate_max_instrument_pct"),
    },
    "aggressive": {
        "max_position_pct": param("sizing.aggressive_max_position_pct"),
        "max_instrument_pct": param("sizing.aggressive_max_instrument_pct"),
    },
}

# A strategy allocation_rule may state its percentage many ways
# ("40%", "allocate 25 % to gold", "60/40"). We only trust an explicit
# percent sign — a bare "60/40" ratio is ambiguous about which side
# applies to this theme, so it falls through to the risk cap.
_PCT_PATTERN = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


class InventedInstrumentError(ValueError):
    """The LLM introduced an instrument not present in any grounding input."""


def extract_allocation_pct(allocation_rule: str | None) -> float | None:
    """First explicit percentage in the strategy's allocation rule, if any."""
    if not allocation_rule:
        return None
    match = _PCT_PATTERN.search(allocation_rule)
    return float(match.group(1)) if match else None


def _allowed_entities(
    theme: dict[str, Any],
    strategy: dict[str, Any],
    holdings: list[dict[str, Any]] | None = None,
) -> set[str]:
    """
    Every entity the LLM is allowed to name: the theme's own cluster key
    and mentioned instruments, the strategy's entity_class, and anything
    the user actually holds — recommending action on an existing holding
    (trim/exit/funding source) is never invention, even when the theme
    didn't name that instrument. Matching is case-insensitive; instrument
    strings like "us_tech_basket (AAPL, NVDA)" from the model are checked
    token-wise against this set.
    """
    allowed = {str(theme.get("entity_cluster_key") or "").lower()}
    for instrument in theme.get("instruments_mentioned") or []:
        allowed.add(str(instrument).lower())
    if strategy.get("entity_class"):
        allowed.add(str(strategy["entity_class"]).lower())
    for holding in holdings or []:
        allowed.add(str(holding.get("entity") or "").lower())
    # The strategy's own prose prescribes asset classes ("40% to
    # long-term government bonds", "60% equities") — an instrument that
    # follows the strategy's stated allocation is grounded BY that
    # strategy, not invented. Tokenize its text fields; keep only
    # meaningful words (4+ chars) so generic fragments can't accidentally
    # ground a truly invented ticker.
    for field in ("strategy_text", "allocation_rule", "position_sizing", "exit_conditions"):
        value = strategy.get(field)
        if value:
            allowed |= {t for t in re.split(r"[^a-z0-9&-]+", str(value).lower()) if len(t) >= 4}
    allowed.discard("")
    return allowed


def _entity_is_grounded(entity: str, allowed: set[str]) -> bool:
    """
    True if the model's entity string is traceable to the allowed set.
    Accepts exact match or any allowed entity appearing as a token inside
    a composite label (e.g. "us_tech_basket (AAPL, NVDA)" is grounded if
    "aapl" or "us_tech" is allowed).
    """
    lowered = entity.lower()
    if lowered in allowed:
        return True
    # Two tokenizations: keeping underscores (matches canonical entity
    # names like us_tech_ai in the allowed set) and splitting them
    # (matches strategy-prose words — 'long_term_govt_bonds' must ground
    # against a strategy that wrote "long-term government bonds").
    tokens = (
        set(re.split(r"[^a-z0-9_&-]+", lowered))
        | set(re.split(r"[^a-z0-9&-]+", lowered))
    ) - {""}
    return bool(tokens & allowed)


def apply_sizing_gate(
    payload: dict[str, Any],
    theme: dict[str, Any],
    strategy: dict[str, Any],
    risk_profile: str,
    holdings: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Enforce sizing traceability on a parsed guidance response, in place.

    Returns (payload, sizing_source) where sizing_source is one of:
    - "strategy_allocation_rule" — to_pct validated/clamped against the
      strategy's own explicit percentage
    - "risk_profile_cap" — strategy stated no percentage; caps applied
      from RISK_PROFILE_CAPS
    - "reduction_not_capped" — guidance reduces/exits exposure; caps only
      constrain NEW exposure, never how much a user may sell

    Raises InventedInstrumentError if any specific instrument cannot be
    traced to the theme/strategy inputs — the whole response is rejected
    rather than persisting invented content.
    """
    caps = RISK_PROFILE_CAPS.get(risk_profile, RISK_PROFILE_CAPS["moderate"])

    # --- Instrument grounding: reject, never repair ---
    allowed = _allowed_entities(theme, strategy, holdings)
    for instrument in payload.get("specific_instruments") or []:
        entity = str(instrument.get("entity") or "")
        if not _entity_is_grounded(entity, allowed):
            raise InventedInstrumentError(
                f"Instrument {entity!r} is not traceable to the theme's "
                f"instruments, entity cluster, or strategy entity_class "
                f"({sorted(allowed)!r})"
            )

    change = payload.get("target_allocation_change") or {}
    to_pct = change.get("to_pct")
    from_pct = change.get("from_pct") or 0.0

    # Reductions/exits: never clamp how much someone sells.
    if to_pct is None or to_pct <= from_pct:
        return payload, "reduction_not_capped"

    rule_pct = extract_allocation_pct(strategy.get("allocation_rule"))
    if rule_pct is not None:
        cap, sizing_source = rule_pct, "strategy_allocation_rule"
    else:
        cap, sizing_source = caps["max_position_pct"], "risk_profile_cap"

    if to_pct > cap:
        logger.info(
            f"Sizing gate: clamping to_pct {to_pct} -> {cap} ({sizing_source})"
        )
        scale = cap / to_pct
        change["to_pct"] = cap
        for instrument in payload.get("specific_instruments") or []:
            if instrument.get("target_pct") is not None:
                instrument["target_pct"] = round(instrument["target_pct"] * scale, 2)

    # Per-instrument cap applies regardless of which rule sized the total.
    for instrument in payload.get("specific_instruments") or []:
        target = instrument.get("target_pct")
        if target is not None and target > caps["max_instrument_pct"]:
            logger.info(
                f"Sizing gate: clamping instrument {instrument.get('entity')!r} "
                f"{target} -> {caps['max_instrument_pct']}"
            )
            instrument["target_pct"] = caps["max_instrument_pct"]

    return payload, sizing_source
