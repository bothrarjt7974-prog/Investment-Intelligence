"""
Investment Intelligence Layer — Streamlit UI.

This is the early-stage UI. Replace with Next.js when investor-ready
polish becomes necessary.

Navigation is a sidebar radio (not st.tabs): st.chat_input inside a tab
resets to the first tab on every rerun, which makes the chat unusable —
sidebar pages persist across reruns via session_state.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import streamlit as st

from src.db.queries import (
    fetch_active_users,
    fetch_by_id,
    fetch_portfolio_holdings,
    fetch_portfolios_for_user,
    fetch_user_by_email,
    fetch_where,
)
from src.guidance.copy import (
    humanize_days,
    refusal_reason_label,
    sizing_source_label,
    source_type_label,
)
from src.guidance.display import compute_action_amounts
from src.guidance.portfolio_seed import (
    VALID_ASSET_CLASSES,
    VALID_RISK_PROFILES,
    add_holdings,
    seed_portfolio,
    validate_portfolio_payload,
)
from src.guidance.readiness import assess_theme_readiness
from src.synthesis.query import answer_query

st.set_page_config(
    page_title="Investment Intelligence",
    page_icon="📊",
    layout="wide",
)

# 2026-07-13: labels rewritten for a general, non-technical user — see
# docs/decision_log.md. Internal terms ("conviction," "fundamentals
# verdict," "superseded") never appear verbatim in the UI after this pass.
CONVICTION_BADGE = {"high": "🟢 High confidence", "medium": "🟡 Medium confidence", "low": "⚪ Low confidence"}
CONVICTION_HELP = (
    "How many experts, reasoning independently, agree on this idea — and how "
    "strong their track record is. A single voice can never reach Medium or "
    "High, no matter how credible."
)
FUNDAMENTALS_BADGE = {
    "pass": "✅ Financially healthy",
    "downgrade": "🟠 Valuation looks stretched",
    "reject": "🔴 Red flag on the balance sheet",
    "not_applicable": "➖ N/A (index/currency/commodity)",
    "insufficient_data": "❔ Not enough data yet",
}
GUIDANCE_ICON = {
    "INTRODUCE_NEW": "🆕",
    "INCREASE_EXPOSURE": "📈",
    "REDUCE_EXPOSURE": "📉",
    "EXIT": "🚪",
    "MAINTAIN": "🤝",
    "REBALANCE": "⚖️",
}


# ---------------------------------------------------------------- helpers


@st.cache_data(ttl=120)
def load_users() -> list[dict[str, Any]]:
    return fetch_active_users()


@st.cache_data(ttl=120)
def load_active_themes() -> list[dict[str, Any]]:
    return fetch_where("themes", {"status": "active"}, limit=500)


def portfolio_value_and_holdings(user_id: str) -> tuple[float, list[dict[str, Any]]]:
    portfolios = fetch_portfolios_for_user(user_id)
    if not portfolios:
        return 0.0, []
    holdings = fetch_portfolio_holdings(portfolios[0]["id"])
    total = sum(h["quantity"] * (h.get("cost_basis") or 0) for h in holdings)
    return total, holdings


def _suggested_questions(user_email: str) -> list[str]:
    """Suggestion chips built from real data — a suggestion should never
    lead to a dead-end answer."""
    suggestions: list[str] = []
    try:
        user = fetch_user_by_email(user_email)
        if user:
            guidance = fetch_where(
                "portfolio_guidance", {"user_id": user["id"], "is_refusal": False}, limit=5
            )
            for g in guidance:
                change = g.get("target_allocation_change") or {}
                entity = change.get("entity")
                if entity:
                    suggestions.append(f"Why did the system recommend {g['guidance_type']} on {entity}?")
            _, holdings = portfolio_value_and_holdings(user["id"])
            if holdings:
                biggest = max(holdings, key=lambda h: h["quantity"] * (h.get("cost_basis") or 0))
                suggestions.append(f"What do experts say about {biggest['entity']}?")
        themes = [t for t in load_active_themes() if t.get("conviction_level") in ("medium", "high")]
        for t in themes[:2]:
            suggestions.append(f"What is the expert view on {t['entity_cluster_key']}?")
        suggestions.append("Which guidance was refused for me, and why?")
    except Exception:  # noqa: BLE001 — suggestions are decorative, never block chat
        pass
    # de-dup preserving order
    return list(dict.fromkeys(suggestions))[:5]


def _parse_date(value: Any) -> date | None:
    """Coerce a date/ISO-datetime string from the DB into a date, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def relevance_badge(guidance: dict[str, Any]) -> tuple[str, str] | None:
    """
    One-line freshness signal for a guidance card — closes the gap where a
    guidance event a user opens weeks later looked exactly as current as
    one generated an hour ago (2026-07-12 finding, src/guidance/relevance.py).

    Returns (severity, message) where severity is 'error'|'warning'|'info',
    or None when there's truly nothing to say (no review_by on record —
    e.g. rows generated before this feature shipped).

    Always answers "what's the current status and time remaining" — a
    fresh row gets a quiet positive count-down, not silence, per the
    founder's direct question (2026-07-12). Wording rewritten 2026-07-13
    for a general reader — "superseded" and "re-synthesis" never appear
    here even though those are the real, technical field/status names
    stored in the database; this function is display-only translation.
    """
    if guidance.get("status") == "superseded":
        generated = _parse_date(guidance.get("generated_at"))
        when = f" on {generated.isoformat()}" if generated else ""
        return "error", f"🔴 Outdated — our analysis has moved on since this was generated{when}. Check for new guidance above."

    review_by = _parse_date(guidance.get("review_by"))
    if review_by is None:
        return None

    days_left = (review_by - date.today()).days
    if days_left < 0:
        return "warning", f"🟡 Due for a refresh — last checked {humanize_days(-days_left)} ago"
    return "info", f"🟢 Up to date — next review in {humanize_days(days_left)}"


