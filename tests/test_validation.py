"""
Tests for the validation layer: entity resolution, claim outcome
validation, and credibility scoring.

All market data is mocked (monkeypatch price_on / price_series /
fetch_latest_market_price / ensure_symbol_backfilled) — no network or
Supabase calls in this test module.
"""

from __future__ import annotations

import pendulum
import pytest

from src.internal import scoring
from src.validation import market_data, validator
from src.validation.entity_symbol_map import UNMAPPABLE, resolve_entity


def make_claim(**overrides):
    base = {
        "id": "claim-1",
        "entity": "AAPL",
        "geography": "us",
        "strength": "strong",
        "target_horizon_months": 5,
        "direction": "bullish",
        "claim_text": "Apple will rally strongly on services growth.",
        "claimed_at": "2024-01-01",
        "target_date": "2024-06-01",
    }
    base.update(overrides)
    return base


def _mock_prices(monkeypatch, price_at_claim, price_at_target, series=None):
    """Patch price_on/price_series as validator.py imports them."""

    def fake_price_on(symbol, target_date, max_lookback_days=7):
        return {"2024-01-01": price_at_claim, "2024-06-01": price_at_target}.get(target_date)

    def fake_price_series(symbol, start, end):
        return series or [
            (pendulum.parse("2024-01-01").date(), price_at_claim),
            (pendulum.parse("2024-06-01").date(), price_at_target),
        ]

    monkeypatch.setattr(validator, "price_on", fake_price_on)
    monkeypatch.setattr(validator, "price_series", fake_price_series)
    # Default: pretend every symbol is already backfilled, so tests don't
    # silently hit the real Supabase/yfinance on-demand path.
    monkeypatch.setattr(validator, "ensure_symbol_backfilled", lambda symbol, fetch_symbol: True)


# =====================================================================
# Directional validation
# =====================================================================


class TestDirectionalValidation:
    def test_bullish_correct(self, monkeypatch):
        _mock_prices(monkeypatch, 100.0, 110.0)
        result = validator.validate_one_claim(make_claim(direction="bullish"))
        assert result["verification_status"] == "correct"
        assert result["verification_outcome"]["return_pct"] == pytest.approx(10.0)

    def test_bullish_incorrect(self, monkeypatch):
        _mock_prices(monkeypatch, 100.0, 95.0)
        result = validator.validate_one_claim(make_claim(direction="bullish"))
        assert result["verification_status"] == "incorrect"

    def test_bullish_partial_band(self, monkeypatch):
        # +1.9% return: not > 2% (correct threshold), but within the 2% partial band.
        _mock_prices(monkeypatch, 100.0, 101.9)
        result = validator.validate_one_claim(make_claim(direction="bullish"))
        assert result["verification_status"] == "partial"

    def test_bearish_correct(self, monkeypatch):
        _mock_prices(monkeypatch, 100.0, 90.0)
        result = validator.validate_one_claim(make_claim(direction="bearish"))
        assert result["verification_status"] == "correct"

    def test_neutral_within_band(self, monkeypatch):
        _mock_prices(monkeypatch, 100.0, 103.0)
        result = validator.validate_one_claim(make_claim(direction="neutral"))
        assert result["verification_status"] == "correct"

    def test_neutral_partial_band(self, monkeypatch):
        _mock_prices(monkeypatch, 100.0, 107.0)
        result = validator.validate_one_claim(make_claim(direction="neutral"))
        assert result["verification_status"] == "partial"

    def test_neutral_incorrect(self, monkeypatch):
        _mock_prices(monkeypatch, 100.0, 120.0)
        result = validator.validate_one_claim(make_claim(direction="neutral"))
        assert result["verification_status"] == "incorrect"


# =====================================================================
# Price-target detection (regex)
# =====================================================================


