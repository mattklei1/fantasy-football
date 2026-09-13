"""MATCHUPS page: per-matchup cards for a selected week. Weeks already
fully processed into metrics (week <= latest_metrics_week) show the
final score. Everything else (the current live week, or a future week)
pulls LIVE ESPN box scores so it shows real-time score + a genuinely
"right now" projected total - deliberately NOT just the raw score, since
a big early lead is often just "the other team's players haven't played
yet," not a real edge. Win probability is our own model (ESPN exposes no
win-probability field at all - confirmed against the installed espn_api
source), centered on that live projected total once one exists."""
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
        f"PPG {ctx['ppg']:.0f} · Last 3 {ctx['last3_ppg']:.0f} · "
        f"All-Play {ctx['all_play_win_pct']*100:.0f}%"
    )


is_final_week = latest_metrics_week is not None and week <= latest_metrics_week

live_by_team: dict[int, dict] = {}
snapshots: dict[int, float] = {}
live_error = False
if not is_final_week:
    try:
        live_df = dd.get_live_box_scores(season, week)
        snapshots = dd.get_projection_snapshots(season, week)
        for _, r in live_df.iterrows():
            live_by_team[r["home_team_pk"]] = {
                "score": r["home_score"], "projected": r["home_projected"], "opp": r["away_team_pk"],
            }
            live_by_team[r["away_team_pk"]] = {
                "score": r["away_score"], "projected": r["away_projected"], "opp": r["home_team_pk"],
            }
    except Exception:  # noqa: BLE001 - ESPN hiccup shouldn't take the whole page down
        live_error = True

for _, m in matchups.iterrows():
    with st.container(border=True):
        col_home, col_vs, col_away = st.columns([5, 1, 5])
        home_pk, away_pk = m["home_team_pk"], m["away_team_pk"]

        with col_home:
            # .strip() matters: a trailing space before the closing ** breaks
            # CommonMark bold parsing (some ESPN team names have one)
            st.markdown(f"**{m['home_team_name'].strip()}**")
            st.caption(team_context_line(home_pk))
        with col_away:
            st.markdown(f"**{m['away_team_name'].strip()}**")
            st.caption(team_context_line(away_pk))
        with col_vs:
            st.markdown("<div style='text-align:center'>VS</div>", unsafe_allow_html=True)

        if is_final_week:
            home_score, away_score = m["home_score"], m["away_score"]
            col_home.metric("Final", f"{home_score:.1f}")
            col_away.metric("Final", f"{away_score:.1f}")
            if home_score is not None and away_score is not None and home_score != away_score:
                winner_col, loser_col = (col_home, col_away) if home_score > away_score else (col_away, col_home)
                winner_col.markdown(ui.status_bubble_html("WON", "win"), unsafe_allow_html=True)
                loser_col.markdown(ui.status_bubble_html("LOST", "loss"), unsafe_allow_html=True)
        elif live_error or home_pk not in live_by_team:
            st.caption("Live data not available right now - try refreshing in a moment.")
        else:
            home_live, away_live = live_by_team[home_pk], live_by_team[away_pk]
            home_proj, away_proj = home_live["projected"], away_live["projected"]
            home_snap, away_snap = snapshots.get(home_pk), snapshots.get(away_pk)

            col_home.metric(
                "Score", f"{home_live['score']:.1f}",
                delta=f"proj {home_proj:.1f}" + (f" ({home_proj - home_snap:+.1f} vs wk start)" if home_snap is not None else ""),
                delta_color="off",
            )
            col_away.metric(
                "Score", f"{away_live['score']:.1f}",
                delta=f"proj {away_proj:.1f}" + (f" ({away_proj - away_snap:+.1f} vs wk start)" if away_snap is not None else ""),
                delta_color="off",
            )

            if home_proj > away_proj:
                col_home.markdown(ui.status_bubble_html("FAVORED", "win"), unsafe_allow_html=True)
                col_away.markdown(ui.status_bubble_html("UNDERDOG", "loss"), unsafe_allow_html=True)
            elif away_proj > home_proj:
                col_away.markdown(ui.status_bubble_html("FAVORED", "win"), unsafe_allow_html=True)
                col_home.markdown(ui.status_bubble_html("UNDERDOG", "loss"), unsafe_allow_html=True)

            prob = dd.live_win_probability(season, home_pk, away_pk, home_proj, away_proj)
            col_home.progress(prob, text=f"{prob*100:.0f}% win prob.")
            col_away.progress(1 - prob, text=f"{(1-prob)*100:.0f}% win prob.")

if not is_final_week:
    st.caption(
        "Score/projection update live from ESPN (refreshes about once a minute). \"proj\" is each "
        "team's CURRENT projected total - points scored so far plus the rest of the lineup's "
        "projections - not just the raw score, so a big early lead from one team's players simply "
        "having played first doesn't look like a bigger edge than it is. \"vs wk start\" compares "
        "that to the first projection this app ever saw for the week. Win probability is our own "
        "model (60%/40% blend of each team's projected total and scoring variance) - ESPN "
        "publishes no win probability of its own."
    )