def user_selector(key: str) -> str | None:
    users = load_users()
    if not users:
        st.info("No users yet — create one in the Portfolio page.")
        return None
    emails = [u["email"] for u in users]
    default = st.session_state.get("selected_email")
    index = emails.index(default) if default in emails else 0
    email = st.selectbox("User", emails, index=index, key=key)
    st.session_state.selected_email = email
    return email


# ---------------------------------------------------------------- sidebar

with st.sidebar:
    st.title("📊 Investment Intelligence")
    st.caption("Expert intelligence → validated themes → attributed guidance")

    page = st.radio(
        "Navigate",
        ["🏠 Overview", "🧭 Guidance", "💬 Ask", "💼 Portfolio", "🔬 Pipeline", "🎛 Quality Tuning"],
        label_visibility="collapsed",
    )

    st.divider()
    with st.expander("Tracked experts"):
        try:
            experts = fetch_where("experts", {"is_active": True}, limit=100)
            for expert in sorted(experts, key=lambda e: e.get("credibility_score") or 50, reverse=True):
                score = expert.get("credibility_score", 50)
                geo = (expert.get("geography") or "?").upper()
                st.write(f"**{expert['name']}** ({geo}) — {score}")
            st.caption("50 = neutral default; scores move once claims validate against real outcomes.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not load experts: {e}")

    st.caption(
        "⚠️ Long-term advisory research tool. Guidance requires founder "
        "review before any real action. Not a trading system."
    )


# ---------------------------------------------------------------- Overview

