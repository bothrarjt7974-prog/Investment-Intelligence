"""
Tests for the quality filter screener module.

Run with: pytest tests/test_screener.py -v
Or use the standalone runner: python run_screener_tests.py
"""

from __future__ import annotations

import pytest

from src.quality.screener import (
    REQUIRED_DIMENSIONS,
    derive_scope,
    derive_verdict_from_scores,
    parse_quality_response,
)

# =====================================================================
# RESPONSE PARSING TESTS
# =====================================================================


class TestParseQualityResponse:
    """Robust parsing of Claude's JSON output for quality scores."""

    def test_parses_clean_response(self):
        raw = """{
            "scores": {
                "reasoning_depth": "strong",
                "data_support": "strong",
                "why_explanation": "strong",
                "counter_argument_acknowledgment": "partial",
                "falsifiability": "strong"
            },
            "verdict": "pass",
            "justification": "Solid multi-step reasoning with data."
        }"""
        result = parse_quality_response(raw)
        assert result["verdict"] == "pass"
        assert result["scores"]["reasoning_depth"] == "strong"

    def test_strips_code_fences(self):
        raw = """```json
        {
            "scores": {
                "reasoning_depth": "weak",
                "data_support": "weak",
                "why_explanation": "weak",
                "counter_argument_acknowledgment": "weak",
                "falsifiability": "weak"
            },
            "verdict": "fail",
            "justification": "No reasoning shown."
        }
        ```"""
        result = parse_quality_response(raw)
        assert result["verdict"] == "fail"

    def test_handles_plain_code_fences(self):
        raw = "```\n" + '{"scores":{"reasoning_depth":"partial","data_support":"partial","why_explanation":"partial","counter_argument_acknowledgment":"partial","falsifiability":"partial"},"verdict":"partial"}' + "\n```"
        result = parse_quality_response(raw)
        assert result["verdict"] == "partial"

    def test_defaults_justification_when_missing(self):
        raw = """{
            "scores": {
                "reasoning_depth": "strong",
                "data_support": "strong",
                "why_explanation": "strong",
                "counter_argument_acknowledgment": "strong",
                "falsifiability": "strong"
            },
            "verdict": "pass"
        }"""
        result = parse_quality_response(raw)
        assert result["justification"] == ""

    def test_rejects_malformed_json(self):
        with pytest.raises(ValueError, match="Malformed JSON"):
            parse_quality_response("this is not json")

    def test_rejects_non_object_response(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            parse_quality_response('["not an object"]')

    def test_rejects_missing_scores(self):
        with pytest.raises(ValueError, match="missing 'scores'"):
            parse_quality_response('{"verdict": "pass"}')

    def test_rejects_missing_dimensions(self):
        raw = """{
            "scores": {
                "reasoning_depth": "strong",
                "data_support": "strong"
            },
            "verdict": "pass"
        }"""
        with pytest.raises(ValueError, match="missing dimensions"):
            parse_quality_response(raw)

    def test_rejects_invalid_score_value(self):
        raw = """{
            "scores": {
                "reasoning_depth": "excellent",
                "data_support": "strong",
                "why_explanation": "strong",
                "counter_argument_acknowledgment": "strong",
                "falsifiability": "strong"
            },
            "verdict": "pass"
        }"""
        with pytest.raises(ValueError, match="Invalid score"):
            parse_quality_response(raw)

    def test_rejects_invalid_verdict(self):
        raw = """{
            "scores": {
                "reasoning_depth": "strong",
                "data_support": "strong",
                "why_explanation": "strong",
                "counter_argument_acknowledgment": "strong",
                "falsifiability": "strong"
            },
            "verdict": "amazing"
        }"""
        with pytest.raises(ValueError, match="Invalid verdict"):
            parse_quality_response(raw)


# =====================================================================
# VERDICT DERIVATION TESTS
# =====================================================================


class TestDeriveVerdict:
    """The rubric: pass = 3+ strong AND <= 1 weak; fail = 3+ weak; else partial."""

    def test_all_strong_is_pass(self):
        scores = dict.fromkeys(REQUIRED_DIMENSIONS, "strong")
        assert derive_verdict_from_scores(scores) == "pass"

    def test_three_strong_one_weak_is_pass(self):
        scores = {
            "reasoning_depth": "strong",
            "data_support": "strong",
            "why_explanation": "strong",
            "counter_argument_acknowledgment": "partial",
            "falsifiability": "weak",
        }
        assert derive_verdict_from_scores(scores) == "pass"

    def test_three_strong_two_weak_is_partial(self):
        """3 strong but 2 weak — fails the 'no more than 1 weak' rule."""
        scores = {
            "reasoning_depth": "strong",
            "data_support": "strong",
            "why_explanation": "strong",
            "counter_argument_acknowledgment": "weak",
            "falsifiability": "weak",
        }
        assert derive_verdict_from_scores(scores) == "partial"

    def test_all_weak_is_fail(self):
        scores = dict.fromkeys(REQUIRED_DIMENSIONS, "weak")
        assert derive_verdict_from_scores(scores) == "fail"

    def test_three_weak_is_fail(self):
        scores = {
            "reasoning_depth": "weak",
            "data_support": "weak",
            "why_explanation": "weak",
            "counter_argument_acknowledgment": "partial",
            "falsifiability": "partial",
        }
        assert derive_verdict_from_scores(scores) == "fail"

    def test_two_strong_three_partial_is_partial(self):
        scores = {
            "reasoning_depth": "strong",
            "data_support": "strong",
            "why_explanation": "partial",
            "counter_argument_acknowledgment": "partial",
            "falsifiability": "partial",
        }
        assert derive_verdict_from_scores(scores) == "partial"

    def test_all_partial_is_partial(self):
        scores = dict.fromkeys(REQUIRED_DIMENSIONS, "partial")
        assert derive_verdict_from_scores(scores) == "partial"

    def test_two_strong_two_partial_one_weak_is_partial(self):
        scores = {
            "reasoning_depth": "strong",
            "data_support": "strong",
            "why_explanation": "partial",
            "counter_argument_acknowledgment": "partial",
            "falsifiability": "weak",
        }
        assert derive_verdict_from_scores(scores) == "partial"


# =====================================================================
# REAL-WORLD SCENARIO TESTS
# =====================================================================


class TestRealisticPatterns:
    """Test scenarios that match Akshat's actual claim patterns."""

    def test_akshat_strong_thesis_claim(self):
        """A typical Akshat claim with data, reasoning, and risk."""
        raw = """{
            "scores": {
                "reasoning_depth": "strong",
                "data_support": "strong",
                "why_explanation": "strong",
                "counter_argument_acknowledgment": "partial",
                "falsifiability": "strong"
            },
            "verdict": "pass",
            "justification": "Specific numbers, multi-step reasoning, testable directional prediction with 4-5 month horizon."
        }"""
        result = parse_quality_response(raw)
        assert result["verdict"] == "pass"
        derived = derive_verdict_from_scores(result["scores"])
        assert derived == "pass"  # rubric agrees with LLM

    def test_borderline_claim_with_disagreement(self):
        """LLM says partial but rubric says pass — should still trust LLM verdict."""
        raw = """{
            "scores": {
                "reasoning_depth": "strong",
                "data_support": "strong",
                "why_explanation": "strong",
                "counter_argument_acknowledgment": "weak",
                "falsifiability": "strong"
            },
            "verdict": "partial",
            "justification": "Strong on most dimensions but completely lacks risk acknowledgment which we weight heavily."
        }"""
        result = parse_quality_response(raw)
        assert result["verdict"] == "partial"
        # Rubric would say pass (4 strong, 1 weak)
        derived = derive_verdict_from_scores(result["scores"])
        assert derived == "pass"
        # The screener will log this disagreement but trust the LLM

    def test_opinion_only_claim(self):
        """Pure opinion: gold should always be in portfolios."""
        raw = """{
            "scores": {
                "reasoning_depth": "weak",
                "data_support": "weak",
                "why_explanation": "weak",
                "counter_argument_acknowledgment": "weak",
                "falsifiability": "weak"
            },
            "verdict": "fail",
            "justification": "Generic statement with no specific reasoning, data, or testable prediction."
        }"""
        result = parse_quality_response(raw)
        assert result["verdict"] == "fail"


# =====================================================================
# EDGE CASES
# =====================================================================


class TestEdgeCases:
    def test_whitespace_around_json(self):
        raw = "  \n  " + '{"scores":{"reasoning_depth":"strong","data_support":"strong","why_explanation":"strong","counter_argument_acknowledgment":"strong","falsifiability":"strong"},"verdict":"pass"}' + "\n\n"
        result = parse_quality_response(raw)
        assert result["verdict"] == "pass"

    def test_unicode_in_justification(self):
        raw = """{
            "scores": {
                "reasoning_depth": "strong",
                "data_support": "strong",
                "why_explanation": "strong",
                "counter_argument_acknowledgment": "strong",
                "falsifiability": "strong"
            },
            "verdict": "pass",
            "justification": "Strong reasoning with ₹1.1 lakh crore data point cited."
        }"""
        result = parse_quality_response(raw)
        assert "₹" in result["justification"]


# =====================================================================
# HORIZON-BASED SCOPING (technical plan §7)
# =====================================================================


class TestDeriveScope:
    def test_short_horizon_is_out_of_scope(self):
        assert derive_scope(3) == "out_of_scope"
        assert derive_scope(5) == "out_of_scope"

    def test_boundary_six_months_is_out_of_scope(self):
        # Cutoff raised to 7 in the 2026-07-09 audit: "short timeframe"
        # claims land on exactly 6 months (bucket median) and are trading
        # calls, so the whole short bucket is out of guidance scope.
        assert derive_scope(6) == "out_of_scope"
        assert derive_scope(7) == "in_scope"

    def test_long_horizons_are_in_scope(self):
        assert derive_scope(12) == "in_scope"
        assert derive_scope(60) == "in_scope"

    def test_null_horizon_stays_in_scope(self):
        # Opinion-only claims are filtered by verification_status downstream,
        # not by scope — no horizon must not mean "trading call".
        assert derive_scope(None) == "in_scope"
