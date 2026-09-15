"""WEEKLY RECAP page: Claude-generated (or deterministic placeholder,
when no ANTHROPIC_API_KEY is set) commentary built ONLY from structured
facts computed elsewhere - see fantasy_football/commentary.py."""
from __future__ import annotations

import streamlit as st

from fantasy_football import commentary
from fantasy_football import dashboard_data as dd
from fantasy_football import ui_common as ui
from fantasy_football.league_context import get_active_espn_client

st.set_page_config(page_title="Weekly Recap", page_icon="📰", layout="wide")
ui.inject_css()

season, sidebar_week = ui.render_sidebar()

st.title("Weekly Recap")
st.caption(
    "Claude-generated recap (or a deterministic placeholder if no ANTHROPIC_API_KEY is configured), "
    "built ONLY from stats already calculated elsewhere in this dashboard - Claude never computes the "
    "numbers, only the writeup."
)

conn = dd.get_connection()

# Recap-specific week picker, independent of the sidebar's week slider -
# lets you browse back through every completed week's ALREADY-generated
# recap (get_or_generate_weekly_recap reads from the DB cache; it only
# ever calls Claude on a genuine cache miss, which never happens for a
# past week once its recap has been generated once - see below, no
# regenerate control is exposed here on purpose so this page can never
# trigger a fresh paid Claude call, only display cached results).
latest_week = dd.get_latest_metrics_week(season) or sidebar_week or 1
week_options = list(range(latest_week, 0, -1))
week = st.selectbox("Week", week_options, index=0, format_func=lambda w: f"Week {w}", key="weekly_recap_week")

# The live `league` object only actually gets used on a cache MISS (see
# get_or_generate_weekly_recap's docstring) - NEXT WEEK'S GAME TO WATCH's
# free-agent-fallback projection (see commentary._game_to_watch) needs
# it, everything else here comes from already-ingested DB data.
league = get_active_espn_client().get_league(season)
result = commentary.get_or_generate_weekly_recap(conn, season, week, league=league)

if result is None:
    st.info(f"Week {week} of {season} hasn't completed yet - nothing to recap.")
    st.stop()

source_label = {
    "claude": "🤖 Claude-generated",
    "placeholder": "📋 Placeholder (no ANTHROPIC_API_KEY configured, or the Claude call failed/declined)",
}.get(result["source"], result["source"])
st.caption(f"{source_label} · generated {result['generated_at']} UTC")

for block in result["commentary"].split("\n\n"):
    st.markdown(block)

with st.expander("Underlying facts (what Claude/the placeholder actually saw)"):
    st.json(result["facts"])
