"""
Tests for guidance generation orchestration (Layer 9). All DB/Claude
access is monkeypatched — no network or Supabase calls.
"""

from __future__ import annotations

import json

import pytest

from src.guidance import generator
from src.guidance.generator import (
    build_attribution,
    generate_guidance_for_portfolio,
    parse_guidance_response,
)


def make_theme(**overrides):
    base = {
        "id": "theme-1",
        "entity_cluster_key": "indian_banking",
        "conviction_level": "high",
        "fundamentals_verdict": "pass",
        # HDFCBANK must be grounded here — the sizing gate rejects any
        # response instrument not traceable to the theme/strategy inputs.
        "instruments_mentioned": ["HDFCBANK"],
        "summary_thesis": "Banking will re-rate",
        "cycle_position": "early bull",
        "supporting_experts": [{"expert_id": "e1", "expert_name": "Akshat"}],
    }
    base.update(overrides)
    return base


def make_strategy(**overrides):
    base = {
        "id": "strategy-1",
        "strategy_text": "Allocate 40% to banks",
        "allocation_rule": "40%",
        "exit_conditions": None,
        "position_sizing": None,
        "source_type": "expert_extracted",
        "source_name": None,
        "expert_id": "e1",
        "backtest_status": "pending",
        "backtest_record": None,
    }
    base.update(overrides)
    return base


def make_portfolio(**overrides):
    base = {"id": "portfolio-1", "user_id": "user-1", "risk_profile": "moderate", "restrictions": [], "tax_notes": None}
    base.update(overrides)
    return base


VALID_RESPONSE = json.dumps(
    {
        "guidance_type": "INCREASE_EXPOSURE",
        "target_allocation_change": {"entity": "indian_banking", "from_pct": 5.0, "to_pct": 15.0},
        "specific_instruments": [{"entity": "HDFCBANK", "target_pct": 15.0}],
        "execution_horizon": "Over 12 months",
        "position_holding_horizon": "3 years",
        "funding_source": None,
        "tax_note": None,
        "estimated_tax_impact": None,
        "risks": "Rate cycle could reverse",
    }
)


# =====================================================================
# RESPONSE PARSING
# =====================================================================


class TestParseGuidanceResponse:
    def test_parses_valid_response(self):
        result = parse_guidance_response(VALID_RESPONSE)
        assert result["guidance_type"] == "INCREASE_EXPOSURE"

    def test_strips_code_fences(self):
        raw = f"```json\n{VALID_RESPONSE}\n```"
        result = parse_guidance_response(raw)
        assert result["guidance_type"] == "INCREASE_EXPOSURE"

    def test_rejects_malformed_json(self):
        with pytest.raises(ValueError, match="Malformed JSON"):
            parse_guidance_response("not json")

    def test_rejects_invalid_guidance_type(self):
        raw = json.dumps({"guidance_type": "SELL_EVERYTHING"})
        with pytest.raises(ValueError, match="Invalid guidance_type"):
            parse_guidance_response(raw)

    def test_defaults_optional_fields(self):
        raw = json.dumps({"guidance_type": "MAINTAIN"})
        result = parse_guidance_response(raw)
        assert result["target_allocation_change"] is None
        assert result["specific_instruments"] == []


# =====================================================================
# build_attribution
# =====================================================================


class TestBuildAttribution:
    def test_assembles_direction_and_execution_from_real_rows(self):
        theme = make_theme()
        strategy = make_strategy(source_name="CFA Framework X")

        attribution = build_attribution(theme, strategy)

        assert attribution["direction"] == theme["supporting_experts"]
        assert attribution["execution"]["strategy_id"] == "strategy-1"
        assert attribution["execution"]["source_name"] == "CFA Framework X"
        assert attribution["execution"]["backtest_summary"] is None  # backtest_status='pending'

    def test_includes_backtest_summary_when_complete(self):
        strategy = make_strategy(backtest_status="complete", backtest_record={"avg_return_pct": 10.0})
        attribution = build_attribution(make_theme(), strategy)
        assert attribution["execution"]["backtest_summary"] == {"avg_return_pct": 10.0}

    def test_sizing_source_reflects_stated_position_sizing(self):
        strategy = make_strategy(position_sizing="Never more than 10%")
        attribution = build_attribution(make_theme(), strategy)
        assert "own stated" in attribution["sizing_source"]


# =====================================================================
# generate_guidance_for_portfolio — refusal paths (no LLM call)
# =====================================================================