if page == "🏠 Overview":
    st.header("Where the system stands")

    try:
        themes = load_active_themes()
        strong = [t for t in themes if t.get("conviction_level") in ("medium", "high")]
        guidance_all = fetch_where("portfolio_guidance", {}, limit=500)
        real_guidance = [g for g in guidance_all if not g["is_refusal"]]
        # 2026-07-12: a guidance row's own status matters now that theme
        # supersession cascades onto it — showing every row as if it were
        # current would be misleading once a newer analysis has replaced it.
        live_guidance = [g for g in real_guidance if g.get("status") not in ("superseded", "completed")]

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Investment ideas being tracked", len(themes))
        c2.metric("Ideas multiple experts agree on", len(strong))
        c3.metric("Current recommendations", len(live_guidance))
        c4.metric(
            "Replaced by newer analysis",
            len(real_guidance) - len(live_guidance),
            help="Recommendations built on an idea that's since been re-checked and updated. Not wrong when they were made — just no longer the latest word.",
        )
        c5.metric(
            "Times we chose not to act",
            len(guidance_all) - len(real_guidance),
            help="The system would rather say nothing than guess. Every one of these is logged with a reason — see the Guidance page.",
        )

        pieces_all = fetch_where("content_pieces", {}, limit=1200)
        track_a_done = sum(1 for p in pieces_all if p.get("processed_at"))
        track_b_done = sum(1 for p in pieces_all if p.get("strategies_processed_at"))
        pv1, pv2 = st.columns(2)
        pv1.metric(
            "Videos analyzed for expert predictions",
            f"{track_a_done} / {len(pieces_all)}",
            help="The rest are a deliberate backlog waiting on budget approval, not stuck or broken.",
        )
        pv2.metric(
            "Videos analyzed for investment methods",
            f"{track_b_done} / {len(pieces_all)}",
            help="How to act on a prediction (not just what the prediction is) — analyzed separately.",
        )

        st.subheader("Ideas where multiple experts agree", divider="gray")
        st.caption(
            "An idea only reaches Medium or High confidence when several experts, "
            "reasoning independently, reach the same conclusion — one voice alone "
            "never counts, no matter how credible."
        )

        if not strong:
            st.info("No multi-expert ideas yet.")

        # 2026-07-13: "is there enough behind this to act on?" — one cheap
        # lookup per idea (there are only ever a handful of medium/high
        # ideas at once) against tables already fetched elsewhere in the
        # app. See src/guidance/readiness.py for what each check means.
        readiness_by_theme: dict[str, dict] = {}
        for t in strong:
            matches = fetch_where("theme_strategy_matches", {"theme_id": t["id"], "rank": 1}, limit=1)
            top_match = matches[0] if matches else None
            strategy = fetch_by_id("strategies", top_match["strategy_id"]) if top_match else None
            readiness_by_theme[t["id"]] = assess_theme_readiness(t, top_match, strategy)

        for t in sorted(strong, key=lambda x: x.get("conviction_level", ""), reverse=True):
            experts_list = ", ".join(
                e.get("expert_name", "?") for e in (t.get("supporting_experts") or [])
            )
            with st.container(border=True):
                left, right = st.columns([3, 1])
                left.markdown(f"### {t['entity_cluster_key']}")
                left.markdown(f"**{experts_list}**")
                left.write(t.get("summary_thesis", ""))
                right.markdown(CONVICTION_BADGE.get(t.get("conviction_level"), "⚪"), help=CONVICTION_HELP)
                right.markdown(FUNDAMENTALS_BADGE.get(t.get("fundamentals_verdict"), "❔ Checking pending"))
                if t.get("time_horizon_years"):
                    yrs = t["time_horizon_years"]
                    frame = "about 1 year" if yrs == 1 else f"about {yrs:g} years"
                    right.markdown(f"Time frame: {frame}")

                readiness = readiness_by_theme.get(t["id"])
                if readiness:
                    overall_icon = {"good": "✅", "fair": "🟡", "poor": "🔴"}[readiness["overall"]["level"]]
                    with st.expander(f"{overall_icon} Is there enough here to act on? — {readiness['overall']['label']}"):
                        for check in readiness["checklist"]:
                            icon = {"good": "✅", "fair": "🟡", "poor": "🔴"}[check["level"]]
                            st.markdown(f"{icon} **{check['label']}** — {check['detail']}")

        with st.expander(f"All {len(themes)} active ideas"):
            st.dataframe(
                [
                    {
                        "Idea": t["entity_cluster_key"],
                        "Confidence": t.get("conviction_level"),
                        "Financials": t.get("fundamentals_verdict") or "pending",
                        "Experts": len(t.get("supporting_experts") or []),
                        "Time frame (yr)": t.get("time_horizon_years"),
                    }
                    for t in themes
                ],
                use_container_width=True,
            )
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load overview: {e}")


