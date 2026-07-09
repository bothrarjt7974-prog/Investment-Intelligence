"""
Fundamentals sanity check (Layer 7).

For each active theme not yet fundamentals-checked, resolve its target
instrument(s), fetch a fundamentals snapshot for each (src/fundamentals/data.py),
apply the deterministic balance-sheet hard veto, and — if the veto didn't
fire — call an LLM (Sonnet) to render a pass/downgrade/reject verdict on
valuation and earnings trajectory. Mirrors the CLI/loop/logging pattern
used by every other pipeline module in this repo.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pendulum
from loguru import logger

from src.db.queries import fetch_themes_pending_fundamentals_check, update_by_id
from src.fundamentals.data import (
    check_balance_sheet_red_flag,
    compute_sector_relative_pe,
    fetch_fundamentals_snapshot,
    fetch_index_valuation,
    resolve_target_instruments,
)
from src.llm.claude_client import claude

# not_applicable is normally a code-side verdict (no resolvable
# instrument), but the LLM legitimately reaches for it too when the only
# resolvable instruments turn out irrelevant to the thesis (seen live:
# a SpaceX theme resolving only to peripheral public comps) — rejecting
# it crashed the check instead of recording an honest answer.
VALID_VERDICTS = ("pass", "downgrade", "reject", "not_applicable")
VALID_FLAGS = ("strong", "neutral", "weak")


def parse_fundamentals_response(raw_response: str) -> dict[str, Any]:
    """Parse Claude JSON output for a fundamentals check and validate shape."""
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON from fundamentals check") from exc

    if not isinstance(payload, dict):
        raise ValueError("Fundamentals check response must be a JSON object")

    if payload.get("verdict") not in VALID_VERDICTS:
        raise ValueError(f"Invalid verdict: {payload.get('verdict')!r}")

    for field in ("valuation_flag", "earnings_trajectory_flag"):
        if payload.get(field) not in VALID_FLAGS:
            raise ValueError(f"Invalid {field}: {payload.get(field)!r}")

    payload.setdefault("notes", "")
    return payload


def _build_instruments_block(instruments: dict[str, dict[str, Any]]) -> str:
    """Render resolved instruments + their snapshots into the text block
    fundamentals_check_v1.md expects."""
    lines = []
    for symbol, data in instruments.items():
        snapshot = data["snapshot"]
        lines.append(
            f"- symbol: {symbol}\n"
            f"  trailing_pe: {snapshot.get('trailingPE')}\n"
            f"  sector_relative_pe_ratio: {data.get('sector_relative_pe')}\n"
            f"  price_to_book: {snapshot.get('priceToBook')}\n"
            f"  earnings_growth: {snapshot.get('earningsGrowth')}\n"
            f"  revenue_growth: {snapshot.get('revenueGrowth')}\n"
            f"  analyst_recommendation: {snapshot.get('recommendationKey')}"
        )
    return "\n".join(lines)


def _result(
    verdict: str, notes: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "fundamentals_verdict": verdict,
        "fundamentals_notes": notes,
        "fundamentals_data": data or {},
        "fundamentals_checked_at": pendulum.now("UTC").to_iso8601_string(),
    }


def check_one_theme(theme: dict[str, Any]) -> dict[str, Any]:
    """
    Run the fundamentals check for one theme. Returns the update dict to
    write to the themes row.
    """
    # Equity-index themes get a real, applicable check: the index's
    # tracker-ETF trailing PE against a documented fair band —
    # deterministic verdict, no LLM needed at index granularity. This
    # replaces the bypass-with-note for the indices that have a proxy;
    # FX/commodity/crypto entities still have no fundamentals analog and
    # fall through to the existing paths.
    index_valuation = fetch_index_valuation(theme.get("entity_cluster_key"))
    if index_valuation is not None:
        position = index_valuation["position"]
        verdict = "downgrade" if position == "above" else "pass"
        return _result(
            verdict,
            (
                f"Index-level valuation via {index_valuation['proxy']}: trailing PE "
                f"{index_valuation['trailing_pe']} is {position} the fair band "
                f"({index_valuation['band_low']}-{index_valuation['band_high']}). "
                + (
                    "Rich valuation warrants caution on fresh exposure — downgraded, not vetoed."
                    if position == "above"
                    else "Within/below the fair band — no valuation objection."
                )
            ),
            {"index_valuation": index_valuation},
        )

    instrument_symbols = resolve_target_instruments(theme)
    if not instrument_symbols:
        return _result(
            "not_applicable",
            "Sector/index/macro-level theme with no named instrument to check.",
        )

    resolved: dict[str, dict[str, Any]] = {}
    for symbol in instrument_symbols:
        snapshot = fetch_fundamentals_snapshot(symbol)
        if snapshot is not None:
            resolved[symbol] = {"snapshot": snapshot}

    if not resolved:
        return _result(
            "insufficient_data",
            f"No fundamentals data available for {instrument_symbols!r}.",
            {"instrument_symbols": instrument_symbols},
        )

    for symbol, data in resolved.items():
        if check_balance_sheet_red_flag(data["snapshot"]):
            snapshot = data["snapshot"]
            return _result(
                "reject",
                (
                    f"Balance sheet hard veto on {symbol}: debt/equity "
                    f"{snapshot.get('debtToEquity')} with current ratio "
                    f"{snapshot.get('currentRatio')} — rejected regardless of "
                    f"theme conviction, per fundamentals policy."
                ),
                {"instruments": resolved, "veto_symbol": symbol},
            )

    entity_cluster_key = theme.get("entity_cluster_key")
    for data in resolved.values():
        data["sector_relative_pe"] = compute_sector_relative_pe(
            entity_cluster_key, data["snapshot"].get("trailingPE")
        )

    variables = {
        "summary_thesis": theme.get("summary_thesis", ""),
        "cycle_position": str(theme.get("cycle_position") or "unknown"),
        "entity_cluster_key": str(entity_cluster_key or ""),
        "instruments_block": _build_instruments_block(resolved),
    }
    response = claude.call(prompt_name="fundamentals_check_v1", content="", variables=variables)
    payload = parse_fundamentals_response(response)

    return _result(
        payload["verdict"],
        payload["notes"],
        {
            "instruments": resolved,
            "valuation_flag": payload["valuation_flag"],
            "earnings_trajectory_flag": payload["earnings_trajectory_flag"],
        },
    )


def run_fundamentals_check(max_themes: int | None = None) -> dict[str, int]:
    """Run the fundamentals check for active themes not yet checked."""
    themes = fetch_themes_pending_fundamentals_check(limit=max_themes or 100)
    logger.info(f"Found {len(themes)} themes pending fundamentals check")

    counts = {
        "pass": 0,
        "downgrade": 0,
        "reject": 0,
        "not_applicable": 0,
        "insufficient_data": 0,
        "error": 0,
        "total": len(themes),
    }

    for index, theme in enumerate(themes, start=1):
        theme_id = theme.get("id")
        try:
            update_fields = check_one_theme(theme)
            update_by_id("themes", theme_id, update_fields)
            verdict = update_fields.get("fundamentals_verdict", "error")
            if verdict in counts:
                counts[verdict] += 1
            else:
                counts["error"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to check fundamentals for theme {theme_id}: {exc}")
            counts["error"] += 1

        if index % 25 == 0 or index == len(themes):
            logger.info(f"Checked {index}/{len(themes)} themes")

    logger.info(
        f"Fundamentals check complete: pass={counts['pass']} downgrade={counts['downgrade']} "
        f"reject={counts['reject']} not_applicable={counts['not_applicable']} "
        f"insufficient_data={counts['insufficient_data']} errors={counts['error']} "
        f"(of {counts['total']})"
    )

    return counts


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for the fundamentals sanity check."""
    parser = argparse.ArgumentParser(
        description="Run the fundamentals sanity check on active themes"
    )
    parser.add_argument(
        "--max-themes",
        type=int,
        default=None,
        help="Limit how many themes to check for a test run",
    )
    args = parser.parse_args(argv)

    counts = run_fundamentals_check(max_themes=args.max_themes)
    print(
        f"Checked {counts['total']} themes: {counts['pass']} pass, "
        f"{counts['downgrade']} downgrade, {counts['reject']} reject, "
        f"{counts['not_applicable']} not_applicable, "
        f"{counts['insufficient_data']} insufficient_data, {counts['error']} errors"
    )


if __name__ == "__main__":
    main()
