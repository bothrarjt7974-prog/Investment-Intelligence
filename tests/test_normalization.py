"""
Tests for the claim normalization module.

Each test corresponds to a real issue we found in v1 or v2 data.
Run with: pytest tests/test_normalization.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.extraction.normalization import (
    Claim,
    compute_target_date,
    deduplicate_within_video,
    normalize_claims,
    normalize_entity,
)


# =====================================================================
# ENTITY NORMALIZATION TESTS
# =====================================================================


class TestEntityNormalization:
    """The core casing/aliasing problem from v2 data."""

    def test_nifty_variants_collapse_to_one(self):
        """v2 had NIFTY50, nifty50, nifty as 3 separate entities for same index."""
        assert normalize_entity("NIFTY50") == "nifty50"
        assert normalize_entity("nifty50") == "nifty50"
        assert normalize_entity("nifty") == "nifty50"
        assert normalize_entity("Nifty 50") == "nifty50"

    def test_nasdaq_casing(self):
        assert normalize_entity("NASDAQ") == "nasdaq"
        assert normalize_entity("nasdaq") == "nasdaq"
        assert normalize_entity("Nasdaq") == "nasdaq"

    def test_sp500_variants(self):
        assert normalize_entity("S&P500") == "sp500"
        assert normalize_entity("sp500") == "sp500"
        assert normalize_entity("SPX") == "sp500"

    def test_stock_tickers_uppercase(self):
        assert normalize_entity("reliance") == "RELIANCE"
        assert normalize_entity("RELIANCE") == "RELIANCE"
        assert normalize_entity("aapl") == "AAPL"
        assert normalize_entity("Tcs") == "TCS"

    def test_ticker_with_exchange_suffix(self):
        assert normalize_entity("RELIANCE.NS") == "RELIANCE"
        assert normalize_entity("TCS.NS") == "TCS"

    def test_btc_variants(self):
        assert normalize_entity("BTC") == "BTC"
        assert normalize_entity("BTC_USD") == "BTC"
        assert normalize_entity("Bitcoin") == "BTC"
        assert normalize_entity("BITCOIN") == "BTC"

    def test_commodities_stay_lowercase(self):
        """gold and silver look like tickers but aren't — explicit lowercase."""
        assert normalize_entity("gold") == "gold"
        assert normalize_entity("GOLD") == "gold"
        assert normalize_entity("Gold") == "gold"
        assert normalize_entity("silver") == "silver"
        assert normalize_entity("SILVER") == "silver"

    def test_sector_lowercase_with_underscores(self):
        assert normalize_entity("indian_IT") == "indian_it"
        assert normalize_entity("indian_it") == "indian_it"
        assert normalize_entity("indian_technology") == "indian_it"
        assert normalize_entity("indian_tech") == "indian_it"

    def test_real_estate_variants(self):
        """v2 data had indian_realestate, indian_real_estate as separate."""
        assert normalize_entity("indian_realestate") == "indian_realestate"
        assert normalize_entity("indian_real_estate") == "indian_realestate"
        assert normalize_entity("india_real_estate") == "indian_realestate"

    def test_macro_factors(self):
        assert normalize_entity("INR") == "inr_usd"
        assert normalize_entity("inr") == "inr_usd"
        assert normalize_entity("INR_USD") == "inr_usd"
        assert normalize_entity("USD_INR") == "inr_usd"
        assert normalize_entity("inr_usd") == "inr_usd"

    def test_oil_variants(self):
        assert normalize_entity("oil_price") == "crude_oil"
        assert normalize_entity("crude_oil") == "crude_oil"
        assert normalize_entity("WTI") == "crude_oil"

    def test_rbi_rate_variants(self):
        assert normalize_entity("rbi_rate") == "rbi_rate"
        assert normalize_entity("repo_rate_india") == "rbi_rate"
        assert normalize_entity("rbi_rates") == "rbi_rate"

    def test_inflation_variants(self):
        assert normalize_entity("india_cpi") == "india_inflation"
        assert normalize_entity("indian_cpi") == "india_inflation"
        assert normalize_entity("india_inflation") == "india_inflation"
        assert normalize_entity("indian_inflation") == "india_inflation"

    def test_none_and_nan_handling(self):
        assert normalize_entity(None) is None
        assert normalize_entity("") is None
        assert normalize_entity("  ") is None
        assert normalize_entity("nan") is None
        assert normalize_entity("NaN") is None
        assert normalize_entity(float("nan")) is None

    def test_unknown_entity_passes_through_lowercased(self):
        """Unknown but valid-looking entity should pass through normalized."""
        # Underscore concept → lowercased
        assert normalize_entity("indian_household_income") == "indian_household_income"
        # Unknown ticker-like → uppercased
        assert normalize_entity("xyz") == "XYZ"


# =====================================================================
# WITHIN-VIDEO DEDUPLICATION TESTS
# =====================================================================