class TestPriceTargetDetection:
    def test_target_of_rupee_symbol(self):
        assert validator.detect_price_target(
            "Reliance could hit a target of ₹3,200 by next year"
        ) == 3200.0

    def test_rs_target(self):
        assert validator.detect_price_target("Analysts see Rs 900 target for the stock") == 900.0

    def test_dollar_target(self):
        assert validator.detect_price_target("$250 target for Tesla shares") == 250.0

    def test_non_target_claim_returns_none(self):
        assert validator.detect_price_target("The stock looks strong going forward") is None

    def test_macro_currency_amount_is_not_a_price_target(self):
        # Regression: "₹1.1 lakh crore" is a liquidity injection amount, not
        # a price target of 1.1. Must not match just because it starts with
        # a currency symbol followed by a number.
        assert (
            validator.detect_price_target(
                "RBI's planned injection of ₹1.1 lakh crore of liquidity into "
                "the banking system will trigger a sharp rebound."
            )
            is None
        )

    def test_bare_currency_number_without_target_word_is_not_a_target(self):
        # A currency-prefixed number alone, with no "target" nearby, is
        # ambiguous (could be a price mention, a valuation, anything) — only
        # count it when "target" actually appears near it.
        assert validator.detect_price_target("The stock is currently trading at ₹450.") is None


class TestPriceTargetEvaluation:
    def test_touched_target(self):
        series = [
            (pendulum.parse("2024-01-01").date(), 100.0),
            (pendulum.parse("2024-03-01").date(), 130.0),
            (pendulum.parse("2024-06-01").date(), 125.0),
        ]
        result, extreme, pct = validator.evaluate_price_target(series, 120.0, "bullish")
        assert result == "correct"
        assert extreme == 130.0

    def test_within_5_percent(self):
        series = [
            (pendulum.parse("2024-01-01").date(), 90.0),
            (pendulum.parse("2024-06-01").date(), 96.0),
        ]
        result, extreme, pct = validator.evaluate_price_target(series, 100.0, "bullish")
        assert result == "partial"

    def test_missed_target(self):
        series = [
            (pendulum.parse("2024-01-01").date(), 100.0),
            (pendulum.parse("2024-06-01").date(), 105.0),
        ]
        result, extreme, pct = validator.evaluate_price_target(series, 200.0, "bullish")
        assert result == "incorrect"

    def test_price_target_claim_end_to_end(self, monkeypatch):
        series = [
            (pendulum.parse("2024-01-01").date(), 900.0),
            (pendulum.parse("2024-03-15").date(), 3250.0),
            (pendulum.parse("2024-06-01").date(), 3100.0),
        ]
        _mock_prices(monkeypatch, 900.0, 3100.0, series=series)
        claim = make_claim(
            claim_text="Reliance could hit a target of ₹3,200 by next year",
            direction="bullish",
        )
        result = validator.validate_one_claim(claim)
        assert result["verification_status"] == "correct"
        assert result["verification_outcome"]["method"] == "price_target"
        assert result["verification_outcome"]["detected_target"] == 3200.0


# =====================================================================
# Opinion-only and data-unavailable dispatch
# =====================================================================


class TestNonTestableClaims:
    def test_opinion_strength(self, monkeypatch):
        result = validator.validate_one_claim(make_claim(strength="opinion"))
        assert result["verification_status"] == "opinion_only"

    def test_missing_horizon(self, monkeypatch):
        result = validator.validate_one_claim(make_claim(target_horizon_months=None))
        assert result["verification_status"] == "opinion_only"

    def test_direction_na(self, monkeypatch):
        result = validator.validate_one_claim(make_claim(direction="na"))
        assert result["verification_status"] == "opinion_only"

    def test_unresolvable_entity(self):
        result = validator.validate_one_claim(
            make_claim(entity="global_liquidity", geography="global")
        )
        assert result["verification_status"] == "data_unavailable"

    def test_missing_price_data(self, monkeypatch):
        def fake_price_on(symbol, target_date, max_lookback_days=7):
            return None

        monkeypatch.setattr(validator, "price_on", fake_price_on)
        monkeypatch.setattr(validator, "ensure_symbol_backfilled", lambda symbol, fetch_symbol: True)
        result = validator.validate_one_claim(make_claim())
        assert result["verification_status"] == "data_unavailable"

    def test_price_lookup_uses_canonical_entity_not_fetch_symbol(self, monkeypatch):
        # Regression: market_data rows are keyed by our canonical entity name
        # (e.g. 'nifty50'), not by the external fetch_symbol resolve_entity
        # returns (e.g. '^NSEI'). Looking up by fetch_symbol silently finds
        # nothing and every such claim resolves to data_unavailable.
        captured_symbols = []

        def fake_price_on(symbol, target_date, max_lookback_days=7):
            captured_symbols.append(symbol)
            return {"2024-01-01": 100.0, "2024-06-01": 110.0}.get(target_date)

        def fake_price_series(symbol, start, end):
            captured_symbols.append(symbol)
            return [
                (pendulum.parse("2024-01-01").date(), 100.0),
                (pendulum.parse("2024-06-01").date(), 110.0),
            ]

        monkeypatch.setattr(validator, "price_on", fake_price_on)
        monkeypatch.setattr(validator, "price_series", fake_price_series)
        monkeypatch.setattr(validator, "ensure_symbol_backfilled", lambda symbol, fetch_symbol: True)

        result = validator.validate_one_claim(
            make_claim(entity="nifty50", geography="india", direction="bullish")
        )
        assert result["verification_status"] == "correct"
        assert all(s == "nifty50" for s in captured_symbols)


