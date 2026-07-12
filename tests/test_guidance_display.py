"""
Tests for concrete rupee/share translation of guidance percentages
(2026-07-12 — founder asked for direct action, not just percentages).
All market-data/entity-resolution calls monkeypatched — no network.
"""

from __future__ import annotations

from src.guidance import display
from src.guidance.display import ILLUSTRATIVE_PORTFOLIO_VALUE, compute_action_amounts


def make_guidance(**overrides):
    base = {
        "target_allocation_change": {"from_pct": 0.0, "to_pct": 8.0},
        "specific_instruments": [
            {"entity": "NVDA", "target_pct": 2.0},
            {"entity": "nifty50", "target_pct": 6.0},  # index — never a share estimate
        ],
    }
    base.update(overrides)
    return base


class TestComputeActionAmounts:
    def test_theme_rupee_change_from_real_portfolio_value(self, monkeypatch):
        monkeypatch.setattr(display, "resolve_entity", lambda entity, geo: None)
        result = compute_action_amounts(make_guidance(), portfolio_value=500_000.0)
        assert result["theme_rupee_change"] == 40_000.0  # 8% of 5,00,000

    def test_instrument_rupee_amounts(self, monkeypatch):
        monkeypatch.setattr(display, "resolve_entity", lambda entity, geo: None)
        result = compute_action_amounts(make_guidance(), portfolio_value=500_000.0)
        by_entity = {i["entity"]: i["rupee_amount"] for i in result["instruments"]}
        assert by_entity["NVDA"] == 10_000.0  # 2% of 5,00,000
        assert by_entity["nifty50"] == 30_000.0  # 6% of 5,00,000

    def test_no_allocation_change_yields_none(self, monkeypatch):
        monkeypatch.setattr(display, "resolve_entity", lambda entity, geo: None)
        guidance = make_guidance(target_allocation_change={}, specific_instruments=[])
        result = compute_action_amounts(guidance, portfolio_value=500_000.0)
        assert result["theme_rupee_change"] is None
        assert result["instruments"] == []

    def test_share_estimate_when_price_resolvable(self, monkeypatch):
        monkeypatch.setattr(display, "resolve_entity", lambda entity, geo: ("NVDA", "yfinance") if entity == "NVDA" else None)
        monkeypatch.setattr(display, "price_on", lambda symbol, d, max_lookback_days: 1000.0 if symbol == "NVDA" else None)
        result = compute_action_amounts(make_guidance(), portfolio_value=500_000.0)
        nvda = next(i for i in result["instruments"] if i["entity"] == "NVDA")
        assert nvda["share_estimate"] == {"symbol": "NVDA", "price": 1000.0, "approx_shares": 10.0}

    def test_index_entity_never_gets_share_estimate(self, monkeypatch):
        # nifty50 is a real ENTITY_SYMBOL_MAP-adjacent name in production,
        # but a theme/sector is not directly purchasable — resolve_entity
        # returning None (or 'manual' source) must suppress share estimates.
        monkeypatch.setattr(display, "resolve_entity", lambda entity, geo: None)
        result = compute_action_amounts(make_guidance(), portfolio_value=500_000.0)
        nifty = next(i for i in result["instruments"] if i["entity"] == "nifty50")
        assert nifty["share_estimate"] is None

    def test_manual_source_never_gets_share_estimate(self, monkeypatch):
        monkeypatch.setattr(display, "resolve_entity", lambda entity, geo: ("rbi_rate", "manual"))
        result = compute_action_amounts(make_guidance(), portfolio_value=500_000.0)
        for inst in result["instruments"]:
            assert inst["share_estimate"] is None

    def test_missing_price_gracefully_omits_not_fabricates(self, monkeypatch):
        monkeypatch.setattr(display, "resolve_entity", lambda entity, geo: ("NVDA", "yfinance"))
        monkeypatch.setattr(display, "price_on", lambda symbol, d, max_lookback_days: None)
        result = compute_action_amounts(make_guidance(), portfolio_value=500_000.0)
        for inst in result["instruments"]:
            assert inst["share_estimate"] is None

    def test_illustrative_uses_fixed_round_value(self, monkeypatch):
        monkeypatch.setattr(display, "resolve_entity", lambda entity, geo: None)
        result = compute_action_amounts(make_guidance(), portfolio_value=37_412.0)
        assert result["illustrative"]["theme_rupee_change"] == 8_000.0  # 8% of exactly 1,00,000
        assert ILLUSTRATIVE_PORTFOLIO_VALUE == 100_000.0

    def test_incomplete_instrument_rows_skipped_not_crashed(self, monkeypatch):
        monkeypatch.setattr(display, "resolve_entity", lambda entity, geo: None)
        guidance = make_guidance(specific_instruments=[{"entity": "NVDA"}, {"target_pct": 5.0}, {}])
        result = compute_action_amounts(guidance, portfolio_value=100_000.0)
        assert result["instruments"] == []
