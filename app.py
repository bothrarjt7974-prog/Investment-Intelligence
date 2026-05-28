"""
Investment Intelligence Layer — Streamlit UI.

This is the early-stage UI. Replace with Next.js when investor-ready
polish becomes necessary.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from src.db.queries import fetch_where

st.set_page_config(
    page_title="Investment Intelligence",
    page_icon="📊",
    layout="wide",
)

st.title("Investment Intelligence Layer")
st.caption("Synthesized expert intelligence on financial markets")

# Sidebar — expert universe
with st.sidebar:
    st.header("Tracked Experts")
    try:
        experts = fetch_where("experts", {"is_active": True}, limit=100)
        for expert in sorted(experts, key=lambda e: e["credibility_score"], reverse=True):
            score = expert.get("credibility_score", 50)
            geo = expert.get("geography", "?")
            st.write(f"**{expert['name']}** ({geo.upper()}) — score {score}")
    except Exception as e:
        st.error(f"Could not load experts: {e}")
        st.info("Did you run the migrations and seed script?")

# Main area
tab_briefing, tab_query, tab_data = st.tabs(
    ["📰 Morning Briefing", "🔍 Query", "📊 Data Status"]
)

with tab_briefing:
    st.header("Today's Briefing")
    st.info("Briefing generation not yet implemented. Build src/synthesis/briefing.py first.")

with tab_query:
    st.header("Ask the Intelligence Layer")
    query = st.text_input("Your question about markets, experts, or assets")
    if query:
        st.info("Query interface not yet implemented. Build src/synthesis/query.py first.")

with tab_data:
    st.header("Pipeline Status")
    try:
        from src.db.queries import supabase

        experts_count = (
            supabase.table("experts").select("id", count="exact").execute().count
        )
        content_count = (
            supabase.table("content_pieces").select("id", count="exact").execute().count
        )
        claims_count = (
            supabase.table("claims").select("id", count="exact").execute().count
        )

        col1, col2, col3 = st.columns(3)
        col1.metric("Active Experts", experts_count or 0)
        col2.metric("Content Pieces", content_count or 0)
        col3.metric("Claims Extracted", claims_count or 0)
    except Exception as e:
        st.error(f"Could not load stats: {e}")
