"""
Track A validator: outcome validation of claims against real market data.

Once a claim's target_date has passed, this module determines whether the
expert's prediction came true and records the result on the claims row.
Follows the same CLI/loop/logging pattern as src/quality/screener.py.

No LLM calls — pure deterministic price-data comparison. The unresolvable
statuses (opinion_only, data_unavailable) exist so we never force a score
onto a claim we can't actually check; downstream credibility scoring only
uses claims where verification_status in (correct, partial, incorrect).
"""

from __future__ import annotations

import argparse
import re
from datetime import date, datetime
from typing import Any

import pendulum
from loguru import logger

from src.config.tuning import param
from src.db.queries import fetch_validatable_claims, update_by_id
from src.internal.scoring import recompute_all_experts
from src.validation.entity_symbol_map import resolve_entity
from src.validation.market_data import ensure_symbol_backfilled, price_on, price_series

DEFAULT_LOOKBACK_DAYS = 7
# Manual macro series (repo rate, CPI) only get a new row when the value
# actually changes — a rate can legitimately hold for 2+ years (e.g. RBI held
# 6.50% Feb 2023 -> Feb 2025), so "no recent row" is normal, not missing data.
# Effectively unbounded (~10y) rather than a short window like daily prices.
MANUAL_LOOKBACK_DAYS = 3650

DIRECTIONAL_NEUTRAL_CORRECT_BAND = param("validation.neutral_correct_band_pct")
DIRECTIONAL_NEUTRAL_PARTIAL_BAND = param("validation.neutral_partial_band_pct")
DIRECTIONAL_MOVE_THRESHOLD = param("validation.directional_move_threshold_pct")
PRICE_TARGET_PARTIAL_BAND = param("validation.price_target_partial_band")  # fraction of target counted as 'partial'

