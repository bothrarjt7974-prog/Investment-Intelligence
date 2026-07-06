"""
Tests for the strategy quality filter screener module (Filter B).

Mirrors tests/test_screener.py's coverage for the claims quality filter.
"""

from __future__ import annotations

import pytest

from src.quality.strategy_screener import (
    REQUIRED_DIMENSIONS,
    derive_scope,
    derive_verdict_from_scores,
    parse_quality_response,
)

# =====================================================================
# RESPONSE PARSING TESTS
# =====================================================================


class TestParseQualityResponse:
    def test_parses_clean_response(self):
        raw = """{
            "scores": {
                "trigger_specificity": "strong",
                "quantification": "strong",
                "completeness": "strong",
                "scale_invariance": "strong",
                "failure_awareness": "partial"
            },
            "verdict": "pass",
            "justification": "Fully quantified with explicit trigger and exit."
        }"""
        result = parse_quality_response(raw)
        assert result["verdict"] == "pass"
        assert result["scores"]["trigger_specificity"] == "strong"

    def test_strips_code_fences(self):
        raw = """```json
        {
            "scores": {
                "trigger_specificity": "weak",
                "quantification": "weak",
                "completeness": "weak",
                "scale_invariance": "weak",
                "failure_awareness": "weak"
            },
            "verdict": "fail",
            "justification": "No specifics at all."
        }
        ```"""
        result = parse_quality_response(raw)
        assert result["verdict"] == "fail"

    def test_defaults_justification_when_missing(self):
        raw = """{
            "scores": {
                "trigger_specificity": "strong",
                "quantification": "strong",
                "completeness": "strong",
                "scale_invariance": "strong",
                "failure_awareness": "strong"
            },
            "verdict": "pass"
        }"""
        result = parse_quality_response(raw)
        assert result["justification"] == ""

    def test_rejects_malformed_json(self):
        with pytest.raises(ValueError, match="Malformed JSON"):
            parse_quality_response("not json")

    def test_rejects_non_object_response(self):
        with pytest.raises(ValueError, match="JSON object"):
            parse_quality_response("[1, 2, 3]")

    def test_rejects_missing_scores(self):
        with pytest.raises(ValueError, match="scores"):
            parse_quality_response('{"verdict": "pass"}')

    def test_rejects_missing_dimensions(self):
        raw = '{"scores": {"trigger_specificity": "strong"}, "verdict": "pass"}'
        with pytest.raises(ValueError, match="missing dimensions"):
            parse_quality_response(raw)

    def test_rejects_invalid_score_value(self):
        raw = """{
            "scores": {
                "trigger_specificity": "excellent",
                "quantification": "strong",
                "completeness": "strong",
                "scale_invariance": "strong",
                "failure_awareness": "strong"
            },
            "verdict": "pass"
        }"""
        with pytest.raises(ValueError, match="Invalid score values"):
            parse_quality_response(raw)

    def test_rejects_invalid_verdict(self):
        raw = """{
            "scores": {
                "trigger_specificity": "strong",
                "quantification": "strong",
                "completeness": "strong",
                "scale_invariance": "strong",
                "failure_awareness": "strong"
            },
            "verdict": "excellent"
        }"""
        with pytest.raises(ValueError, match="Invalid verdict"):
            parse_quality_response(raw)


# =====================================================================
# VERDICT DERIVATION TESTS
# =====================================================================


class TestDeriveVerdict:
    def test_all_strong_is_pass(self):
        scores = dict.fromkeys(REQUIRED_DIMENSIONS, "strong")
        assert derive_verdict_from_scores(scores) == "pass"

    def test_three_strong_one_weak_is_pass(self):
        dims = list(REQUIRED_DIMENSIONS)
        scores = {dims[0]: "weak", dims[1]: "strong", dims[2]: "strong", dims[3]: "strong", dims[4]: "partial"}
        assert derive_verdict_from_scores(scores) == "pass"

    def test_three_weak_is_fail(self):
        dims = list(REQUIRED_DIMENSIONS)
        scores = {dims[0]: "weak", dims[1]: "weak", dims[2]: "weak", dims[3]: "strong", dims[4]: "strong"}
        assert derive_verdict_from_scores(scores) == "fail"

    def test_all_weak_is_fail(self):
        scores = dict.fromkeys(REQUIRED_DIMENSIONS, "weak")
        assert derive_verdict_from_scores(scores) == "fail"

    def test_all_partial_is_partial(self):
        scores = dict.fromkeys(REQUIRED_DIMENSIONS, "partial")
        assert derive_verdict_from_scores(scores) == "partial"

    def test_two_strong_two_weak_one_partial_is_partial(self):
        dims = list(REQUIRED_DIMENSIONS)
        scores = {dims[0]: "strong", dims[1]: "strong", dims[2]: "weak", dims[3]: "weak", dims[4]: "partial"}
        assert derive_verdict_from_scores(scores) == "partial"


# =====================================================================
# SCOPE DERIVATION TESTS
# =====================================================================


class TestDeriveScope:
    def test_short_horizon_is_out_of_scope(self):
        assert derive_scope(3) == "out_of_scope"

    def test_boundary_six_months_is_in_scope(self):
        assert derive_scope(6) == "in_scope"

    def test_long_horizons_are_in_scope(self):
        assert derive_scope(18) == "in_scope"

    def test_null_horizon_stays_in_scope(self):
        assert derive_scope(None) == "in_scope"