# ---------------------------------------------------------------- Guidance

elif page == "🧭 Guidance":
    st.header("Your guidance")
    st.caption(
        "Every recommendation traces back to named experts, a real investing "
        "strategy, and a check on the underlying financials — and every time "
        "we choose not to act, the reason is logged too. Staying quiet is a "
        "valid answer."
    )

    email = user_selector("guidance-user")
    if email:
        user = fetch_user_by_email(email)

        # The compliant path to a FRESH recommendation: run the gated
        # pipeline itself (eligibility gates → Opus → sizing gate →
        # logged event/refusal). Cheap by construction — ineligible
        # theme×portfolio pairs refuse before any LLM call, and pairs
        # already covered by active guidance are refused as duplicates.
        col_btn, col_note = st.columns([1, 3])
        if col_btn.button("🔄 Check for new guidance"):
            with st.spinner("Checking your portfolio against the latest expert analysis..."):
                try:
                    from src.guidance.generator import run_guidance_generation

                    counts = run_guidance_generation()
                    st.success(
                        f"Done — {counts['guidance_generated']} new recommendation(s), "
                        f"{counts['refused']} time(s) we chose not to act, "
                        f"{counts['error']} had errors and were skipped."
                    )
                    st.cache_data.clear()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Something went wrong while checking: {e}")
        col_note.caption(
            "Checks every current idea against your holdings and your risk profile. "
            "You'll only see a new recommendation when experts agree, the numbers "
            "check out, and there's a real strategy behind it."
        )

        rows = fetch_where("portfolio_guidance", {"user_id": user["id"]}, limit=200)
        rows.sort(key=lambda r: r.get("generated_at") or "", reverse=True)
        real = [r for r in rows if not r["is_refusal"]]
        refusals = [r for r in rows if r["is_refusal"]]

        if not real:
            st.info(
                "No current recommendations for this user yet — the system stays "
                "quiet until multiple experts agree, the financials check out, and "
                "there's a real strategy to act on."
            )

        for g in real:
            change = g.get("target_allocation_change") or {}
            icon = GUIDANCE_ICON.get(g.get("guidance_type"), "🧭")
            # MAINTAIN carries no allocation change by design — name the
            # card after its theme instead of showing empty percentages.
            entity_label = change.get("entity")
            if not entity_label and g.get("theme_id"):
                theme_row = fetch_by_id("themes", g["theme_id"])
                entity_label = (theme_row or {}).get("entity_cluster_key")
            title = f"{icon} {g.get('guidance_type', '?').replace('_', ' ').title()} — {entity_label or '?'}"
            with st.container(border=True):
                st.markdown(f"### {title}")
                badge = relevance_badge(g)
                if badge:
                    severity, message = badge
                    {"error": st.error, "warning": st.warning, "info": st.info}[severity](message)
                if change.get("to_pct") is not None:
                    c1, c2, c3 = st.columns(3)
                    c1.metric("From", f"{change.get('from_pct', '—')}%")
                    c2.metric("To", f"{change.get('to_pct', '—')}%")
                    c3.metric("Status", g.get("status", "active"))
                else:
                    st.info(
                        "No changes needed — your current position already matches "
                        "this idea well. Holding steady is a deliberate "
                        "recommendation, not missing data."
                    )

                # Concrete rupee/share action — the founder's most direct
                # ask: not just percentages, but what to actually buy/sell
                # in real terms. Pure arithmetic on numbers already decided
                # above, never a new invented figure (src/guidance/display.py).
                if not g.get("is_refusal") and g.get("status") != "superseded":
                    portfolio_value, _ = portfolio_value_and_holdings(user["id"])
                    theme_geo = (fetch_by_id("themes", g["theme_id"]) or {}).get("geography") if g.get("theme_id") else None
                    amounts = compute_action_amounts(g, portfolio_value or 0.0, theme_geo)
                    if portfolio_value and (amounts["theme_rupee_change"] or amounts["instruments"]):
                        st.markdown("**In rupees, on your actual portfolio:**")
                        if amounts["theme_rupee_change"]:
                            st.write(f"Approx. ₹{amounts['theme_rupee_change']:,.0f} shift in this theme's allocation.")
                        for inst in amounts["instruments"]:
                            share = inst["share_estimate"]
                            share_txt = f" (~{share['approx_shares']:,.1f} shares @ ₹{share['price']:,.2f})" if share else ""
                            st.write(f"• **{inst['entity']}**: ~₹{inst['rupee_amount']:,.0f} ({inst['target_pct']}%){share_txt}")
                    illus = amounts["illustrative"]
                    if illus["theme_rupee_change"] or illus["instruments"]:
                        with st.expander("Example: if your portfolio were ₹1,00,000"):
                            if illus["theme_rupee_change"]:
                                st.write(f"Approx. ₹{illus['theme_rupee_change']:,.0f} shift in this theme's allocation.")
                            for inst in illus["instruments"]:
                                share = inst["share_estimate"]
                                share_txt = f" (~{share['approx_shares']:,.1f} shares @ ₹{share['price']:,.2f})" if share else ""
                                st.write(f"• **{inst['entity']}**: ~₹{inst['rupee_amount']:,.0f} ({inst['target_pct']}%){share_txt}")

                if g.get("specific_instruments"):
                    st.markdown("**What to hold**")
                    st.dataframe(
                        [
                            {"Stock / Fund": inst.get("entity"), "Target %": inst.get("target_pct")}
                            for inst in g["specific_instruments"]
                        ],
                        use_container_width=True,
                    )

                if g.get("execution_horizon"):
                    st.markdown(f"**How to execute:** {g['execution_horizon']}")
                if g.get("position_holding_horizon"):
                    st.markdown(f"**How long to hold:** {g['position_holding_horizon']}")
                if g.get("funding_source"):
                    st.markdown(f"**Where the money comes from:** {g['funding_source']}")
                if g.get("tax_note"):
                    st.warning(f"**Tax note:** {g['tax_note']}")
                if g.get("risks"):
                    st.error(f"**Risks:** {g['risks']}")

                attribution = g.get("attribution") or {}
                with st.expander("Why we're saying this"):
                    direction = attribution.get("direction") or []
                    if direction:
                        st.markdown(
                            "**Based on views from:** "
                            + ", ".join(d.get("expert_name", "?") for d in direction)
                        )
                    execution = attribution.get("execution") or {}
                    if execution:
                        method_label = execution.get("source_name") or source_type_label(execution.get("source_type"))
                        st.markdown(f"**Method used:** {method_label}")
                        st.write(execution.get("strategy_text", ""))
                        # Real historical performance — never an invented
                        # forward projection, only what the matched
                        # strategy's own backtest already showed
                        # (2026-07-12: "what growth can I expect").
                        backtest = execution.get("backtest_summary")
                        if backtest:
                            st.markdown("**How this has performed historically (real numbers, not a forecast):**")
                            st.json(backtest)
                        else:
                            st.caption("Not tested against historical data yet.")
                    st.markdown(f"**Position size:** {sizing_source_label(attribution.get('sizing_source'))}")

                    created = _parse_date(g.get("generated_at"))
                    relevant = _parse_date(g.get("relevant_as_of"))
                    review = _parse_date(g.get("review_by"))
                    bits = []
                    if created:
                        bits.append(f"Created {created.isoformat()}")
                    if relevant:
                        bits.append(f"based on analysis from {relevant.isoformat()}")
                    if review:
                        bits.append(f"next review around {review.isoformat()}")
                    if bits:
                        st.caption(" · ".join(bits))

        with st.expander(f"Times we chose not to act ({len(refusals)})"):
            theme_names = {}
            for r in refusals:
                tid = r.get("theme_id")
                if tid and tid not in theme_names:
                    theme = fetch_by_id("themes", tid)
                    theme_names[tid] = (theme or {}).get("entity_cluster_key", tid)
            st.dataframe(
                [
                    {
                        "Idea": theme_names.get(r.get("theme_id"), "?"),
                        "Why": refusal_reason_label(r.get("refusal_reason")),
                        "Full detail": r.get("refusal_reason"),
                        "When": str(r.get("generated_at", ""))[:16],
                    }
                    for r in refusals
                ],
                use_container_width=True,
            )