# "target of ₹1,200" / "target of $250" — currency symbol after "target of" optional.
_TARGET_PHRASE_RE = re.compile(
    r"target\s+of\s+(?:₹|rs\.?|inr|\$)?\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE
)
# "₹3,200", "Rs 900", "Rs. 900", "$250" anywhere in the text.
_CURRENCY_NUMBER_RE = re.compile(r"(?:₹|rs\.?|inr|\$)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
# A currency+number immediately followed by one of these is a monetary amount
# (macro spending, liquidity injection, market cap) — never a price level.
_MAGNITUDE_WORD_RE = re.compile(
    r"^\s*(lakh|crore|cr\.?|thousand|million|mn|billion|bn|trillion)\b", re.IGNORECASE
)
_TARGET_WORD_WINDOW = 40


def _is_magnitude_amount(claim_text: str, match_end: int) -> bool:
    """True if the number is followed by a magnitude word (lakh crore, billion, ...)."""
    tail = claim_text[match_end : match_end + 20]
    return bool(_MAGNITUDE_WORD_RE.match(tail))


def detect_price_target(claim_text: str) -> float | None:
    """
    Extract a numeric price target from claim text, or None if this isn't a
    price-target claim.

    Two guards against false positives on macro claims ("₹1.1 lakh crore
    liquidity injection" is not a price target of 1.1):
      - a number immediately followed by a magnitude word (lakh/crore/
        billion/...) is a monetary amount, never a price level
      - a bare currency+number (no "target of" phrasing) only counts if the
        word "target" actually appears near it, not just anywhere in the claim
    """
    if not claim_text:
        return None

    match = _TARGET_PHRASE_RE.search(claim_text)
    if match and not _is_magnitude_amount(claim_text, match.end()):
        return float(match.group(1).replace(",", ""))

    for match in _CURRENCY_NUMBER_RE.finditer(claim_text):
        if _is_magnitude_amount(claim_text, match.end()):
            continue
        window_start = max(0, match.start() - _TARGET_WORD_WINDOW)
        window_end = min(len(claim_text), match.end() + _TARGET_WORD_WINDOW)
        if "target" in claim_text[window_start:window_end].lower():
            return float(match.group(1).replace(",", ""))

    return None


def _to_date_str(value: str | date | datetime) -> str:
    if isinstance(value, str):
        return pendulum.parse(value).to_date_string()
    if isinstance(value, datetime):
        return pendulum.instance(value).to_date_string()
    return value.isoformat()


def compute_mfe_mae(
    series: list[tuple[date, float]], entry_price: float
) -> tuple[float | None, float | None]:
    """
    Max favorable / adverse excursion (%) relative to entry_price over the
    validation window. Direction-agnostic: MFE is the best point the price
    ever reached, MAE the worst — unused for scoring today, scoring v2 wants
    them for "was the expert early/right but the claim's exact window missed it".
    """
    if not series or entry_price == 0:
        return None, None
    returns = [(p - entry_price) / entry_price * 100 for _, p in series]
    return max(returns), min(returns)


def classify_directional(direction: str, return_pct: float) -> str:
    """
    Directional outcome classification.

    Order matters: the "correct" check for bullish/bearish is evaluated
    first; only if that fails does the |return| <= 2% partial band apply.
    neutral and na are handled by the caller before this is reached.
    """
    if direction == "bullish" and return_pct > DIRECTIONAL_MOVE_THRESHOLD:
        return "correct"
    if direction == "bearish" and return_pct < -DIRECTIONAL_MOVE_THRESHOLD:
        return "correct"
    if abs(return_pct) <= DIRECTIONAL_MOVE_THRESHOLD:
        return "partial"
    return "incorrect"


def classify_neutral(return_pct: float) -> str:
    """direction='neutral': correct if the move stayed small, partial if moderate."""
    abs_return = abs(return_pct)
    if abs_return <= DIRECTIONAL_NEUTRAL_CORRECT_BAND:
        return "correct"
    if abs_return <= DIRECTIONAL_NEUTRAL_PARTIAL_BAND:
        return "partial"
    return "incorrect"


def evaluate_price_target(
    series: list[tuple[date, float]], target_value: float, direction: str | None
) -> tuple[str, float, float]:
    """
    Did the price series touch a stated price target?

    direction='bearish' means the target is a downside level (price falling
    to X); anything else assumes an upside target (price rising to X), which
    covers the overwhelming majority of price-target claims ("reach ₹3,200").
    Returns (result, extreme_price_reached, pct_of_target_reached).
    """
    prices = [p for _, p in series]
    if direction == "bearish":
        extreme = min(prices)
        touched = extreme <= target_value
    else:
        extreme = max(prices)
        touched = extreme >= target_value

    pct_of_target = (extreme / target_value * 100) if target_value else None
    closeness = abs(target_value - extreme) / target_value if target_value else None

    if touched:
        result = "correct"
    elif closeness is not None and closeness <= PRICE_TARGET_PARTIAL_BAND:
        result = "partial"
    else:
        result = "incorrect"

    return result, extreme, pct_of_target


def validate_one_claim(claim: dict[str, Any]) -> dict[str, Any] | None:
    """
    Determine the outcome of one claim. Returns the update dict to write to
    the claims row (verification_status, verification_outcome, verified_at),
    or None if the claim is malformed beyond repair (missing id).
    """
    claim_id = claim.get("id")
    if not claim_id:
        logger.warning("Skipping claim with no id")
        return None

    now = pendulum.now("UTC").to_iso8601_string()
    strength = claim.get("strength")
    target_horizon_months = claim.get("target_horizon_months")
    entity = claim.get("entity")
    geography = claim.get("geography")
    direction = claim.get("direction")
    claim_text = claim.get("claim_text") or ""
    claimed_at = claim.get("claimed_at")
    target_date = claim.get("target_date")

    # claim_extraction_v4 tags is_conditional/condition_text — a claim like
    # "if RBI cuts, banks re-rate" can't be honestly scored against a price
    # series without knowing whether the condition itself resolved, which
    # we don't track. Force-scoring it as unconditional (the pre-v4
    # behavior) would produce false negatives whenever the condition never
    # materialized. Claims from v2/v3-era extraction default is_conditional
    # to False (see normalization._parse_claim) and take the normal path.
    if claim.get("is_conditional"):
        return {"verification_status": "conditional_unresolved", "verified_at": now}

    # (a) opinions and horizon-less claims aren't testable against a price series.
    if strength == "opinion" or target_horizon_months is None:
        return {"verification_status": "opinion_only", "verified_at": now}

    # direction='na' is also non-testable, regardless of entity resolution.
    if direction == "na":
        return {"verification_status": "opinion_only", "verified_at": now}

    # (b) no data source for this entity at all.
    resolved = resolve_entity(entity, geography)
    if resolved is None:
        logger.info(f"Claim {claim_id}: entity '{entity}' unresolvable, data_unavailable")
        return {"verification_status": "data_unavailable", "verified_at": now}
    # market_data rows are keyed by our canonical entity name (what
    # backfill_all/load_manual_csv write as `symbol`), not by the external
    # fetch_symbol resolve_entity returns (e.g. entity='nifty50' but
    # fetch_symbol='^NSEI') — resolve_entity is only consulted here to
    # confirm the entity is mappable at all.
    symbol = entity
    fetch_symbol, source = resolved
    # Manual macro series (repo rate, CPI) only get a new row every 1-3
    # months, unlike daily market data — a 7-day lookback would miss the
    # last known value for most claim/target dates. MANUAL_LOOKBACK_DAYS
    # covers the widest realistic gap (RBI repo rate changes) with margin.
    lookback_days = MANUAL_LOOKBACK_DAYS if source == "manual" else DEFAULT_LOOKBACK_DAYS

    # On-demand backfill for entities not covered by the bulk backfill —
    # either a new explicit map entry, or (most often) the geography-based
    # .NS/bare-ticker fallback in resolve_entity(). No-ops if data already
    # exists. Manual-source entities come from CSVs, not yfinance — nothing
    # to backfill on demand for those.
    if source == "yfinance" and not ensure_symbol_backfilled(symbol, fetch_symbol):
        logger.info(f"Claim {claim_id}: no market data available for '{symbol}' ({fetch_symbol})")
        return {"verification_status": "data_unavailable", "verified_at": now}

    claimed_at_str = _to_date_str(claimed_at)
    target_date_str = _to_date_str(target_date)
    validated_window_days = (
        pendulum.parse(target_date_str) - pendulum.parse(claimed_at_str)
    ).days

    detected_target = detect_price_target(claim_text)

    if detected_target is not None:
        # (c) price-target claims: scan the whole window for a touch.
        series = price_series(symbol, claimed_at_str, target_date_str)
        if not series:
            logger.info(f"Claim {claim_id}: no price series for '{symbol}', data_unavailable")
            return {"verification_status": "data_unavailable", "verified_at": now}

        price_at_claim = series[0][1]
        price_at_target = series[-1][1]
        result, extreme_price, pct_of_target = evaluate_price_target(
            series, detected_target, direction
        )
        mfe_pct, mae_pct = compute_mfe_mae(series, price_at_claim)
        return_pct = (
            (price_at_target - price_at_claim) / price_at_claim * 100
            if price_at_claim
            else None
        )

        outcome = {
            "method": "price_target",
            "symbol": symbol,
            "price_at_claim": price_at_claim,
            "price_at_target": price_at_target,
            "return_pct": return_pct,
            "predicted_direction": direction,
            "result": result,
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "validated_window_days": validated_window_days,
            "detected_target": detected_target,
            "max_price_reached": extreme_price,
            "pct_of_target": pct_of_target,
        }
        return {
            "verification_status": result,
            "verification_outcome": outcome,
            "verified_at": now,
        }

    # (d) directional validation.
    price_at_claim = price_on(symbol, claimed_at_str, max_lookback_days=lookback_days)
    price_at_target = price_on(symbol, target_date_str, max_lookback_days=lookback_days)
    if price_at_claim is None or price_at_target is None:
        logger.info(f"Claim {claim_id}: missing price endpoint for '{symbol}', data_unavailable")
        return {"verification_status": "data_unavailable", "verified_at": now}

    return_pct = (price_at_target - price_at_claim) / price_at_claim * 100

    if direction == "neutral":
        result = classify_neutral(return_pct)
    elif direction in ("bullish", "bearish"):
        result = classify_directional(direction, return_pct)
    else:
        logger.warning(f"Claim {claim_id}: unrecognized direction {direction!r}, opinion_only")
        return {"verification_status": "opinion_only", "verified_at": now}

    series = price_series(symbol, claimed_at_str, target_date_str)
    mfe_pct, mae_pct = compute_mfe_mae(series, price_at_claim) if series else (None, None)

    outcome = {
        "method": "directional",
        "symbol": symbol,
        "price_at_claim": price_at_claim,
        "price_at_target": price_at_target,
        "return_pct": return_pct,
        "predicted_direction": direction,
        "result": result,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "validated_window_days": validated_window_days,
    }
    return {"verification_status": result, "verification_outcome": outcome, "verified_at": now}


def run_validation(max_claims: int | None = None) -> dict[str, int]:
    """
    Validate claims where quality_verdict='pass', verification_status is
    pending/null, and target_date has passed. Recomputes all expert
    credibility scores at the end of the batch.
    """
    claims = fetch_validatable_claims(limit=max_claims or 100)
    logger.info(f"Found {len(claims)} validatable claims")

    counts = {
        "correct": 0,
        "partial": 0,
        "incorrect": 0,
        "opinion_only": 0,
        "data_unavailable": 0,
        "error": 0,
        "total": len(claims),
    }

    for index, claim in enumerate(claims, start=1):
        claim_id = claim.get("id")
        try:
            update_fields = validate_one_claim(claim)
            if update_fields is None:
                counts["error"] += 1
                continue
            update_by_id("claims", claim_id, update_fields)
            status = update_fields.get("verification_status", "error")
            if status in counts:
                counts[status] += 1
            else:
                counts["error"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to validate claim {claim_id}: {exc}")
            counts["error"] += 1

        if index % 25 == 0 or index == len(claims):
            logger.info(f"Validated {index}/{len(claims)} claims")

    logger.info(
        f"Validation complete: correct={counts['correct']} partial={counts['partial']} "
        f"incorrect={counts['incorrect']} opinion_only={counts['opinion_only']} "
        f"data_unavailable={counts['data_unavailable']} errors={counts['error']} "
        f"(of {counts['total']})"
    )

    recompute_all_experts()

    return counts


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for claim outcome validation."""
    parser = argparse.ArgumentParser(
        description="Validate claims against real market data outcomes"
    )
    parser.add_argument(
        "--max-claims",
        type=int,
        default=None,
        help="Limit how many claims to validate for a test run",
    )
    args = parser.parse_args(argv)

    counts = run_validation(max_claims=args.max_claims)
    print(
        f"Validated {counts['total']} claims: "
        f"{counts['correct']} correct, {counts['partial']} partial, "
        f"{counts['incorrect']} incorrect, {counts['opinion_only']} opinion_only, "
        f"{counts['data_unavailable']} data_unavailable, {counts['error']} errors"
    )


if __name__ == "__main__":
    main()
