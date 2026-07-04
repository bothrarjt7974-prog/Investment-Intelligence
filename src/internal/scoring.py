# PROPRIETARY — do not share externally.
# Credibility scoring formula and weights are the product's moat. See
# src/internal/README.md for handling rules (no public screenshots, no
# detailed description in blog posts/marketing, confidentiality with
# external collaborators).
"""
Expert credibility scoring from validated claim outcomes.

Computes a per-horizon (short/long/structural) credibility score for each
expert from their validated claims, then an overall score weighted by each
bucket's claim count. Pure deterministic math — no LLM involved.
"""

from __future__ import annotations

from typing import Any

import pendulum
from loguru import logger

from src.db.queries import (
    fetch_active_expert_ids,
    fetch_claims_for_expert_by_status,
    update_by_id,
)

VALIDATED_STATUSES = ("correct", "partial", "incorrect")

SHORT_HORIZON_MAX_MONTHS = 12
LONG_HORIZON_MAX_MONTHS = 36

OUTCOME_VALUE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
STRENGTH_WEIGHT = {"strong": 1.0, "soft": 0.5}

RECENCY_RECENT_YEARS = 2
RECENCY_MID_YEARS = 4
RECENCY_RECENT_WEIGHT = 1.0
RECENCY_MID_WEIGHT = 0.5
RECENCY_OLD_WEIGHT = 0.25

SHRINKAGE_K = 20  # claims needed before the bucket's hit rate is trusted at ~50%
DEFAULT_SCORE = 50.0
SAMPLE_SIZE_THRESHOLD = 20


def _bucket_for(target_horizon_months: int | None) -> str:
    """Classify a claim into short/long/structural by its target horizon."""
    if target_horizon_months is None:
        return "short"
    if target_horizon_months < SHORT_HORIZON_MAX_MONTHS:
        return "short"
    if target_horizon_months <= LONG_HORIZON_MAX_MONTHS:
        return "long"
    return "structural"


def _recency_weight(claimed_at: str) -> float:
    """More recent claims count more — an expert's edge can decay or improve."""
    age_years = (pendulum.now("UTC") - pendulum.parse(claimed_at)).in_years()
    if age_years < RECENCY_RECENT_YEARS:
        return RECENCY_RECENT_WEIGHT
    if age_years < RECENCY_MID_YEARS:
        return RECENCY_MID_WEIGHT
    return RECENCY_OLD_WEIGHT


def score_bucket(claims: list[dict[str, Any]]) -> tuple[float, int]:
    """
    Score one horizon bucket from its validated claims.

    Returns (score, claim_count). An empty bucket keeps the neutral default
    score of 50 rather than being penalized for lack of data.
    """
    if not claims:
        return DEFAULT_SCORE, 0

    weighted_outcome_sum = 0.0
    weight_sum = 0.0
    for claim in claims:
        outcome_value = OUTCOME_VALUE.get(claim.get("verification_status"))
        if outcome_value is None:
            continue
        strength_weight = STRENGTH_WEIGHT.get(claim.get("strength"), 0.5)
        recency_weight = _recency_weight(claim["claimed_at"])
        weight = strength_weight * recency_weight
        weighted_outcome_sum += outcome_value * weight
        weight_sum += weight

    if weight_sum == 0:
        return DEFAULT_SCORE, len(claims)

    hit_rate = weighted_outcome_sum / weight_sum
    n = len(claims)
    shrink = n / (n + SHRINKAGE_K)
    score = DEFAULT_SCORE + (hit_rate - 0.5) * 100 * shrink
    score = max(0.0, min(100.0, score))
    return score, n


def recompute_expert_credibility(expert_id: str) -> dict[str, Any] | None:
    """
    Recompute and persist one expert's credibility scores across the three
    horizon buckets plus an overall claim-count-weighted score.
    """
    claims = fetch_claims_for_expert_by_status(expert_id, list(VALIDATED_STATUSES))

    buckets: dict[str, list[dict[str, Any]]] = {"short": [], "long": [], "structural": []}
    for claim in claims:
        buckets[_bucket_for(claim.get("target_horizon_months"))].append(claim)

    short_score, short_n = score_bucket(buckets["short"])
    long_score, long_n = score_bucket(buckets["long"])
    structural_score, structural_n = score_bucket(buckets["structural"])

    total_n = short_n + long_n + structural_n
    if total_n == 0:
        overall_score = DEFAULT_SCORE
    else:
        overall_score = (
            short_score * short_n + long_score * long_n + structural_score * structural_n
        ) / total_n

    correct_claims = sum(1 for c in claims if c.get("verification_status") == "correct")

    update_fields = {
        "credibility_short_horizon": round(short_score, 2),
        "credibility_long_horizon": round(long_score, 2),
        "credibility_structural": round(structural_score, 2),
        "credibility_score": round(overall_score, 2),
        "total_claims": len(claims),
        "validated_claims": total_n,
        "correct_claims": correct_claims,
        "sample_size_flag": total_n < SAMPLE_SIZE_THRESHOLD,
    }
    return update_by_id("experts", expert_id, update_fields)


def recompute_all_experts() -> int:
    """Recompute credibility for every expert. Called at the end of every validation batch."""
    expert_ids = fetch_active_expert_ids()
    updated = 0
    for expert_id in expert_ids:
        try:
            recompute_expert_credibility(expert_id)
            updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to recompute credibility for expert {expert_id}: {exc}")
    logger.info(f"Recomputed credibility for {updated}/{len(expert_ids)} experts")
    return updated
