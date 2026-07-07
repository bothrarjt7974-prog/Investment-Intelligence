"""
Tests for the theme -> strategy matcher (Layer 8). OpenAI/DB calls are
monkeypatched; Chroma runs for real via an ephemeral in-memory client.
"""

from __future__ import annotations

import uuid

import chromadb

from src.strategies import matcher


def _ephemeral_collection():
    # A fresh EphemeralClient() still shares underlying state for a
    # collection *name* across instances within the same process (verified
    # empirically) — a unique name per test is what actually guarantees
    # isolation, not just calling EphemeralClient() again.
    client = chromadb.EphemeralClient()
    return client.get_or_create_collection(f"test-{uuid.uuid4()}", metadata={"hnsw:space": "cosine"})


def make_theme(**overrides):
    base = {"id": "theme-1", "entity_cluster_key": "indian_banking", "summary_thesis": "Banking will re-rate"}
    base.update(overrides)
    return base


# =====================================================================
# CREDIBILITY WEIGHT
# =====================================================================


class TestCredibilityWeightForStrategy:
    def test_cfa_framework_gets_fixed_default(self):
        assert (
            matcher._credibility_weight_for_strategy({"expert_id": None})
            == matcher.CFA_DEFAULT_CREDIBILITY_WEIGHT
        )

    def test_uses_long_horizon_bucket_by_default(self, monkeypatch):
        monkeypatch.setattr(
            matcher, "fetch_by_id", lambda table, row_id: {"credibility_long_horizon": 70.0, "credibility_structural": 80.0}
        )
        strategy = {"expert_id": "e1", "holding_horizon_months": 18}
        assert matcher._credibility_weight_for_strategy(strategy) == 70.0

    def test_uses_structural_bucket_beyond_36_months(self, monkeypatch):
        monkeypatch.setattr(
            matcher, "fetch_by_id", lambda table, row_id: {"credibility_long_horizon": 70.0, "credibility_structural": 80.0}
        )
        strategy = {"expert_id": "e1", "holding_horizon_months": 48}
        assert matcher._credibility_weight_for_strategy(strategy) == 80.0

    def test_missing_expert_falls_back_to_neutral_default(self, monkeypatch):
        monkeypatch.setattr(matcher, "fetch_by_id", lambda table, row_id: None)
        assert matcher._credibility_weight_for_strategy({"expert_id": "e1"}) == matcher.DEFAULT_SCORE


# =====================================================================
# BACKTEST WEIGHT
# =====================================================================


class TestBacktestWeightForStrategy:
    def test_incomplete_backtest_is_neutral(self):
        strategy = {"backtest_status": "insufficient_history"}
        assert matcher._backtest_weight_for_strategy(strategy) == matcher.NEUTRAL_BACKTEST_WEIGHT

    def test_complete_backtest_uses_outperformance(self):
        strategy = {"backtest_status": "complete", "backtest_record": {"outperformance_pct": 10.0}}
        assert matcher._backtest_weight_for_strategy(strategy) == 60.0

    def test_clamped_to_zero_and_hundred(self):
        strategy_low = {"backtest_status": "complete", "backtest_record": {"outperformance_pct": -80.0}}
        strategy_high = {"backtest_status": "complete", "backtest_record": {"outperformance_pct": 80.0}}
        assert matcher._backtest_weight_for_strategy(strategy_low) == 0.0
        assert matcher._backtest_weight_for_strategy(strategy_high) == 100.0

    def test_missing_outperformance_is_neutral(self):
        strategy = {"backtest_status": "complete", "backtest_record": {}}
        assert matcher._backtest_weight_for_strategy(strategy) == matcher.NEUTRAL_BACKTEST_WEIGHT


# =====================================================================
# match_theme_to_strategies
# =====================================================================


