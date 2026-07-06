"""
Tests for fundamentals data resolution and sector-relative valuation
(Layer 7). All yfinance/DB access is monkeypatched — no network calls.
"""

from __future__ import annotations

from src.fundamentals import data as fundamentals_data
from src.fundamentals.data import (
    check_balance_sheet_red_flag,
    compute_sector_relative_pe,
    resolve_target_instruments,
)

# =====================================================================
# INSTRUMENT RESOLUTION
# =====================================================================


class TestResolveTargetInstruments:
    def test_instruments_mentioned_takes_priority(self):
        theme = {
            "entity_cluster_key": "indian_banking",
            "instruments_mentioned": ["HDFCBANK", "ICICIBANK"],
        }
        assert resolve_target_instruments(theme) == ["HDFCBANK", "ICICIBANK"]

    def test_falls_back_to_ticker_shaped_entity_cluster_key(self):
        theme = {"entity_cluster_key": "RELIANCE", "instruments_mentioned": []}
        assert resolve_target_instruments(theme) == ["RELIANCE"]

    def test_sector_level_theme_with_nothing_named_returns_empty(self):
        theme = {"entity_cluster_key": "indian_banking", "instruments_mentioned": []}
        assert resolve_target_instruments(theme) == []

    def test_macro_entity_returns_empty(self):
        theme = {"entity_cluster_key": "gold", "instruments_mentioned": None}
        assert resolve_target_instruments(theme) == []

    def test_non_ticker_shaped_instruments_mentioned_filtered_out(self):
        # Lowercase sector names sometimes slip into instruments_mentioned
        # from the LLM — these aren't ticker-shaped, so they're filtered,
        # falling through to the entity_cluster_key check.
        theme = {"entity_cluster_key": "AAPL", "instruments_mentioned": ["us_tech"]}
        assert resolve_target_instruments(theme) == ["AAPL"]


# =====================================================================
# SECTOR-RELATIVE PE
# =====================================================================


class TestComputeSectorRelativePe:
    def test_computes_ratio_against_peer_average(self, monkeypatch):
        def fake_snapshot(symbol):
            return {"HDFCBANK": {"trailingPE": 18.0}, "ICICIBANK": {"trailingPE": 20.0}}.get(symbol)

        monkeypatch.setattr(fundamentals_data, "fetch_fundamentals_snapshot", fake_snapshot)
        monkeypatch.setitem(
            fundamentals_data.SECTOR_PEER_TICKERS, "indian_banking", ["HDFCBANK", "ICICIBANK"]
        )

        # peer average = 19.0, trailing_pe 28.5 -> ratio 1.5
        result = compute_sector_relative_pe("indian_banking", 28.5)

        assert result == 1.5

    def test_returns_none_for_unknown_sector(self):
        assert compute_sector_relative_pe("not_a_known_sector", 20.0) is None

    def test_returns_none_when_entity_cluster_key_missing(self):
        assert compute_sector_relative_pe(None, 20.0) is None

    def test_returns_none_when_trailing_pe_missing(self):
        assert compute_sector_relative_pe("indian_banking", None) is None

    def test_returns_none_with_fewer_than_two_resolved_peers(self, monkeypatch):
        monkeypatch.setattr(
            fundamentals_data, "fetch_fundamentals_snapshot", lambda symbol: None
        )
        assert compute_sector_relative_pe("indian_banking", 20.0) is None


# =====================================================================
# BALANCE SHEET HARD VETO
# =====================================================================


class TestCheckBalanceSheetRedFlag:
    def test_both_conditions_fail_triggers_veto(self):
        snapshot = {"debtToEquity": 250.0, "currentRatio": 0.8}
        assert check_balance_sheet_red_flag(snapshot) is True

    def test_only_debt_to_equity_fails_no_veto(self):
        snapshot = {"debtToEquity": 250.0, "currentRatio": 1.2}
        assert check_balance_sheet_red_flag(snapshot) is False

    def test_only_current_ratio_fails_no_veto(self):
        snapshot = {"debtToEquity": 50.0, "currentRatio": 0.8}
        assert check_balance_sheet_red_flag(snapshot) is False

    def test_both_pass_no_veto(self):
        snapshot = {"debtToEquity": 50.0, "currentRatio": 1.5}
        assert check_balance_sheet_red_flag(snapshot) is False

    def test_missing_debt_to_equity_never_vetoes(self):
        snapshot = {"debtToEquity": None, "currentRatio": 0.5}
        assert check_balance_sheet_red_flag(snapshot) is False

    def test_missing_current_ratio_never_vetoes(self):
        snapshot = {"debtToEquity": 300.0, "currentRatio": None}
        assert check_balance_sheet_red_flag(snapshot) is False
