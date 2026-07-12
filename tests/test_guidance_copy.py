"""
Tests for plain-language display translations (2026-07-13 — founder read
the app as a general user and found internal pipeline vocabulary leaking
into the UI: "superseded," raw enum values, "1826d remaining").
"""

from __future__ import annotations

from src.guidance.copy import (
    humanize_days,
    refusal_reason_label,
    sizing_source_label,
    source_type_label,
)


class TestSourceTypeLabel:
    def test_known_values_translated(self):
        assert source_type_label("expert_extracted") == "an expert's own stated method"
        assert source_type_label("historical_pattern") == "a method one expert has repeated many times"
        assert source_type_label("cfa_framework") == "a textbook (CFA) investing framework"
        assert source_type_label("institutional") == "a professional/institutional framework"

    def test_none_has_a_fallback(self):
        assert "unspecified" in source_type_label(None)

    def test_unknown_value_passes_through(self):
        assert source_type_label("some_new_type") == "some_new_type"


class TestSizingSourceLabel:
    def test_known_values_translated(self):
        assert sizing_source_label("strategy_allocation_rule") == "sized using the strategy's own stated rule"
        assert sizing_source_label("risk_profile_cap") == "capped by your risk profile, not the strategy"
        assert sizing_source_label("reduction_not_capped") == "a full exit — no sizing cap applies"

    def test_already_plain_fallback_strings_pass_through_unchanged(self):
        already_plain = "no explicit sizing rule stated by the matched strategy"
        assert sizing_source_label(already_plain) == already_plain

    def test_none_has_a_fallback(self):
        assert sizing_source_label(None) == "not recorded"


class TestRefusalReasonLabel:
    def test_low_conviction(self):
        reason = "theme conviction is 'low', requires medium or high (convergence across independent experts, not a single voice)"
        assert refusal_reason_label(reason) == "Not enough expert agreement yet"

    def test_fundamentals_verdict(self):
        reason = "theme fundamentals_verdict is 'reject', requires pass or downgrade (or an index/macro entity where company fundamentals don't apply)"
        assert refusal_reason_label(reason) == "Financials didn't check out"

    def test_no_matched_strategy(self):
        reason = "no matched execution strategy found for this theme"
        assert refusal_reason_label(reason) == "No concrete strategy matched"

    def test_duplicate_guidance(self):
        reason = "active or executing guidance already exists for this theme and portfolio"
        assert refusal_reason_label(reason) == "Already recommended for this"

    def test_entity_overlap(self):
        reason = "active guidance abc123 already covers this theme's target entities (overlap on 'nvda') — one live recommendation per holding at a time"
        assert refusal_reason_label(reason) == "Would overlap another active recommendation"

    def test_already_aligned(self):
        reason = "portfolio is already sufficiently aligned with this theme's entity/instruments"
        assert refusal_reason_label(reason) == "You're already well-positioned here"

    def test_unrecognized_reason_passes_through_not_hidden(self):
        reason = "some brand new refusal reason nobody categorized yet"
        assert refusal_reason_label(reason) == reason

    def test_none_has_a_fallback(self):
        assert refusal_reason_label(None) == "No reason recorded"


class TestHumanizeDays:
    def test_years(self):
        assert humanize_days(1826) == "about 5 years"

    def test_single_year(self):
        assert humanize_days(365) == "about 1 year"

    def test_months(self):
        assert humanize_days(90) == "about 3 months"

    def test_weeks(self):
        assert humanize_days(14) == "about 2 weeks"

    def test_days(self):
        assert humanize_days(3) == "about 3 days"

    def test_single_day(self):
        assert humanize_days(1) == "about 1 day"

    def test_zero(self):
        assert humanize_days(0) == "today"

    def test_negative_treated_as_magnitude(self):
        # Callers pass an already-signed "days ago"/"days remaining" — this
        # helper only humanizes the magnitude, sign is handled by the caller.
        assert humanize_days(-90) == "about 3 months"
