"""
Tests for historical pattern detection (Layer 8). Pure deterministic
clustering — no LLM. DB access is monkeypatched.
"""

from __future__ import annotations

from src.strategies import historical_patterns
from src.strategies.historical_patterns import (
    MIN_PATTERN_REPETITIONS,
    _cluster_by_similarity,
    _to_strategy_obj,
    detect_patterns_for_expert,
)


def make_row(**overrides):
    base = {
        "id": "strat-1",
        "content_piece_id": "piece-1",
        "expert_id": "expert-1",
        "strategy_text": "Allocate 40 percent of equity to PSU banks weighted by market cap",
        "strategy_type": "allocation",
        "entity_class": "indian_banking",
        "trigger_conditions": {"type": "rate_cut_cycle"},
        "allocation_rule": "40%",
        "position_sizing": None,
        "entry_methodology": None,
        "holding_horizon_months": 18,
        "exit_conditions": None,
        "risk_framework": None,
        "reason_given": "Rate cuts re-rate PSU banks",
    }
    base.update(overrides)
    return base


class TestToStrategyObj:
    def test_retains_raw_row(self):
        row = make_row()
        obj = _to_strategy_obj(row)
        assert obj.strategy_text == row["strategy_text"]
        assert obj.raw is row


class TestClusterBySimilarity:
    def test_similar_strategies_cluster_together(self):
        a = _to_strategy_obj(make_row(id="s1", content_piece_id="p1"))
        b = _to_strategy_obj(
            make_row(
                id="s2",
                content_piece_id="p2",
                strategy_text="Allocate 40 percent to PSU banks weighted by market cap",
            )
        )
        clusters = _cluster_by_similarity([a, b])
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_dissimilar_strategies_stay_separate(self):
        a = _to_strategy_obj(make_row(id="s1", strategy_text="Allocate 40% to PSU banks"))
        b = _to_strategy_obj(
            make_row(id="s2", content_piece_id="p2", strategy_text="Keep 5-10% of portfolio in gold")
        )
        clusters = _cluster_by_similarity([a, b])
        assert len(clusters) == 2


class TestDetectPatternsForExpert:
    def test_repeated_strategy_across_pieces_becomes_a_pattern(self, monkeypatch):
        rows = [
            make_row(id="s1", content_piece_id="p1"),
            make_row(id="s2", content_piece_id="p2"),
            make_row(
                id="s3",
                content_piece_id="p3",
                strategy_text="Allocate 40 percent to PSU banks weighted by market cap",
            ),
        ]
        monkeypatch.setattr(historical_patterns, "fetch_strategies_by_expert", lambda expert_id: rows)

        patterns = detect_patterns_for_expert("expert-1")

        assert len(patterns) == 1
        pattern = patterns[0]
        assert pattern["source_type"] == "historical_pattern"
        assert pattern["expert_id"] == "expert-1"
        assert pattern["content_piece_id"] is None
        assert sorted(pattern["source_strategy_ids"]) == ["s1", "s2", "s3"]
        assert pattern["entity_class"] == "indian_banking"
        assert pattern["quality_verdict"] == "pass"

    def test_fewer_than_minimum_pieces_no_pattern(self, monkeypatch):
        rows = [
            make_row(id="s1", content_piece_id="p1"),
            make_row(id="s2", content_piece_id="p2"),
        ]
        assert len(rows) < MIN_PATTERN_REPETITIONS
        monkeypatch.setattr(historical_patterns, "fetch_strategies_by_expert", lambda expert_id: rows)

        assert detect_patterns_for_expert("expert-1") == []

    def test_repeated_within_same_pieces_not_a_pattern(self, monkeypatch):
        # 3 strategies but only 2 distinct content pieces -> not a real pattern.
        rows = [
            make_row(id="s1", content_piece_id="p1"),
            make_row(id="s2", content_piece_id="p1"),
            make_row(id="s3", content_piece_id="p2"),
        ]
        monkeypatch.setattr(historical_patterns, "fetch_strategies_by_expert", lambda expert_id: rows)

        assert detect_patterns_for_expert("expert-1") == []

    def test_different_entity_class_not_merged(self, monkeypatch):
        rows = [
            make_row(id="s1", content_piece_id="p1", entity_class="indian_banking"),
            make_row(id="s2", content_piece_id="p2", entity_class="indian_banking"),
            make_row(id="s3", content_piece_id="p3", entity_class="gold", strategy_text="Keep 5-10% in gold"),
        ]
        monkeypatch.setattr(historical_patterns, "fetch_strategies_by_expert", lambda expert_id: rows)

        # gold group has only 1 strategy -> below MIN_PATTERN_REPETITIONS, no pattern from it.
        # indian_banking group has 2 -> also below threshold on its own.
        assert detect_patterns_for_expert("expert-1") == []


class TestRunHistoricalPatternDetection:
    def test_skips_already_promoted_patterns(self, monkeypatch):
        monkeypatch.setattr(historical_patterns, "fetch_active_expert_ids", lambda: ["expert-1"])

        candidate = {
            "source_type": "historical_pattern",
            "expert_id": "expert-1",
            "source_strategy_ids": ["s1", "s2", "s3"],
            "strategy_text": "x",
            "strategy_type": "allocation",
        }
        monkeypatch.setattr(
            historical_patterns, "detect_patterns_for_expert", lambda expert_id: [candidate]
        )
        monkeypatch.setattr(
            historical_patterns,
            "fetch_where",
            lambda table, filters: [{"source_strategy_ids": ["s1", "s2", "s3"]}],
        )
        inserted = []
        monkeypatch.setattr(historical_patterns, "insert_many", lambda table, rows: inserted.extend(rows))

        counts = historical_patterns.run_historical_pattern_detection()

        assert counts["patterns_created"] == 0
        assert inserted == []

    def test_inserts_new_patterns(self, monkeypatch):
        monkeypatch.setattr(historical_patterns, "fetch_active_expert_ids", lambda: ["expert-1"])
        candidate = {
            "source_type": "historical_pattern",
            "expert_id": "expert-1",
            "source_strategy_ids": ["s1", "s2", "s3"],
            "strategy_text": "x",
            "strategy_type": "allocation",
        }
        monkeypatch.setattr(
            historical_patterns, "detect_patterns_for_expert", lambda expert_id: [candidate]
        )
        monkeypatch.setattr(historical_patterns, "fetch_where", lambda table, filters: [])
        inserted = []
        monkeypatch.setattr(historical_patterns, "insert_many", lambda table, rows: inserted.extend(rows))

        counts = historical_patterns.run_historical_pattern_detection()

        assert counts["patterns_created"] == 1
        assert inserted == [candidate]