def _make_claim(text: str, entity: str | None, strength: str = "soft",
                claim_type: str = "stock_pick", reason: str = "test reason") -> Claim:
    """Helper to construct a Claim for tests."""
    return Claim(
        claim_text=text,
        claim_type=claim_type,
        geography="india",
        timeframe="medium",
        strength=strength,
        direction="bullish",
        reason_given=reason,
        entity=entity,
        target_horizon_months=12,
    )


class TestDedup:
    """Reproduce the exact duplicate patterns we saw in v2 video 7056b9c4."""

    def test_no_dedup_for_unique_claims(self):
        claims = [
            _make_claim("Reliance is a buy", "RELIANCE"),
            _make_claim("TCS is a buy", "TCS"),
            _make_claim("Gold will rise", "gold"),
        ]
        result = deduplicate_within_video(claims)
        assert len(result) == 3

    def test_dedup_two_claims_same_entity_similar_text(self):
        """The TCS duplicate from video 7056b9c4."""
        a = _make_claim(
            "TCS is a value buy currently, having delivered zero price "
            "appreciation over the last four years despite stable revenues.",
            "TCS", strength="soft"
        )
        b = _make_claim(
            "TCS is a value buy at current prices because the stock has not "
            "moved in four years despite stable revenues and profits.",
            "TCS", strength="strong"
        )
        result = deduplicate_within_video([a, b])
        assert len(result) == 1
        # Should keep the stronger one
        assert result[0].strength == "strong"

    def test_dedup_keeps_unique_when_same_entity_different_topic(self):
        """Same entity but genuinely different claim shouldn't be deduped."""
        a = _make_claim(
            "Reliance Jio will hit 600 million subscribers by 2027",
            "RELIANCE"
        )
        b = _make_claim(
            "Reliance is overvalued at current PE of 30x",
            "RELIANCE"
        )
        result = deduplicate_within_video([a, b])
        assert len(result) == 2

    def test_dedup_strategy_claims_without_entity(self):
        """Strategy claims with entity=None should still dedup by text."""
        a = _make_claim(
            "Bitcoin should constitute 5-10% of a portfolio for medium-risk investors",
            None, claim_type="strategy"
        )
        b = _make_claim(
            "Bitcoin should be 5-10% of portfolio as an asymmetric bet for medium-risk investors",
            None, claim_type="strategy"
        )
        result = deduplicate_within_video([a, b])
        assert len(result) == 1

    def test_dedup_prefers_strong_over_soft(self):
        a = _make_claim("US tech will deliver 14-15% CAGR", "us_tech", strength="soft")
        b = _make_claim("US tech will deliver 14-15% CAGR in dollar terms", "us_tech", strength="strong")
        result = deduplicate_within_video([a, b])
        assert len(result) == 1
        assert result[0].strength == "strong"

    def test_dedup_prefers_longer_text_within_same_strength(self):
        a = _make_claim("INR will weaken", "inr_usd", strength="strong")
        b = _make_claim(
            "INR will weaken substantially against USD over next 6 months due to oil import pressure",
            "inr_usd", strength="strong"
        )
        result = deduplicate_within_video([a, b])
        assert len(result) == 1
        assert "oil import" in result[0].claim_text

    def test_dedup_handles_six_duplicates_from_v2_video(self):
        """Reproduce the actual 6-duplicate-pairs-in-one-video case."""
        claims = [
            # ASIANPAINT pair
            _make_claim("Asian Paints offers 41% upside, broken below historical channel",
                       "ASIANPAINT", strength="soft"),
            _make_claim("Asian Paints, 41% below historical channel, is a capital-rotation target",
                       "ASIANPAINT", strength="strong"),
            # TCS pair
            _make_claim("TCS is a value buy, zero appreciation in 4 years",
                       "TCS", strength="soft"),
            _make_claim("TCS is a value buy at current prices, stock flat for 4 years",
                       "TCS", strength="strong"),
            # BTC strategy pair (no entity)
            _make_claim("Bitcoin should be 5-10% of medium-risk portfolio",
                       None, strength="strong", claim_type="strategy"),
            _make_claim("Bitcoin (BTC) should constitute 5-10% of a portfolio for medium-risk investors",
                       None, strength="soft", claim_type="strategy"),
            # Unique claim (should survive)
            _make_claim("Indian bonds yield 6-7% post-tax, poor vs 10-12% inflation",
                       "india_inflation"),
        ]
        result = deduplicate_within_video(claims)
        # 7 claims → should become 4 (3 pairs dedup'd, 1 unique)
        assert len(result) == 4


# =====================================================================
# TARGET DATE COMPUTATION TESTS
# =====================================================================


