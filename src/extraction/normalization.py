"""
Claim normalization and post-processing.

This module runs AFTER Claude extracts claims, BEFORE they are inserted
into the database. It handles the deterministic parts that LLMs are
unreliable at:

    1. Entity normalization — canonicalize names so the same thing
       (NIFTY50 / nifty50 / nifty) becomes one canonical entity.

    2. Within-video deduplication — merge claims that are about the
       same entity in the same video (LLM forgets earlier claims as
       it generates later ones in a long output).

    3. Target date computation — use the LLM's target_horizon_months
       field (added in prompt v2) instead of throwing it away and
       defaulting to bucket medians.

Architectural principle:
    LLMs handle meaning. Code handles mechanics.
    Use this module to enforce deterministic rules the prompt
    cannot reliably enforce.

Usage in extractor.py:

    from src.extraction.normalization import normalize_claims

    raw_claims = call_claude_extraction(transcript)         # LLM output
    clean_claims = normalize_claims(raw_claims, claimed_at) # this module
    insert_into_claims_table(clean_claims)                  # storage
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

from loguru import logger

# =====================================================================
# ENTITY ALIAS MAP
# =====================================================================
# Maps every known variant of an entity to its canonical form.
# Grow this map over time as you find new variants in production.
#
# Conventions:
#   - Stock tickers: ALL UPPERCASE, no underscores, no suffixes
#       BTC, RELIANCE, AAPL, NVDA
#   - Indices: lowercase_with_underscores
#       nifty50, nifty_midcap150, nasdaq, sp500
#   - Sectors: lowercase_with_underscores, geography prefix
#       indian_it, us_tech, indian_realestate
#   - Macro factors: lowercase_with_underscores
#       inr_usd, fed_rate, crude_oil, gold, silver
# =====================================================================

ENTITY_ALIASES: dict[str, str] = {
    # --- Stock tickers (force uppercase, strip exchange suffixes) ---
    # The function _normalize_ticker handles uppercasing automatically;
    # this map only catches non-trivial renames.
    "BTC_USD": "BTC",
    "BTCUSD": "BTC",
    "BITCOIN": "BTC",
    "RELIANCE.NS": "RELIANCE",
    "TCS.NS": "TCS",
    "INFY.NS": "INFY",
    # --- Indices: lowercase canonical names ---
    "NIFTY50": "nifty50",
    "Nifty50": "nifty50",
    "Nifty 50": "nifty50",
    "nifty": "nifty50",  # "nifty" alone almost always means Nifty 50
    "NIFTY": "nifty50",
    "nifty_500": "nifty500",
    "NIFTY500": "nifty500",
    "NIFTY_MIDCAP150": "nifty_midcap150",
    "nifty_midcap_150": "nifty_midcap150",
    "NIFTY_SMALLCAP_250": "nifty_smallcap250",
    "nifty_smallcap_250": "nifty_smallcap250",
    "NASDAQ": "nasdaq",
    "Nasdaq": "nasdaq",
    "SP500": "sp500",
    "S&P500": "sp500",
    "S&P_500": "sp500",
    "SPX": "sp500",
    "QQQ": "qqq",  # treat as index, not ticker, in this context
    # --- Indian sectors: pick one canonical form per concept ---
    "indian_IT": "indian_it",
    "indian_technology": "indian_it",
    "indian_tech": "indian_it",
    "indian_real_estate": "indian_realestate",
    "india_real_estate": "indian_realestate",
    "real_estate_india": "indian_realestate",
    "indian_smallcap_midcap": "indian_smallcap",
    "indian_midcap_smallcap": "indian_smallcap",  # treat as smallcap when combined
    "indian_large_cap_equity": "indian_largecap",
    "indian_large_cap": "indian_largecap",
    "indian_equities": "indian_equity_market",
    "indian_equity": "indian_equity_market",
    "indian_consumer_staples": "indian_fmcg",
    "indian_pharmaceutical": "indian_pharma",
    # --- US sectors ---
    "us_technology": "us_tech",
    "us_equity": "us_equities",
    "us_equity_market": "us_equities",
    "us_small_cap_ai": "us_smallcap",
    "us_smallcap_ai": "us_smallcap",
    # --- Macro factors ---
    "oil_price": "crude_oil",
    "oil": "crude_oil",
    "OIL": "crude_oil",
    "WTI": "crude_oil",
    "brent": "crude_oil",
    "INR_USD": "inr_usd",
    "INR/USD": "inr_usd",
    "inr": "inr_usd",
    "INR": "inr_usd",
    "USD_INR": "inr_usd",  # canonicalize to one ordering
    "JPY_USD": "jpy_usd",
    "USD_JPY": "jpy_usd",
    "DXY": "dxy",
    "Gold": "gold",
    "GOLD": "gold",
    "Silver": "silver",
    "SILVER": "silver",
    "Bitcoin": "BTC",
    "FED_RATE": "fed_rate",
    "fed_rates": "fed_rate",
    "rbi_rates": "rbi_rate",
    "repo_rate_india": "rbi_rate",
    "india_repo_rate": "rbi_rate",
    "indian_inflation": "india_inflation",
    "india_cpi": "india_inflation",
    "indian_cpi": "india_inflation",
    "us_cpi": "us_inflation",
    "indian_gdp": "india_gdp",
    "us_treasury_yield": "us_10y_yield",
    "us_10yr": "us_10y_yield",
}

# Patterns to recognize ticker-like strings (treated as stock tickers,
# uppercased automatically). Bound is 10, not 8 — ASIANPAINT, HINDUNILVR,
# BAJFINANCE, TATAMOTORS (10 chars) and ICICIBANK, KOTAKBANK (9 chars) are
# real tickers already in ENTITY_SYMBOL_MAP; an 8-char cap silently
# lowercased them instead of uppercasing (bug found while building Layer 7,
# 2026-07-06 — see decision log).
TICKER_PATTERN = re.compile(r"^[A-Za-z0-9]{1,10}$")

# Concepts that look like tickers but aren't — these stay lowercase.
NOT_TICKERS = {
    "gold", "silver", "oil", "inr", "usd", "jpy", "eur", "gbp",
    "real_estate", "nasdaq", "sp500", "qqq",
}


def normalize_entity(entity: str | None) -> str | None:
    """
    Convert an entity string to its canonical form.

    Rules applied in order:
      1. None/empty/"nan" → None
      2. Strip whitespace
      3. Exact match in ENTITY_ALIASES → return canonical
      4. Has underscore → lowercase the whole thing, re-check aliases
      5. Looks like a ticker (1-8 alphanumeric, no underscore) → UPPERCASE
         UNLESS it's in NOT_TICKERS (gold, silver, etc.)
      6. Otherwise → return as-is

    Examples:
        normalize_entity("NIFTY50") -> "nifty50"
        normalize_entity("nifty") -> "nifty50"
        normalize_entity("reliance") -> "RELIANCE"
        normalize_entity("indian_IT") -> "indian_it"
        normalize_entity("gold") -> "gold"
        normalize_entity("BTC_USD") -> "BTC"
        normalize_entity(None) -> None
    """
    if entity is None:
        return None

    if isinstance(entity, float):  # pandas NaN
        return None

    cleaned = str(entity).strip()
    if not cleaned or cleaned.lower() == "nan" or cleaned.lower() == "null":
        return None

    # Step 1: exact alias match
    if cleaned in ENTITY_ALIASES:
        return ENTITY_ALIASES[cleaned]

    # Step 2: case-insensitive alias match (catches "Nifty50" vs "NIFTY50")
    for alias, canonical in ENTITY_ALIASES.items():
        if cleaned.lower() == alias.lower():
            return canonical

    # Step 3: underscore-containing concept → lowercase
    if "_" in cleaned:
        lowered = cleaned.lower()
        if lowered in ENTITY_ALIASES:
            return ENTITY_ALIASES[lowered]
        return lowered

    # Step 4: ticker-like (short alphanumeric, no underscore)
    if TICKER_PATTERN.match(cleaned):
        if cleaned.lower() in NOT_TICKERS:
            return cleaned.lower()
        return cleaned.upper()

    # Step 5: longer concept name → lowercase
    return cleaned.lower()


# =====================================================================
# WITHIN-VIDEO DEDUPLICATION
# =====================================================================
# When Claude generates 20+ claims from a single transcript, it often
# emits the same claim twice — once as a stock_pick about TCS and once
# as a strategy claim mentioning TCS, for example.
#
# We dedup by:
#   1. Group claims with the same canonical entity in the same video
#   2. If multiple claims in a group, compare claim_text similarity
#   3. If similarity > threshold, keep the "best" version and drop others
#
# "Best" version selection:
#   - Prefer strength=strong over soft over opinion
#   - Within same strength, prefer the longer/more specific claim_text
# =====================================================================

SIMILARITY_THRESHOLD = 0.50  # tuned for claim text comparison
STRENGTH_RANK = {"strong": 3, "soft": 2, "opinion": 1}


@dataclass
class Claim:
    """In-memory claim representation matching the prompt's JSON output."""

    claim_text: str
    claim_type: str
    geography: str
    timeframe: str
    strength: str
    direction: str
    reason_given: str
    entity: str | None = None
    target_horizon_months: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)  # original LLM payload


