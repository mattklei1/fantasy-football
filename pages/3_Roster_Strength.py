"""ROSTER STRENGTH page: forward-looking roster quality, blending this
week's ESPN projection and FantasyPros' rest-of-season expert rankings,
weighted across starters vs. bench with a season-position-aware split.
See fantasy_football/metrics/roster_strength.py for the full
methodology, including why ESPN's season-to-date positional rank was
deliberately dropped from the blend."""
from __future__ import annotations

import streamlit as st

from fantasy_football import dashboard_data as dd
from fantasy_football import ui_common as ui

st.set_page_config(page_title="Roster Strength", page_icon="💪", layout="wide")
ui.inject_css()

season, _ = ui.render_sidebar()
meta = dd.get_season_meta(season)

st.title("Roster Strength")

fp_last_refreshed = dd.get_fantasypros_last_refreshed(season)
if fp_last_refreshed:
    st.caption(f"**As of {fp_last_refreshed} UTC** (last FantasyPros rest-of-season rankings pull)")
else:
    st.caption("**As of:** FantasyPros rest-of-season rankings haven't been pulled yet this season.")

st.caption(
    "Forward-looking roster quality (NOT past performance - see Power Score on Home for that). "
    "Blends this week's ESPN projection and a third-party rest-of-season expert consensus rank "
    "- no season-to-date performance data. Refreshes automatically once a day - the projection "
    "component is always a PREGAME number, never mid-game: that refresh is skipped entirely "
    "during live NFL broadcast windows, so it can't pick up a player's live, in-progress score."
)

df = dd.get_roster_strength(season)

if df.empty:
    st.info(
        "Not computed for this season yet. Roster Strength only reflects the CURRENT week of "
        "the CURRENT season - ESPN doesn't expose historical positional ranks, so this can't be "
        "backfilled for past weeks or past seasons. Run a refresh during the current season to "
        "populate it."
    )
    st.stop()

week = int(df["week"].iloc[0])
bench_weight = df["bench_weight"].iloc[0]
st.caption(
    f"As of week {week} · Starter weight {100*(1-bench_weight):.0f}% / "
    f"Bench weight {100*bench_weight:.0f}% "
    "(bench share decays across the regular season as bye weeks pass, but never to zero - "
    "injury-replacement value doesn't go away)"
)

df = df.reset_index(drop=True)
# "Rank" is the fixed Roster Strength rank (identity column) - it does NOT
# renumber when the table below is sorted by a different metric, so you can
# still see each team's overall rank while browsing by Starter/Bench Value.
df.insert(0, "Rank", df.index + 1)
display = df.copy()
display["Starter Value"] = (display["starter_value"] * 100).round(1)
display["Bench Value"] = (display["bench_value"] * 100).round(1)
display["Roster Strength"] = display["roster_strength"].round(1)

sort_col1, sort_col2 = st.columns([3, 1])
sort_by = sort_col1.selectbox(
    "Sort by", ["Roster Strength", "Starter Value", "Bench Value"], key="roster_strength_sort_by"
)
descending = sort_col2.checkbox("Descending", value=True, key="roster_strength_sort_desc")

table = (
    display[["Rank", "team_name", "manager_name", "Starter Value", "Bench Value", "Roster Strength"]]
    .rename(columns={"team_name": "Team", "manager_name": "Manager"})
    .sort_values(sort_by, ascending=not descending)
)
st.markdown(table.to_html(escape=False, index=False, classes="ff-table"), unsafe_allow_html=True)

with st.expander("Methodology"):
    st.markdown(
        """
Each rostered player gets a blended **player value** (0-1), from up to 2 signals:
- **This week's ESPN projection** (weight 20), converted to a percentile *within their
  position* across every rostered player league-wide (so a QB's projection is only compared
  to other QBs)
- **A third-party rest-of-season expert consensus positional rank** (weight 40 - the larger of
  the two, since it's genuinely forward-looking and comes from a licensed panel of experts, not
  a single source), converted to a bounded 0-1 score via `1 / (1 + (rank-1)/12)` (rank 1 → 1.0,
  rank 13 → 0.5, rank 25 → 0.33, ...). Matched to our player records by name (D/ST by NFL team,
  since the source lists "Houston Texans" where ESPN lists "Texans D/ST") - see
  `fantasy_football/player_matching.py`. A player who can't be matched, or isn't ranked by that
  source, falls back entirely to the ESPN projection signal - missing data is never treated as
  a zero.

Two other signals from the original design aren't used: an alternate rest-of-season ranking
source (weight 25) was never wired up, and ESPN's own season-to-date positional rank (weight
15) was deliberately dropped - it's backward-looking (cumulative points scored so far this
season), which cuts against this metric's own forward-looking premise, and its useful signal
was already largely captured by the expert consensus above. The remaining 2 weights are
renormalized to sum to 100 (20:40 → proportionally 20/60 : 40/60, roughly a 1:2 split).

Team-level **Starter Value** and **Bench Value** are the average player value within each
group (IR-slot players are excluded entirely - they can't play). The final score blends the
two using a week-dependent bench weight that decays from 35% in week 1 down to a 10% floor by
the end of the regular season, and holds there through the playoffs.
        """
    )
