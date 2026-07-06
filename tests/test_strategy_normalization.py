"""
Tests for the strategy normalization module (Track B).

Mirrors tests/test_normalization.py's coverage for the claims pipeline:
parsing, entity_class normalization, within-content dedup, and horizon
coercion.
"""

from __future__ import annotations

from src.extraction.normalization import (
    Strategy,
    _parse_strategy,
    deduplicate_strategies_within_video,
    normalize_strategies,
)

# =====================================================================
# PARSING TESTS
# =====================================================================


class TestParseStrategy:
    def test_parses_minimal_valid_strategy(self):
        raw = {"strategy_text": "Allocate 40% to PSU banks", "strategy_type": "allocation"}
        strategy = _parse_strategy(raw)
        assert strategy is not None
        assert strategy.strategy_text == "Allocate 40% to PSU banks"
        assert strategy.strategy_type == "allocation"

    def test_drops_strategy_missing_strategy_text(self):
        raw = {"strategy_type": "allocation"}
        assert _parse_strategy(raw) is None

    def test_drops_strategy_missing_strategy_type(self):
        raw = {"strategy_text": "Allocate 40% to PSU banks"}
        assert _parse_strategy(raw) is None

    def test_entity_class_normalized_via_shared_alias_map(self):
        # Same canonical naming rules as claims — reuses normalize_entity.
        raw = {
            "strategy_text": "Hold Nifty 50 index funds",
            "strategy_type": "allocation",
            "entity_class": "Nifty 50",
        }
        strategy = _parse_strategy(raw)
        assert strategy.entity_class == "nifty50"

    def test_linked_claim_entity_normalized(self):
        raw = {
            "strategy_text": "Allocate to banking on rate cuts",
            "strategy_type": "allocation",
            "linked_claim_entity": "Indian_Banking",
        }
        strategy = _parse_strategy(raw)
        assert strategy.linked_claim_entity == "indian_banking"

    def test_positive_horizon_kept(self):
        raw = {
            "strategy_text": "Hold for 18 months",
            "strategy_type": "allocation",
            "holding_horizon_months": 18,
        }
        assert _parse_strategy(raw).holding_horizon_months == 18

    def test_non_positive_horizon_dropped(self):
        raw = {
            "strategy_text": "Hold forever",
            "strategy_type": "allocation",
            "holding_horizon_months": 0,
        }
        assert _parse_strategy(raw).holding_horizon_months is None

    def test_non_numeric_horizon_dropped(self):
        raw = {
            "strategy_text": "Hold for a while",
            "strategy_type": "allocation",
            "holding_horizon_months": "a while",
        }
        assert _parse_strategy(raw).holding_horizon_months is None

    def test_missing_horizon_stays_none(self):
        raw = {"strategy_text": "Allocate opportunistically", "strategy_type": "allocation"}
        assert _parse_strategy(raw).holding_horizon_months is None


# =====================================================================
# WITHIN-VIDEO DEDUP TESTS
# =====================================================================


class TestDeduplicateStrategiesWithinVideo:
    def test_single_strategy_passes_through(self):
        strategies = [Strategy(strategy_text="Allocate 40% to banking", strategy_type="allocation")]
        assert deduplicate_strategies_within_video(strategies) == strategies

    def test_merges_near_duplicate_same_entity_class(self):
        a = Strategy(
            strategy_text="Allocate 40 percent of equity to PSU banks weighted by market cap",
            strategy_type="allocation",
            entity_class="indian_banking",
            exit_conditions="Trim at 30 percent above trend",
        )
        b = Strategy(
            strategy_text="Allocate 40 percent to PSU banks weighted by market cap",
            strategy_type="allocation",
            entity_class="indian_banking",
        )

        result = deduplicate_strategies_within_video([a, b])

        assert len(result) == 1
        # The more complete one (has exit_conditions) should be kept.
        assert result[0].exit_conditions == "Trim at 30 percent above trend"

    def test_different_entity_classes_not_merged(self):
        a = Strategy(strategy_text="Allocate to PSU banks", strategy_type="allocation", entity_class="indian_banking")
        b = Strategy(strategy_text="Allocate to gold", strategy_type="allocation", entity_class="gold")

        result = deduplicate_strategies_within_video([a, b])

        assert len(result) == 2


# =====================================================================
# FULL PIPELINE TESTS
# =====================================================================


class TestNormalizeStrategies:
    def test_empty_input_returns_empty_list(self):
        assert normalize_strategies([]) == []

    def test_full_pipeline_produces_db_ready_dicts(self):
        raw = [
            {
                "strategy_text": "Allocate 40% to PSU banks, hold 18 months",
                "strategy_type": "allocation",
                "trigger_conditions": {"type": "rate_cut_cycle"},
                "entity_class": "indian_banking",
                "allocation_rule": "40%",
                "position_sizing": None,
                "entry_methodology": None,
                "holding_horizon_months": 18,
                "exit_conditions": None,
                "risk_framework": None,
                "reason_given": "Rate cuts re-rate PSU banks",
                "linked_claim_entity": "indian_banking",
            }
        ]

        result = normalize_strategies(raw)

        assert len(result) == 1
        row = result[0]
        assert row["strategy_text"] == "Allocate 40% to PSU banks, hold 18 months"
        assert row["entity_class"] == "indian_banking"
        assert row["holding_horizon_months"] == 18
        assert row["linked_claim_entity"] == "indian_banking"

    def test_malformed_entries_dropped_without_crashing(self):
        raw = [
            {"strategy_text": "Valid strategy", "strategy_type": "allocation"},
            {"strategy_type": "allocation"},  # missing strategy_text
            {},
        ]

        result = normalize_strategies(raw)

        assert len(result) == 1
        assert result[0]["strategy_text"] == "Valid strategy"
