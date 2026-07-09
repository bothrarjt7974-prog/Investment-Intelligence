"""
Tests for the deterministic sizing gate (Layer 9). Pure logic — no DB,
no LLM.
"""

from __future__ import annotations

import pytest

from src.guidance.sizing import (
    InventedInstrumentError,
    apply_sizing_gate,
    extract_allocation_pct,
)


def make_theme(**overrides):
    base = {
        "entity_cluster_key": "us_tech",
        "instruments_mentioned": ["AAPL", "NVDA", "MSFT"],
    }
    base.update(overrides)
    return base


def make_strategy(**overrides):
    base = {"entity_class": "us_equity", "allocation_rule": None}
    base.update(overrides)
    return base


def make_payload(**overrides):
    base = {
        "guidance_type": "INTRODUCE_NEW",
        "target_allocation_change": {"entity": "us_tech", "from_pct": 0.0, "to_pct": 8.0},
        "specific_instruments": [
            {"entity": "NVDA", "target_pct": 4.0},
            {"entity": "AAPL", "target_pct": 4.0},
        ],
    }
    base.update(overrides)
    return base


class TestExtractAllocationPct:
    def test_explicit_percent(self):
        assert extract_allocation_pct("Allocate 40% to banks") == 40.0

    def test_decimal_percent(self):
        assert extract_allocation_pct("hold 7.5 % in gold") == 7.5

    def test_none_rule(self):
        assert extract_allocation_pct(None) is None

    def test_bare_ratio_not_trusted(self):
        # "60/40" is ambiguous about which side applies — no % sign, no match.
        assert extract_allocation_pct("A 60/40 split") is None


class TestInstrumentGrounding:
    def test_grounded_instruments_pass(self):
        payload, _ = apply_sizing_gate(make_payload(), make_theme(), make_strategy(), "moderate")
        assert payload["target_allocation_change"]["to_pct"] == 8.0

    def test_invented_instrument_rejected(self):
        payload = make_payload(
            specific_instruments=[{"entity": "DOGECOIN", "target_pct": 5.0}]
        )
        with pytest.raises(InventedInstrumentError, match="DOGECOIN"):
            apply_sizing_gate(payload, make_theme(), make_strategy(), "moderate")

    def test_existing_holding_is_always_grounded(self):
        # Recommending action on something the user actually holds (a
        # trim/exit/funding source) is never invention — even when the
        # theme never named that instrument.
        payload = make_payload(
            guidance_type="EXIT",
            target_allocation_change={"entity": "TECHM", "from_pct": 4.0, "to_pct": 0.0},
            specific_instruments=[{"entity": "TECHM", "target_pct": 0.0}],
        )
        holdings = [{"entity": "TECHM", "quantity": 26, "cost_basis": 1497.92}]
        payload, source = apply_sizing_gate(
            payload, make_theme(instruments_mentioned=["INFY", "TCS"]), make_strategy(), "moderate", holdings
        )
        assert source == "reduction_not_capped"

    def test_composite_label_grounded_by_token(self):
        payload = make_payload(
            specific_instruments=[
                {"entity": "us_tech_basket (AAPL, NVDA)", "target_pct": 5.0}
            ]
        )
        # AAPL and us_tech are both allowed tokens — grounded.
        apply_sizing_gate(payload, make_theme(), make_strategy(), "moderate")


class TestSizingCaps:
    def test_no_rule_clamps_to_risk_cap(self):
        payload = make_payload(
            target_allocation_change={"entity": "us_tech", "from_pct": 0.0, "to_pct": 20.0}
        )
        payload, source = apply_sizing_gate(payload, make_theme(), make_strategy(), "moderate")
        assert source == "risk_profile_cap"
        assert payload["target_allocation_change"]["to_pct"] == 10.0
        # Instruments scaled proportionally: 4.0 * (10/20) = 2.0
        assert payload["specific_instruments"][0]["target_pct"] == 2.0

    def test_strategy_rule_used_when_present(self):
        strategy = make_strategy(allocation_rule="Allocate 25% to this theme")
        payload = make_payload(
            target_allocation_change={"entity": "us_tech", "from_pct": 0.0, "to_pct": 30.0}
        )
        payload, source = apply_sizing_gate(payload, make_theme(), strategy, "moderate")
        assert source == "strategy_allocation_rule"
        assert payload["target_allocation_change"]["to_pct"] == 25.0

    def test_within_cap_untouched(self):
        payload, source = apply_sizing_gate(make_payload(), make_theme(), make_strategy(), "moderate")
        assert source == "risk_profile_cap"
        assert payload["target_allocation_change"]["to_pct"] == 8.0

    def test_conservative_tighter_than_moderate(self):
        payload = make_payload(
            target_allocation_change={"entity": "us_tech", "from_pct": 0.0, "to_pct": 8.0}
        )
        payload, _ = apply_sizing_gate(payload, make_theme(), make_strategy(), "conservative")
        assert payload["target_allocation_change"]["to_pct"] == 5.0

    def test_per_instrument_cap_applied(self):
        payload = make_payload(
            target_allocation_change={"entity": "us_tech", "from_pct": 0.0, "to_pct": 9.0},
            specific_instruments=[{"entity": "NVDA", "target_pct": 9.0}],
        )
        payload, _ = apply_sizing_gate(payload, make_theme(), make_strategy(), "moderate")
        assert payload["specific_instruments"][0]["target_pct"] == 5.0

    def test_unknown_risk_profile_falls_back_to_moderate(self):
        payload = make_payload(
            target_allocation_change={"entity": "us_tech", "from_pct": 0.0, "to_pct": 20.0}
        )
        payload, _ = apply_sizing_gate(payload, make_theme(), make_strategy(), "yolo")
        assert payload["target_allocation_change"]["to_pct"] == 10.0


class TestReductionsNotCapped:
    def test_reduce_exposure_untouched(self):
        payload = make_payload(
            guidance_type="REDUCE_EXPOSURE",
            target_allocation_change={"entity": "AAPL", "from_pct": 12.0, "to_pct": 0.0},
            specific_instruments=[{"entity": "AAPL", "target_pct": 0.0}],
        )
        payload, source = apply_sizing_gate(payload, make_theme(), make_strategy(), "conservative")
        assert source == "reduction_not_capped"
        assert payload["target_allocation_change"]["to_pct"] == 0.0

    def test_missing_to_pct_treated_as_no_new_exposure(self):
        payload = make_payload(target_allocation_change=None, specific_instruments=[])
        payload, source = apply_sizing_gate(payload, make_theme(), make_strategy(), "moderate")
        assert source == "reduction_not_capped"