# ---------------------------------------------------------------- Ask

elif page == "💬 Ask":
    st.header("Ask the intelligence layer")
    st.caption(
        "Answers describe recorded system data — logged guidance, expert claims, "
        "themes — with sources. It will refuse to invent fresh advice."
    )

    email = user_selector("ask-user")
    if email:
        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []

        suggestions = _suggested_questions(email)
        if suggestions and not st.session_state.chat_history:
            st.write("**Try asking:**")
            cols = st.columns(min(len(suggestions), 3))
            for i, suggestion in enumerate(suggestions):
                if cols[i % len(cols)].button(suggestion, key=f"suggest-{i}"):
                    st.session_state.pending_question = suggestion
                    st.rerun()

        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("sources"):
                    with st.expander("Sources"):
                        st.json(msg["sources"])

        typed = st.chat_input("Ask about your holdings, guidance, or what experts have said")
        prompt = typed or st.session_state.pop("pending_question", None)
        if prompt:
            st.session_state.chat_history.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                with st.spinner("Checking recorded data..."):
                    try:
                        result = answer_query(prompt, email)
                        st.markdown(result["answer"])
                        with st.expander("Sources"):
                            st.json(result["sources"])
                        st.session_state.chat_history.append(
                            {
                                "role": "assistant",
                                "content": result["answer"],
                                "sources": result["sources"],
                            }
                        )
                    except Exception as e:  # noqa: BLE001
                        error_msg = f"Could not answer: {e}"
                        st.error(error_msg)
                        st.session_state.chat_history.append(
                            {"role": "assistant", "content": error_msg}
                        )

        if st.session_state.chat_history and st.button("Clear conversation"):
            st.session_state.chat_history = []
            st.rerun()


