"""ROSTER STRENGTH page: forward-looking roster quality, blending this
week's ESPN projection, ESPN's season-long positional rank, and
FantasyPros' rest-of-season expert rankings, weighted across starters vs.
bench with a season-position-aware split. See
fantasy_football/metrics/roster_strength.py for the full methodology."""
from __future__ import annotations

import streamlit as st

from fantasy_football import dashboard_data as dd
from fantasy_football import ui_common as ui

st.set_page_config(page_title="Roster Strength", page_icon="💪", layout="wide")
ui.inject_css()

season, _ = ui.render_sidebar()
meta = dd.get_season_meta(season)

st.title("Roster Strength")
st.caption(
    "Forward-looking roster quality (NOT past performance - see Power Score on Home for that). "
    "Blends this week's ESPN projection, ESPN's season-long positional rank, and FantasyPros' "
    "rest-of-season expert consensus rank."
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
df.insert(0, "Rank", df.index + 1)
display = df.copy()
display["Starter Value"] = (display["starter_value"] * 100).round(1)
display["Bench Value"] = (display["bench_value"] * 100).round(1)
display["Roster Strength"] = display["roster_strength"].round(1)

table = display[["Rank", "team_name", "manager_name", "Starter Value", "Bench Value", "Roster Strength"]].rename(
    columns={"team_name": "Team", "manager_name": "Manager"}
)
st.markdown(table.to_html(escape=False, index=False, classes="ff-table"), unsafe_allow_html=True)

with st.expander("Methodology"):
    st.markdown(
        """
Each rostered player gets a blended **player value** (0-1), from up to 3 signals:
- **This week's ESPN projection** (weight 20), converted to a percentile *within their
  position* across every rostered player league-wide (so a QB's projection is only compared
  to other QBs)
- **ESPN's season-long positional rank** (weight 15), converted to a bounded 0-1 score via
  `1 / (1 + (rank-1)/12)` (rank 1 → 1.0, rank 13 → 0.5, rank 25 → 0.33, ...)
- **FantasyPros' rest-of-season expert consensus positional rank** (weight 40 - the largest
  single signal, since it's the only genuinely forward-looking one of the three and comes
  from a licensed panel of experts, not a single source), converted with the same bounded
  decay as ESPN's rank. Matched to our player records by name (D/ST by NFL team, since
  FantasyPros lists "Houston Texans" where ESPN lists "Texans D/ST") - see
  `fantasy_football/player_matching.py`. A player who can't be matched, or isn't ranked by
  FantasyPros, simply falls back to whichever of the other two signals it has - missing data
  is never treated as a zero.
- Yahoo rest-of-season rankings were considered in the original design (weight 25) but
  aren't wired up - Yahoo has no generic rankings endpoint outside of league-specific OAuth
  access. The remaining 3 weights are renormalized to sum to 100 without it (20:15:40 →
  proportionally 20/75 : 15/75 : 40/75).

Team-level **Starter Value** and **Bench Value** are the average player value within each
group (IR-slot players are excluded entirely - they can't play). The final score blends the
two using a week-dependent bench weight that decays from 35% in week 1 down to a 10% floor by
the end of the regular season, and holds there through the playoffs.
        """
    )
