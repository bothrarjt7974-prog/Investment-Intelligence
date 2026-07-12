"""
Tests for deterministic guidance relevance/staleness computation
(2026-07-12 — see decision log for the gap this closes).
"""

from __future__ import annotations

from datetime import date

from src.guidance.relevance import compute_review_by, relevant_as_of


class TestRelevantAsOf:
    def test_snapshots_theme_synthesized_at(self):
        theme = {"synthesized_at": "2026-07-09T06:45:38+00:00"}
        assert relevant_as_of(theme) == date(2026, 7, 9)

    def test_accepts_plain_date_string(self):
        theme = {"synthesized_at": "2026-07-09"}
        assert relevant_as_of(theme) == date(2026, 7, 9)

    def test_none_when_missing(self):
        assert relevant_as_of({}) is None


class TestComputeReviewBy:
    GENERATED = date(2026, 7, 12)

    def test_prefers_strategy_holding_horizon(self):
        strategy = {"holding_horizon_months": 6}
        theme = {"time_horizon_years": 3.0}  # should be ignored — strategy wins
        result = compute_review_by(self.GENERATED, strategy, theme)
        # 6 months at the codebase's 30.44-day approximation
        assert (result - self.GENERATED).days in range(178, 186)

    def test_falls_back_to_theme_horizon(self):
        result = compute_review_by(self.GENERATED, {}, {"time_horizon_years": 2.0})
        # 24 months
        assert (result - self.GENERATED).days in range(725, 735)

    def test_falls_back_to_theme_horizon_when_strategy_is_none(self):
        result = compute_review_by(self.GENERATED, None, {"time_horizon_years": 1.0})
        assert (result - self.GENERATED).days in range(360, 370)

    def test_falls_back_to_tunable_default_when_neither_stated(self):
        result = compute_review_by(self.GENERATED, {}, {})
        # default is 12 months (src/config/tuning.py: guidance.default_review_months)
        assert (result - self.GENERATED).days in range(360, 370)

    def test_falls_back_to_default_when_both_none(self):
        result = compute_review_by(self.GENERATED, None, None)
        assert (result - self.GENERATED).days in range(360, 370)

    def test_strategy_horizon_zero_falls_through(self):
        # A falsy-but-present 0 should not be treated as "stated" — an
        # entry-methodology strategy with no real holding period shouldn't
        # produce a same-day expiry.
        result = compute_review_by(self.GENERATED, {"holding_horizon_months": 0}, {"time_horizon_years": 2.0})
        assert (result - self.GENERATED).days in range(725, 735)
