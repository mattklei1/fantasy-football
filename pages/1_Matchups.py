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
from fantasy_football import history_data as hd
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

# Manager names resolved independent of standings (which don't exist yet
# in week 1 before any games are played) - same persistent-identity/
# real-full-name lookup the History page uses, not the ESPN account
# display name/username.
_managers_df = hd.get_managers()
_name_by_manager_id = dict(zip(_managers_df["manager_id"], _managers_df["display_name"]))


def manager_name_for(team_pk: int) -> str:
    mgr_id = hd.get_primary_manager_id(team_pk)
    return _name_by_manager_id.get(mgr_id, "Unknown manager")


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


def playoff_round_label(season_id: int, week: int) -> str:
    """Quarterfinals/Semifinals/Finals instead of a raw week number - the
    standard 6-team, top-2-seed-bye, 3-week ESPN playoff bracket shape.
    Falls back to a raw week number if a season's real reg_season_count
    doesn't line up with that shape (e.g. a different playoff format)."""
    reg_count = dd.get_season_meta(season_id).get("reg_season_count")
    if not reg_count:
        return f"Wk{week}"
    round_num = week - reg_count
    return {1: "Quarterfinals", 2: "Semifinals", 3: "Finals"}.get(round_num, f"Playoff Wk{week}")


def render_last_meetings(home_pk: int, away_pk: int, home_name: str, away_name: str) -> None:
    """Last 5 head-to-head meetings between these two teams' MANAGERS
    (not team_pk, which resets every season - a rivalry spans team-name
    changes) - a persistent-identity lookup, same approach as the History
    page's Head-to-Head tab."""
    home_mgr = hd.get_primary_manager_id(home_pk)
    away_mgr = hd.get_primary_manager_id(away_pk)
    with st.expander("Last 5 meetings"):
        if not home_mgr or not away_mgr:
            st.caption("No manager history available.")
            return
        h2h = hd.get_head_to_head(home_mgr, away_mgr)
        if h2h.get("games_played", 0) == 0:
            st.caption("These two have never played each other.")
            return
        last5 = h2h["history"].sort_values(["season_id", "week"], ascending=False).head(5)
        for _, g in last5.iterrows():
            week_label = (
                playoff_round_label(int(g["season_id"]), int(g["week"]))
                if g["is_playoff"] else f"Wk{int(g['week'])}"
            )
            label = f"{int(g['season_id'])} {week_label}"
            if g["a_result"] == "T":
                home_badge = ui.status_bubble_html("T", "neutral")
                away_badge = ui.status_bubble_html("T", "neutral")
            else:
                home_won = g["a_result"] == "W"
                home_badge = ui.status_bubble_html("W" if home_won else "L", "win" if home_won else "loss")
                away_badge = ui.status_bubble_html("L" if home_won else "W", "loss" if home_won else "win")
            row = st.columns([2, 3, 3])
            row[0].caption(label)
            row[1].markdown(f"{home_badge} {home_name} {g['a_score']:.1f}", unsafe_allow_html=True)
            row[2].markdown(f"{away_badge} {away_name} {g['b_score']:.1f}", unsafe_allow_html=True)


is_final_week = latest_metrics_week is not None and week <= latest_metrics_week
# A real, verified ESPN API quirk (checked directly against the installed
# espn_api library, 2026-09-13): league.box_scores(week) for any week
# PAST the real current week silently returns the CURRENT week's box
# scores instead of an error or zeros - there's no reliable live data
# source for a week that hasn't started yet, so don't even ask for it.
is_future_week = week > current_week

live_by_team: dict[int, dict] = {}
live_error = False
if not is_final_week and not is_future_week:
    try:
        live_df = dd.get_live_box_scores(season, week)
        for _, r in live_df.iterrows():
            live_by_team[r["home_team_pk"]] = {
                "score": r["home_score"], "projected": r["home_projected"], "opp": r["away_team_pk"],
            }
            live_by_team[r["away_team_pk"]] = {
                "score": r["away_score"], "projected": r["away_projected"], "opp": r["home_team_pk"],
            }
    except Exception:  # noqa: BLE001 - ESPN hiccup shouldn't take the whole page down
        live_error = True

team_name_by_pk = {}
for _, m in matchups.iterrows():
    team_name_by_pk[m["home_team_pk"]] = m["home_team_name"].strip()
    team_name_by_pk[m["away_team_pk"]] = m["away_team_name"].strip()