def _important_words(text: str) -> set[str]:
    """
    Extract meaningful words from claim text for similarity comparison.
    Strips stopwords and short words so comparison focuses on content.
    """
    stopwords = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "have", "in", "is", "it", "its", "of", "on", "or", "that",
        "the", "to", "was", "will", "with", "this", "these", "those",
        "but", "not", "no", "yes", "if", "then", "than", "should", "would",
        "could", "may", "might", "can", "do", "does", "did", "been", "being",
        "i", "we", "you", "he", "she", "they", "them", "their", "his", "her",
        "very", "more", "most", "much", "many", "some", "any", "all", "each",
        "currently", "current", "expect", "expects", "expected",
        "because", "having", "delivered", "due", "next", "over",
    }
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in stopwords}


def _claim_similarity(a: Claim, b: Claim) -> float:
    """
    Compute similarity between two claims based on their important
    content words. Uses three signals:

      1. Jaccard similarity — symmetric overlap
      2. Containment — does the shorter claim's content live inside
         the longer one? Catches "INR will weaken" vs "INR will weaken
         substantially against USD due to oil pressure" — same claim,
         one just more detailed.
      3. SequenceMatcher — catches paraphrasing with different wording

    Returns 0.0–1.0. Higher = more similar.

    Same-entity bonus: if both claims share a non-null entity, we add
    a small boost. The entity already proves they're about the same
    thing; the text comparison just needs to confirm they're saying
    something similar about it.
    """
    words_a = _important_words(a.claim_text)
    words_b = _important_words(b.claim_text)

    if not words_a or not words_b:
        return 0.0

    intersection = words_a & words_b
    union = words_a | words_b

    # Symmetric Jaccard
    jaccard = len(intersection) / len(union)

    # Containment: how much of the smaller claim is inside the larger?
    smaller = min(len(words_a), len(words_b))
    containment = len(intersection) / smaller if smaller > 0 else 0.0

    # Sequence similarity (catches reordering and paraphrasing)
    seq = SequenceMatcher(None, a.claim_text.lower(), b.claim_text.lower()).ratio()

    # Same-entity bonus: if both claims share a meaningful entity,
    # we need less textual overlap to call them duplicates because
    # the entity already establishes shared subject.
    same_entity_bonus = 0.0
    if a.entity is not None and b.entity is not None and a.entity == b.entity:
        same_entity_bonus = 0.15

    # Weighted combination, with containment given strong weight
    # because it catches the long-vs-short claim case.
    score = 0.4 * jaccard + 0.4 * containment + 0.2 * seq + same_entity_bonus
    return min(score, 1.0)