class TestMatchThemeToStrategies:
    def test_structured_filter_excludes_wrong_entity_class(self, monkeypatch):
        collection = _ephemeral_collection()
        collection.upsert(
            ids=["s1", "s2", "s3"],
            embeddings=[[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]],
            documents=["banking strategy 1", "banking strategy 2", "gold strategy"],
            metadatas=[
                {"entity_class": "indian_banking", "scope": "in_scope", "quality_verdict": "pass", "source_type": "expert_extracted"},
                {"entity_class": "indian_banking", "scope": "in_scope", "quality_verdict": "partial", "source_type": "expert_extracted"},
                {"entity_class": "gold", "scope": "in_scope", "quality_verdict": "pass", "source_type": "expert_extracted"},
            ],
        )
        monkeypatch.setattr(matcher, "get_chroma_collection", lambda: collection)
        monkeypatch.setattr(matcher, "get_embedding", lambda text: [1.0, 0.0])

        strategies_by_id = {
            "s1": {"id": "s1", "expert_id": None, "backtest_status": "pending"},
            "s2": {"id": "s2", "expert_id": None, "backtest_status": "pending"},
        }
        monkeypatch.setattr(matcher, "fetch_by_id", lambda table, row_id: strategies_by_id.get(row_id))
        deleted = []
        inserted = []
        monkeypatch.setattr(matcher, "delete_theme_strategy_matches", lambda theme_id: deleted.append(theme_id))
        monkeypatch.setattr(matcher, "insert_many", lambda table, rows: inserted.extend(rows))

        matches = matcher.match_theme_to_strategies(make_theme())

        assert {m["strategy_id"] for m in matches} == {"s1", "s2"}  # gold (s3) excluded
        assert deleted == ["theme-1"]
        assert inserted == matches

    def test_matches_ranked_by_final_score_descending(self, monkeypatch):
        collection = _ephemeral_collection()
        collection.upsert(
            ids=["s1", "s2"],
            embeddings=[[1.0, 0.0], [0.99, 0.01]],  # s1 closer to query -> higher semantic similarity
            documents=["banking strategy 1", "banking strategy 2"],
            metadatas=[
                {"entity_class": "indian_banking", "scope": "in_scope", "quality_verdict": "pass", "source_type": "expert_extracted"},
                {"entity_class": "indian_banking", "scope": "in_scope", "quality_verdict": "pass", "source_type": "expert_extracted"},
            ],
        )
        monkeypatch.setattr(matcher, "get_chroma_collection", lambda: collection)
        monkeypatch.setattr(matcher, "get_embedding", lambda text: [1.0, 0.0])
        monkeypatch.setattr(
            matcher,
            "fetch_by_id",
            lambda table, row_id: {"id": row_id, "expert_id": None, "backtest_status": "pending"},
        )
        monkeypatch.setattr(matcher, "delete_theme_strategy_matches", lambda theme_id: None)
        monkeypatch.setattr(matcher, "insert_many", lambda table, rows: None)

        matches = matcher.match_theme_to_strategies(make_theme())

        assert matches[0]["strategy_id"] == "s1"
        assert matches[0]["rank"] == 1
        assert matches[0]["final_score"] >= matches[1]["final_score"]

    def test_respects_top_n(self, monkeypatch):
        collection = _ephemeral_collection()
        ids = [f"s{i}" for i in range(10)]
        collection.upsert(
            ids=ids,
            embeddings=[[1.0, float(i) / 100] for i in range(10)],
            documents=[f"strategy {i}" for i in range(10)],
            metadatas=[
                {"entity_class": "indian_banking", "scope": "in_scope", "quality_verdict": "pass", "source_type": "expert_extracted"}
                for _ in range(10)
            ],
        )
        monkeypatch.setattr(matcher, "get_chroma_collection", lambda: collection)
        monkeypatch.setattr(matcher, "get_embedding", lambda text: [1.0, 0.0])
        monkeypatch.setattr(
            matcher, "fetch_by_id", lambda table, row_id: {"id": row_id, "expert_id": None, "backtest_status": "pending"}
        )
        monkeypatch.setattr(matcher, "delete_theme_strategy_matches", lambda theme_id: None)
        monkeypatch.setattr(matcher, "insert_many", lambda table, rows: None)

        matches = matcher.match_theme_to_strategies(make_theme(), top_n=3)

        assert len(matches) == 3


# =====================================================================
# run_matching — loop/error handling
# =====================================================================


def test_run_matching_counts_by_outcome(monkeypatch):
    themes = [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}]
    monkeypatch.setattr(matcher, "fetch_themes_ready_for_matching", lambda limit: themes)

    def fake_match(theme, top_n=5):
        if theme["id"] == "t1":
            return [{"strategy_id": "s1"}]
        if theme["id"] == "t2":
            return []
        raise ValueError("boom")

    monkeypatch.setattr(matcher, "match_theme_to_strategies", fake_match)

    counts = matcher.run_matching()

    assert counts == {"matched": 1, "no_candidates": 1, "error": 1, "total": 3}