# =====================================================================
# resolve_entity
# =====================================================================


class TestResolveEntity:
    def test_mapped_entity(self):
        assert resolve_entity("AAPL", "us") == ("AAPL", "yfinance")

    def test_india_ticker_fallback(self):
        assert resolve_entity("SOMENEWSTOCK", "india") == ("SOMENEWSTOCK.NS", "yfinance")

    def test_us_ticker_fallback(self):
        assert resolve_entity("SOMENEWTICKER", "us") == ("SOMENEWTICKER", "yfinance")

    def test_unmappable_member_returns_none(self):
        assert "global_liquidity" in UNMAPPABLE
        assert resolve_entity("global_liquidity", "global") is None

    def test_none_entity_returns_none(self):
        assert resolve_entity(None, "us") is None


# =====================================================================
# On-demand backfill (src/validation/market_data.ensure_symbol_backfilled)
#
# Regression coverage: a fallback-resolved entity (e.g. 'INDIGO' -> 'INDIGO.NS')
# is never touched by backfill_all()'s bulk loop, which only iterates
# ENTITY_SYMBOL_MAP — without this on-demand path such a claim would resolve
# a valid-looking ticker and then find zero market_data rows forever.
# =====================================================================


class TestEnsureSymbolBackfilled:
    def test_noop_when_data_already_exists(self, monkeypatch):
        monkeypatch.setattr(market_data, "symbol_has_market_data", lambda symbol: True)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("backfill_symbol should not be called when data exists")

        monkeypatch.setattr(market_data, "backfill_symbol", fail_if_called)

        assert market_data.ensure_symbol_backfilled("AAPL", "AAPL") is True

    def test_fetches_when_missing_and_succeeds(self, monkeypatch):
        monkeypatch.setattr(market_data, "symbol_has_market_data", lambda symbol: False)
        monkeypatch.setattr(
            market_data, "backfill_symbol", lambda symbol, fetch_symbol, start_date: (500, None)
        )

        assert market_data.ensure_symbol_backfilled("INDIGO", "INDIGO.NS") is True

    def test_returns_false_for_genuinely_bad_ticker(self, monkeypatch):
        # A renamed/delisted ticker (like ZOMATO.NS or TATAMOTORS.NS were)
        # yfinance returns nothing for — this must not be treated as success.
        monkeypatch.setattr(market_data, "symbol_has_market_data", lambda symbol: False)
        monkeypatch.setattr(
            market_data,
            "backfill_symbol",
            lambda symbol, fetch_symbol, start_date: (0, "no data returned"),
        )

        assert market_data.ensure_symbol_backfilled("DELISTEDCO", "DELISTEDCO.NS") is False

    def test_manual_source_claims_never_trigger_backfill(self, monkeypatch):
        # Manual macro entities (rbi_rate, etc.) come from CSVs, not yfinance —
        # validate_one_claim must skip ensure_symbol_backfilled for source='manual'.
        def fail_if_called(*args, **kwargs):
            raise AssertionError("ensure_symbol_backfilled should not run for manual source")

        monkeypatch.setattr(validator, "ensure_symbol_backfilled", fail_if_called)
        monkeypatch.setattr(validator, "price_on", lambda symbol, d, max_lookback_days=7: None)

        result = validator.validate_one_claim(
            make_claim(entity="rbi_rate", geography="india", direction="bearish")
        )
        assert result["verification_status"] == "data_unavailable"

    def test_data_unavailable_when_on_demand_backfill_fails(self, monkeypatch):
        monkeypatch.setattr(validator, "ensure_symbol_backfilled", lambda symbol, fetch_symbol: False)

        result = validator.validate_one_claim(
            make_claim(entity="INDIGO", geography="india", direction="bullish")
        )
        assert result["verification_status"] == "data_unavailable"


