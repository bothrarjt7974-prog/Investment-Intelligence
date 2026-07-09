"""
Tests for the fundamentals sanity check orchestration (Layer 7).

All DB/yfinance/Claude access is monkeypatched — no network or Supabase
calls, same convention as every existing test file.
"""

from __future__ import annotations

import json

import pytest

from src.fundamentals import checker
from src.fundamentals.checker import check_one_theme, parse_fundamentals_response


def make_theme(**overrides):
    base = {"id": "theme-1", "entity_cluster_key": "RELIANCE", "instruments_mentioned": []}
    base.update(overrides)
    return base


# =====================================================================
# RESPONSE PARSING
# =====================================================================


class TestParseFundamentalsResponse:
    def test_parses_clean_response(self):
        raw = json.dumps(
            {
                "verdict": "pass",
                "valuation_flag": "strong",
                "earnings_trajectory_flag": "neutral",
                "notes": "Looks fine.",
            }
        )
        result = parse_fundamentals_response(raw)
        assert result["verdict"] == "pass"

    def test_strips_code_fences(self):
        raw = '```json\n{"verdict": "reject", "valuation_flag": "weak", "earnings_trajectory_flag": "weak"}\n```'
        result = parse_fundamentals_response(raw)
        assert result["verdict"] == "reject"
        assert result["notes"] == ""

    def test_rejects_malformed_json(self):
        with pytest.raises(ValueError, match="Malformed JSON"):
            parse_fundamentals_response("not json")

    def test_rejects_invalid_verdict(self):
        raw = json.dumps({"verdict": "maybe", "valuation_flag": "strong", "earnings_trajectory_flag": "strong"})
        with pytest.raises(ValueError, match="Invalid verdict"):
            parse_fundamentals_response(raw)

    def test_rejects_invalid_flag(self):
        raw = json.dumps({"verdict": "pass", "valuation_flag": "excellent", "earnings_trajectory_flag": "strong"})
        with pytest.raises(ValueError, match="Invalid valuation_flag"):
            parse_fundamentals_response(raw)


# =====================================================================
# check_one_theme — exit paths
# =====================================================================


class TestIndexValuationBranch:
    def test_index_within_band_passes_deterministically(self, monkeypatch):
        monkeypatch.setattr(
            checker,
            "fetch_index_valuation",
            lambda key: {"proxy": "NIFTYBEES.NS", "trailing_pe": 21.5, "band_low": 17.0, "band_high": 24.0, "position": "within"},
        )

        def fail_if_called(*args, **kwargs):
            raise AssertionError("LLM must not be called for index-level checks")

        monkeypatch.setattr(checker.claude, "call", fail_if_called)

        result = check_one_theme(make_theme(entity_cluster_key="nifty50"))

        assert result["fundamentals_verdict"] == "pass"
        assert "21.5" in result["fundamentals_notes"]

    def test_index_above_band_downgrades(self, monkeypatch):
        monkeypatch.setattr(
            checker,
            "fetch_index_valuation",
            lambda key: {"proxy": "SPY", "trailing_pe": 26.8, "band_low": 16.0, "band_high": 24.0, "position": "above"},
        )

        result = check_one_theme(make_theme(entity_cluster_key="sp500"))

        assert result["fundamentals_verdict"] == "downgrade"

    def test_fetch_failure_falls_through_to_existing_paths(self, monkeypatch):
        monkeypatch.setattr(checker, "fetch_index_valuation", lambda key: None)
        monkeypatch.setattr(checker, "resolve_target_instruments", lambda theme: [])

        result = check_one_theme(make_theme(entity_cluster_key="sp500"))

        assert result["fundamentals_verdict"] == "not_applicable"


class TestCheckOneTheme:
    def test_not_applicable_when_no_instruments_resolved(self, monkeypatch):
        monkeypatch.setattr(checker, "resolve_target_instruments", lambda theme: [])

        result = check_one_theme(make_theme())

        assert result["fundamentals_verdict"] == "not_applicable"

    def test_insufficient_data_when_no_snapshots_resolve(self, monkeypatch):
        monkeypatch.setattr(checker, "resolve_target_instruments", lambda theme: ["RELIANCE"])
        monkeypatch.setattr(checker, "fetch_fundamentals_snapshot", lambda symbol: None)

        result = check_one_theme(make_theme())

        assert result["fundamentals_verdict"] == "insufficient_data"

    def test_deterministic_reject_on_balance_sheet_veto_skips_llm(self, monkeypatch):
        monkeypatch.setattr(checker, "resolve_target_instruments", lambda theme: ["RELIANCE"])
        monkeypatch.setattr(
            checker,
            "fetch_fundamentals_snapshot",
            lambda symbol: {"debtToEquity": 300.0, "currentRatio": 0.5, "trailingPE": 20.0},
        )
        monkeypatch.setattr(checker, "check_balance_sheet_red_flag", lambda snapshot: True)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("LLM should not be called when the balance-sheet veto fires")

        monkeypatch.setattr(checker.claude, "call", fail_if_called)

        result = check_one_theme(make_theme())

        assert result["fundamentals_verdict"] == "reject"
        assert "Balance sheet hard veto" in result["fundamentals_notes"]

    def test_llm_verdict_used_when_no_veto(self, monkeypatch):
        monkeypatch.setattr(checker, "resolve_target_instruments", lambda theme: ["RELIANCE"])
        monkeypatch.setattr(
            checker,
            "fetch_fundamentals_snapshot",
            lambda symbol: {"debtToEquity": 30.0, "currentRatio": 1.5, "trailingPE": 22.0},
        )
        monkeypatch.setattr(checker, "check_balance_sheet_red_flag", lambda snapshot: False)
        monkeypatch.setattr(checker, "compute_sector_relative_pe", lambda entity, pe: 1.05)
        monkeypatch.setattr(
            checker.claude,
            "call",
            lambda **kwargs: json.dumps(
                {
                    "verdict": "pass",
                    "valuation_flag": "strong",
                    "earnings_trajectory_flag": "neutral",
                    "notes": "In line with peers.",
                }
            ),
        )

        result = check_one_theme(make_theme())

        assert result["fundamentals_verdict"] == "pass"
        assert result["fundamentals_notes"] == "In line with peers."
        assert result["fundamentals_data"]["valuation_flag"] == "strong"


# =====================================================================
# run_fundamentals_check — loop/error handling
# =====================================================================


def test_run_fundamentals_check_counts_by_verdict(monkeypatch):
    updates: dict[str, dict] = {}
    themes = [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}]
    results = {
        "t1": {"fundamentals_verdict": "pass"},
        "t2": {"fundamentals_verdict": "not_applicable"},
        "t3": None,  # simulate a failure
    }

    monkeypatch.setattr(checker, "fetch_themes_pending_fundamentals_check", lambda limit: themes)

    def fake_check(theme):
        result = results[theme["id"]]
        if result is None:
            raise ValueError("boom")
        return result

    monkeypatch.setattr(checker, "check_one_theme", fake_check)
    monkeypatch.setattr(checker, "update_by_id", lambda table, row_id, fields: updates.setdefault(row_id, fields))

    counts = checker.run_fundamentals_check()

    assert counts["pass"] == 1
    assert counts["not_applicable"] == 1
    assert counts["error"] == 1
    assert counts["total"] == 3
    assert "t3" not in updates
