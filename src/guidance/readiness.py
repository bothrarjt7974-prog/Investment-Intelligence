"""
"Is there enough behind this to act on?" — per-theme readiness assessment
(Layer 8/9, display-only).

Answers a plain question the founder asked reading the app as a general
user would: when we say a theme looks good, do we actually have a
concrete way to invest in it, or is it a thesis with nothing specific
behind it yet? Every input already exists — expert agreement and
fundamentals verdict on the theme row, the top strategy match and its
entity_class/backtest_status. This module only assembles and labels
what's already there; it computes nothing new and never invents a
missing signal (2026-07-13).
"""

from __future__ import annotations

from typing import Any, Literal

Level = Literal["good", "fair", "poor"]

BROAD_EQUITY_ENTITY_CLASS = "broad_equity"


def _expert_agreement_level(theme: dict[str, Any]) -> tuple[Level, str]:
    supporting = theme.get("supporting_experts") or []
    count = len(supporting)
    credibility = theme.get("credibility_weighted_score")
    if count >= 3 and (credibility is None or credibility >= 55):
        return "good", f"{count} experts, reasoning independently, reached this view"
    if count >= 2:
        return "fair", f"{count} experts agree, but the signal is still thin"
    return "poor", "fewer than 2 independent voices support this"


def _fundamentals_level(theme: dict[str, Any]) -> tuple[Level, str]:
    verdict = theme.get("fundamentals_verdict")
    if verdict == "pass":
        return "good", "financials checked out"
    if verdict == "downgrade":
        return "fair", "financials checked, but valuation looks stretched"
    if verdict == "not_applicable":
        return "fair", "this is an index/currency/commodity — no company financials to check"
    if verdict == "reject":
        return "poor", "financials raised a red flag"
    return "poor", "financials haven't been checked yet"


def _strategy_level(top_match: dict[str, Any] | None, strategy: dict[str, Any] | None) -> tuple[Level, str]:
    if top_match is None or strategy is None:
        return "poor", "no investing strategy has been matched to this idea yet"
    # broad_equity is always the matcher's generic catch-all fallback class
    # (src/strategies/matcher.py) — a strategy tagged with it was never
    # built for this specific idea, just the closest available guess.
    if strategy.get("entity_class") == BROAD_EQUITY_ENTITY_CLASS:
        return "fair", "matched to a general investing approach, not one built for this specific idea"
    return "good", "matched to a strategy built for this kind of idea"


def _track_record_level(strategy: dict[str, Any] | None) -> tuple[Level, str]:
    if strategy is None:
        return "poor", "no strategy to check a track record against"
    status = strategy.get("backtest_status")
    if status == "complete":
        return "good", "this approach has been checked against real market history"
    if status == "insufficient_history":
        return "fair", "not enough history exists yet to test this approach"
    return "fair", "history check hasn't run yet"


def _named_stocks_level(strategy: dict[str, Any] | None, theme: dict[str, Any]) -> tuple[Level, str]:
    instruments = theme.get("instruments_mentioned") or []
    if instruments:
        return "good", f"names specific instruments ({', '.join(instruments[:3])}{'…' if len(instruments) > 3 else ''})"
    if strategy is None:
        return "poor", "no specific stock, fund, or instrument identified"
    return "fair", "this is a market-wide idea (like Nifty 50), not a single stock pick"


_LEVEL_WEIGHT = {"good": 2, "fair": 1, "poor": 0}
_OVERALL_LABEL = {
    2: ("good", "Ready for a specific recommendation"),
    1: ("fair", "Directionally useful, but we don't have a specific way to act on it yet"),
    0: ("poor", "Just a thesis so far — not enough behind it to recommend anything specific"),
}


def assess_theme_readiness(
    theme: dict[str, Any],
    top_match: dict[str, Any] | None,
    strategy: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Build the readiness checklist + overall label for one theme.

    Args:
        theme: a themes row (supporting_experts, credibility_weighted_score,
            fundamentals_verdict, instruments_mentioned).
        top_match: the rank=1 theme_strategy_matches row for this theme, or
            None if the matcher found no candidates.
        strategy: the strategies row top_match points to (entity_class,
            backtest_status), or None alongside top_match=None.

    Returns:
        {"overall": {"level": "good"|"fair"|"poor", "label": str},
         "checklist": [{"label": str, "level": ..., "detail": str}, ...]}
    """
    checks = [
        ("Expert agreement", _expert_agreement_level(theme)),
        ("Financials checked", _fundamentals_level(theme)),
        ("A real way to invest exists", _strategy_level(top_match, strategy)),
        ("Real-world track record", _track_record_level(strategy)),
        ("Named stocks/funds available", _named_stocks_level(strategy, theme)),
    ]
    checklist = [{"label": label, "level": level, "detail": detail} for label, (level, detail) in checks]

    # Overall = the weakest link on the two checks that actually gate a
    # concrete recommendation (a real strategy + something to name) —
    # strong expert agreement alone was never the bottleneck this session's
    # audits found; the strategy library was.
    gating_levels = [checklist[2]["level"], checklist[4]["level"]]
    worst = min(_LEVEL_WEIGHT[lvl] for lvl in gating_levels)
    overall_level, overall_label = _OVERALL_LABEL[worst]

    return {"overall": {"level": overall_level, "label": overall_label}, "checklist": checklist}