# =====================================================================
# Credibility scoring (src/internal/scoring.py)
# =====================================================================


def make_scoring_claim(status, strength="strong", years_ago=0.5):
    return {
        "verification_status": status,
        "strength": strength,
        "claimed_at": pendulum.now("UTC").subtract(days=int(years_ago * 365)).to_iso8601_string(),
        "target_horizon_months": 5,
    }


class TestScoreBucket:
    def test_empty_bucket_defaults_to_50(self):
        score, n = scoring.score_bucket([])
        assert score == 50.0
        assert n == 0

    def test_five_of_five_correct_shrinks_to_about_60(self):
        claims = [make_scoring_claim("correct") for _ in range(5)]
        score, n = scoring.score_bucket(claims)
        assert n == 5
        assert 58 <= score <= 62

    def test_mixed_outcomes(self):
        claims = [
            make_scoring_claim("correct"),
            make_scoring_claim("correct"),
            make_scoring_claim("partial"),
            make_scoring_claim("incorrect"),
        ]
        score, n = scoring.score_bucket(claims)
        # hit_rate = (1+1+0.5+0)/4 = 0.625, n=4, shrink=4/24=0.1667
        # score = 50 + (0.625-0.5)*100*0.1667 ~= 52.08
        assert n == 4
        assert score == pytest.approx(52.08, abs=0.1)

    def test_recency_decay(self):
        # One recent correct claim, one old (>4y) incorrect claim.
        claims = [
            make_scoring_claim("correct", years_ago=0.5),
            make_scoring_claim("incorrect", years_ago=5),
        ]
        score, n = scoring.score_bucket(claims)
        # Without recency decay both weight 1.0 -> hit_rate=0.5 -> score=50.
        # With decay the old incorrect claim counts less -> score pulled above 50.
        assert score > 50.0

    def test_strength_weighting(self):
        # One recent correct strong claim, one recent incorrect soft claim.
        claims = [
            make_scoring_claim("correct", strength="strong", years_ago=0.5),
            make_scoring_claim("incorrect", strength="soft", years_ago=0.5),
        ]
        score, n = scoring.score_bucket(claims)
        # Without strength weighting both weight 1.0 -> hit_rate=0.5 -> score=50.
        # Strong-correct outweighs soft-incorrect -> score pulled above 50.
        assert score > 50.0

    def test_clamping_stays_within_bounds(self):
        all_correct = [make_scoring_claim("correct") for _ in range(500)]
        score, _ = scoring.score_bucket(all_correct)
        assert 0.0 <= score <= 100.0
        assert score > 90  # large n, all correct -> should approach 100

        all_incorrect = [make_scoring_claim("incorrect") for _ in range(500)]
        score_low, _ = scoring.score_bucket(all_incorrect)
        assert 0.0 <= score_low <= 100.0
        assert score_low < 10  # large n, all incorrect -> should approach 0


# =====================================================================
# price_on prior-trading-day fallback
# =====================================================================


class TestPriceOnFallback:
    def test_falls_back_to_prior_trading_day_across_weekend_gap(self, monkeypatch):
        captured = {}

        def fake_fetch(symbol, on_or_before, earliest):
            captured["args"] = (symbol, on_or_before, earliest)
            # Simulate a weekend gap: target_date is Saturday 2024-06-15,
            # DB has no row for it, only for Friday 2024-06-14.
            return {"close_price": "123.45", "snapshot_date": "2024-06-14"}

        monkeypatch.setattr(market_data, "fetch_latest_market_price", fake_fetch)
        price = market_data.price_on("AAPL", "2024-06-15", max_lookback_days=7)

        assert price == 123.45
        symbol, on_or_before, earliest = captured["args"]
        assert symbol == "AAPL"
        assert on_or_before == "2024-06-15"
        assert earliest == "2024-06-08"

    def test_returns_none_when_no_row_found(self, monkeypatch):
        monkeypatch.setattr(market_data, "fetch_latest_market_price", lambda *a, **k: None)
        assert market_data.price_on("AAPL", "2024-06-15") is None
