"""
Tests for deterministic guidance relevance/staleness computation
(2026-07-12 — see decision log for the gap this closes).
"""

from __future__ import annotations

from datetime import date

from src.guidance.relevance import _horizon_months, compute_review_by, relevant_as_of


class TestRelevantAsOf:
    def test_snapshots_theme_synthesized_at(self):
        theme = {"synthesized_at": "2026-07-09T06:45:38+00:00"}
        assert relevant_as_of(theme) == date(2026, 7, 9)

    def test_accepts_plain_date_string(self):
        theme = {"synthesized_at": "2026-07-09"}
        assert relevant_as_of(theme) == date(2026, 7, 9)

    def test_none_when_missing(self):
        assert relevant_as_of({}) is None


class TestHorizonMonths:
    """Priority order of the idea's own stated horizon (before any cap)."""

    def test_prefers_strategy_holding_horizon(self):
        assert _horizon_months({"holding_horizon_months": 6}, {"time_horizon_years": 3.0}) == 6

    def test_falls_back_to_theme_horizon(self):
        assert _horizon_months({}, {"time_horizon_years": 2.0}) == 24

    def test_falls_back_to_theme_horizon_when_strategy_is_none(self):
        assert _horizon_months(None, {"time_horizon_years": 1.0}) == 12

    def test_falls_back_to_tunable_default_when_neither_stated(self):
        # default is 12 months (src/config/tuning.py: guidance.default_review_months)
        assert _horizon_months({}, {}) == 12
        assert _horizon_months(None, None) == 12

    def test_strategy_horizon_zero_falls_through(self):
        # A falsy-but-present 0 should not be treated as "stated" — an
        # entry-methodology strategy with no real holding period shouldn't
        # produce a same-day expiry.
        assert _horizon_months({"holding_horizon_months": 0}, {"time_horizon_years": 2.0}) == 24


class TestComputeReviewBy:
    GENERATED = date(2026, 7, 12)

    def test_short_horizon_is_used_as_is(self):
        result = compute_review_by(self.GENERATED, {"holding_horizon_months": 3}, None)
        assert (result - self.GENERATED).days in range(88, 95)

    def test_long_horizon_capped_at_max_review_months(self):
        # Real 2026-07-12 bug: a 5-year strategy horizon produced a 2031
        # review_by. Capped at guidance.max_review_months (default 6).
        result = compute_review_by(
            self.GENERATED, {"holding_horizon_months": 60}, {"entity_cluster_key": "indian_fmcg"}
        )
        assert (result - self.GENERATED).days in range(178, 186)

    def test_default_horizon_also_capped(self):
        result = compute_review_by(self.GENERATED, None, None)
        assert (result - self.GENERATED).days in range(178, 186)

    def test_volatile_entity_gets_tighter_cap(self):
        result = compute_review_by(
            self.GENERATED, {"holding_horizon_months": 60}, {"entity_cluster_key": "crypto_sector"}
        )
        # guidance.volatile_max_review_months default 3
        assert (result - self.GENERATED).days in range(88, 95)