class TestTargetDate:
    """The v2 code bug was ignoring target_horizon_months entirely."""

    def test_uses_explicit_horizon_when_provided(self):
        claimed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = compute_target_date(claimed_at, "short", target_horizon_months=6)
        # 6 months ≈ 183 days from Jan 1
        assert result is not None
        days_diff = (result - claimed_at).days
        assert 180 <= days_diff <= 185

    def test_horizon_overrides_bucket_when_both_present(self):
        """If LLM gives horizon=6 but bucket=long, use horizon=6."""
        claimed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = compute_target_date(claimed_at, "long", target_horizon_months=6)
        days_diff = (result - claimed_at).days
        assert days_diff < 200  # ~6 months, not 3 years

    def test_falls_back_to_bucket_when_horizon_missing(self):
        """If horizon is None, use bucket median."""
        claimed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = compute_target_date(claimed_at, "medium", target_horizon_months=None)
        # medium = 24 months ≈ 730 days
        days_diff = (result - claimed_at).days
        assert 725 <= days_diff <= 735

    def test_returns_none_for_unfalsifiable(self):
        claimed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = compute_target_date(claimed_at, "n/a", target_horizon_months=None)
        assert result is None

    def test_horizon_zero_treated_as_missing(self):
        claimed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = compute_target_date(claimed_at, "short", target_horizon_months=0)
        # Should fall back to bucket
        days_diff = (result - claimed_at).days
        assert 180 <= days_diff <= 185


# =====================================================================
# END-TO-END PIPELINE TESTS
# =====================================================================


class TestFullPipeline:
    """End-to-end: raw LLM output → cleaned DB-ready dicts."""

    def test_pipeline_normalizes_and_dedups(self):
        raw = [
            {
                "claim_text": "TCS is a value buy currently",
                "type": "stock_pick",
                "geography": "india",
                "timeframe": "medium",
                "target_horizon_months": 18,
                "strength": "soft",
                "entity": "tcs",  # lowercase should become uppercase
                "direction": "bullish",
                "reason_given": "Flat for 4 years",
            },
            {
                "claim_text": "TCS is a value buy at current prices",
                "type": "stock_pick",
                "geography": "india",
                "timeframe": "medium",
                "target_horizon_months": 18,
                "strength": "strong",
                "entity": "TCS",
                "direction": "bullish",
                "reason_given": "Stock flat 4 years",
            },
            {
                "claim_text": "Reliance is overvalued",
                "type": "stock_pick",
                "geography": "india",
                "timeframe": "medium",
                "target_horizon_months": 12,
                "strength": "strong",
                "entity": "RELIANCE.NS",  # should strip .NS suffix
                "direction": "bearish",
                "reason_given": "High PE",
            },
        ]
        claimed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = normalize_claims(raw, claimed_at)

        # Two TCS claims dedup → one. Plus Reliance. Total = 2.
        assert len(result) == 2
        entities = sorted(c["entity"] for c in result)
        assert entities == ["RELIANCE", "TCS"]

        # All target_dates should be populated
        for c in result:
            assert c["target_date"] is not None

    def test_pipeline_skips_malformed_claims(self):
        raw = [
            {  # missing required fields
                "claim_text": "Some claim",
                "type": "macro",
                # missing geography, timeframe, strength
            },
            {
                "claim_text": "Good claim",
                "type": "stock_pick",
                "geography": "us",
                "timeframe": "long",
                "strength": "strong",
                "entity": "AAPL",
                "direction": "bullish",
                "reason_given": "Strong moat",
                "target_horizon_months": 36,
            },
        ]
        claimed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = normalize_claims(raw, claimed_at)
        assert len(result) == 1
        assert result[0]["entity"] == "AAPL"

    def test_pipeline_handles_empty_input(self):
        claimed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert normalize_claims([], claimed_at) == []

    def test_pipeline_accepts_v3_claim_type_field(self):
        # claim_extraction_v3 renames the output field "type" -> "claim_type"
        # to match the DB column; the parser must accept both so prompt
        # versions can switch without a lockstep code change.
        raw = [{
            "claim_text": "Nifty will rally 10% in 6 months",
            "claim_type": "macro",
            "geography": "india",
            "timeframe": "short",
            "strength": "strong",
            "entity": "nifty50",
            "direction": "bullish",
            "reason_given": "Liquidity injection",
            "target_horizon_months": 6,
        }]
        claimed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = normalize_claims(raw, claimed_at)
        assert len(result) == 1
        assert result[0]["claim_type"] == "macro"

    def test_pipeline_preserves_all_required_db_fields(self):
        raw = [{
            "claim_text": "Test claim",
            "type": "macro",
            "geography": "india",
            "timeframe": "short",
            "strength": "strong",
            "entity": "inr_usd",
            "direction": "bearish",
            "reason_given": "Some reasoning",
            "target_horizon_months": 6,
        }]
        claimed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = normalize_claims(raw, claimed_at)
        assert len(result) == 1
        row = result[0]
        # All keys the DB insert expects
        expected_keys = {
            "claim_text", "claim_type", "geography", "timeframe",
            "strength", "entity", "direction", "reason_given",
            "target_date", "target_horizon_months",
        }
        assert set(row.keys()) == expected_keys
