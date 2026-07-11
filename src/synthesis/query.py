"""
Grounded chat / query interface ("ask your advisor's library").

Scope decision (2026-07-08, user-confirmed): grounded Q&A only. The chat
answers strictly from data the pipeline has already produced and logged
— it never generates a fresh recommendation. Context assembly is
deterministic code, exactly like build_attribution in
src/guidance/generator.py: the LLM only narrates what's already true, it
never chooses what counts as evidence. Every Q&A is logged to
query_log (migration 012) for the same record-keeping reason
portfolio_guidance is logged.
"""

from __future__ import annotations

import re
from typing import Any

import pendulum

from src.config.tuning import param
from src.db.queries import (
    fetch_portfolio_holdings,
    fetch_portfolios_for_user,
    fetch_user_by_email,
    fetch_where,
    insert_one,
)
from src.llm.claude_client import claude

MAX_CLAIMS_IN_CONTEXT = param("query.max_claims_in_context")


def _keyword_tokens(question: str) -> set[str]:
    """Crude but effective: lowercase alnum tokens 3+ chars, for keyword
    matching claims against the question. Semantic search over claims is
    a reasonable future upgrade; not needed at this data volume."""
    return {t for t in re.findall(r"[a-z0-9_]+", question.lower()) if len(t) >= 3}


def _find_relevant_claims(question: str, limit: int = MAX_CLAIMS_IN_CONTEXT) -> list[dict[str, Any]]:
    """
    Keyword-match quality-passed claims against the question's tokens —
    deterministic retrieval, not an LLM picking its own evidence. Matches
    on entity, claim_text, and reason_given.
    """
    tokens = _keyword_tokens(question)
    if not tokens:
        return []

    claims = fetch_where("claims", {}, limit=1000)
    scored = []
    for claim in claims:
        if claim.get("quality_verdict") not in ("pass", "partial"):
            continue
        haystack = " ".join(
            str(claim.get(f, "")).lower()
            for f in ("entity", "claim_text", "reason_given")
        )
        score = sum(1 for t in tokens if t in haystack)
        if score > 0:
            scored.append((score, claim))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [claim for _, claim in scored[:limit]]


def _holdings_block(holdings: list[dict[str, Any]]) -> str:
    if not holdings:
        return "(no holdings on file)"
    total = sum(h["quantity"] * (h.get("cost_basis") or 0) for h in holdings)
    lines = []
    for h in holdings:
        value = h["quantity"] * (h.get("cost_basis") or 0)
        pct = (value / total * 100) if total else 0.0
        lines.append(f"- {h['entity']}: {pct:.1f}% of portfolio, asset_class={h.get('asset_class', 'equity')}")
    return "\n".join(lines)


def _guidance_block(guidance_rows: list[dict[str, Any]]) -> str:
    if not guidance_rows:
        return "(no guidance events yet for this user)"
    lines = []
    for g in guidance_rows:
        if g["is_refusal"]:
            lines.append(f"- REFUSED (theme {g.get('theme_id')}): {g.get('refusal_reason')}")
        else:
            change = g.get("target_allocation_change") or {}
            lines.append(
                f"- {g['guidance_type']} on {change.get('entity', '?')} "
                f"({change.get('from_pct', '?')}% -> {change.get('to_pct', '?')}%), "
                f"generated {g.get('generated_at', '')}"
            )
    return "\n".join(lines)


def _themes_block(themes: list[dict[str, Any]]) -> str:
    if not themes:
        return "(no medium+ conviction themes yet — convergence requires 2+ independent experts)"
    lines = []
    for t in themes:
        experts = ", ".join(e.get("expert_name", "?") for e in (t.get("supporting_experts") or []))
        lines.append(
            f"- {t['entity_cluster_key']} ({t['conviction_level']}, "
            f"fundamentals={t.get('fundamentals_verdict')}): {experts}\n"
            f"  thesis: {t.get('summary_thesis', '')}"
        )
    return "\n".join(lines)


def _claims_block(claims: list[dict[str, Any]]) -> str:
    if not claims:
        return "(no matching claims found)"
    lines = []
    for c in claims:
        lines.append(f"- [{c['id']}] {c.get('entity')}: {c.get('claim_text')} — {c.get('reason_given', '')[:200]}")
    return "\n".join(lines)


