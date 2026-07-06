# PROPRIETARY — do not share externally.
# Convergence detection scoring rules are the product's moat. See
# src/internal/README.md for handling rules (no public screenshots, no
# detailed description in blog posts/marketing, confidentiality with
# external collaborators).
"""
Theme convergence/credibility weighting (Layer 6: structural theme
synthesis).

The LLM (prompts/theme_synthesis_v1.md, Opus) classifies each claim in a
cluster as independent/correlated/echo relative to the rest of the
cluster. This module turns those labels plus each contributing expert's
already-computed credibility (src/internal/scoring.py) into the theme's
independence_score, credibility_weighted_score, and conviction_level. Pure
deterministic math — no LLM involved, mirrors scoring.py's isolation.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

from src.internal.scoring import DEFAULT_SCORE, LONG_HORIZON_MAX_MONTHS

VALID_INDEPENDENCE_LABELS = ("independent", "correlated", "echo")

# Per-claim weight by independence label. Echo is handled separately below
# (all echo-labeled claims in a cluster collapse into ONE signal, not one
# per echoing expert or claim) — see technical_execution_plan_v3.docx §9:
# "independent reasoning 1.0x per expert, correlated 0.5x, echo counts as
# one signal total."
INDEPENDENCE_WEIGHT = {"independent": 1.0, "correlated": 0.5}
ECHO_GROUP_WEIGHT = 1.0

# Conviction thresholds — v1 placeholders, same "documented for calibration"
# status as every other threshold in this codebase (quality filter pass
# rates, drawdown thresholds). A single-expert cluster is always LOW
# regardless of that expert's credibility (Principle 2: aggressive
# rejection over aggressive recommendation) — one voice is not convergence.
HIGH_MIN_SIGNAL = 3.0
HIGH_MIN_CREDIBILITY = 65.0
MEDIUM_MIN_SIGNAL = 2.0
MEDIUM_MIN_CREDIBILITY = 50.0

_LABEL_PRIORITY = {"independent": 2, "correlated": 1, "echo": 0}


def _credibility_bucket(target_horizon_months: int | None) -> str:
    """
    Which of an expert's credibility scores applies to this claim.

    Theme synthesis only ever sees claims with target_horizon_months >= 12
    (enforced by fetch_synthesizable_claims) — the "short" bucket from
    src/internal/scoring.py is unreachable here by construction, so this
    only distinguishes long vs structural.
    """
    if target_horizon_months is None or target_horizon_months <= LONG_HORIZON_MAX_MONTHS:
        return "long"
    return "structural"


def _relevant_credibility(claim: dict[str, Any], expert_credibility: dict[str, dict[str, Any]]) -> float:
    expert = expert_credibility.get(claim.get("expert_id"), {})
    bucket = _credibility_bucket(claim.get("target_horizon_months"))
    key = "credibility_structural" if bucket == "structural" else "credibility_long_horizon"
    return float(expert.get(key) if expert.get(key) is not None else DEFAULT_SCORE)


def derive_conviction_level(
    effective_signal_count: float, credibility_weighted_score: float, raw_expert_count: int
) -> str:
    """
    Deterministic HIGH/MEDIUM/LOW rubric from the convergence math.

    A single contributing expert can never reach beyond LOW, no matter how
    credible — conviction requires actual convergence across voices.
    """
    if raw_expert_count <= 1:
        return "low"
    if effective_signal_count >= HIGH_MIN_SIGNAL and credibility_weighted_score >= HIGH_MIN_CREDIBILITY:
        return "high"
    if effective_signal_count >= MEDIUM_MIN_SIGNAL and credibility_weighted_score >= MEDIUM_MIN_CREDIBILITY:
        return "medium"
    return "low"


def aggregate_cluster(
    claims: list[dict[str, Any]],
    independence_labels: dict[str, str],
    expert_credibility: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Combine a cluster's claims, their LLM-assigned independence labels, and
    each contributing expert's credibility into theme-level scoring.

    Args:
        claims: cluster's claim rows (must have id, expert_id, target_horizon_months)
        independence_labels: claim_id -> 'independent'|'correlated'|'echo'
        expert_credibility: expert_id -> expert row (name, credibility_long_horizon,
            credibility_structural)

    Returns: dict with independence_score, credibility_weighted_score,
    conviction_level, supporting_experts (attribution list for the theme row).
    """
    weighted_entries: list[tuple[float, float]] = []  # (weight, credibility)
    echo_credibilities: list[float] = []
    per_expert: dict[str, dict[str, Any]] = {}

    for claim in claims:
        claim_id = claim["id"]
        expert_id = claim.get("expert_id")
        label = independence_labels.get(claim_id, "independent")
        if label not in VALID_INDEPENDENCE_LABELS:
            label = "correlated"  # unknown label from the LLM: trust cautiously, don't discard
        credibility = _relevant_credibility(claim, expert_credibility)

        if label == "echo":
            echo_credibilities.append(credibility)
        else:
            weighted_entries.append((INDEPENDENCE_WEIGHT[label], credibility))

        expert = per_expert.setdefault(
            expert_id,
            {
                "expert_id": expert_id,
                "expert_name": expert_credibility.get(expert_id, {}).get("name", "unknown"),
                "labels": [],
                "credibilities": [],
                "claim_ids": [],
            },
        )
        expert["labels"].append(label)
        expert["credibilities"].append(credibility)
        expert["claim_ids"].append(claim_id)

    effective_signal_count = sum(w for w, _ in weighted_entries)
    weighted_credibility_sum = sum(w * c for w, c in weighted_entries)
    weight_sum = sum(w for w, _ in weighted_entries)

    if echo_credibilities:
        echo_avg_credibility = mean(echo_credibilities)
        effective_signal_count += ECHO_GROUP_WEIGHT
        weighted_credibility_sum += ECHO_GROUP_WEIGHT * echo_avg_credibility
        weight_sum += ECHO_GROUP_WEIGHT

    raw_expert_count = len(per_expert)
    independence_score = effective_signal_count / raw_expert_count if raw_expert_count else 0.0
    credibility_weighted_score = (
        weighted_credibility_sum / weight_sum if weight_sum else DEFAULT_SCORE
    )
    conviction_level = derive_conviction_level(
        effective_signal_count, credibility_weighted_score, raw_expert_count
    )

    supporting_experts = [
        {
            "expert_id": expert_id,
            "expert_name": data["expert_name"],
            # Most-favorable label represents the expert's strongest claim
            # in this cluster when they contributed more than one.
            "independence_label": max(data["labels"], key=lambda lbl: _LABEL_PRIORITY[lbl]),
            "credibility_used": round(mean(data["credibilities"]), 2),
            "claim_ids": data["claim_ids"],
        }
        for expert_id, data in per_expert.items()
    ]

    return {
        "independence_score": round(independence_score, 3),
        "credibility_weighted_score": round(credibility_weighted_score, 2),
        "conviction_level": conviction_level,
        "supporting_experts": supporting_experts,
    }