# ---------------------------------------------------------------- Portfolio

elif page == "💼 Portfolio":
    st.header("Portfolios")
    st.caption(
        "Real financial data — stored in the advisory database, never in git. "
        "Guidance quality depends on accurate acquisition dates and cost basis."
    )

    try:
        portfolio_users = load_users()
    except Exception as e:  # noqa: BLE001
        portfolio_users = []
        st.error(f"Could not load users: {e}")

    for user in portfolio_users:
        with st.expander(
            f"👤 {user['name']} ({user['email']}) — {user.get('risk_profile', '?')}",
            expanded=(user["email"] == st.session_state.get("selected_email")),
        ):
            portfolios = fetch_portfolios_for_user(user["id"])
            for portfolio in portfolios:
                holdings = fetch_portfolio_holdings(portfolio["id"])
                total = sum(h["quantity"] * (h.get("cost_basis") or 0) for h in holdings)
                st.subheader(
                    f"{portfolio.get('name', 'Primary')} — ₹{total:,.0f} invested "
                    f"({len(holdings)} lots)",
                    divider="gray",
                )
                if holdings:
                    st.dataframe(
                        [
                            {
                                "Entity": h["entity"],
                                "Qty": h["quantity"],
                                "Cost basis (₹)": h.get("cost_basis"),
                                "Acquired": str(h.get("acquired_at", ""))[:10],
                                "Asset class": h.get("asset_class"),
                                "% of portfolio": round(
                                    h["quantity"] * (h.get("cost_basis") or 0) / total * 100, 1
                                )
                                if total
                                else 0.0,
                            }
                            for h in sorted(
                                holdings,
                                key=lambda h: h["quantity"] * (h.get("cost_basis") or 0),
                                reverse=True,
                            )
                        ],
                        use_container_width=True,
                    )
                else:
                    st.info("No holdings yet.")

                with st.form(key=f"add-holding-{portfolio['id']}"):
                    st.write(
                        "**Add a holding** (one tax lot — same stock bought at "
                        "different times = separate entries)"
                    )
                    c1, c2, c3 = st.columns(3)
                    entity = c1.text_input(
                        "Entity (ticker e.g. HDFCBANK, or nifty50/gold)", key=f"e-{portfolio['id']}"
                    )
                    quantity = c2.number_input("Quantity", min_value=0.0, step=1.0, key=f"q-{portfolio['id']}")
                    cost_basis = c3.number_input("Cost basis per unit (₹)", min_value=0.0, key=f"c-{portfolio['id']}")
                    c4, c5 = st.columns(2)
                    acquired_at = c4.date_input("Acquired on", key=f"d-{portfolio['id']}")
                    asset_class = c5.selectbox("Asset class", VALID_ASSET_CLASSES, key=f"a-{portfolio['id']}")
                    if st.form_submit_button("Add holding"):
                        try:
                            added = add_holdings(
                                portfolio["id"],
                                [
                                    {
                                        "entity": entity.strip(),
                                        "quantity": quantity,
                                        "acquired_at": str(acquired_at),
                                        "cost_basis": cost_basis or None,
                                        "asset_class": asset_class,
                                    }
                                ],
                            )
                            st.success(f"Added {added} holding.")
                            st.cache_data.clear()
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))

    st.subheader("Add a new user + portfolio", divider="gray")
    with st.form(key="new-user-form"):
        c1, c2 = st.columns(2)
        new_name = c1.text_input("Name")
        new_email = c2.text_input("Email (unique — used as the account key)")
        c3, c4 = st.columns(2)
        new_risk = c3.selectbox("Risk profile", VALID_RISK_PROFILES, index=1)
        new_portfolio_name = c4.text_input("Portfolio name", value="Primary")
        new_tax_notes = st.text_area(
            "Tax notes (optional — slab, NRI status, LTCG exemption already used this FY...)",
            height=68,
        )
        new_restrictions = st.text_input(
            "Restrictions (optional, comma-separated — e.g. no tobacco, no F&O)"
        )
        if st.form_submit_button("Create user"):
            try:
                if fetch_user_by_email(new_email.strip()):
                    st.error(f"A user with email {new_email} already exists.")
                else:
                    parsed = validate_portfolio_payload(
                        {
                            "name": new_name.strip(),
                            "email": new_email.strip(),
                            "risk_profile": new_risk,
                            "tax_notes": new_tax_notes.strip() or None,
                            "restrictions": [
                                r.strip() for r in new_restrictions.split(",") if r.strip()
                            ],
                            "portfolio_name": new_portfolio_name.strip() or "Primary",
                            "holdings": [],
                        },
                        source_name="new user form",
                    )
                    seed_portfolio(parsed)
                    st.success(f"Created {new_name}. Add holdings via the panel above.")
                    st.cache_data.clear()
                    st.rerun()
            except ValueError as e:
                st.error(str(e))