def _credibility_block(expert_ids: set[str]) -> str:
    if not expert_ids:
        return "(no experts referenced)"
    experts = fetch_where("experts", {}, limit=100)
    lines = []
    for e in experts:
        if e["id"] in expert_ids:
            lines.append(f"- {e['name']}: overall {e.get('credibility_score', 50)}")
    return "\n".join(lines) if lines else "(no experts referenced)"


def assemble_context(question: str, user_email: str) -> dict[str, Any]:
    """
    Deterministically assemble every piece of grounding data for one
    question. Returns both the rendered text blocks (for the prompt) and
    the raw rows (for the sources record) — same split as
    build_attribution in src/guidance/generator.py.
    """
    user = fetch_user_by_email(user_email)
    if user is None:
        raise ValueError(f"No user found for email {user_email!r}")

    portfolios = fetch_portfolios_for_user(user["id"])
    holdings = fetch_portfolio_holdings(portfolios[0]["id"]) if portfolios else []

    guidance_rows = fetch_where("portfolio_guidance", {"user_id": user["id"]}, limit=100)
    themes = [t for t in fetch_where("themes", {"status": "active"}, limit=200)
              if t.get("conviction_level") in ("medium", "high")]
    relevant_claims = _find_relevant_claims(question)

    expert_ids = {c.get("expert_id") for c in relevant_claims if c.get("expert_id")}
    for t in themes:
        expert_ids |= {e.get("expert_id") for e in (t.get("supporting_experts") or []) if e.get("expert_id")}

    return {
        "user": user,
        "holdings": holdings,
        "guidance_rows": guidance_rows,
        "themes": themes,
        "claims": relevant_claims,
        "expert_ids": expert_ids,
        "blocks": {
            "holdings_block": _holdings_block(holdings),
            "guidance_block": _guidance_block(guidance_rows),
            "themes_block": _themes_block(themes),
            "claims_block": _claims_block(relevant_claims),
            "credibility_block": _credibility_block(expert_ids),
        },
    }


def _sources_from_context(context: dict[str, Any]) -> dict[str, Any]:
    """Sources record assembled from real rows — never from LLM output."""
    return {
        "guidance_ids": [g["id"] for g in context["guidance_rows"]],
        "theme_ids": [t["id"] for t in context["themes"]],
        "claim_ids": [c["id"] for c in context["claims"]],
        "expert_ids": sorted(context["expert_ids"]),
    }


def answer_query(user_question: str, user_email: str) -> dict[str, Any]:
    """
    Answer one grounded question and log it. Returns
    {"answer": str, "sources": dict, "refused_fresh_advice": bool}.
    refused_fresh_advice is a best-effort heuristic for logging/reporting
    only (looks for a refusal-shaped opening) — the actual enforcement is
    the prompt's hard rule, not this flag.
    """
    context = assemble_context(user_question, user_email)

    variables = {
        "user_question": user_question,
        **context["blocks"],
    }
    answer = claude.call(prompt_name="grounded_query_v2", content="", variables=variables)

    # Best-effort heuristic for audit/reporting only (see module docstring
    # on answer_query) — the real enforcement is the prompt's hard rule,
    # not this flag. Checks the first sentence for refusal language rather
    # than requiring an exact opening phrase, since the model may lead
    # with bolding/framing before the refusal itself.
    first_sentence = answer.strip().lower().split(".")[0]
    refused = any(
        phrase in first_sentence
        for phrase in (
            "cannot provide", "can't provide",
            "cannot generate", "can't generate",
            "cannot recommend", "can't recommend",
            "cannot answer", "can't answer",
            "no logged guidance", "no guidance event",
        )
    )
    sources = _sources_from_context(context)

    insert_one(
        "query_log",
        {
            "user_id": context["user"]["id"],
            "question": user_question,
            "answer": answer,
            "sources": sources,
            "refused_fresh_advice": refused,
            "created_at": pendulum.now("UTC").to_iso8601_string(),
        },
    )

    return {"answer": answer, "sources": sources, "refused_fresh_advice": refused}