if meta.get("median_scoring") and not is_final_week and not is_future_week and not live_error and live_by_team:
    # Median (top-half) bonus cutline: rank every team by CURRENT
    # projected score (not raw score-so-far, same reasoning as the rest
    # of this page). Shows ALL teams (not just the 3 nearest the
    # cutline) as one plain HTML table (not st.dataframe - that widget
    # scrolls past a fixed height instead of growing to fit every row,
    # and its canvas-rendered grid can't have a column responsively
    # hidden), row-tinted by how safe/unsafe each team's spot is, and
    # drops the Manager subtext on a narrow (phone) screen to save
    # width - see ui.cutline_table_html.
    ranked = sorted(
        ((pk, team_name_by_pk.get(pk, "?"), manager_name_for(pk), v["projected"]) for pk, v in live_by_team.items()),
        key=lambda t: t[3], reverse=True,
    )
    if len(ranked) >= 2:
        n_teams = len(ranked)
        cutoff_idx = n_teams // 2
        cutline_score = ranked[cutoff_idx - 1][3]

        projected_by_team = {pk: proj for pk, _, _, proj in ranked}
        analysis = dd.live_cutline_analysis(season, projected_by_team)

        cutline_rows = [
            {
                "rank": rank,
                "team": team_name,
                "manager": mgr.split()[-1] if mgr else mgr,
                "projected": proj,
                "vs_cutline": proj - cutline_score,
                "make_pct": (analysis.get(pk, {}).get("p_making_it") or 0) * 100,
                "p10": analysis.get(pk, {}).get("p10") or 0.0,
                "p90": analysis.get(pk, {}).get("p90") or 0.0,
                "tone": ui.tone_for_probability(analysis.get(pk, {}).get("p_making_it") or 0),
            }
            for rank, (pk, team_name, mgr, proj) in enumerate(ranked, start=1)
        ]

        with st.container(border=True):
            st.markdown("#### Median Cutline (top half earns the bonus win)")
            st.markdown(ui.cutline_table_html(cutline_rows), unsafe_allow_html=True)
            st.caption(
                "Based on current PROJECTED totals, not scores-so-far - this will keep moving as "
                "games finish. Make % and the 10th-90th range come from modeling each team's final "
                "score as a bell curve centered on its live projection, using that team's own real "
                "scoring volatility this season."
            )

for _, m in matchups.iterrows():
    with st.container(border=True):
        col_home, col_vs, col_away = st.columns([5, 1, 5])
        home_pk, away_pk = m["home_team_pk"], m["away_team_pk"]
        # .strip() matters: a trailing space before a closing ** breaks
        # CommonMark bold parsing (some ESPN team names have one)
        home_name, away_name = m["home_team_name"].strip(), m["away_team_name"].strip()

        with col_vs:
            st.markdown(
                "<div style='text-align:center; padding-top:14px; color:var(--text-muted);'>VS</div>",
                unsafe_allow_html=True,
            )

        # Header (bold manager name, team name + record/rank as small
        # subtext) is the same in every branch below - built once per
        # side to keep the per-branch logic focused on the score itself.
        def _render_header(col, team_pk: int, team_name: str) -> None:
            col.markdown(
                ui.team_header_html(manager_name_for(team_pk), f"{team_name} · {team_context_line(team_pk)}"),
                unsafe_allow_html=True,
            )

        _render_header(col_home, home_pk, home_name)
        _render_header(col_away, away_pk, away_name)

        if is_final_week:
            home_score, away_score = m["home_score"], m["away_score"]
            home_tone = "win" if home_score > away_score else ("loss" if home_score < away_score else "neutral")
            away_tone = "loss" if home_tone == "win" else ("win" if home_tone == "loss" else "neutral")
            col_home.markdown(ui.score_row_html(f"{home_score:.1f}", None, home_tone), unsafe_allow_html=True)
            col_away.markdown(ui.score_row_html(f"{away_score:.1f}", None, away_tone), unsafe_allow_html=True)
            if home_tone != "neutral":
                col_home.markdown(ui.status_bubble_html("WON" if home_tone == "win" else "LOST", home_tone), unsafe_allow_html=True)
                col_away.markdown(ui.status_bubble_html("WON" if away_tone == "win" else "LOST", away_tone), unsafe_allow_html=True)
        elif is_future_week:
            col_home.markdown(ui.score_row_html("0.0", None, "neutral"), unsafe_allow_html=True)
            col_away.markdown(ui.score_row_html("0.0", None, "neutral"), unsafe_allow_html=True)
            st.caption("This week hasn't started yet.")
        elif live_error or home_pk not in live_by_team:
            st.caption("Live data not available right now - try refreshing in a moment.")
        else:
            home_live, away_live = live_by_team[home_pk], live_by_team[away_pk]
            home_proj, away_proj = home_live["projected"], away_live["projected"]
            prob = dd.live_win_probability(season, home_pk, away_pk, home_proj, away_proj)
            home_tone, away_tone = ui.tone_for_probability(prob), ui.tone_for_probability(1 - prob)
            home_p10, home_p90 = dd.live_score_percentile_range(season, home_pk, home_proj)
            away_p10, away_p90 = dd.live_score_percentile_range(season, away_pk, away_proj)

            col_home.markdown(
                ui.score_row_html(f"{home_live['score']:.1f}", f"{home_proj:.1f}", home_tone), unsafe_allow_html=True
            )
            col_home.markdown(
                ui.score_detail_html(f"{prob*100:.0f}%", f"{home_p10:.0f}-{home_p90:.0f}", home_tone),
                unsafe_allow_html=True,
            )
            col_away.markdown(
                ui.score_row_html(f"{away_live['score']:.1f}", f"{away_proj:.1f}", away_tone), unsafe_allow_html=True
            )
            col_away.markdown(
                ui.score_detail_html(f"{(1-prob)*100:.0f}%", f"{away_p10:.0f}-{away_p90:.0f}", away_tone),
                unsafe_allow_html=True,
            )

        render_last_meetings(home_pk, away_pk, home_name, away_name)

if is_future_week:
    st.caption("Future week - no live data available yet (ESPN doesn't publish box scores this far ahead).")
elif not is_final_week:
    st.caption(
        "Score/projection update live from ESPN (refreshes about once a minute). The right-hand "
        "number is each team's CURRENT projected total - points scored so far plus the rest of the "
        "lineup's projections - not just the raw score, so a big early lead from one team's players "
        "simply having played first doesn't look like a bigger edge than it is. Green/amber/red "
        "color the projection by that team's live win probability (ESPN publishes no win "
        "probability of its own)."
    )
