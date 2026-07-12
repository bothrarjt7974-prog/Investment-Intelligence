"""
Tests for "is there enough behind this to act on?" readiness assessment
(2026-07-13 — surfaces Layer 8 strategy-matching thinness in plain terms,
per theme, in the product itself).
"""

from __future__ import annotations

from src.guidance.readiness import assess_theme_readiness


def make_theme(**overrides):
    base = {
        "supporting_experts": [{"expert_name": "A"}, {"expert_name": "B"}, {"expert_name": "C"}],
        "credibility_weighted_score": 60.0,
        "fundamentals_verdict": "pass",
        "instruments_mentioned": [],
    }
    base.update(overrides)
    return base


def make_strategy(**overrides):
    base = {"id": "s1", "entity_class": "indian_banking", "backtest_status": "complete"}
    base.update(overrides)
    return base


class TestAssessThemeReadiness:
    def test_fully_ready_theme(self):
        result = assess_theme_readiness(
            make_theme(instruments_mentioned=["HDFCBANK"]),
            top_match={"strategy_id": "s1"},
            strategy=make_strategy(),
        )
        assert result["overall"]["level"] == "good"
        assert all(c["level"] == "good" for c in result["checklist"] if c["label"] in
                   ("A real way to invest exists", "Named stocks/funds available"))

    def test_no_strategy_matched_is_poor(self):
        result = assess_theme_readiness(make_theme(), top_match=None, strategy=None)
        assert result["overall"]["level"] == "poor"
        strategy_check = next(c for c in result["checklist"] if c["label"] == "A real way to invest exists")
        assert strategy_check["level"] == "poor"

    def test_generic_broad_equity_fallback_is_fair_not_poor(self):
        result = assess_theme_readiness(
            make_theme(),
            top_match={"strategy_id": "s1"},
            strategy=make_strategy(entity_class="broad_equity"),
        )
        strategy_check = next(c for c in result["checklist"] if c["label"] == "A real way to invest exists")
        assert strategy_check["level"] == "fair"
        assert result["overall"]["level"] == "fair"

    def test_index_theme_with_no_named_instruments_is_fair_not_poor(self):
        # An index/macro theme (nifty50, inr_usd) will never name a single
        # stock — that's honest, not a failure, as long as a strategy
        # exists at all.
        result = assess_theme_readiness(
            make_theme(instruments_mentioned=[]),
            top_match={"strategy_id": "s1"},
            strategy=make_strategy(),
        )
        named_check = next(c for c in result["checklist"] if c["label"] == "Named stocks/funds available")
        assert named_check["level"] == "fair"

    def test_no_strategy_and_no_instruments_is_poor_on_named_stocks(self):
        result = assess_theme_readiness(make_theme(), top_match=None, strategy=None)
        named_check = next(c for c in result["checklist"] if c["label"] == "Named stocks/funds available")
        assert named_check["level"] == "poor"

    def test_unbacktested_strategy_is_fair(self):
        result = assess_theme_readiness(
            make_theme(),
            top_match={"strategy_id": "s1"},
            strategy=make_strategy(backtest_status="pending"),
        )
        track_check = next(c for c in result["checklist"] if c["label"] == "Real-world track record")
        assert track_check["level"] == "fair"

    def test_thin_expert_agreement_flagged(self):
        result = assess_theme_readiness(
            make_theme(supporting_experts=[{"expert_name": "A"}]),
            top_match={"strategy_id": "s1"},
            strategy=make_strategy(),
        )
        expert_check = next(c for c in result["checklist"] if c["label"] == "Expert agreement")
        assert expert_check["level"] == "poor"

    def test_fundamentals_not_applicable_is_fair(self):
        result = assess_theme_readiness(
            make_theme(fundamentals_verdict="not_applicable"),
            top_match={"strategy_id": "s1"},
            strategy=make_strategy(),
        )
        fund_check = next(c for c in result["checklist"] if c["label"] == "Financials checked")
        assert fund_check["level"] == "fair"

    def test_fundamentals_rejected_is_poor(self):
        result = assess_theme_readiness(
            make_theme(fundamentals_verdict="reject"),
            top_match={"strategy_id": "s1"},
            strategy=make_strategy(),
        )
        fund_check = next(c for c in result["checklist"] if c["label"] == "Financials checked")
        assert fund_check["level"] == "poor"

    def test_checklist_has_all_five_dimensions(self):
        result = assess_theme_readiness(make_theme(), top_match=None, strategy=None)
        labels = {c["label"] for c in result["checklist"]}
        assert labels == {
            "Expert agreement", "Financials checked", "A real way to invest exists",
            "Real-world track record", "Named stocks/funds available",
        }

    def test_overall_never_worse_than_the_two_gating_checks(self):
        # Weak expert agreement + weak fundamentals shouldn't drag down an
        # otherwise-actionable theme — the gate is specifically about
        # whether there's something concrete to DO, not the thesis quality.
        result = assess_theme_readiness(
            make_theme(
                supporting_experts=[], fundamentals_verdict="reject",
                instruments_mentioned=["HDFCBANK"],
            ),
            top_match={"strategy_id": "s1"},
            strategy=make_strategy(),
        )
        assert result["overall"]["level"] == "good"