def _pick_best(claims: list[Claim]) -> Claim:
    """
    From a group of similar claims, pick the best one to keep.
    Preference order:
      1. Higher strength rank (strong > soft > opinion)
      2. Longer claim_text (more specific)
      3. Longer reason_given (more substantive reasoning)
    """
    def score(c: Claim) -> tuple[int, int, int]:
        return (
            STRENGTH_RANK.get(c.strength, 0),
            len(c.claim_text),
            len(c.reason_given),
        )

    return max(claims, key=score)


def deduplicate_within_video(claims: list[Claim]) -> list[Claim]:
    """
    Remove duplicate claims within a single video's extraction batch.

    Algorithm:
      1. Group claims by canonical entity (None grouped separately)
      2. Within each group, cluster claims whose pairwise similarity
         exceeds SIMILARITY_THRESHOLD
      3. From each cluster, keep only the best claim (see _pick_best)

    Claims with entity=None (strategy/overview claims) are also
    dedup'd against each other using text similarity alone, since
    they share no entity grouping.
    """
    if len(claims) <= 1:
        return claims

    # Group by entity (None gets its own bucket)
    groups: dict[str | None, list[Claim]] = {}
    for c in claims:
        groups.setdefault(c.entity, []).append(c)

    kept: list[Claim] = []
    removed_count = 0

    for entity, group in groups.items():
        if len(group) == 1:
            kept.append(group[0])
            continue

        # Cluster within this entity group
        clusters: list[list[Claim]] = []
        assigned: set[int] = set()

        for i, claim in enumerate(group):
            if i in assigned:
                continue
            cluster = [claim]
            assigned.add(i)
            for j in range(i + 1, len(group)):
                if j in assigned:
                    continue
                if _claim_similarity(claim, group[j]) >= SIMILARITY_THRESHOLD:
                    cluster.append(group[j])
                    assigned.add(j)
            clusters.append(cluster)

        for cluster in clusters:
            best = _pick_best(cluster)
            if len(cluster) > 1:
                removed_count += len(cluster) - 1
                logger.debug(
                    f"Merged {len(cluster)} similar claims (entity={entity}); "
                    f"kept strength={best.strength}, len={len(best.claim_text)}"
                )
            kept.append(best)

    if removed_count > 0:
        logger.info(
            f"Dedup: {len(claims)} → {len(kept)} claims "
            f"({removed_count} duplicates removed)"
        )

    return kept


