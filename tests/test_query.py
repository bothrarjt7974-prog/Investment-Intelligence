"""
Tests for the grounded chat/query interface (src/synthesis/query.py).
All DB/LLM access is monkeypatched — no network or Supabase calls.
"""

from __future__ import annotations

from src.synthesis import query


def make_user(**overrides):
    base = {"id": "user-1", "email": "test@example.com"}
    base.update(overrides)
    return base


def make_claim(**overrides):
    base = {
        "id": "claim-1",
        "entity": "gold",
        "claim_text": "Gold will rise on inflation fears",
        "reason_given": "central banks buying gold as reserve diversification",
        "quality_verdict": "pass",
        "expert_id": "expert-1",
    }
    base.update(overrides)
    return base


class TestKeywordMatching:
    def test_finds_matching_claim(self, monkeypatch):
        monkeypatch.setattr(query, "fetch_where", lambda table, filters, limit=100: (
            [make_claim()] if table == "claims" else []
        ))
        results = query._find_relevant_claims("what do experts say about gold")
        assert len(results) == 1
        assert results[0]["entity"] == "gold"

    def test_fail_verdict_claims_excluded(self, monkeypatch):
        monkeypatch.setattr(query, "fetch_where", lambda table, filters, limit=100: (
            [make_claim(quality_verdict="fail")] if table == "claims" else []
        ))
        results = query._find_relevant_claims("gold")
        assert results == []

    def test_no_matching_tokens_returns_empty(self, monkeypatch):
        monkeypatch.setattr(query, "fetch_where", lambda table, filters, limit=100: (
            [make_claim()] if table == "claims" else []
        ))
        results = query._find_relevant_claims("crypto bitcoin")
        assert results == []


class TestAssembleContext:
    def test_assembles_all_sections(self, monkeypatch):
        monkeypatch.setattr(query, "fetch_user_by_email", lambda email: make_user())
        monkeypatch.setattr(query, "fetch_portfolios_for_user", lambda uid: [{"id": "p1"}])
        monkeypatch.setattr(query, "fetch_portfolio_holdings", lambda pid: [
            {"entity": "HDFCBANK", "quantity": 10, "cost_basis": 1500.0, "asset_class": "equity"}
        ])

        def fake_fetch_where(table, filters, limit=100):
            if table == "portfolio_guidance":
                return [{"id": "g1", "is_refusal": True, "refusal_reason": "low conviction", "theme_id": "t1"}]
            if table == "themes":
                return [{
                    "id": "t1", "entity_cluster_key": "gold", "conviction_level": "medium",
                    "fundamentals_verdict": "pass", "summary_thesis": "gold rises",
                    "supporting_experts": [{"expert_name": "X", "expert_id": "e1"}],
                }]
            if table == "claims":
                return [make_claim()]
            if table == "experts":
                return [{"id": "expert-1", "name": "Y", "credibility_score": 50}]
            return []

        monkeypatch.setattr(query, "fetch_where", fake_fetch_where)

        context = query.assemble_context("what about gold", "test@example.com")

        assert context["user"]["email"] == "test@example.com"
        assert len(context["holdings"]) == 1
        assert len(context["guidance_rows"]) == 1
        assert len(context["themes"]) == 1
        assert "gold" in context["blocks"]["themes_block"]

    def test_raises_on_unknown_user(self, monkeypatch):
        monkeypatch.setattr(query, "fetch_user_by_email", lambda email: None)
        try:
            query.assemble_context("hello", "nobody@example.com")
        except ValueError as exc:
            assert "No user found" in str(exc)
        else:
            raise AssertionError("Expected ValueError for unknown user")


class TestAnswerQuery:
    def test_logs_and_returns_answer(self, monkeypatch):
        monkeypatch.setattr(query, "assemble_context", lambda q, email: {
            "user": make_user(),
            "holdings": [],
            "guidance_rows": [],
            "themes": [],
            "claims": [],
            "expert_ids": set(),
            "blocks": {
                "holdings_block": "", "guidance_block": "", "themes_block": "",
                "claims_block": "", "credibility_block": "",
            },
        })
        monkeypatch.setattr(query.claude, "call", lambda **kwargs: "Gold has been discussed by experts.")
        logged = []
        monkeypatch.setattr(query, "insert_one", lambda table, row: logged.append(row) or row)

        result = query.answer_query("what about gold", "test@example.com")

        assert result["answer"] == "Gold has been discussed by experts."
        assert result["refused_fresh_advice"] is False
        assert len(logged) == 1
        assert logged[0]["question"] == "what about gold"

    def test_refusal_detected_and_logged(self, monkeypatch):
        monkeypatch.setattr(query, "assemble_context", lambda q, email: {
            "user": make_user(),
            "holdings": [], "guidance_rows": [], "themes": [], "claims": [], "expert_ids": set(),
            "blocks": {k: "" for k in ("holdings_block", "guidance_block", "themes_block", "claims_block", "credibility_block")},
        })
        monkeypatch.setattr(
            query.claude, "call",
            lambda **kwargs: "This question asks for a new buy recommendation, which this system cannot provide conversationally.",
        )
        monkeypatch.setattr(query, "insert_one", lambda table, row: row)

        result = query.answer_query("what should I buy now", "test@example.com")

        assert result["refused_fresh_advice"] is True
