"""HOME page: headline cards + standings / power rankings table."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from fantasy_football import dashboard_data as dd
from fantasy_football import ui_common as ui
from fantasy_football.badges import fraud_badge

st.set_page_config(page_title="Fantasy Football Dashboard", page_icon="🏈", layout="wide")
ui.inject_css()

season, week = ui.render_sidebar()

st.title("Home")

standings = dd.get_standings(season, through_week=week)

if standings.empty:
    st.info(
        "No completed regular-season weeks yet for this season, so there are no metrics to "
        "show. Metrics populate once week 1 finishes and a refresh runs."
    )
    st.stop()

st.caption(f"Standings and Power Rankings through week {week} (regular season)")

# ---- Headline cards ----
best_team = standings.loc[standings["power_score"].idxmax()]
biggest_fraud = standings.loc[standings["fraud_index"].idxmax()]
unluckiest = standings.loc[standings["luck_wins"].idxmin()]

c1, c2, c3, c4 = st.columns(4)
with c1:
    ui.stat_card(
        "Best Team",
        best_team["team_name"],
        f"Power Score {best_team['power_score']:.1f}",
    )
with c2:
    ui.stat_card(
        "Biggest Fraud",
        biggest_fraud["team_name"],
        f"{fraud_badge(biggest_fraud['fraud_index'])} · Fraud Index {biggest_fraud['fraud_index']:+.3f}",
    )
with c3:
    ui.stat_card(
        "Unluckiest Team",
        unluckiest["team_name"],
        f"Luck Wins {unluckiest['luck_wins']:+.2f}",
    )
with c4:
    ui.stat_card("Projected Champion", "Coming soon", "Playoff simulation not built yet (Phase 7)")

st.markdown("### Standings & Power Rankings")

display = standings.copy()
display["Record"] = display.apply(
    lambda r: ui.format_record(
        r["matchup_wins"] + (r["median_wins"] if pd.notna(r["median_wins"]) else 0),
        r["matchup_losses"] + (r["median_losses"] if pd.notna(r["median_losses"]) else 0),
        r["matchup_ties"] + (r["median_ties"] if pd.notna(r["median_ties"]) else 0),
    ),
    axis=1,
)
display["Δ"] = display["rank_change"].apply(ui.rank_change_html)
display["PPG"] = display["ppg"].round(1)
display["All-Play %"] = (display["all_play_win_pct"] * 100).round(1).astype(str) + "%"
display["Power Score"] = display["power_score"].round(1)

table = display[
    ["power_rank", "Δ", "team_name", "manager_name", "Record", "PPG", "All-Play %", "Power Score"]
].rename(columns={"power_rank": "Rank", "team_name": "Team", "manager_name": "Manager"})

st.markdown(
    table.to_html(escape=False, index=False, classes="ff-table"),
    unsafe_allow_html=True,
)

st.caption(
    "Record = matchup result + median (top-half) bonus combined, matching ESPN's official "
    "standings for seasons using that format. Playoff Probability and per-team commentary "
    "are not built yet (Phases 7-8)."
)
