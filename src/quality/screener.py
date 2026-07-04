"""Quality screening pipeline for unprocessed claims.

Sends each claim through the quality_filter prompt and records:
  - quality_verdict (pass | partial | fail)
  - quality_scores (JSON breakdown across 5 dimensions)
  - quality_filtered_at (timestamp)

Failed claims are NOT deleted; they are tagged. Downstream layers
(convergence, briefing) filter by quality_verdict = 'pass' when they
want clean data.

Architectural notes:
  - Follows the same pattern as src/extraction/extractor.py
  - One Claude call per claim, much cheaper than extraction
  - Idempotent — only processes claims where quality_verdict IS NULL
  - Robust to malformed LLM responses (logs and continues)
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pendulum
from loguru import logger

from src.db.queries import fetch_unfiltered_claims, update_by_id
from src.llm.claude_client import claude

VALID_SCORES = {"strong", "partial", "weak"}
VALID_VERDICTS = {"pass", "partial", "fail"}
REQUIRED_DIMENSIONS = {
    "reasoning_depth",
    "data_support",
    "why_explanation",
    "counter_argument_acknowledgment",
    "falsifiability",
}

# Horizon-based scoping (technical plan §7): claims resolving inside this
# window are trading calls, not investment theses — marked out_of_scope for
# guidance regardless of quality score. They still flow through validation
# and count toward expert credibility.
OUT_OF_SCOPE_HORIZON_MONTHS = 6


def derive_scope(target_horizon_months: int | None) -> str:
    """
    Deterministic guidance-scope classification from the claim's horizon.

    Code, not LLM — this is a hard product boundary (we are investors, not
    traders), so it must be enforced mechanically, not judged per-claim.
    None (no testable horizon) stays in_scope: opinion-only claims are
    filtered by verification_status downstream, not by scope.
    """
    if (
        target_horizon_months is not None
        and target_horizon_months < OUT_OF_SCOPE_HORIZON_MONTHS
    ):
        return "out_of_scope"
    return "in_scope"


def parse_quality_response(raw_response: str) -> dict[str, Any]:
    """Parse Claude JSON output for a quality assessment and validate shape."""
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON from quality filter") from exc

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

    # Justification is optional but expected; default to empty string if missing
    payload.setdefault("justification", "")

    return payload


def derive_verdict_from_scores(scores: dict[str, str]) -> str:
    """
    Fallback verdict computation from dimension scores.

    Used as a safety net if Claude's verdict disagrees obviously with its
    own scores (rare but possible). The LLM's verdict is the primary
    source of truth; this only logs a warning if it disagrees significantly.

    Rubric:
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


def screen_one_claim(claim: dict[str, Any]) -> dict[str, Any] | None:
    """
    Send one claim through the quality filter and update its row.

    Returns the updated row, or None if screening failed.
    """
    claim_id = claim.get("id")
    if not claim_id:
        logger.warning("Skipping claim with no id")
        return None

    claim_text = (claim.get("claim_text") or "").strip()
    reason_given = (claim.get("reason_given") or "").strip()
    if not claim_text:
        logger.warning(f"Skipping claim {claim_id} with empty claim_text")
        return None

    variables = {
        "claim_text": claim_text,
        "claim_type": claim.get("claim_type") or "unknown",
        "entity": str(claim.get("entity") or "n/a"),
        "reason_given": reason_given or "(no reasoning captured)",
    }

    # The quality filter prompt needs the claim+reasoning as variables,
    # not as the trailing content. We pass an empty content string and
    # let the prompt template do the work via {{var}} substitution.
    response = claude.call(
        prompt_name="quality_filter_v3",
        content="",
        variables=variables,
    )

    payload = parse_quality_response(response)

    scores = payload["scores"]
    verdict = payload["verdict"]

    # Sanity check: does the LLM's verdict match what the rubric says?
    derived = derive_verdict_from_scores(scores)
    if derived != verdict:
        logger.warning(
            f"Claim {claim_id}: LLM verdict={verdict} but rubric implies "
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
        "scope": derive_scope(claim.get("target_horizon_months")),
    }

    updated = update_by_id("claims", claim_id, update_fields)
    return updated


def run_screening(max_claims: int | None = None) -> dict[str, int]:
    """
    Run quality filtering for claims where quality_verdict IS NULL.

    Returns a counts dict: { pass, partial, fail, error, total }.
    """
    claims = fetch_unfiltered_claims(limit=max_claims or 100)
    logger.info(f"Found {len(claims)} unfiltered claims")

    counts = {"pass": 0, "partial": 0, "fail": 0, "error": 0, "total": len(claims)}

    for index, claim in enumerate(claims, start=1):
        claim_id = claim.get("id")
        remaining = len(claims) - index
        logger.info(
            f"Screening {index}/{len(claims)} ({remaining} remaining): "
            f"claim {claim_id}"
        )
        try:
            result = screen_one_claim(claim)
            if result is None:
                counts["error"] += 1
                continue
            verdict = result.get("quality_verdict", "error")
            if verdict in counts:
                counts[verdict] += 1
            else:
                counts["error"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to screen claim {claim_id}: {exc}")
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
    """Command-line entry point for quality screening."""
    parser = argparse.ArgumentParser(
        description="Score unfiltered claims on 5 reasoning dimensions"
    )
    parser.add_argument(
        "--max-claims",
        type=int,
        default=None,
        help="Limit how many claims to screen for a test run",
    )
    args = parser.parse_args(argv)

    counts = run_screening(max_claims=args.max_claims)
    print(
        f"Screened {counts['total']} claims: "
        f"{counts['pass']} pass, {counts['partial']} partial, "
        f"{counts['fail']} fail, {counts['error']} errors"
    )


if __name__ == "__main__":
    main()