# ---------------------------------------------------------------- Pipeline

elif page == "🔬 Pipeline":
    st.header("Pipeline status")
    try:
        from src.db.queries import supabase

        def _count(table: str, **filters) -> int:
            q = supabase.table(table).select("id", count="exact")
            for col, val in filters.items():
                q = q.eq(col, val)
            return q.execute().count or 0

        row1 = st.columns(4)
        row1[0].metric("Active Experts", _count("experts", is_active=True))
        row1[1].metric("Content Pieces", _count("content_pieces"))
        row1[2].metric("Claims Extracted", _count("claims"))
        row1[3].metric("Strategies Extracted", _count("strategies"))

        row2 = st.columns(4)
        row2[0].metric("Active Themes", _count("themes", status="active"))
        row2[1].metric(
            "Medium+ Conviction",
            _count("themes", status="active", conviction_level="medium")
            + _count("themes", status="active", conviction_level="high"),
        )
        row2[2].metric("Strategy Matches", _count("theme_strategy_matches"))
        row2[3].metric("Guidance Events", _count("portfolio_guidance"))

        row3 = st.columns(4)
        row3[0].metric("Guidance Generated", _count("portfolio_guidance", is_refusal=False))
        row3[1].metric("Guidance Refused", _count("portfolio_guidance", is_refusal=True))
        row3[2].metric("Chat Q&A Logged", _count("query_log"))
        row3[3].metric("Seeded Users", _count("users"))
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load stats: {e}")


