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
    playoff_sim = dd.get_playoff_simulation(season)
    if len(playoff_sim) >= 3:
        top3 = playoff_sim.head(3)
        podium_lines = "<br>".join(
            f'{medal} {row.team_name} '
            f'<span style="color:var(--text-muted); font-weight:400;">{row.championship_pct * 100:.0f}%</span>'
            for medal, row in zip(["🥇", "🥈", "🥉"], top3.itertuples())
        )
        podium_html = f'<div style="font-size:1.05rem; line-height:1.7;">{podium_lines}</div>'
        ui.stat_card(
            "Championship Odds", podium_html, "Monte Carlo playoff simulation - see Playoff Odds tab",
            icon="🏆", accent="#c9a227",
        )
    else:
        ui.stat_card(
            "Championship Odds", "Not enough data yet",
            "Needs at least 1 completed regular-season week",
            icon="🏆", accent="#9aa2b1",
        )

st.markdown("### Standings & Power Rankings")

display = standings.copy()

# median_wins/losses/ties are computed and stored for EVERY league
# (purely informational - who finished top-half of scoring each week),
# but only actually COUNT toward the displayed Record for a season that
# genuinely uses ESPN's median-scoring format - folding them in
# unconditionally showed a false 2-0 for a team that ESPN itself has at
# 1-0 on a league without median scoring turned on (user report,
# 2026-09-16, screenshot of "BIR and Friends" showing inflated records;
# confirmed against live ESPN settings - that league's median_scoring
# is False, matchup-only 1-0/0-1 is the real record). Same flag
# season_metrics.py's own actual_win_pct already gates on.
median_scoring = bool(dd.get_season_meta(season).get("median_scoring"))
if median_scoring:
    display["Record"] = display.apply(
        lambda r: ui.format_record(
            r["matchup_wins"] + (r["median_wins"] if pd.notna(r["median_wins"]) else 0),
            r["matchup_losses"] + (r["median_losses"] if pd.notna(r["median_losses"]) else 0),
            r["matchup_ties"] + (r["median_ties"] if pd.notna(r["median_ties"]) else 0),
        ),
        axis=1,
    )
else:
    display["Record"] = display.apply(
        lambda r: ui.format_record(r["matchup_wins"], r["matchup_losses"], r["matchup_ties"]), axis=1,
    )

MEDALS = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}
display["Power Rank"] = display["power_rank"].apply(lambda r: f"{MEDALS.get(r, '')}{r}")
display["Since Wk0"] = display["rank_change"].apply(ui.trend_arrow)
display["PPG"] = display["ppg"].round(0).astype(int)
display["All-Play %"] = (display["all_play_win_pct"] * 100).round(1)
display["Power Score"] = display["power_score"].round(1)

# Default sort: real standings order (combined matchup+median record, this
# league's own ESPN tiebreak of total points scored - same two-key sort
# playoff_sim.py's rank_teams() uses for real seeding) - NOT Power Rank,
# so the table opens on "who's actually winning," with Power Rank as its
# own separate, clearly-labeled column rather than silently driving row
# order (user, 2026-09-16: "have the default sort be by actual record").
display = display.sort_values(["actual_win_pct", "points_for"], ascending=[False, False]).reset_index(drop=True)

table = display[
    ["Power Rank", "Since Wk0", "team_name", "manager_name", "Record", "PPG", "All-Play %", "Power Score"]
].rename(columns={"team_name": "Team", "manager_name": "Manager"})

styled = table.style.map(ui.trend_color, subset=["Since Wk0"])

st.dataframe(
    styled,
    hide_index=True,
    use_container_width=True,
    column_config={
        "Power Rank": st.column_config.TextColumn(
            "Power Rank",
            help="Rank by Power Score (see the Power Score column) - NOT the same as actual "
            "standings position (this table's own sort order, below), NOT Roster Strength "
            "(a separate, forward-looking 'how good is this roster right now' metric - see "
            "the Roster Strength tab), and NOT a projected final standing. It's a "
            "results-plus-underlying-quality blend of THIS SEASON so far.",
        ),
        "Since Wk0": st.column_config.TextColumn(
            "Since Wk0",
            help="Power Rank movement since Week 0 - real draft-time Roster Strength rank "
            "(not this season's own results) vs. current Power Rank, so e.g. ▲6 means "
            "you're outperforming your preseason roster grade by 6 spots. Falls back to "
            "movement since week 1's Power Rank if no real Week-0 Roster Strength snapshot "
            "exists yet for this season.",
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
    "standings for seasons using that format. Hover the ⓘ on a column header for what it means."
)
