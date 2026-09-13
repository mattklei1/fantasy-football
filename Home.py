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
        icon="🏆",
        accent="#2e7d32",
    )
with c2:
    ui.stat_card(
        "Biggest Fraud",
        biggest_fraud["team_name"],
        f"{fraud_badge(biggest_fraud['fraud_index'])} · Fraud Index {biggest_fraud['fraud_index']:+.3f}",
        icon="🚩",
        accent="#c9622a",
    )
with c3:
    ui.stat_card(
        "Unluckiest Team",
        unluckiest["team_name"],
        f"Luck Wins {unluckiest['luck_wins']:+.2f}",
        icon="🍀",
        accent="#4f46e5",
    )
with c4:
    ui.stat_card(
        "Projected Champion", "Coming soon", "Playoff simulation not built yet (Phase 7)",
        icon="🔮", accent="#9aa2b1",
    )

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

MEDALS = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}
display["Rank"] = display["power_rank"].apply(lambda r: f"{MEDALS.get(r, '')}{r}")
display["Since Wk1"] = display["rank_change"].apply(ui.trend_arrow)
display["PPG"] = display["ppg"].round(0).astype(int)
display["All-Play %"] = (display["all_play_win_pct"] * 100).round(1)
display["Power Score"] = display["power_score"].round(1)

table = display[
    ["Rank", "Since Wk1", "team_name", "manager_name", "Record", "PPG", "All-Play %", "Power Score"]
].rename(columns={"team_name": "Team", "manager_name": "Manager"})

styled = table.style.map(ui.trend_color, subset=["Since Wk1"])

st.dataframe(
    styled,
    hide_index=True,
    use_container_width=True,
    column_config={
        "Since Wk1": st.column_config.TextColumn(
            "Since Wk1",
            help="Power Rank movement since Week 1 of THIS season - our own Power Score "
            "methodology (not ESPN's), so e.g. ▲6 means risen 6 spots since week 1.",
        ),
        "All-Play %": st.column_config.ProgressColumn(
            "All-Play %",
            help="Win% if you'd played every other team's score that week, every week - not "
            "just your actual opponent. The fairest read on how good a team REALLY is, "
            "independent of schedule luck.",
            format="%.0f%%",
            min_value=0,
            max_value=100,
        ),
        "Power Score": st.column_config.ProgressColumn(
            "Power Score",
            help="Blended 0-100 rating: 35% season PPG percentile + 30% All-Play win% + 20% "
            "recent form (last 3 weeks) percentile + 15% actual win%. Higher = a stronger team "
            "overall, independent of luck or schedule.",
            format="%.1f",
            min_value=0,
            max_value=100,
        ),
    },
)

st.caption(
    "Record = matchup result + median (top-half) bonus combined, matching ESPN's official "
    "standings for seasons using that format. Hover the ⓘ on a column header for what it "
    "means. Playoff Probability and per-team commentary are not built yet (Phases 7-8)."
)