# =====================================================================
# TARGET DATE COMPUTATION
# =====================================================================
# v2 prompt added `target_horizon_months` — use it instead of bucket
# defaults. This preserves the expert's actual stated horizon.
#
# Fallback to bucket median only if the field is missing or null.
# =====================================================================

BUCKET_MEDIAN_MONTHS = {
    "short": 6,
    "medium": 24,
    "long": 60,
}


def compute_target_date(
    claimed_at: datetime,
    timeframe: str,
    target_horizon_months: int | None,
) -> datetime | None:
    """
    Compute the validation target date for a claim.

    Uses target_horizon_months from the LLM if provided (preferred).
    Falls back to bucket median if missing.
    Returns None for unfalsifiable claims (no timeframe + no horizon).
    """
    months: int | None = None

    if target_horizon_months is not None and target_horizon_months > 0:
        months = int(target_horizon_months)
    elif timeframe in BUCKET_MEDIAN_MONTHS:
        months = BUCKET_MEDIAN_MONTHS[timeframe]

    if months is None:
        return None

    # Approximate months as 30.44 days (average) for consistent arithmetic
    days = int(months * 30.44)
    return claimed_at + timedelta(days=days)


# =====================================================================
# MAIN ENTRY POINT
# =====================================================================


def _parse_claim(raw: dict[str, Any]) -> Claim | None:
    """
    Convert one raw dict from Claude's JSON output to a Claim object.
    Returns None if the dict is missing required fields.
    """
    # claim_extraction_v2 emits the category as "type"; v3 renames it to
    # "claim_type" to match the DB column. Accept both so switching prompt
    # versions never requires a lockstep code change.
    raw_type = raw.get("claim_type", raw.get("type"))

    required = ["claim_text", "geography", "timeframe", "strength"]
    missing = [f for f in required if f not in raw or raw[f] is None]
    if raw_type is None:
        missing.append("claim_type/type")
    if missing:
        logger.warning(f"Skipping claim missing fields {missing}: {raw}")
        return None

    horizon = raw.get("target_horizon_months")
    if horizon is not None:
        try:
            horizon = int(horizon)
        except (TypeError, ValueError):
            horizon = None

    return Claim(
        claim_text=str(raw["claim_text"]).strip(),
        claim_type=str(raw_type).strip().lower(),
        geography=str(raw["geography"]).strip().lower(),
        timeframe=str(raw["timeframe"]).strip().lower(),
        strength=str(raw["strength"]).strip().lower(),
        direction=str(raw.get("direction", "na")).strip().lower(),
        reason_given=str(raw.get("reason_given", "")).strip(),
        entity=normalize_entity(raw.get("entity")),
        target_horizon_months=horizon,
        raw=raw,
    )


