"""
Tests for guidance generation's deterministic eligibility gates (Layer 9).
No LLM calls — pure logic plus one DB-backed check, monkeypatched.
"""

from __future__ import annotations

from src.guidance import eligibility
from src.guidance.eligibility import (
    check_already_aligned,
    check_duplicate_guidance,
    check_theme_eligible,
)


def make_theme(**overrides):
    base = {
        "id": "theme-1",
        "entity_cluster_key": "indian_banking",
        "conviction_level": "high",
        "fundamentals_verdict": "pass",
        "instruments_mentioned": [],
    }
    base.update(overrides)
    return base


def make_strategy(**overrides):
    base = {"id": "strategy-1", "strategy_text": "Allocate 40% to banks"}
    base.update(overrides)
    return base


def make_holding(**overrides):
    base = {"entity": "HDFCBANK", "quantity": 100, "cost_basis": 1500.0}
    base.update(overrides)
    return base


class TestCheckThemeEligible:
    def test_low_conviction_refused(self):
        eligible, reason = check_theme_eligible(make_theme(conviction_level="low"), make_strategy())
        assert eligible is False
        assert "conviction" in reason

    def test_medium_conviction_eligible(self):
        eligible, reason = check_theme_eligible(make_theme(conviction_level="medium"), make_strategy())
        assert eligible is True
        assert reason is None

    def test_reject_fundamentals_refused(self):
        eligible, reason = check_theme_eligible(
            make_theme(fundamentals_verdict="reject"), make_strategy()
        )
        assert eligible is False
        assert "fundamentals_verdict" in reason

    def test_insufficient_data_fundamentals_refused(self):
        eligible, reason = check_theme_eligible(
            make_theme(fundamentals_verdict="insufficient_data"), make_strategy()
        )
        assert eligible is False

    def test_downgrade_fundamentals_eligible(self):
        eligible, reason = check_theme_eligible(
            make_theme(fundamentals_verdict="downgrade"), make_strategy()
        )
        assert eligible is True

    def test_no_matched_strategy_refused(self):
        eligible, reason = check_theme_eligible(make_theme(), None)
        assert eligible is False
        assert "matched" in reason

    def test_all_clear_eligible(self):
        eligible, reason = check_theme_eligible(make_theme(), make_strategy())
        assert eligible is True
        assert reason is None


class TestCheckAlreadyAligned:
    def test_no_holdings_not_aligned(self):
        assert check_already_aligned(make_theme(), []) is False

    def test_large_existing_position_is_aligned(self):
        holdings = [
            make_holding(entity="HDFCBANK", quantity=100, cost_basis=1500.0),  # 150,000
            make_holding(entity="GOLD_ETF", quantity=10, cost_basis=5000.0),  # 50,000, unrelated
        ]
        # HDFCBANK isn't the theme's own entity_cluster_key or instruments_mentioned
        # by default — use a theme whose instruments include it.
        theme = make_theme(entity_cluster_key="indian_banking", instruments_mentioned=["HDFCBANK"])
        # 150,000 / 200,000 = 75% >> 15% threshold
        assert check_already_aligned(theme, holdings) is True

    def test_small_existing_position_not_aligned(self):
        holdings = [
            make_holding(entity="HDFCBANK", quantity=1, cost_basis=1500.0),  # 1,500
            make_holding(entity="GOLD_ETF", quantity=10, cost_basis=5000.0),  # 50,000
        ]
        theme = make_theme(entity_cluster_key="indian_banking", instruments_mentioned=["HDFCBANK"])
        # 1,500 / 51,500 ≈ 2.9% < 15% threshold
        assert check_already_aligned(theme, holdings) is False

    def test_unrelated_holdings_not_aligned(self):
        holdings = [make_holding(entity="GOLD_ETF", quantity=10, cost_basis=5000.0)]
        theme = make_theme(entity_cluster_key="indian_banking", instruments_mentioned=[])
        assert check_already_aligned(theme, holdings) is False

    def test_zero_total_value_not_aligned(self):
        holdings = [make_holding(entity="HDFCBANK", quantity=100, cost_basis=None)]
        theme = make_theme(entity_cluster_key="indian_banking")
        assert check_already_aligned(theme, holdings) is False


class TestCheckDuplicateGuidance:
    def test_existing_active_guidance_is_duplicate(self, monkeypatch):
        monkeypatch.setattr(eligibility, "fetch_active_guidance", lambda theme_id, portfolio_id: [{"id": "g1"}])
        assert check_duplicate_guidance("theme-1", "portfolio-1") is True

    def test_no_existing_guidance_not_duplicate(self, monkeypatch):
        monkeypatch.setattr(eligibility, "fetch_active_guidance", lambda theme_id, portfolio_id: [])
        assert check_duplicate_guidance("theme-1", "portfolio-1") is False
