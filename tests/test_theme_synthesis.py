"""
Tests for theme synthesis orchestration (Layer 6).

All DB/LLM access is monkeypatched — no network or Supabase calls.
"""

from __future__ import annotations

import pytest

from src.synthesis import theme_synthesis
from src.synthesis.theme_synthesis import cluster_claims_by_entity, parse_synthesis_response

# =====================================================================
# CLUSTERING
# =====================================================================


class TestClusterClaimsByEntity:
    def test_groups_by_entity(self):
        claims = [
            {"id": "c1", "entity": "indian_banking"},
            {"id": "c2", "entity": "indian_banking"},
            {"id": "c3", "entity": "gold"},
        ]
        result = cluster_claims_by_entity(claims)
        assert set(result.keys()) == {"indian_banking", "gold"}
        assert len(result["indian_banking"]) == 2
        assert len(result["gold"]) == 1

    def test_null_entity_claims_excluded(self):
        claims = [{"id": "c1", "entity": None}, {"id": "c2", "entity": "gold"}]
        result = cluster_claims_by_entity(claims)
        assert "gold" in result
        assert sum(len(v) for v in result.values()) == 1

    def test_empty_input(self):
        assert cluster_claims_by_entity([]) == {}


# =====================================================================
# RESPONSE PARSING
# =====================================================================


class TestParseSynthesisResponse:
    def test_parses_clean_response(self):
        raw = """{
            "claim_independence": [{"claim_id": "c1", "independence_label": "independent"}],
            "summary_thesis": "Banking will re-rate.",
            "cycle_position": "early cycle",
            "dissenting_views": null,
            "instruments_mentioned": ["HDFCBANK"]
        }"""
        result = parse_synthesis_response(raw)
        assert result["summary_thesis"] == "Banking will re-rate."
        assert result["claim_independence"][0]["independence_label"] == "independent"

    def test_strips_code_fences(self):
        raw = '```json\n{"claim_independence": [], "summary_thesis": "x"}\n```'
        result = parse_synthesis_response(raw)
        assert result["summary_thesis"] == "x"

    def test_defaults_optional_fields(self):
        raw = '{"claim_independence": [], "summary_thesis": "x"}'
        result = parse_synthesis_response(raw)
        assert result["cycle_position"] is None
        assert result["dissenting_views"] is None
        assert result["instruments_mentioned"] == []

    def test_rejects_malformed_json(self):
        with pytest.raises(ValueError, match="Malformed JSON"):
            parse_synthesis_response("not json")

    def test_rejects_non_object(self):
        with pytest.raises(ValueError, match="JSON object"):
            parse_synthesis_response("[1,2,3]")

    def test_rejects_missing_claim_independence(self):
        with pytest.raises(ValueError, match="claim_independence"):
            parse_synthesis_response('{"summary_thesis": "x"}')

    def test_rejects_invalid_independence_label(self):
        raw = '{"claim_independence": [{"claim_id": "c1", "independence_label": "maybe"}], "summary_thesis": "x"}'
        with pytest.raises(ValueError, match="Invalid independence_label"):
            parse_synthesis_response(raw)

    def test_rejects_missing_summary_thesis(self):
        with pytest.raises(ValueError, match="summary_thesis"):
            parse_synthesis_response('{"claim_independence": []}')


# =====================================================================
# RUN LOOP
# =====================================================================


def test_run_theme_synthesis_creates_supersedes_and_counts_errors(monkeypatch):
    claims = [
        {"id": "c1", "entity": "indian_banking", "expert_id": "e1"},
        {"id": "c2", "entity": "indian_banking", "expert_id": "e2"},
        {"id": "c3", "entity": "gold", "expert_id": "e1"},
        {"id": "c4", "entity": "crude_oil", "expert_id": "e1"},  # will error
    ]
    monkeypatch.setattr(theme_synthesis, "fetch_synthesizable_claims", lambda window_days: claims)

    def fake_synthesize_cluster(entity, cluster_claims, window_start, window_end):
        if entity == "indian_banking":
            return {"entity_cluster_key": entity, "summary_thesis": "x"}
        if entity == "gold":
            return None  # skipped, e.g. no resolvable experts
        raise ValueError("boom")

    monkeypatch.setattr(theme_synthesis, "synthesize_cluster", fake_synthesize_cluster)

    superseded: list[str] = []
    inserted: list[dict] = []
    monkeypatch.setattr(
        theme_synthesis, "fetch_where", lambda table, filters: [{"id": "old-theme-1"}] if filters.get("entity_cluster_key") == "indian_banking" else []
    )
    monkeypatch.setattr(
        theme_synthesis, "update_by_id", lambda table, row_id, fields: superseded.append(row_id)
    )
    monkeypatch.setattr(theme_synthesis, "insert_one", lambda table, row: inserted.append(row))

    counts = theme_synthesis.run_theme_synthesis()

    assert counts["themes_created"] == 1
    assert counts["clusters_skipped"] == 1
    assert counts["errors"] == 1
    assert counts["total_claims"] == 4
    assert superseded == ["old-theme-1"]
    assert inserted == [{"entity_cluster_key": "indian_banking", "summary_thesis": "x"}]


def test_run_theme_synthesis_respects_max_clusters(monkeypatch):
    claims = [
        {"id": "c1", "entity": "indian_banking", "expert_id": "e1"},
        {"id": "c2", "entity": "gold", "expert_id": "e1"},
    ]
    monkeypatch.setattr(theme_synthesis, "fetch_synthesizable_claims", lambda window_days: claims)

    calls = []

    def fake_synthesize_cluster(entity, cluster_claims, window_start, window_end):
        calls.append(entity)
        return None

    monkeypatch.setattr(theme_synthesis, "synthesize_cluster", fake_synthesize_cluster)
    monkeypatch.setattr(theme_synthesis, "fetch_where", lambda table, filters: [])
    monkeypatch.setattr(theme_synthesis, "update_by_id", lambda table, row_id, fields: None)
    monkeypatch.setattr(theme_synthesis, "insert_one", lambda table, row: None)

    counts = theme_synthesis.run_theme_synthesis(max_clusters=1)

    assert len(calls) == 1
    assert counts["clusters_skipped"] == 1