def normalize_claims(
    raw_claims: list[dict[str, Any]],
    claimed_at: datetime,
) -> list[dict[str, Any]]:
    """
    Full normalization pipeline.

    Input: list of raw claim dicts from Claude's JSON response.
    Output: list of cleaned dicts ready to insert into the claims table.

    Pipeline:
      1. Parse each raw dict → Claim object (drops malformed entries)
      2. Normalize entity names (canonical aliasing)
      3. Deduplicate within video
      4. Compute target_date from target_horizon_months
      5. Convert back to dicts in DB insert shape

    Args:
        raw_claims: Claude's JSON output for one content_piece
        claimed_at: the publish date of the source content_piece
                    (used as the timestamp for the claim and base
                    for target_date computation)

    Returns:
        List of dicts with these keys ready for DB insert:
            claim_text, claim_type, geography, timeframe, strength,
            entity, direction, reason_given, target_date,
            target_horizon_months (None if not set)
    """
    if claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=timezone.utc)

    # Step 1 — parse
    parsed: list[Claim] = []
    for raw in raw_claims:
        claim = _parse_claim(raw)
        if claim is not None:
            parsed.append(claim)

    if not parsed:
        return []

    initial_count = len(parsed)

    # Step 2 — normalization already applied during _parse_claim via
    # normalize_entity call. Nothing more to do here.

    # Step 3 — within-video dedup
    deduped = deduplicate_within_video(parsed)

    # Step 4 + 5 — compute target_date and emit DB-ready dicts
    result: list[dict[str, Any]] = []
    for c in deduped:
        target_date = compute_target_date(
            claimed_at, c.timeframe, c.target_horizon_months
        )
        result.append({
            "claim_text": c.claim_text,
            "claim_type": c.claim_type,
            "geography": c.geography,
            "timeframe": c.timeframe,
            "strength": c.strength,
            "entity": c.entity,
            "direction": c.direction,
            "reason_given": c.reason_given,
            "target_date": target_date.date().isoformat() if target_date else None,
            "target_horizon_months": c.target_horizon_months,
        })

    logger.info(
        f"Normalization: {len(raw_claims)} raw → {initial_count} parsed → "
        f"{len(result)} after dedup"
    )

    return result


# =====================================================================
# STRATEGY NORMALIZATION (Track B)
# =====================================================================
# Mirrors the claims pipeline above but kept self-contained (own dataclass,
# own dedup) rather than sharing code with it — the grouping key and
# required fields differ (entity_class vs entity, strategy_text vs
# claim_text, no timeframe/target_date computation for strategies).
#
# entity_class reuses normalize_entity() directly: the same canonical
# naming rules apply to a strategy's applicable sector/asset-class/
# instrument as to a claim's entity (technical_execution_plan_v3 §6).
# =====================================================================

STRATEGY_SIMILARITY_THRESHOLD = 0.50


@dataclass
class Strategy:
    """In-memory strategy representation matching the prompt's JSON output."""

    strategy_text: str
    strategy_type: str
    trigger_conditions: dict[str, Any] | None = None
    entity_class: str | None = None
    allocation_rule: str | None = None
    position_sizing: str | None = None
    entry_methodology: str | None = None
    holding_horizon_months: int | None = None
    exit_conditions: str | None = None
    risk_framework: str | None = None
    reason_given: str = ""
    linked_claim_entity: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_strategy(raw: dict[str, Any]) -> Strategy | None:
    """
    Convert one raw dict from Claude's strategy JSON output to a Strategy
    object. Returns None if required fields are missing.
    """
    required = ["strategy_text", "strategy_type"]
    missing = [f for f in required if f not in raw or not raw[f]]
    if missing:
        logger.warning(f"Skipping strategy missing fields {missing}: {raw}")
        return None

    horizon = raw.get("holding_horizon_months")
    if horizon is not None:
        try:
            horizon = int(horizon)
        except (TypeError, ValueError):
            horizon = None
        else:
            if horizon <= 0:
                logger.warning(
                    f"Dropping non-positive holding_horizon_months={horizon}: {raw}"
                )
                horizon = None

    return Strategy(
        strategy_text=str(raw["strategy_text"]).strip(),
        strategy_type=str(raw["strategy_type"]).strip().lower(),
        trigger_conditions=raw.get("trigger_conditions"),
        entity_class=normalize_entity(raw.get("entity_class")),
        allocation_rule=raw.get("allocation_rule"),
        position_sizing=raw.get("position_sizing"),
        entry_methodology=raw.get("entry_methodology"),
        holding_horizon_months=horizon,
        exit_conditions=raw.get("exit_conditions"),
        risk_framework=raw.get("risk_framework"),
        reason_given=str(raw.get("reason_given", "")).strip(),
        linked_claim_entity=normalize_entity(raw.get("linked_claim_entity")),
        raw=raw,
    )


def _strategy_similarity(a: Strategy, b: Strategy) -> float:
    """Same three-signal approach as _claim_similarity, applied to strategy_text."""
    words_a = _important_words(a.strategy_text)
    words_b = _important_words(b.strategy_text)

    if not words_a or not words_b:
        return 0.0

    intersection = words_a & words_b
    union = words_a | words_b
    jaccard = len(intersection) / len(union)

    smaller = min(len(words_a), len(words_b))
    containment = len(intersection) / smaller if smaller > 0 else 0.0

    seq = SequenceMatcher(None, a.strategy_text.lower(), b.strategy_text.lower()).ratio()

    same_entity_bonus = 0.0
    if (
        a.entity_class is not None
        and b.entity_class is not None
        and a.entity_class == b.entity_class
    ):
        same_entity_bonus = 0.15

    score = 0.4 * jaccard + 0.4 * containment + 0.2 * seq + same_entity_bonus
    return min(score, 1.0)


