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
from src.guidance.portfolio_seed import (
    VALID_ASSET_CLASSES,
    VALID_RISK_PROFILES,
    add_holdings,
    seed_portfolio,
    validate_portfolio_payload,
)
from src.synthesis.query import answer_query

st.set_page_config(
    page_title="Investment Intelligence",
    page_icon="📊",
    layout="wide",
)

CONVICTION_BADGE = {"high": "🟢 HIGH", "medium": "🟡 MEDIUM", "low": "⚪ low"}
FUNDAMENTALS_BADGE = {
    "pass": "✅ pass",
    "downgrade": "🟠 downgrade",
    "reject": "🔴 reject",
    "not_applicable": "➖ n/a",
    "insufficient_data": "❔ no data",
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
        ["🏠 Overview", "🧭 Guidance", "💬 Ask", "💼 Portfolio", "🔬 Pipeline"],
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

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Active themes", len(themes))
        c2.metric("Multi-expert conviction", len(strong))
        c3.metric("Guidance events", len(real_guidance))
        c4.metric("Refusals logged", len(guidance_all) - len(real_guidance))

        st.subheader("Themes with real expert convergence", divider="gray")
        st.caption(
            "A theme only reaches MEDIUM/HIGH when independent experts agree on "
            "the same story — single voices stay LOW by design."
        )

        if not strong:
            st.info("No multi-expert themes yet.")
        for t in sorted(strong, key=lambda x: x.get("conviction_level", ""), reverse=True):
            experts_list = ", ".join(
                e.get("expert_name", "?") for e in (t.get("supporting_experts") or [])
            )
            with st.container(border=True):
                left, right = st.columns([3, 1])
                left.markdown(f"### {t['entity_cluster_key']}")
                left.markdown(f"**{experts_list}**")
                left.write(t.get("summary_thesis", ""))
                right.markdown(CONVICTION_BADGE.get(t.get("conviction_level"), "⚪"))
                right.markdown(
                    "Fundamentals: " + FUNDAMENTALS_BADGE.get(t.get("fundamentals_verdict"), "❔ pending")
                )
                if t.get("time_horizon_years"):
                    right.markdown(f"Horizon: ~{t['time_horizon_years']} yr")

        with st.expander(f"All {len(themes)} active themes"):
            st.dataframe(
                [
                    {
                        "Theme": t["entity_cluster_key"],
                        "Conviction": t.get("conviction_level"),
                        "Fundamentals": t.get("fundamentals_verdict") or "pending",
                        "Experts": len(t.get("supporting_experts") or []),
                        "Horizon (yr)": t.get("time_horizon_years"),
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
        "Every recommendation traces to named experts, a real strategy, and a "
        "fundamentals check — and every refusal is logged with its reason. "
        "Silence is a valid output."
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
            with st.spinner("Running the gated guidance pipeline (eligibility → synthesis → sizing)..."):
                try:
                    from src.guidance.generator import run_guidance_generation

                    counts = run_guidance_generation()
                    st.success(
                        f"Done — {counts['guidance_generated']} new guidance, "
                        f"{counts['refused']} refusals logged, {counts['error']} rejected responses."
                    )
                    st.cache_data.clear()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Pipeline run failed: {e}")
        col_note.caption(
            "Evaluates every eligible theme against your portfolio through the full "
            "compliance gates. New recommendations only appear when conviction, "
            "fundamentals, and a matched strategy all line up."
        )

        rows = fetch_where("portfolio_guidance", {"user_id": user["id"]}, limit=200)
        rows.sort(key=lambda r: r.get("generated_at") or "", reverse=True)
        real = [r for r in rows if not r["is_refusal"]]
        refusals = [r for r in rows if r["is_refusal"]]

        if not real:
            st.info(
                "No active recommendations for this user yet — the system refuses "
                "until multi-expert conviction, fundamentals, and a matched "
                "strategy all line up."
            )

        for g in real:
            change = g.get("target_allocation_change") or {}
            icon = GUIDANCE_ICON.get(g.get("guidance_type"), "🧭")
            title = f"{icon} {g.get('guidance_type', '?').replace('_', ' ').title()} — {change.get('entity', '?')}"
            with st.container(border=True):
                st.markdown(f"### {title}")
                c1, c2, c3 = st.columns(3)
                c1.metric("From", f"{change.get('from_pct', '—')}%")
                c2.metric("To", f"{change.get('to_pct', '—')}%")
                c3.metric("Status", g.get("status", "active"))

                if g.get("specific_instruments"):
                    st.markdown("**Instruments**")
                    st.dataframe(g["specific_instruments"], use_container_width=True)

                if g.get("execution_horizon"):
                    st.markdown(f"**How to execute:** {g['execution_horizon']}")
                if g.get("position_holding_horizon"):
                    st.markdown(f"**Holding horizon:** {g['position_holding_horizon']}")
                if g.get("funding_source"):
                    st.markdown(f"**Funded by:** {g['funding_source']}")
                if g.get("tax_note"):
                    st.warning(f"**Tax note:** {g['tax_note']}")
                if g.get("risks"):
                    st.error(f"**Risks:** {g['risks']}")

                attribution = g.get("attribution") or {}
                with st.expander("Full attribution (who said what, which strategy, how it was sized)"):
                    direction = attribution.get("direction") or []
                    if direction:
                        st.markdown(
                            "**Direction from:** "
                            + ", ".join(d.get("expert_name", "?") for d in direction)
                        )
                    execution = attribution.get("execution") or {}
                    if execution:
                        st.markdown(
                            f"**Execution methodology:** {execution.get('source_name') or execution.get('source_type', '?')}"
                        )
                        st.write(execution.get("strategy_text", ""))
                    st.markdown(f"**Sizing enforced via:** `{attribution.get('sizing_source', '?')}`")
                    st.caption(f"Generated {g.get('generated_at', '')} · guidance id {g['id']}")

        with st.expander(f"Refusal log ({len(refusals)}) — why the system stayed silent"):
            theme_names = {}
            for r in refusals:
                tid = r.get("theme_id")
                if tid and tid not in theme_names:
                    theme = fetch_by_id("themes", tid)
                    theme_names[tid] = (theme or {}).get("entity_cluster_key", tid)
            st.dataframe(
                [
                    {
                        "Theme": theme_names.get(r.get("theme_id"), "?"),
                        "Reason": r.get("refusal_reason"),
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
