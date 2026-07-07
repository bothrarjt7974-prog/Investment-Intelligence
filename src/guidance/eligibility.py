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
    if fundamentals_verdict not in ELIGIBLE_FUNDAMENTALS_VERDICTS:
        return False, (
            f"theme fundamentals_verdict is '{fundamentals_verdict}', "
            f"requires pass or downgrade"
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
