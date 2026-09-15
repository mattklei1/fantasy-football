"""PLAYOFF ODDS page: Monte Carlo playoff simulation (>=10,000 trials).
See fantasy_football/metrics/playoff_sim.py for the full methodology."""
from __future__ import annotations

import streamlit as st

from fantasy_football import dashboard_data as dd
from fantasy_football import ui_common as ui
from fantasy_football.metrics.playoff_sim import DEFAULT_N_SIMS, SHRINKAGE_GAMES
from fantasy_football.metrics.win_probability import MIN_STDEV

st.set_page_config(page_title="Playoff Odds", page_icon="🎲", layout="wide")
ui.inject_css()

season, _ = ui.render_sidebar()

st.title("Playoff Odds")
st.caption(
    f"Monte Carlo simulation ({DEFAULT_N_SIMS:,} simulated seasons) of every remaining regular-season "
    "game and the full 6-team playoff bracket - NOT ESPN's own projection, a separate model built for "
    "this dashboard."
)

df = dd.get_playoff_simulation(season)

if df.empty:
    meta = dd.get_season_meta(season)
    if meta and meta.get("playoff_team_count") != 6:
        st.info(
            f"This season used a {meta.get('playoff_team_count')}-team playoff format, not the "
            "6-team/top-2-bye format this simulator's bracket logic is verified against - not "
            "simulating rather than guessing at an unverified bracket shape."
        )
    else:
        st.info(
            "Not enough data yet - the simulation needs at least one completed regular-season week "
            "this season to project forward from. Check back once week 1 wraps up."
        )
    st.stop()

df = df.reset_index(drop=True)
df.insert(0, "Rank", df.index + 1)

display = df.copy()
display["Championship %"] = (display["championship_pct"] * 100).round(1)
display["Playoff %"] = (display["playoff_pct"] * 100).round(1)
display["Bye %"] = (display["bye_pct"] * 100).round(1)
display["#1 Seed %"] = (display["seed1_pct"] * 100).round(1)

table = display[
    ["Rank", "team_name", "manager_name", "Championship %", "Playoff %", "Bye %", "#1 Seed %"]
].rename(columns={"team_name": "Team", "manager_name": "Manager"})

st.markdown(table.to_html(escape=False, index=False, classes="ff-table"), unsafe_allow_html=True)

with st.expander("Methodology"):
    st.markdown(
        f"""
Runs **{DEFAULT_N_SIMS:,} simulated versions** of the rest of the season, each one:

1. **Simulates every remaining regular-season game.** Each team's score in a simulated game is drawn
   from a Normal distribution centered on a blend of `60% x season PPG + 40% x last-3-week PPG` (the
   same formula, and the same underlying `expected_score()`, as the Matchups page's single-game win
   probability) and the league-wide average PPG - weighted toward the team's own number as it plays more
   real games (`games played / (games played + {SHRINKAGE_GAMES})`), so one huge or terrible early week
   doesn't get treated as a fully-proven, permanent gap before there's enough of a sample to trust it.
   Each score also uses that team's own observed weekly scoring standard deviation (floored at
   {MIN_STDEV:g} points for a team with 0-1 real games - not enough of a sample yet to trust, and an
   un-floored stdev would make the simulation falsely overconfident). Both the shrinkage weight and the
   stdev floor are calibrated against this league's own 10 completed real seasons, not guessed.
2. **Determines final regular-season standings** from the combined real + simulated record (matchup wins,
   plus the top-half/median bonus win in seasons that use it), seeded exactly like this league's real ESPN
   settings: combined win % first, then total points scored as the tiebreaker.
3. **Simulates the playoff bracket** for whichever 6 teams made it, using the same per-game scoring model.
   This league's bracket is a **fixed** (not reseeded) top-2-bye format, confirmed against every one of
   this league's 10 completed seasons, including 4 where a lower seed upset a higher seed in round 1 -
   in every case the upsetting team still played the seed the un-upset bracket would have predicted:
   - Round 1: #3 seed vs #6 seed, and #4 seed vs #5 seed (#1 and #2 seeds have a bye)
   - Semifinals: #1 seed vs the #4/#5 winner, #2 seed vs the #3/#6 winner
   - Championship: the two semifinal winners

A team's own scoring projection is frozen at its CURRENT real value for the whole simulated season
(including any playoff run) - it doesn't get better or worse as that trial's simulated games play out.

**Playoff %** = made the 6-team field in that simulated trial · **Bye %** = finished #1 or #2 seed ·
**#1 Seed %** = finished #1 exactly · **Championship %** = won the simulated bracket.

Requires at least 1 completed regular-season week this season, and only supports this league's real
6-team/top-2-bye playoff format (every season 2015-present) - a different format isn't guessed at.
        """
    )
