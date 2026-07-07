"""
Theme -> strategy matcher (Layer 8: strategy library, the matcher half).

Structured filter and semantic rank fuse into one Chroma query: every
strategy is embedded (src/strategies/embeddings.py) with filterable
metadata attached, so `collection.query(..., where={...})` does the
"reduce 1000 strategies to ~20 candidates by entity_class/scope/quality"
step and the cosine-similarity ranking in a single call — see
embeddings.py's docstring for why this differs from the plan's literal
two-stage description.

Credibility/backtest weighting on top of the semantic candidates is
retrieval-fusion plumbing, not the same kind of proprietary scoring
judgment as src/internal/ houses (credibility formula, convergence
detection) — kept here rather than there; see 2026-07-07 decision log.
"""

from __future__ import annotations

import argparse
from typing import Any

from loguru import logger

from src.db.queries import (
    delete_theme_strategy_matches,
    fetch_by_id,
    fetch_themes_ready_for_matching,
    insert_many,
)
from src.internal.scoring import DEFAULT_SCORE, LONG_HORIZON_MAX_MONTHS
from src.strategies.embeddings import get_chroma_collection, get_embedding
from src.validation.entity_symbol_map import UNMAPPABLE

CANDIDATE_POOL_SIZE = 20

# strategy_extraction_v2's catch-all entity_class for general (not
# sector-specific) allocation strategies. A real finding from the first
# calibration run (2026-07-07 decision log): 5 of 6 extracted strategies
# used it, and it never exact-matches any theme's entity_cluster_key, so
# the matcher returned 0 candidates for every one of them. A general
# strategy is a reasonable fallback candidate for an equity/sector theme
# (semantic similarity + weighting still ranks it appropriately) — but
# meaningless for a macro/commodity/FX/crypto theme (there's no "broad
# equity way to invest" in response to a currency or rate view).
BROAD_EQUITY_ENTITY_CLASS = "broad_equity"

# Non-equity entity classes already known in ENTITY_SYMBOL_MAP — explicit,
# curated exclusion list (same curation spirit as SECTOR_PEER_TICKERS in
# src/fundamentals/data.py) rather than an inferred heuristic.
_NON_EQUITY_ENTITY_CLASSES = frozenset(
    {
        "BTC", "ETH", "SOL",
        "inr_usd", "jpy_usd", "dxy", "eur_usd",
        "gold", "silver", "crude_oil",
        "rbi_rate", "fed_rate", "india_inflation", "us_inflation", "india_gdp", "us_gdp",
    }
    | UNMAPPABLE
)


def _entity_class_candidates(entity_cluster_key: str) -> list[str]:
    """Which entity_class values are acceptable matches for this theme."""
    if entity_cluster_key in _NON_EQUITY_ENTITY_CLASSES:
        return [entity_cluster_key]
    return list({entity_cluster_key, BROAD_EQUITY_ENTITY_CLASS})

# CFA/institutional strategies have no expert to derive credibility from —
# the plan says these "carry high default weight" by construction (they're
# pre-vetted, canonical methodology, not a claim needing validation).
CFA_DEFAULT_CREDIBILITY_WEIGHT = 75.0

# Strategies with no complete backtest record get a neutral (not
# penalized, not rewarded) weight — absence of a backtest isn't evidence
# against the strategy.
NEUTRAL_BACKTEST_WEIGHT = 50.0

# v1 placeholder weights (documented for calibration, same status as every
# other threshold this session) — must sum to 1.0.
SEMANTIC_WEIGHT = 0.5
CREDIBILITY_WEIGHT = 0.3
BACKTEST_WEIGHT = 0.2


def _credibility_weight_for_strategy(strategy: dict[str, Any]) -> float:
    """
    Expert's relevant-bucket credibility (same long/structural bucket
    logic as src/internal/theme_scoring.py), or a fixed default for
    CFA/institutional strategies which have no expert_id.
    """
    expert_id = strategy.get("expert_id")
    if not expert_id:
        return CFA_DEFAULT_CREDIBILITY_WEIGHT

    expert = fetch_by_id("experts", expert_id)
    if not expert:
        return DEFAULT_SCORE

    horizon = strategy.get("holding_horizon_months")
    is_structural = horizon is not None and horizon > LONG_HORIZON_MAX_MONTHS
    key = "credibility_structural" if is_structural else "credibility_long_horizon"
    value = expert.get(key)
    return float(value) if value is not None else DEFAULT_SCORE