def _pick_best_strategy(strategies: list[Strategy]) -> Strategy:
    """
    From a group of similar strategies, keep the most complete one:
    more populated fields first, then longer strategy_text.
    """
    def score(s: Strategy) -> tuple[int, int]:
        populated = sum(
            1
            for v in (
                s.trigger_conditions,
                s.allocation_rule,
                s.position_sizing,
                s.entry_methodology,
                s.holding_horizon_months,
                s.exit_conditions,
                s.risk_framework,
            )
            if v
        )
        return (populated, len(s.strategy_text))

    return max(strategies, key=score)


def deduplicate_strategies_within_video(strategies: list[Strategy]) -> list[Strategy]:
    """
    Remove duplicate strategies within a single content piece's extraction
    batch. Same grouping + clustering algorithm as deduplicate_within_video,
    grouped by entity_class instead of entity.
    """
    if len(strategies) <= 1:
        return strategies

    groups: dict[str | None, list[Strategy]] = {}
    for s in strategies:
        groups.setdefault(s.entity_class, []).append(s)

    kept: list[Strategy] = []
    removed_count = 0

    for entity_class, group in groups.items():
        if len(group) == 1:
            kept.append(group[0])
            continue

        clusters: list[list[Strategy]] = []
        assigned: set[int] = set()

        for i, strategy in enumerate(group):
            if i in assigned:
                continue
            cluster = [strategy]
            assigned.add(i)
            for j in range(i + 1, len(group)):
                if j in assigned:
                    continue
                if _strategy_similarity(strategy, group[j]) >= STRATEGY_SIMILARITY_THRESHOLD:
                    cluster.append(group[j])
                    assigned.add(j)
            clusters.append(cluster)

        for cluster in clusters:
            best = _pick_best_strategy(cluster)
            if len(cluster) > 1:
                removed_count += len(cluster) - 1
                logger.debug(
                    f"Merged {len(cluster)} similar strategies (entity_class={entity_class}); "
                    f"kept len={len(best.strategy_text)}"
                )
            kept.append(best)

    if removed_count > 0:
        logger.info(
            f"Strategy dedup: {len(strategies)} → {len(kept)} strategies "
            f"({removed_count} duplicates removed)"
        )

    return kept


def normalize_strategies(raw_strategies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Full normalization pipeline for Track B (strategy) extraction output.

    Input: list of raw strategy dicts from Claude's JSON response.
    Output: list of cleaned dicts ready to insert into the strategies table.

    Pipeline:
      1. Parse each raw dict → Strategy object (drops malformed entries,
         normalizes entity_class/linked_claim_entity via normalize_entity)
      2. Deduplicate within the content piece
      3. Emit DB-ready dicts

    linked_claim_entity is passed through as a canonical entity name, not
    resolved to an actual claim UUID here — that requires a DB lookup
    against this content piece's already-persisted claims, which this
    (DB-free) module doesn't perform. strategy_extractor.py resolves it.
    """
    parsed: list[Strategy] = []
    for raw in raw_strategies:
        strategy = _parse_strategy(raw)
        if strategy is not None:
            parsed.append(strategy)

    if not parsed:
        return []

    initial_count = len(parsed)
    deduped = deduplicate_strategies_within_video(parsed)

    result: list[dict[str, Any]] = [
        {
            "strategy_text": s.strategy_text,
            "strategy_type": s.strategy_type,
            "trigger_conditions": s.trigger_conditions,
            "entity_class": s.entity_class,
            "allocation_rule": s.allocation_rule,
            "position_sizing": s.position_sizing,
            "entry_methodology": s.entry_methodology,
            "holding_horizon_months": s.holding_horizon_months,
            "exit_conditions": s.exit_conditions,
            "risk_framework": s.risk_framework,
            "reason_given": s.reason_given,
            "linked_claim_entity": s.linked_claim_entity,
        }
        for s in deduped
    ]

    logger.info(
        f"Strategy normalization: {len(raw_strategies)} raw → {initial_count} parsed → "
        f"{len(result)} after dedup"
    )

    return result
