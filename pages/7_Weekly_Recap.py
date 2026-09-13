"""WEEKLY RECAP page: Claude-generated (or deterministic placeholder,
when no ANTHROPIC_API_KEY is set) commentary built ONLY from structured
facts computed elsewhere - see fantasy_football/commentary.py."""
from __future__ import annotations

import streamlit as st

from fantasy_football import commentary
from fantasy_football import dashboard_data as dd
from fantasy_football import ui_common as ui

st.set_page_config(page_title="Weekly Recap", page_icon="📰", layout="wide")
ui.inject_css()

season, week = ui.render_sidebar()

st.title("Weekly Recap")
st.caption(
    "Claude-generated recap (or a deterministic placeholder if no ANTHROPIC_API_KEY is configured), "
    "built ONLY from stats already calculated elsewhere in this dashboard - Claude never computes the "
    "numbers, only the writeup."
)

conn = dd.get_connection()
result = commentary.get_or_generate_weekly_recap(conn, season, week)

if result is None:
    st.info(f"Week {week} of {season} hasn't completed yet - nothing to recap.")
    st.stop()

source_label = {
    "claude": "🤖 Claude-generated",
    "placeholder": "📋 Placeholder (no ANTHROPIC_API_KEY configured, or the Claude call failed/declined)",
}.get(result["source"], result["source"])
col1, col2 = st.columns([3, 1])
with col1:
    st.caption(f"{source_label} · generated {result['generated_at']} UTC")
with col2:
    if st.button("🔄 Regenerate"):
        commentary.get_or_generate_weekly_recap(conn, season, week, force_regenerate=True)
        st.rerun()

for block in result["commentary"].split("\n\n"):
    st.markdown(block)

with st.expander("Underlying facts (what Claude/the placeholder actually saw)"):
    st.json(result["facts"])