def _backtest_weight_for_strategy(strategy: dict[str, Any]) -> float:
    """Backtest-record-derived weight, neutral when no complete backtest exists."""
    if strategy.get("backtest_status") != "complete":
        return NEUTRAL_BACKTEST_WEIGHT

    record = strategy.get("backtest_record") or {}
    outperformance = record.get("outperformance_pct")
    if outperformance is None:
        return NEUTRAL_BACKTEST_WEIGHT

    return max(0.0, min(100.0, 50.0 + outperformance))


def match_theme_to_strategies(theme: dict[str, Any], top_n: int = 5) -> list[dict[str, Any]]:
    """
    Find and persist the top-N candidate strategies for one theme.
    Replaces any prior matches for this theme (see module docstring —
    theme_strategy_matches is a recomputed ranking cache, not an
    append-only log).
    """
    query_embedding = get_embedding(theme["summary_thesis"])
    collection = get_chroma_collection()

    where = {
        "$and": [
            {"entity_class": {"$in": _entity_class_candidates(theme["entity_cluster_key"])}},
            {"scope": "in_scope"},
            {"quality_verdict": {"$in": ["pass", "partial"]}},
        ]
    }
    results = collection.query(
        query_embeddings=[query_embedding], n_results=CANDIDATE_POOL_SIZE, where=where
    )
    candidate_ids = results["ids"][0] if results.get("ids") else []
    distances = results["distances"][0] if results.get("distances") else []

    scored: list[dict[str, Any]] = []
    for strategy_id, distance in zip(candidate_ids, distances, strict=True):
        strategy = fetch_by_id("strategies", strategy_id)
        if strategy is None:
            continue  # deleted since being embedded — skip, don't crash

        semantic_similarity = 1.0 - distance
        credibility_weight = _credibility_weight_for_strategy(strategy)
        backtest_weight = _backtest_weight_for_strategy(strategy)
        final_score = (
            SEMANTIC_WEIGHT * (semantic_similarity * 100)
            + CREDIBILITY_WEIGHT * credibility_weight
            + BACKTEST_WEIGHT * backtest_weight
        )
        scored.append(
            {
                "theme_id": theme["id"],
                "strategy_id": strategy_id,
                "semantic_similarity": round(semantic_similarity, 4),
                "credibility_weight": round(credibility_weight, 2),
                "backtest_weight": round(backtest_weight, 2),
                "final_score": round(final_score, 4),
            }
        )

    scored.sort(key=lambda match: match["final_score"], reverse=True)
    top_matches = scored[:top_n]
    for rank, match in enumerate(top_matches, start=1):
        match["rank"] = rank

    delete_theme_strategy_matches(theme["id"])
    if top_matches:
        insert_many("theme_strategy_matches", top_matches)

    return top_matches


def run_matching(max_themes: int | None = None) -> dict[str, int]:
    """Run the matcher for every theme ready for it (fundamentals-passed/downgraded)."""
    themes = fetch_themes_ready_for_matching(limit=max_themes or 100)
    logger.info(f"Found {len(themes)} themes ready for matching")

    counts = {"matched": 0, "no_candidates": 0, "error": 0, "total": len(themes)}

    for theme in themes:
        theme_id = theme.get("id")
        try:
            matches = match_theme_to_strategies(theme)
            if matches:
                counts["matched"] += 1
            else:
                counts["no_candidates"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to match theme {theme_id}: {exc}")
            counts["error"] += 1

    logger.info(
        f"Matching complete: {counts['matched']} matched, "
        f"{counts['no_candidates']} no_candidates, {counts['error']} errors "
        f"(of {counts['total']})"
    )
    return counts


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for theme-strategy matching."""
    parser = argparse.ArgumentParser(description="Match fundamentals-passed themes to library strategies")
    parser.add_argument(
        "--max-themes", type=int, default=None, help="Limit how many themes to match for a test run"
    )
    args = parser.parse_args(argv)

    counts = run_matching(max_themes=args.max_themes)
    print(
        f"Matched {counts['total']} themes: {counts['matched']} matched, "
        f"{counts['no_candidates']} no_candidates, {counts['error']} errors"
    )


if __name__ == "__main__":
    main()