class TestGenerateGuidanceRefusalPaths:
    def _fail_if_llm_called(self, monkeypatch):
        def fail(*args, **kwargs):
            raise AssertionError("LLM must not be called when a gate refuses eligibility")

        monkeypatch.setattr(generator.claude, "synthesize", fail)

    def test_low_conviction_refused_without_llm_call(self, monkeypatch):
        self._fail_if_llm_called(monkeypatch)
        monkeypatch.setattr(generator, "fetch_best_strategy_match", lambda theme_id: make_strategy())

        result = generate_guidance_for_portfolio(make_portfolio(), make_theme(conviction_level="low"))

        assert result["is_refusal"] is True
        assert "conviction" in result["refusal_reason"]

    def test_no_matched_strategy_refused_without_llm_call(self, monkeypatch):
        self._fail_if_llm_called(monkeypatch)
        monkeypatch.setattr(generator, "fetch_best_strategy_match", lambda theme_id: None)

        result = generate_guidance_for_portfolio(make_portfolio(), make_theme())

        assert result["is_refusal"] is True
        assert "matched" in result["refusal_reason"]

    def test_duplicate_guidance_refused_without_llm_call(self, monkeypatch):
        self._fail_if_llm_called(monkeypatch)
        monkeypatch.setattr(generator, "fetch_best_strategy_match", lambda theme_id: make_strategy())
        monkeypatch.setattr(generator, "check_duplicate_guidance", lambda theme_id, portfolio_id: True)

        result = generate_guidance_for_portfolio(make_portfolio(), make_theme())

        assert result["is_refusal"] is True
        assert "already exists" in result["refusal_reason"]

    def test_already_aligned_refused_without_llm_call(self, monkeypatch):
        self._fail_if_llm_called(monkeypatch)
        monkeypatch.setattr(generator, "fetch_best_strategy_match", lambda theme_id: make_strategy())
        monkeypatch.setattr(generator, "check_duplicate_guidance", lambda theme_id, portfolio_id: False)
        monkeypatch.setattr(generator, "check_entity_overlap_guidance", lambda theme, portfolio_id: None)
        monkeypatch.setattr(generator, "fetch_portfolio_holdings", lambda portfolio_id: [{"entity": "HDFCBANK", "quantity": 100, "cost_basis": 1500.0}])
        monkeypatch.setattr(generator, "check_already_aligned", lambda theme, holdings: True)

        result = generate_guidance_for_portfolio(make_portfolio(), make_theme())

        assert result["is_refusal"] is True
        assert "aligned" in result["refusal_reason"]


# =====================================================================
# generate_guidance_for_portfolio — happy path
# =====================================================================


class TestGenerateGuidanceHappyPath:
    def test_calls_llm_and_builds_full_row(self, monkeypatch):
        strategy = make_strategy()
        monkeypatch.setattr(generator, "fetch_best_strategy_match", lambda theme_id: strategy)
        monkeypatch.setattr(generator, "check_duplicate_guidance", lambda theme_id, portfolio_id: False)
        monkeypatch.setattr(generator, "check_entity_overlap_guidance", lambda theme, portfolio_id: None)
        monkeypatch.setattr(generator, "fetch_portfolio_holdings", lambda portfolio_id: [])
        monkeypatch.setattr(generator, "check_already_aligned", lambda theme, holdings: False)
        monkeypatch.setattr(generator.claude, "synthesize", lambda **kwargs: VALID_RESPONSE)

        result = generate_guidance_for_portfolio(make_portfolio(), make_theme())

        assert result["is_refusal"] is False
        assert result["guidance_type"] == "INCREASE_EXPOSURE"
        assert result["matched_strategy_id"] == "strategy-1"
        assert result["attribution"]["execution"]["strategy_id"] == "strategy-1"


# =====================================================================
# run_guidance_generation — loop/error handling
# =====================================================================


def test_run_guidance_generation_counts_by_outcome(monkeypatch):
    themes = [make_theme(id="t1"), make_theme(id="t2")]
    users = [{"id": "user-1", "risk_profile": "moderate", "restrictions": [], "tax_notes": None, "is_active": True}]
    portfolios = [make_portfolio()]

    monkeypatch.setattr(generator, "fetch_themes_ready_for_matching", lambda limit: themes)
    monkeypatch.setattr(generator, "fetch_active_users", lambda: users)
    monkeypatch.setattr(generator, "fetch_portfolios_for_user", lambda user_id: portfolios)

    def fake_generate(portfolio, theme):
        if theme["id"] == "t1":
            return {"is_refusal": True, "refusal_reason": "x",
                    "theme_id": "t1", "portfolio_id": portfolio["id"]}
        raise ValueError("boom")

    monkeypatch.setattr(generator, "generate_guidance_for_portfolio", fake_generate)
    monkeypatch.setattr(generator, "fetch_active_refusal", lambda tid, pid, reason: [])
    inserted = []
    monkeypatch.setattr(generator, "insert_one", lambda table, row: inserted.append(row))

    counts = generator.run_guidance_generation()

    assert counts["refused"] == 1
    assert counts["error"] == 1
    assert counts["guidance_generated"] == 0
    assert counts["total"] == 2
    assert len(inserted) == 1  # the errored one never got a row inserted


class TestRefusalDedup:
    """One active refusal row per (theme, portfolio, reason) — 2026-07-10."""

    def _run(self, monkeypatch, existing_refusal_rows):
        inserted = []
        monkeypatch.setattr(generator, "fetch_themes_ready_for_matching",
                            lambda limit: [make_theme(conviction_level="low")])
        monkeypatch.setattr(generator, "fetch_active_users",
                            lambda: [{"id": "u1", "risk_profile": "moderate",
                                      "restrictions": [], "tax_notes": None}])
        monkeypatch.setattr(generator, "fetch_portfolios_for_user",
                            lambda uid: [{"id": "p1", "user_id": "u1"}])
        monkeypatch.setattr(generator, "fetch_best_strategy_match", lambda tid: make_strategy())
        monkeypatch.setattr(generator, "fetch_active_refusal",
                            lambda tid, pid, reason: existing_refusal_rows)
        monkeypatch.setattr(generator, "insert_one",
                            lambda table, row: inserted.append(row))
        counts = generator.run_guidance_generation()
        return counts, inserted

    def test_first_refusal_inserted(self, monkeypatch):
        counts, inserted = self._run(monkeypatch, existing_refusal_rows=[])
        assert counts["refused"] == 1
        assert counts["refusal_deduped"] == 0
        assert len(inserted) == 1
        assert inserted[0]["is_refusal"] is True

    def test_repeat_refusal_skipped(self, monkeypatch):
        counts, inserted = self._run(monkeypatch, existing_refusal_rows=[{"id": "r1"}])
        assert counts["refused"] == 0
        assert counts["refusal_deduped"] == 1
        assert inserted == []
