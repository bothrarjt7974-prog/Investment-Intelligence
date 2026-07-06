"""
Tests for the trigger vocabulary detectors (Layer 4, Track B).

All price/rate data is monkeypatched (price_series) — no network or
Supabase access, same convention as tests/test_validation.py.
"""

from __future__ import annotations

from datetime import date

from src.validation import trigger_vocabulary


def _mock_price_series(monkeypatch, series_by_symbol: dict[str, list[tuple[date, float]]]):
    def fake_price_series(symbol, start, end):
        return series_by_symbol.get(symbol, [])

    monkeypatch.setattr(trigger_vocabulary, "price_series", fake_price_series)


# =====================================================================
# RATE CUT / HIKE CYCLE
# =====================================================================


class TestRateCycles:
    def test_detects_two_consecutive_cuts(self, monkeypatch):
        series = [
            (date(2020, 1, 1), 6.50),
            (date(2020, 3, 1), 6.25),
            (date(2020, 5, 1), 6.00),  # 2nd consecutive cut -> window here
            (date(2020, 7, 1), 6.00),  # hold, resets streak
            (date(2020, 9, 1), 5.75),  # single cut, no window
        ]
        _mock_price_series(monkeypatch, {"rbi_rate": series})

        result = trigger_vocabulary.detect_rate_cut_cycle("nifty50", is_india_linked=True)

        assert result == [date(2020, 5, 1)]

    def test_detects_two_consecutive_hikes(self, monkeypatch):
        series = [
            (date(2022, 1, 1), 4.00),
            (date(2022, 3, 1), 4.25),
            (date(2022, 5, 1), 4.50),  # 2nd consecutive hike -> window here
        ]
        _mock_price_series(monkeypatch, {"fed_rate": series})

        result = trigger_vocabulary.detect_rate_hike_cycle("AAPL", is_india_linked=False)

        assert result == [date(2022, 5, 1)]

    def test_uses_fed_rate_when_not_india_linked(self, monkeypatch):
        _mock_price_series(
            monkeypatch,
            {
                "rbi_rate": [(date(2020, 1, 1), 6.5), (date(2020, 3, 1), 6.0), (date(2020, 5, 1), 5.5)],
                "fed_rate": [],
            },
        )

        result = trigger_vocabulary.detect_rate_cut_cycle("AAPL", is_india_linked=False)

        # fed_rate series is empty -> no windows, even though rbi_rate has a cycle
        assert result == []

    def test_no_series_returns_empty(self, monkeypatch):
        _mock_price_series(monkeypatch, {})
        assert trigger_vocabulary.detect_rate_cut_cycle("nifty50", is_india_linked=True) == []


# =====================================================================
# DRAWDOWN THRESHOLD
# =====================================================================


class TestDrawdownThreshold:
    def test_detects_one_window_per_episode(self, monkeypatch):
        series = [
            (date(2020, 1, 1), 100.0),
            (date(2020, 1, 2), 110.0),  # peak
            (date(2020, 1, 3), 105.0),  # dd ~ -4.5%
            (date(2020, 1, 4), 95.0),  # dd ~ -13.6%
            (date(2020, 1, 5), 85.0),  # dd ~ -22.7% -> crosses, window here
            (date(2020, 1, 6), 80.0),  # still in drawdown, no new window
            (date(2020, 1, 7), 112.0),  # recovers, new peak
            (date(2020, 6, 1), 130.0),  # new peak, far later
            (date(2020, 6, 2), 100.0),  # dd ~ -23.1% -> new episode, window here
        ]
        _mock_price_series(monkeypatch, {"RELIANCE": series})

        result = trigger_vocabulary.detect_drawdown_threshold("RELIANCE", is_india_linked=True)

        assert result == [date(2020, 1, 5), date(2020, 6, 2)]

    def test_no_drawdown_returns_empty(self, monkeypatch):
        series = [(date(2020, 1, 1), 100.0), (date(2020, 1, 2), 105.0), (date(2020, 1, 3), 110.0)]
        _mock_price_series(monkeypatch, {"RELIANCE": series})

        assert trigger_vocabulary.detect_drawdown_threshold("RELIANCE", is_india_linked=True) == []

    def test_short_series_returns_empty(self, monkeypatch):
        _mock_price_series(monkeypatch, {"RELIANCE": [(date(2020, 1, 1), 100.0)]})
        assert trigger_vocabulary.detect_drawdown_threshold("RELIANCE", is_india_linked=True) == []


# =====================================================================
# CURRENCY REGIME
# =====================================================================


class TestCurrencyRegime:
    def test_detects_sustained_move(self, monkeypatch):
        series = [
            (date(2020, 1, 1), 75.0),
            (date(2020, 4, 1), 80.0),  # ~6.7% move over ~91 days -> triggers
        ]
        _mock_price_series(monkeypatch, {"inr_usd": series})

        result = trigger_vocabulary.detect_currency_regime("nifty50", is_india_linked=True)

        assert result == [date(2020, 4, 1)]

    def test_small_move_does_not_trigger(self, monkeypatch):
        series = [
            (date(2020, 1, 1), 75.0),
            (date(2020, 4, 1), 76.0),  # ~1.3% move -> below threshold
        ]
        _mock_price_series(monkeypatch, {"inr_usd": series})

        assert trigger_vocabulary.detect_currency_regime("nifty50", is_india_linked=True) == []

    def test_hardcodes_inr_usd_regardless_of_symbol(self, monkeypatch):
        # v1 always scans inr_usd — the symbol arg (what the strategy is
        # about) is deliberately ignored for this detector.
        _mock_price_series(
            monkeypatch,
            {"inr_usd": [(date(2020, 1, 1), 75.0), (date(2020, 4, 1), 80.0)]},
        )

        result = trigger_vocabulary.detect_currency_regime("AAPL", is_india_linked=False)

        assert result == [date(2020, 4, 1)]


# =====================================================================
# ALWAYS ON
# =====================================================================


class TestAlwaysOn:
    def test_samples_full_history_at_stride(self, monkeypatch):
        series = [(date(2020, 1, 1), 100.0), (date(2020, 10, 1), 150.0)]
        _mock_price_series(monkeypatch, {"BTC": series})

        result = trigger_vocabulary.detect_always_on("BTC", is_india_linked=False)

        assert result[0] == date(2020, 1, 1)
        assert all(
            (result[i + 1] - result[i]).days == trigger_vocabulary.ALWAYS_ON_STRIDE_DAYS
            for i in range(len(result) - 1)
        )
        assert result[-1] <= date(2020, 10, 1)

    def test_empty_series_returns_empty(self, monkeypatch):
        _mock_price_series(monkeypatch, {})
        assert trigger_vocabulary.detect_always_on("BTC", is_india_linked=False) == []


# =====================================================================
# REGISTRY
# =====================================================================


def test_valuation_triggers_not_registered():
    # Deliberately absent — no historical PE/valuation data ingested yet
    # (Layer 6 fundamentals). Never faked with a technical proxy.
    assert "valuation_extreme_cheap" not in trigger_vocabulary.TRIGGER_DETECTORS
    assert "valuation_extreme_rich" not in trigger_vocabulary.TRIGGER_DETECTORS


def test_five_supported_types_registered():
    assert set(trigger_vocabulary.TRIGGER_DETECTORS.keys()) == {
        "rate_cut_cycle",
        "rate_hike_cycle",
        "drawdown_threshold",
        "currency_regime",
        "always_on",
    }
