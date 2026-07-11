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

    def test_index_entity_not_applicable_is_eligible(self):
        # Founder policy 2026-07-09: company fundamentals categorically
        # don't exist for index/macro entities — not_applicable doesn't
        # block them.
        eligible, reason = check_theme_eligible(
            make_theme(entity_cluster_key="nifty50", fundamentals_verdict="not_applicable"),
            make_strategy(),
        )
        assert eligible is True

    def test_index_entity_insufficient_data_is_eligible(self):
        eligible, _ = check_theme_eligible(
            make_theme(entity_cluster_key="crude_oil", fundamentals_verdict="insufficient_data"),
            make_strategy(),
        )
        assert eligible is True

    def test_stock_sector_entity_not_applicable_still_refused(self):
        # Sector themes have constituent stocks — they must earn
        # pass/downgrade; the index/macro carve-out never applies.
        eligible, _ = check_theme_eligible(
            make_theme(entity_cluster_key="indian_banking", fundamentals_verdict="not_applicable"),
            make_strategy(),
        )
        assert eligible is False

    def test_index_entity_reject_still_refused(self):
        eligible, _ = check_theme_eligible(
            make_theme(entity_cluster_key="nifty50", fundamentals_verdict="reject"),
            make_strategy(),
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


class TestCheckEntityOverlapGuidance:
    """Entity-overlap dedup across theme generations (2026-07-10 audit)."""

    @staticmethod
    def _active_guidance(*entity_sets):
        rows = []
        for i, entities in enumerate(entity_sets):
            first, *rest = entities
            rows.append({
                "id": f"g{i}",
                "theme_id": f"other-theme-{i}",
                "target_allocation_change": {"entity": first},
                "specific_instruments": [{"entity": e} for e in rest],
            })
        return rows

    def test_no_active_guidance_no_overlap(self, monkeypatch):
        monkeypatch.setattr(eligibility, "fetch_portfolio_active_guidance", lambda pid: [])
        assert eligibility.check_entity_overlap_guidance(make_theme(), "p1") is None

    def test_successor_theme_same_holdings_refused(self, monkeypatch):
        # The audit's real case: superseded indian_it theme's REDUCE on
        # PERSISTENT/TECHM, then a successor theme targeting the same stocks.
        monkeypatch.setattr(
            eligibility, "fetch_portfolio_active_guidance",
            lambda pid: self._active_guidance(["indian_it_services", "PERSISTENT", "TECHM"]),
        )
        theme = make_theme(
            id="successor-theme",
            entity_cluster_key="indian_it",
            instruments_mentioned=["PERSISTENT", "indian_it_services"],
        )
        reason = eligibility.check_entity_overlap_guidance(theme, "p1")
        assert reason is not None
        assert "g0" in reason

    def test_unrelated_theme_not_refused(self, monkeypatch):
        monkeypatch.setattr(
            eligibility, "fetch_portfolio_active_guidance",
            lambda pid: self._active_guidance(["gold", "GOLDBEES"]),
        )
        theme = make_theme(entity_cluster_key="indian_banking",
                           instruments_mentioned=["HDFCBANK"])
        assert eligibility.check_entity_overlap_guidance(theme, "p1") is None

    def test_same_theme_rows_ignored(self, monkeypatch):
        # Same-theme duplication is check_duplicate_guidance's job.
        rows = self._active_guidance(["indian_banking", "HDFCBANK"])
        rows[0]["theme_id"] = "theme-1"
        monkeypatch.setattr(eligibility, "fetch_portfolio_active_guidance", lambda pid: rows)
        theme = make_theme(id="theme-1", entity_cluster_key="indian_banking")
        assert eligibility.check_entity_overlap_guidance(theme, "p1") is None

    def test_composite_instrument_label_overlaps(self, monkeypatch):
        # Guidance instruments written as composite labels still match.
        monkeypatch.setattr(
            eligibility, "fetch_portfolio_active_guidance",
            lambda pid: self._active_guidance(["us_tech_basket (AAPL, NVDA, MSFT)"]),
        )
        theme = make_theme(entity_cluster_key="us_tech_ai", instruments_mentioned=["NVDA"])
        assert eligibility.check_entity_overlap_guidance(theme, "p1") is not None
