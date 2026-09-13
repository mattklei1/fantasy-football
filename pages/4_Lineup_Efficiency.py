"""LINEUP EFFICIENCY page: Actual vs. Optimal starter points, and
whether a different legal lineup would have won the matchup. Season-to-
date through the selected week, matching the Home/Luck page convention."""
from __future__ import annotations

import streamlit as st

from fantasy_football import dashboard_data as dd
from fantasy_football import ui_common as ui

st.set_page_config(page_title="Lineup Efficiency", page_icon="🧠", layout="wide")
ui.inject_css()

season, week = ui.render_sidebar()

st.title("Lineup Efficiency")

df = dd.get_lineup_efficiency(season, through_week=week)

if df.empty:
    st.info(
        "No lineup efficiency data for this season/week. This requires per-player slot "
        "eligibility data that's only available for 2019 onward, and only for weeks ingested "
        "since this feature shipped (2026-09-13) - run a refresh to backfill."
    )
    st.stop()

st.caption(f"Through week {week} (regular season) · ranked by season-to-date Lineup Efficiency")

df = df.reset_index(drop=True)
df.insert(0, "Rank", df.index + 1)

display = df.copy()
display["Actual Record"] = display.apply(
    lambda r: ui.format_record(r["matchup_wins"], r["matchup_losses"], r["matchup_ties"]), axis=1
)
display["Optimal-Lineup Record"] = display.apply(
    lambda r: ui.format_record(r["optimal_wins"], r["optimal_losses"], r["optimal_ties"]), axis=1
)
display["Efficiency"] = (display["lineup_efficiency"] * 100).round(1).astype(str) + "%"
display["Actual Pts"] = display["actual_starter_points"].round(1)
display["Optimal Pts"] = display["optimal_starter_points"].round(1)
display["Left on Bench"] = display["points_left_on_bench"].round(1)
display["Manager-Caused Losses"] = display["manager_caused_losses"].astype(int)

table = display[
    ["Rank", "team_name", "manager_name", "Efficiency", "Actual Pts", "Optimal Pts",
     "Left on Bench", "Actual Record", "Optimal-Lineup Record", "Manager-Caused Losses"]
].rename(columns={"team_name": "Team", "manager_name": "Manager"})

st.markdown(table.to_html(escape=False, index=False, classes="ff-table"), unsafe_allow_html=True)

with st.expander("Methodology"):
    st.markdown(
        """
For every completed week, the **optimal legal lineup** is computed exactly (not a heuristic) -
an exact maximum-weight assignment of rostered players to starting slots, respecting each
player's real slot eligibility and the league's real roster settings pulled from ESPN
(`league.settings.position_slot_counts` - never hardcoded).

- **Lineup Efficiency** = season-to-date Actual Starter Points ÷ Optimal Starter Points
- **Optimal-Lineup Record** recomputes each week's result using the optimal lineup's points
  against the opponent's real actual score, then aggregates to a record just like the real one
- **Manager-Caused Losses** counts weeks where the optimal lineup would have won, but the
  real lineup didn't - a loss caused by a lineup decision, not bad luck

Only available for 2019+ (needs per-player box-score data) and only for weeks ingested since
this feature shipped - no backfill exists for weeks ingested before eligibility tracking began.
        """
    )
