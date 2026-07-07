"""
Historical pattern detection (Layer 8: strategy library, source #2).

Derived from clustering one expert's own quality-passed strategies for
repeated methodology across content pieces — "every time Akshat discusses
gold, he suggests 5-10 percent via ETF" (technical_execution_plan_v3.docx
§11). Reuses the exact similarity math src/extraction/normalization.py
already uses to dedup near-duplicate strategies *within* one video
(`strategy_similarity`, `pick_best_strategy`) — this is the same
"restating the same methodology" signal, just clustered across time
instead of within one content piece.

No LLM calls — pure deterministic clustering, same principle as every
other code-does-mechanics module in this codebase.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.db.queries import (
    fetch_active_expert_ids,
    fetch_strategies_by_expert,
    fetch_where,
    insert_many,
)
from src.extraction.normalization import (
    STRATEGY_SIMILARITY_THRESHOLD,
    Strategy,
    pick_best_strategy,
    strategy_similarity,
)

# A repeated methodology needs to show up across at least this many
# distinct content pieces to count as a real pattern, not a within-video
# restatement (already deduped by normalize_strategies) or a coincidence.
MIN_PATTERN_REPETITIONS = 3


def _to_strategy_obj(row: dict[str, Any]) -> Strategy:
    """Convert a strategies-table row into the Strategy dataclass so the
    existing similarity/best-pick logic can operate on it directly. `raw`
    retains the full row for attribution (id, content_piece_id)."""
    return Strategy(
        strategy_text=row["strategy_text"],
        strategy_type=row["strategy_type"],
        trigger_conditions=row.get("trigger_conditions"),
        entity_class=row.get("entity_class"),
        allocation_rule=row.get("allocation_rule"),
        position_sizing=row.get("position_sizing"),
        entry_methodology=row.get("entry_methodology"),
        holding_horizon_months=row.get("holding_horizon_months"),
        exit_conditions=row.get("exit_conditions"),
        risk_framework=row.get("risk_framework"),
        reason_given=row.get("reason_given") or "",
        raw=row,
    )


def _cluster_by_similarity(strategies: list[Strategy]) -> list[list[Strategy]]:
    """
    Group strategies whose pairwise similarity clears the threshold. Same
    clustering shape as normalization.deduplicate_strategies_within_video,
    but returns every cluster (not just the best pick per cluster) — here
    we want to know how many distinct pieces repeat a methodology, not
    collapse them into one row.
    """
    clusters: list[list[Strategy]] = []
    assigned: set[int] = set()

    for i, strategy in enumerate(strategies):
        if i in assigned:
            continue
        cluster = [strategy]
        assigned.add(i)
        for j in range(i + 1, len(strategies)):
            if j in assigned:
                continue
            if strategy_similarity(strategy, strategies[j]) >= STRATEGY_SIMILARITY_THRESHOLD:
                cluster.append(strategies[j])
                assigned.add(j)
        clusters.append(cluster)

    return clusters


def detect_patterns_for_expert(expert_id: str) -> list[dict[str, Any]]:
    """
    Detect candidate historical-pattern strategy rows for one expert.
    Returns dicts ready for insert_many("strategies", ...) — does not
    insert or check for already-promoted duplicates itself (see
    run_historical_pattern_detection).
    """
    rows = fetch_strategies_by_expert(expert_id)
    objects = [_to_strategy_obj(row) for row in rows]

    groups: dict[tuple[str | None, str], list[Strategy]] = {}
    for obj in objects:
        groups.setdefault((obj.entity_class, obj.strategy_type), []).append(obj)

    candidates: list[dict[str, Any]] = []
    for (entity_class, strategy_type), group in groups.items():
        if len(group) < MIN_PATTERN_REPETITIONS:
            continue

        for cluster in _cluster_by_similarity(group):
            content_piece_ids = {s.raw["content_piece_id"] for s in cluster}
            if len(content_piece_ids) < MIN_PATTERN_REPETITIONS:
                continue  # repeated within the same handful of pieces isn't a pattern

            best = pick_best_strategy(cluster)
            source_strategy_ids = sorted(s.raw["id"] for s in cluster)
            # Inherit the weakest source verdict, not a blanket 'pass' — a
            # pattern built partly from 'partial' source strategies isn't
            # more trustworthy than its shakiest input (2026-07-07 review).
            source_verdicts = {s.raw.get("quality_verdict") for s in cluster}
            inherited_verdict = "partial" if "partial" in source_verdicts else "pass"

            candidates.append(
                {
                    "source_type": "historical_pattern",
                    "expert_id": expert_id,
                    "content_piece_id": None,
                    "source_strategy_ids": source_strategy_ids,
                    "strategy_text": best.strategy_text,
                    "strategy_type": strategy_type,
                    "trigger_conditions": best.trigger_conditions,
                    "entity_class": entity_class,
                    "allocation_rule": best.allocation_rule,
                    "position_sizing": best.position_sizing,
                    "entry_methodology": best.entry_methodology,
                    "holding_horizon_months": best.holding_horizon_months,
                    "exit_conditions": best.exit_conditions,
                    "risk_framework": best.risk_framework,
                    "reason_given": (
                        f"Repeated pattern across {len(content_piece_ids)} pieces: "
                        f"{best.reason_given}"
                    ),
                    "quality_verdict": inherited_verdict,
                    "scope": "in_scope",
                }
            )

    return candidates


def run_historical_pattern_detection(max_experts: int | None = None) -> dict[str, int]:
    """Detect and persist historical patterns for every active expert."""
    expert_ids = fetch_active_expert_ids()
    if max_experts is not None:
        expert_ids = expert_ids[:max_experts]

    counts = {"patterns_created": 0, "experts_processed": 0, "error": 0, "total": len(expert_ids)}

    for expert_id in expert_ids:
        try:
            candidates = detect_patterns_for_expert(expert_id)

            existing = fetch_where(
                "strategies", {"expert_id": expert_id, "source_type": "historical_pattern"}
            )
            already_promoted = {
                frozenset(row.get("source_strategy_ids") or []) for row in existing
            }
            new_rows = [
                c for c in candidates if frozenset(c["source_strategy_ids"]) not in already_promoted
            ]

            if new_rows:
                insert_many("strategies", new_rows)
                counts["patterns_created"] += len(new_rows)
            counts["experts_processed"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to detect historical patterns for expert {expert_id}: {exc}")
            counts["error"] += 1

    logger.info(
        f"Historical pattern detection complete: {counts['patterns_created']} patterns created "
        f"across {counts['experts_processed']} experts, {counts['error']} errors"
    )
    return counts


def main() -> None:
    """Command-line entry point for historical pattern detection."""
    counts = run_historical_pattern_detection()
    print(
        f"Processed {counts['total']} experts: {counts['patterns_created']} patterns created, "
        f"{counts['error']} errors"
    )


if __name__ == "__main__":
    main()
