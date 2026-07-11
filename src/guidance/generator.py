"""
Portfolio guidance generation (Layer 9, core — no delivery surface).

For each active user's portfolio and each theme ready for guidance
(fundamentals-passed, matched to a strategy), run the deterministic
eligibility gates (src/guidance/eligibility.py); if all pass, call the
highest-stakes prompt in this codebase (guidance_generation_v1, Opus) to
produce a specific allocation recommendation. Every outcome — a real
recommendation or a refusal — is logged to portfolio_guidance, per the
plan's "refusal as a feature... every refusal is logged with its reason"
(SEBI 5-year record-keeping requirement).

Attribution is assembled by code from real theme/strategy rows, never by
the LLM — see build_attribution.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pendulum
from loguru import logger

from src.db.queries import (
    fetch_active_refusal,
    fetch_active_users,
    fetch_best_strategy_match,
    fetch_portfolio_holdings,
    fetch_portfolios_for_user,
    fetch_themes_ready_for_matching,
    insert_one,
)
from src.guidance.eligibility import (
    check_already_aligned,
    check_duplicate_guidance,
    check_entity_overlap_guidance,
    check_theme_eligible,
)
from src.guidance.sizing import apply_sizing_gate
from src.llm.claude_client import claude

VALID_GUIDANCE_TYPES = (
    "INCREASE_EXPOSURE",
    "REDUCE_EXPOSURE",
    "INTRODUCE_NEW",
    "EXIT",
    "MAINTAIN",
    "REBALANCE",
)


def parse_guidance_response(raw_response: str) -> dict[str, Any]:
    """Parse Claude JSON output for a guidance generation call and validate shape."""
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON from guidance generation") from exc

    if not isinstance(payload, dict):
        raise ValueError("Guidance generation response must be a JSON object")

    if payload.get("guidance_type") not in VALID_GUIDANCE_TYPES:
        raise ValueError(f"Invalid guidance_type: {payload.get('guidance_type')!r}")

    for field in ("target_allocation_change", "funding_source", "tax_note", "estimated_tax_impact"):
        payload.setdefault(field, None)
    payload.setdefault("specific_instruments", [])

    return payload


def build_attribution(
    theme: dict[str, Any],
    strategy: dict[str, Any],
    sizing_source: str | None = None,
) -> dict[str, Any]:
    """
    Assemble the attribution record from real theme/strategy rows —
    never from the LLM's own words. "Full attribution, never invention."

    sizing_source, when provided, is the sizing gate's enforcement record
    (strategy_allocation_rule / risk_profile_cap / reduction_not_capped)
    — a code-verified fact, which takes precedence over the descriptive
    fallback derived from the strategy row alone.
    """
    backtest_summary = None
    if strategy.get("backtest_status") == "complete":
        backtest_summary = strategy.get("backtest_record")

    fundamentals_verdict = theme.get("fundamentals_verdict")
    fundamentals_note = None
    if fundamentals_verdict not in ("pass", "downgrade"):
        # Only reachable for index/macro entities (the eligibility gate
        # blocks everything else) — record the policy explicitly so the
        # SEBI trail shows WHY no company-fundamentals verdict backs this.
        fundamentals_note = (
            "Company-level fundamentals check not applicable to index/"
            "macro-level entities; guidance rests on expert convergence "
            "and strategy match only (2026-07-09 policy)."
        )

    return {
        "direction": theme.get("supporting_experts", []),
        "fundamentals": {"verdict": fundamentals_verdict, "note": fundamentals_note},
        "execution": {
            "strategy_id": strategy["id"],
            "source_type": strategy.get("source_type"),
            "source_name": strategy.get("source_name"),
            "expert_id": strategy.get("expert_id"),
            "strategy_text": strategy.get("strategy_text"),
            "backtest_summary": backtest_summary,
        },
        "sizing_source": sizing_source
        or (
            "strategy's own stated position sizing"
            if strategy.get("position_sizing")
            else "no explicit sizing rule stated by the matched strategy"
        ),
    }


def _holdings_block(holdings: list[dict[str, Any]]) -> str:
    """Render portfolio holdings with % weight and holding period for the prompt."""
    if not holdings:
        return "(no current holdings — this would be a fresh portfolio)"

    total_value = sum(h["quantity"] * (h.get("cost_basis") or 0) for h in holdings)
    lines = []
    today = pendulum.now("UTC").date()
    for h in holdings:
        value = h["quantity"] * (h.get("cost_basis") or 0)
        pct = (value / total_value * 100) if total_value else 0.0
        acquired = pendulum.parse(str(h["acquired_at"])).date()
        months_held = (today.year - acquired.year) * 12 + (today.month - acquired.month)
        lines.append(
            f"- {h['entity']}: {pct:.1f}% of portfolio, held {months_held} months "
            f"(acquired {acquired.isoformat()}), asset_class={h.get('asset_class', 'equity')}"
        )
    return "\n".join(lines)


def _refusal(
    user_id: str, portfolio_id: str, theme: dict[str, Any], reason: str
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "portfolio_id": portfolio_id,
        "theme_id": theme.get("id"),
        "matched_strategy_id": None,
        "guidance_type": None,
        "target_allocation_change": None,
        "specific_instruments": None,
        "execution_horizon": None,
        "position_holding_horizon": None,
        "funding_source": None,
        "tax_note": None,
        "estimated_tax_impact": None,
        "conviction_level": theme.get("conviction_level"),
        "attribution": None,
        "risks": None,
        "is_refusal": True,
        "refusal_reason": reason,
        "status": "active",
    }


def generate_guidance_for_portfolio(portfolio: dict[str, Any], theme: dict[str, Any]) -> dict[str, Any]:
    """
    Run the eligibility gates for one theme against one portfolio; if all
    pass, call guidance_generation_v1 and build the full row. Otherwise
    return a refusal row — no LLM call spent on an ineligible theme.
    """
    user_id = portfolio["user_id"]
    portfolio_id = portfolio["id"]

    matched_strategy = fetch_best_strategy_match(theme["id"])

    eligible, reason = check_theme_eligible(theme, matched_strategy)
    if not eligible:
        return _refusal(user_id, portfolio_id, theme, reason)

    if check_duplicate_guidance(theme["id"], portfolio_id):
        return _refusal(
            user_id, portfolio_id, theme,
            "active or executing guidance already exists for this theme and portfolio",
        )

    overlap_reason = check_entity_overlap_guidance(theme, portfolio_id)
    if overlap_reason:
        return _refusal(user_id, portfolio_id, theme, overlap_reason)

    holdings = fetch_portfolio_holdings(portfolio_id)
    if check_already_aligned(theme, holdings):
        return _refusal(
            user_id, portfolio_id, theme,
            "portfolio is already sufficiently aligned with this theme's entity/instruments",
        )

    variables = {
        "theme_summary": theme.get("summary_thesis", ""),
        "theme_conviction": theme.get("conviction_level", ""),
        "theme_cycle_position": str(theme.get("cycle_position") or "unknown"),
        "strategy_text": matched_strategy.get("strategy_text", ""),
        "strategy_allocation_rule": str(matched_strategy.get("allocation_rule") or "not specified"),
        "strategy_exit_conditions": str(matched_strategy.get("exit_conditions") or "not specified"),
        "strategy_backtest_summary": json.dumps(matched_strategy.get("backtest_record"))
        if matched_strategy.get("backtest_status") == "complete"
        else "(no completed backtest for this strategy)",
        "portfolio_holdings_block": _holdings_block(holdings),
        "user_risk_profile": portfolio.get("risk_profile", "moderate"),
        "user_restrictions": json.dumps(portfolio.get("restrictions") or []),
        "user_tax_notes": str(portfolio.get("tax_notes") or "none provided"),
    }

    response = claude.synthesize(prompt_name="guidance_generation_v1", content="", variables=variables)
    payload = parse_guidance_response(response)
    payload, sizing_source = apply_sizing_gate(
        payload, theme, matched_strategy, portfolio.get("risk_profile", "moderate"), holdings
    )

    return {
        "user_id": user_id,
        "portfolio_id": portfolio_id,
        "theme_id": theme["id"],
        "matched_strategy_id": matched_strategy["id"],
        "guidance_type": payload["guidance_type"],
        "target_allocation_change": payload["target_allocation_change"],
        "specific_instruments": payload["specific_instruments"],
        "execution_horizon": payload.get("execution_horizon"),
        "position_holding_horizon": payload.get("position_holding_horizon"),
        "funding_source": payload["funding_source"],
        "tax_note": payload["tax_note"],
        "estimated_tax_impact": payload["estimated_tax_impact"],
        "conviction_level": theme.get("conviction_level"),
        "attribution": build_attribution(theme, matched_strategy, sizing_source),
        "risks": payload.get("risks"),
        "is_refusal": False,
        "refusal_reason": None,
        "status": "active",
    }


def run_guidance_generation(max_themes: int | None = None) -> dict[str, int]:
    """Run guidance generation for every active user's portfolio against every eligible theme."""
    themes = fetch_themes_ready_for_matching(limit=max_themes or 100)
    users = fetch_active_users()
    logger.info(f"Found {len(themes)} themes ready and {len(users)} active users")

    counts = {"guidance_generated": 0, "refused": 0, "refusal_deduped": 0, "error": 0, "total": 0}

    for user in users:
        portfolios = fetch_portfolios_for_user(user["id"])
        # user profile fields (risk_profile, restrictions, tax_notes) live on
        # `users`, not `portfolios` — merge them onto each portfolio dict so
        # generate_guidance_for_portfolio has a single object to read from.
        for portfolio in portfolios:
            portfolio = {
                **portfolio,
                "risk_profile": user.get("risk_profile"),
                "restrictions": user.get("restrictions"),
                "tax_notes": user.get("tax_notes"),
            }
            for theme in themes:
                counts["total"] += 1
                try:
                    row = generate_guidance_for_portfolio(portfolio, theme)
                    # Refusal dedup: the identical refusal re-logged every run
                    # adds nothing to the SEBI record — one active row per
                    # (theme, portfolio, reason) until something changes
                    # (2026-07-10 decision log). Real guidance always inserts.
                    if row["is_refusal"] and fetch_active_refusal(
                        row["theme_id"], row["portfolio_id"], row["refusal_reason"]
                    ):
                        logger.debug(
                            f"Skipping duplicate refusal for theme {row['theme_id']}, "
                            f"portfolio {row['portfolio_id']}"
                        )
                        counts["refusal_deduped"] += 1
                        continue
                    insert_one("portfolio_guidance", row)
                    if row["is_refusal"]:
                        counts["refused"] += 1
                    else:
                        counts["guidance_generated"] += 1
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        f"Failed to generate guidance for portfolio {portfolio['id']}, "
                        f"theme {theme.get('id')}: {exc}"
                    )
                    counts["error"] += 1

    logger.info(
        f"Guidance generation complete: {counts['guidance_generated']} generated, "
        f"{counts['refused']} refused, {counts['error']} errors (of {counts['total']})"
    )
    return counts


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for guidance generation."""
    parser = argparse.ArgumentParser(description="Generate portfolio guidance for active users")
    parser.add_argument(
        "--max-themes", type=int, default=None, help="Limit how many themes to consider for a test run"
    )
    args = parser.parse_args(argv)

    counts = run_guidance_generation(max_themes=args.max_themes)
    print(
        f"Processed {counts['total']} theme x portfolio pairs: "
        f"{counts['guidance_generated']} guidance generated, "
        f"{counts['refused']} refused, {counts['error']} errors"
    )


if __name__ == "__main__":
    main()
