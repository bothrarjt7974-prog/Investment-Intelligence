"""
Tests for the theme convergence/credibility scoring module (Layer 6).

Pure deterministic math, no DB/LLM access needed.
"""

from __future__ import annotations

from src.internal.theme_scoring import aggregate_cluster, derive_conviction_level


def make_claim(claim_id, expert_id, horizon_months=18):
    return {"id": claim_id, "expert_id": expert_id, "target_horizon_months": horizon_months}


def make_expert(expert_id, name, long_cred=70.0, structural_cred=70.0):
    return {
        expert_id: {
            "name": name,
            "credibility_long_horizon": long_cred,
            "credibility_structural": structural_cred,
        }
    }


class TestDeriveConvictionLevel:
    def test_single_expert_always_low(self):
        assert derive_conviction_level(effective_signal_count=5.0, credibility_weighted_score=95.0, raw_expert_count=1) == "low"

    def test_high_requires_both_thresholds(self):
        assert derive_conviction_level(3.0, 65.0, raw_expert_count=3) == "high"
        assert derive_conviction_level(3.0, 64.9, raw_expert_count=3) == "medium"  # credibility just under HIGH bar
        assert derive_conviction_level(2.9, 90.0, raw_expert_count=3) == "medium"  # signal just under HIGH bar

    def test_medium_requires_both_thresholds(self):
        assert derive_conviction_level(2.0, 50.0, raw_expert_count=2) == "medium"
        assert derive_conviction_level(1.9, 90.0, raw_expert_count=2) == "low"
        assert derive_conviction_level(2.0, 49.9, raw_expert_count=2) == "low"


class TestAggregateCluster:
    def test_all_independent_high_conviction(self):
        claims = [make_claim("c1", "e1"), make_claim("c2", "e2"), make_claim("c3", "e3")]
        labels = {"c1": "independent", "c2": "independent", "c3": "independent"}
        experts = {}
        for eid in ("e1", "e2", "e3"):
            experts.update(make_expert(eid, f"Expert {eid}", long_cred=70.0))

        result = aggregate_cluster(claims, labels, experts)

        assert result["independence_score"] == 1.0
        assert result["credibility_weighted_score"] == 70.0
        assert result["conviction_level"] == "high"
        assert len(result["supporting_experts"]) == 3

    def test_echo_group_collapses_to_one_signal(self):
        # 3 experts all echoing the same fact -> ONE signal, not three.
        claims = [make_claim("c1", "e1"), make_claim("c2", "e2"), make_claim("c3", "e3")]
        labels = {"c1": "echo", "c2": "echo", "c3": "echo"}
        experts = {}
        for eid in ("e1", "e2", "e3"):
            experts.update(make_expert(eid, f"Expert {eid}", long_cred=80.0))

        result = aggregate_cluster(claims, labels, experts)

        # effective_signal_count = 1.0 (echo collapse), raw_expert_count = 3
        assert result["independence_score"] == round(1.0 / 3, 3)
        assert result["credibility_weighted_score"] == 80.0
        assert result["conviction_level"] == "low"  # signal count 1.0 < MEDIUM_MIN_SIGNAL

    def test_single_expert_cluster_is_always_low_regardless_of_credibility(self):
        claims = [make_claim("c1", "e1")]
        labels = {"c1": "independent"}
        experts = make_expert("e1", "Solo Expert", long_cred=95.0)

        result = aggregate_cluster(claims, labels, experts)

        assert result["conviction_level"] == "low"

    def test_correlated_weight_is_half(self):
        claims = [make_claim("c1", "e1"), make_claim("c2", "e2")]
        labels = {"c1": "correlated", "c2": "correlated"}
        experts = {}
        for eid in ("e1", "e2"):
            experts.update(make_expert(eid, f"Expert {eid}", long_cred=80.0))

        result = aggregate_cluster(claims, labels, experts)

        # effective_signal_count = 0.5 + 0.5 = 1.0, raw_expert_count = 2
        assert result["independence_score"] == 0.5
        assert result["conviction_level"] == "low"  # 1.0 < MEDIUM_MIN_SIGNAL (2.0)

    def test_uses_structural_bucket_for_long_horizons(self):
        claims = [make_claim("c1", "e1", horizon_months=48)]  # > 36 months -> structural bucket
        labels = {"c1": "independent"}
        experts = make_expert("e1", "Expert", long_cred=40.0, structural_cred=90.0)

        result = aggregate_cluster(claims, labels, experts)

        assert result["credibility_weighted_score"] == 90.0

    def test_supporting_experts_use_most_favorable_label(self):
        # Same expert contributes two claims in this cluster: one independent, one echo.
        claims = [make_claim("c1", "e1"), make_claim("c2", "e1")]
        labels = {"c1": "echo", "c2": "independent"}
        experts = make_expert("e1", "Expert", long_cred=70.0)

        result = aggregate_cluster(claims, labels, experts)

        assert len(result["supporting_experts"]) == 1
        assert result["supporting_experts"][0]["independence_label"] == "independent"
        assert set(result["supporting_experts"][0]["claim_ids"]) == {"c1", "c2"}

    def test_unknown_label_treated_as_correlated_not_dropped(self):
        claims = [make_claim("c1", "e1"), make_claim("c2", "e2")]
        labels = {"c1": "independent", "c2": "something_unexpected"}
        experts = {}
        for eid in ("e1", "e2"):
            experts.update(make_expert(eid, f"Expert {eid}", long_cred=70.0))

        result = aggregate_cluster(claims, labels, experts)

        # c2 falls back to 'correlated' (0.5) rather than being excluded.
        assert result["independence_score"] == round(1.5 / 2, 3)
