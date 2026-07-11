"""Quality screening pipeline for unprocessed strategies (Filter B).

Mirrors src/quality/screener.py's structure exactly, scoring strategies on
specificity/completeness dimensions instead of claims' reasoning-quality
dimensions. Sends each strategy through the quality_filter_strategy_v1
prompt and records:
  - quality_verdict (pass | partial | fail)
  - quality_scores (JSON breakdown across 5 dimensions)
  - quality_filtered_at (timestamp)

Failed strategies are NOT deleted; they are tagged. Downstream layers
(the strategy library, Layer 7) filter by quality_verdict IN
('pass', 'partial') — 'fail' is the only excluded verdict, same convention
as claims (see 2026-07-04 decision log).

Architectural notes:
  - Follows the same pattern as src/quality/screener.py
  - One Claude call per strategy
  - Idempotent — only processes strategies where quality_verdict IS NULL
  - Robust to malformed LLM responses (logs and continues)
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pendulum
from loguru import logger

from src.config.tuning import param
from src.db.queries import fetch_unfiltered_strategies, update_by_id
from src.llm.claude_client import claude

VALID_SCORES = {"strong", "partial", "weak"}
VALID_VERDICTS = {"pass", "partial", "fail"}
REQUIRED_DIMENSIONS = {
    "trigger_specificity",
    "quantification",
    "completeness",
    "scale_invariance",
    "failure_awareness",
}

# Horizon-based scoping (technical plan §7), same 6-month threshold and
# rationale as claims' derive_scope: short-horizon/trading strategies are
# out_of_scope for guidance regardless of quality score, but still count
# toward expert credibility. Kept as its own function rather than sharing
# claims' derive_scope across two unrelated tables/fields.
OUT_OF_SCOPE_HORIZON_MONTHS = param("quality.out_of_scope_horizon_months")


def derive_scope(holding_horizon_months: int | None) -> str:
    """
    Deterministic guidance-scope classification from the strategy's stated
    holding horizon. Code, not LLM — same hard product boundary as claims.
    None (no stated horizon) stays in_scope.
    """
    if (
        holding_horizon_months is not None
        and holding_horizon_months < OUT_OF_SCOPE_HORIZON_MONTHS
    ):
        return "out_of_scope"
    return "in_scope"


def parse_quality_response(raw_response: str) -> dict[str, Any]:
    """Parse Claude JSON output for a strategy quality assessment and validate shape."""
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON from strategy quality filter") from exc

    if not isinstance(payload, dict):
        raise ValueError("Quality response must be a JSON object")

    scores = payload.get("scores")
    if not isinstance(scores, dict):
        raise ValueError("Quality response missing 'scores' object")

    missing = REQUIRED_DIMENSIONS - set(scores.keys())
    if missing:
        raise ValueError(f"Quality scores missing dimensions: {sorted(missing)}")

    invalid_scores = {
        dim: val
        for dim, val in scores.items()
        if dim in REQUIRED_DIMENSIONS and val not in VALID_SCORES
    }
    if invalid_scores:
        raise ValueError(f"Invalid score values: {invalid_scores}")

    verdict = payload.get("verdict")
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"Invalid verdict: {verdict!r}")

    payload.setdefault("justification", "")

    return payload


def derive_verdict_from_scores(scores: dict[str, str]) -> str:
    """
    Fallback verdict computation from dimension scores. Same rubric as
    claims' derive_verdict_from_scores:
      - pass: 3+ strong AND <= 1 weak
      - fail: 3+ weak
      - partial: everything else
    """
    counts = {"strong": 0, "partial": 0, "weak": 0}
    for dim in REQUIRED_DIMENSIONS:
        counts[scores[dim]] += 1

    if counts["strong"] >= 3 and counts["weak"] <= 1:
        return "pass"
    if counts["weak"] >= 3:
        return "fail"
    return "partial"


def screen_one_strategy(strategy: dict[str, Any]) -> dict[str, Any] | None:
    """
    Send one strategy through the quality filter and update its row.

    Returns the updated row, or None if screening failed.
    """
    strategy_id = strategy.get("id")
    if not strategy_id:
        logger.warning("Skipping strategy with no id")
        return None

    strategy_text = (strategy.get("strategy_text") or "").strip()
    if not strategy_text:
        logger.warning(f"Skipping strategy {strategy_id} with empty strategy_text")
        return None

    variables = {
        "strategy_text": strategy_text,
        "strategy_type": strategy.get("strategy_type") or "unknown",
        "entity_class": str(strategy.get("entity_class") or "n/a"),
        "trigger_conditions": json.dumps(strategy.get("trigger_conditions"))
        if strategy.get("trigger_conditions")
        else "(none stated)",
        "allocation_rule": strategy.get("allocation_rule") or "(none stated)",
        "exit_conditions": strategy.get("exit_conditions") or "(none stated)",
        "reason_given": strategy.get("reason_given") or "(no reasoning captured)",
    }

    response = claude.call(
        prompt_name="quality_filter_strategy_v1",
        content="",
        variables=variables,
    )

    payload = parse_quality_response(response)

    scores = payload["scores"]
    verdict = payload["verdict"]

    derived = derive_verdict_from_scores(scores)
    if derived != verdict:
        logger.warning(
            f"Strategy {strategy_id}: LLM verdict={verdict} but rubric implies "
            f"{derived}. Trusting LLM but logging for audit."
        )

    update_fields = {
        "quality_verdict": verdict,
        "quality_scores": {
            "dimensions": scores,
            "justification": payload.get("justification", ""),
            "rubric_derived_verdict": derived,
        },
        "quality_filtered_at": pendulum.now("UTC").to_iso8601_string(),
        "scope": derive_scope(strategy.get("holding_horizon_months")),
    }

    updated = update_by_id("strategies", strategy_id, update_fields)
    return updated


def run_screening(max_strategies: int | None = None) -> dict[str, int]:
    """
    Run quality filtering for strategies where quality_verdict IS NULL.

    Returns a counts dict: { pass, partial, fail, error, total }.
    """
    strategies = fetch_unfiltered_strategies(limit=max_strategies or 100)
    logger.info(f"Found {len(strategies)} unfiltered strategies")

    counts = {"pass": 0, "partial": 0, "fail": 0, "error": 0, "total": len(strategies)}

    for index, strategy in enumerate(strategies, start=1):
        strategy_id = strategy.get("id")
        remaining = len(strategies) - index
        logger.info(
            f"Screening {index}/{len(strategies)} ({remaining} remaining): "
            f"strategy {strategy_id}"
        )
        try:
            result = screen_one_strategy(strategy)
            if result is None:
                counts["error"] += 1
                continue
            verdict = result.get("quality_verdict", "error")
            if verdict in counts:
                counts[verdict] += 1
            else:
                counts["error"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to screen strategy {strategy_id}: {exc}")
            counts["error"] += 1

    logger.info(
        f"Screening complete: {counts['pass']} pass, {counts['partial']} partial, "
        f"{counts['fail']} fail, {counts['error']} errors (of {counts['total']})"
    )

    if counts["total"] > 0:
        total_scored = counts["pass"] + counts["partial"] + counts["fail"]
        if total_scored > 0:
            pass_rate = 100 * counts["pass"] / total_scored
            fail_rate = 100 * counts["fail"] / total_scored
            logger.info(
                f"Distribution: pass={pass_rate:.0f}% partial="
                f"{100 - pass_rate - fail_rate:.0f}% fail={fail_rate:.0f}%"
            )

    return counts


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for strategy quality screening."""
    parser = argparse.ArgumentParser(
        description="Score unfiltered strategies on 5 specificity dimensions"
    )
    parser.add_argument(
        "--max-strategies",
        type=int,
        default=None,
        help="Limit how many strategies to screen for a test run",
    )
    args = parser.parse_args(argv)

    counts = run_screening(max_strategies=args.max_strategies)
    print(
        f"Screened {counts['total']} strategies: "
        f"{counts['pass']} pass, {counts['partial']} partial, "
        f"{counts['fail']} fail, {counts['error']} errors"
    )


if __name__ == "__main__":
    main()
