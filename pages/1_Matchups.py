"""MATCHUPS page: per-matchup cards for a selected week, with a custom
(non-ESPN) win probability for weeks that haven't been played yet."""
from __future__ import annotations

import streamlit as st

from fantasy_football import dashboard_data as dd
from fantasy_football import ui_common as ui

st.set_page_config(page_title="Matchups", page_icon="🏈", layout="wide")
ui.inject_css()

season, _ = ui.render_sidebar()
meta = dd.get_season_meta(season)
reg_season_count = meta.get("reg_season_count") or 1
current_week = meta.get("current_week") or 1

st.title("Matchups")

week = st.selectbox(
    "Week", options=list(range(1, reg_season_count + 1)), index=min(current_week, reg_season_count) - 1
)

matchups = dd.get_matchups_for_week(season, week)
latest_metrics_week = dd.get_latest_metrics_week(season)
standings = dd.get_standings(season, through_week=latest_metrics_week) if latest_metrics_week else None

if matchups.empty:
    st.info("No matchup data for this week yet.")
    st.stop()

context_by_team = {}
if standings is not None and not standings.empty:
    context_by_team = standings.set_index("team_pk").to_dict(orient="index")


def team_context_line(team_pk: int) -> str:
    ctx = context_by_team.get(team_pk)
    if not ctx:
        return "No stats yet this season"
    record = ui.format_record(
        ctx["matchup_wins"] + (ctx["median_wins"] or 0),
        ctx["matchup_losses"] + (ctx["median_losses"] or 0),
        ctx["matchup_ties"] + (ctx["median_ties"] or 0),
    )
    return (
        f"Record {record} · Power Rank #{int(ctx['power_rank'])} · "
        f"PPG {ctx['ppg']:.1f} · Last 3 {ctx['last3_ppg']:.1f} · "
        f"All-Play {ctx['all_play_win_pct']*100:.0f}%"
    )


played = matchups["completed"].iloc[0] == 1 if not matchups.empty else False

for _, m in matchups.iterrows():
    with st.container(border=True):
        col_home, col_vs, col_away = st.columns([5, 1, 5])

        with col_home:
            # .strip() matters: a trailing space before the closing ** breaks
            # CommonMark bold parsing (some ESPN team names have one)
            st.markdown(f"**{m['home_team_name'].strip()}**")
            st.caption(team_context_line(m["home_team_pk"]))
        with col_away:
            st.markdown(f"**{m['away_team_name'].strip()}**")
            st.caption(team_context_line(m["away_team_pk"]))

        if m["home_score"] is not None and m["away_score"] is not None:
            with col_vs:
                st.markdown("<div style='text-align:center'>VS</div>", unsafe_allow_html=True)
            col_home.metric("Score", f"{m['home_score']:.1f}")
            col_away.metric("Score", f"{m['away_score']:.1f}")
        else:
            prob = None
            if latest_metrics_week:
                prob = dd.project_matchup_win_probability(
                    season, m["home_team_pk"], m["away_team_pk"], latest_metrics_week
                )
            with col_vs:
                st.markdown("<div style='text-align:center'>VS</div>", unsafe_allow_html=True)
            if prob is not None:
                col_home.progress(prob, text=f"{prob*100:.0f}% win prob.")
                col_away.progress(1 - prob, text=f"{(1-prob)*100:.0f}% win prob.")
            else:
                st.caption("Not enough data yet for a projection.")

if not played and matchups["completed"].iloc[0] == 0:
    st.caption(
        "Win probability is our own projection (60% season PPG + 40% last-3-week PPG, each "
        "team's observed scoring variance) - not ESPN's projected score."
    )
