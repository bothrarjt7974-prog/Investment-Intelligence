"""
Tests for the strategy backtesting engine (Layer 4, Track B).

All DB/price access is monkeypatched — no network or Supabase calls, same
convention as tests/test_validation.py.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.validation import backtest


def make_strategy(**overrides):
    base = {
        "id": "strategy-1",
        "entity_class": "RELIANCE",
        "trigger_conditions": {"type": "drawdown_threshold"},
        "holding_horizon_months": 12,
    }
    base.update(overrides)
    return base


# =====================================================================
# simulate_strategy — happy path
# =====================================================================


class TestSimulateStrategyHappyPath:
    def _setup(self, monkeypatch, window_starts):
        monkeypatch.setattr(backtest, "resolve_entity", lambda entity, geography: ("RELIANCE.NS", "yfinance"))
        monkeypatch.setattr(
            backtest, "TRIGGER_DETECTORS", {"drawdown_threshold": lambda symbol, is_india_linked: window_starts}
        )

        # Indexed by elapsed years from the 2020-01-01 epoch, rounded to the
        # nearest year rather than keyed on exact year: _add_months uses a
        # 30.44-days/month approximation, so a 12-month window from a leap-
        # year Jan 1 lands on Dec 31, not Jan 1 next year — rounding keeps
        # this test robust to that day-count drift instead of asserting an
        # implementation detail of the date math.
        epoch = date(2020, 1, 1)
        prices_by_elapsed_year = [100.0, 110.0, 121.0, 133.1]

        def fake_price_on(symbol, d, max_lookback_days=7):
            elapsed_years = round((d - epoch).days / 365.25)
            idx = min(max(elapsed_years, 0), len(prices_by_elapsed_year) - 1)
            return prices_by_elapsed_year[idx]

        def fake_price_series(symbol, start, end):
            if start == "2010-01-01":
                return [(date(2010, 1, 1), 50.0), (date(2023, 1, 1), 150.0)]
            return []

        monkeypatch.setattr(backtest, "price_on", fake_price_on)
        monkeypatch.setattr(backtest, "price_series", fake_price_series)

    def test_computes_correct_aggregates(self, monkeypatch):
        window_starts = [date(2020, 1, 1), date(2021, 1, 1), date(2022, 1, 1)]
        self._setup(monkeypatch, window_starts)

        result = backtest.simulate_strategy(make_strategy())

        assert result["backtest_status"] == "complete"
        record = result["backtest_record"]
        assert record["num_windows"] == 3
        assert record["win_rate"] == 1.0
        assert record["avg_return_pct"] == pytest.approx(10.0, abs=0.01)
        assert record["worst_drawdown_pct"] is None  # price_series returns [] for window queries
        assert record["holding_horizon_months_used"] == 12

        days = (date(2023, 1, 1) - date(2010, 1, 1)).days
        expected_cagr = (150.0 / 50.0) ** (365.25 / days) - 1
        expected_benchmark = ((1 + expected_cagr) ** (12 / 12) - 1) * 100
        assert record["benchmark_return_pct"] == pytest.approx(expected_benchmark, abs=0.01)
        assert record["outperformance_pct"] == pytest.approx(10.0 - expected_benchmark, abs=0.01)

    def test_default_horizon_used_when_unstated(self, monkeypatch):
        window_starts = [date(2020, 1, 1), date(2021, 1, 1), date(2022, 1, 1)]
        self._setup(monkeypatch, window_starts)

        result = backtest.simulate_strategy(make_strategy(holding_horizon_months=None))

        assert result["backtest_record"]["holding_horizon_months_used"] == (
            backtest.DEFAULT_BACKTEST_HORIZON_MONTHS
        )


# =====================================================================
# simulate_strategy — insufficient_history paths
# =====================================================================


class TestSimulateStrategyInsufficientHistory:
    def test_unresolvable_entity_class(self, monkeypatch):
        monkeypatch.setattr(backtest, "resolve_entity", lambda entity, geography: None)

        result = backtest.simulate_strategy(make_strategy(entity_class="NOT_A_REAL_ENTITY"))

        assert result["backtest_status"] == "insufficient_history"
        assert "not resolvable" in result["backtest_record"]["reason"]

    def test_unsupported_trigger_type_never_faked(self, monkeypatch):
        # valuation_extreme_cheap is deliberately absent from TRIGGER_DETECTORS —
        # real registry, no monkeypatching needed.
        monkeypatch.setattr(backtest, "resolve_entity", lambda entity, geography: ("RELIANCE.NS", "yfinance"))

        result = backtest.simulate_strategy(
            make_strategy(trigger_conditions={"type": "valuation_extreme_cheap"})
        )

        assert result["backtest_status"] == "insufficient_history"
        assert "valuation_extreme_cheap" in result["backtest_record"]["reason"]

    def test_missing_trigger_conditions(self, monkeypatch):
        monkeypatch.setattr(backtest, "resolve_entity", lambda entity, geography: ("RELIANCE.NS", "yfinance"))

        result = backtest.simulate_strategy(make_strategy(trigger_conditions=None))

        assert result["backtest_status"] == "insufficient_history"

    def test_fewer_than_minimum_windows(self, monkeypatch):
        monkeypatch.setattr(backtest, "resolve_entity", lambda entity, geography: ("RELIANCE.NS", "yfinance"))
        monkeypatch.setattr(
            backtest,
            "TRIGGER_DETECTORS",
            {"drawdown_threshold": lambda symbol, is_india_linked: [date(2020, 1, 1)]},
        )
        monkeypatch.setattr(backtest, "price_on", lambda symbol, d, max_lookback_days=7: 100.0)
        monkeypatch.setattr(backtest, "price_series", lambda symbol, start, end: [])

        result = backtest.simulate_strategy(make_strategy())

        assert result["backtest_status"] == "insufficient_history"
        assert result["backtest_record"]["windows_found"] == 1
        assert f"need >= {backtest.MIN_TRIGGER_WINDOWS}" in result["backtest_record"]["reason"]

    def test_windows_with_missing_price_data_are_dropped(self, monkeypatch):
        monkeypatch.setattr(backtest, "resolve_entity", lambda entity, geography: ("RELIANCE.NS", "yfinance"))
        monkeypatch.setattr(
            backtest,
            "TRIGGER_DETECTORS",
            {
                "drawdown_threshold": lambda symbol, is_india_linked: [
                    date(2020, 1, 1),
                    date(2021, 1, 1),
                    date(2022, 1, 1),
                ]
            },
        )
        # Only 2020 has price data -> only 1 window survives (need entry AND exit).
        monkeypatch.setattr(
            backtest,
            "price_on",
            lambda symbol, d, max_lookback_days=7: 100.0 if d.year in (2020, 2021) else None,
        )
        monkeypatch.setattr(backtest, "price_series", lambda symbol, start, end: [])

        result = backtest.simulate_strategy(make_strategy())

        assert result["backtest_status"] == "insufficient_history"
        assert result["backtest_record"]["windows_found"] == 1


# =====================================================================
# run_backtesting — loop/error handling
# =====================================================================


def test_run_backtesting_counts_by_status(monkeypatch):
    updates: dict[str, dict] = {}
    strategies = [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]
    results = {
        "s1": {"backtest_status": "complete", "backtest_record": {}},
        "s2": {"backtest_status": "insufficient_history", "backtest_record": {}},
        "s3": None,  # simulate a failure below
    }

    monkeypatch.setattr(backtest, "fetch_backtestable_strategies", lambda limit: strategies)

    def fake_simulate(strategy):
        result = results[strategy["id"]]
        if result is None:
            raise ValueError("boom")
        return result

    monkeypatch.setattr(backtest, "simulate_strategy", fake_simulate)
    monkeypatch.setattr(backtest, "update_by_id", lambda table, row_id, fields: updates.setdefault(row_id, fields))
    monkeypatch.setattr(backtest, "requeue_newly_resolvable", lambda: 0)

    counts = backtest.run_backtesting()

    assert counts == {"complete": 1, "insufficient_history": 1, "error": 1, "total": 3}
    assert updates["s1"]["backtest_status"] == "complete"
    assert updates["s2"]["backtest_status"] == "insufficient_history"
    assert "s3" not in updates  # the raising strategy never got an update call