# ---------------------------------------------------------------- Quality Tuning

elif page == "🎛 Quality Tuning":
    from src.config.tuning import (
        PARAMETERS,
        effective_values,
        load_overrides,
        save_overrides,
    )

    st.header("Quality Tuning — admin")
    st.caption(
        "Every quality knob in the pipeline, grouped by layer. Changes are "
        "saved to `config/tuning_overrides.json` and take effect the **next "
        "time each pipeline job runs** (jobs read their configuration at "
        "startup, so a running batch is never reconfigured mid-flight). "
        "Threshold changes are decision-log events — record the why."
    )

    current = effective_values()
    overrides = load_overrides()
    if overrides:
        st.info(
            f"**{len(overrides)} parameter(s) currently overridden** — "
            "everything else is at its shipped default."
        )

    # Group parameters by layer, preserving registry (pipeline) order.
    layers: dict[str, list] = {}
    for spec in PARAMETERS.values():
        layers.setdefault(spec.layer, []).append(spec)

    new_values: dict[str, float | int] = {}
    for layer_name, specs in layers.items():
        internal_layer = any(s.internal for s in specs)
        label = f"{layer_name}" + ("  🔒" if internal_layer else "")
        with st.expander(label, expanded=False):
            if internal_layer:
                st.warning(
                    "Proprietary scoring logic (`src/internal/`) — never "
                    "screenshot or share these values outside the team.",
                    icon="🔒",
                )
            for spec in specs:
                is_overridden = spec.key in overrides
                title = f"**{spec.title}**" + ("  🟠 overridden" if is_overridden else "")
                st.markdown(title)
                st.caption(spec.plain)

                if isinstance(spec.default, int):
                    value = st.slider(
                        spec.key,
                        min_value=int(spec.min_value),
                        max_value=int(spec.max_value),
                        value=int(current[spec.key]),
                        step=int(spec.step),
                        label_visibility="collapsed",
                    )
                else:
                    value = st.slider(
                        spec.key,
                        min_value=float(spec.min_value),
                        max_value=float(spec.max_value),
                        value=float(current[spec.key]),
                        step=float(spec.step),
                        label_visibility="collapsed",
                    )
                new_values[spec.key] = value

                unit = f" {spec.unit}" if spec.unit else ""
                st.caption(
                    f"Default: **{spec.default}{unit}** · "
                    f"⬆️ Higher: {spec.raise_effect} · "
                    f"⬇️ Lower: {spec.lower_effect}"
                )
                st.divider()

    # Matcher ranking weights must sum to 1.0 — warn live, don't block.
    weight_sum = (
        new_values.get("matcher.semantic_weight", 0)
        + new_values.get("matcher.credibility_weight", 0)
        + new_values.get("matcher.backtest_weight", 0)
    )
    if abs(weight_sum - 1.0) > 0.001:
        st.warning(
            f"The three strategy-matching ranking weights sum to "
            f"**{weight_sum:.2f}**, not 1.00 — rankings will still work but "
            f"scores lose their 0-100 interpretation. Consider rebalancing."
        )

    col_save, col_reset = st.columns(2)
    with col_save:
        if st.button("💾 Save changes", type="primary", use_container_width=True):
            save_overrides(new_values)
            changed = {k: v for k, v in new_values.items() if v != PARAMETERS[k].default}
            st.success(
                f"Saved. {len(changed)} parameter(s) now differ from defaults. "
                "New values apply on each job's next run."
            )
            st.rerun()
    with col_reset:
        if st.button("↩️ Reset ALL to defaults", use_container_width=True):
            save_overrides({})
            st.success("All parameters reset to shipped defaults.")
            st.rerun()
