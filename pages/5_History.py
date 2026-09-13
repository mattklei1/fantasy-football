"""HISTORY page: Hall of Fame, league records, and a head-to-head
rivalry explorer. Spans ALL seasons - manager identity persists across
team name changes and seasons (joins via team_owners/managers, not
team_pk, which resets every season)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from fantasy_football import history_data as hd
from fantasy_football import ui_common as ui

st.set_page_config(page_title="History", page_icon="🏆", layout="wide")
ui.inject_css()

# History spans all seasons, so it doesn't need the season/week selector -
# just render the league info sidebar without capturing its return values.
ui.render_sidebar()

st.title("History")

tab_hof, tab_records, tab_h2h = st.tabs(["Hall of Fame", "League Records", "Head-to-Head"])

with tab_hof:
    st.caption(
        "Championships/playoff appearances/career record are exact facts. Best/Worst Season "
        "use season-relative PPG percentile, NOT raw points - this league's scoring rules have "
        "changed over time (PPR value, roster/flex slots), so raw point totals from different "
        "eras aren't a fair comparison. Career Points is shown for reference only, not for "
        "ranking - same reason."
    )
    hof = hd.get_hall_of_fame()
    if hof.empty:
        st.info("No history data available yet.")
    else:
        display = hof.copy()
        display["Career Record"] = display.apply(
            lambda r: ui.format_record(r["career_wins"], r["career_losses"], r["career_ties"]), axis=1
        )
        display["Career Win%"] = (
            (display["career_wins"] + 0.5 * display["career_ties"])
            / (display["career_wins"] + display["career_losses"] + display["career_ties"]).replace(0, 1)
            * 100
        ).round(1).astype(str) + "%"
        display["Career Points (raw)"] = display["career_points"].round(0).astype(int)
        display["Best Season"] = display.apply(
            lambda r: (
                f"{int(r['best_season'])} ({r['best_season_team']}, "
                f"{r['best_season_percentile']*100:.0f}th pct)"
            ) if pd.notna(r.get("best_season")) else "—",
            axis=1,
        ) if "best_season" in display.columns else "—"
        display["Worst Season"] = display.apply(
            lambda r: (
                f"{int(r['worst_season'])} ({r['worst_season_team']}, "
                f"{r['worst_season_percentile']*100:.0f}th pct)"
            ) if pd.notna(r.get("worst_season")) else "—",
            axis=1,
        ) if "worst_season" in display.columns else "—"

        table = display[
            ["manager_name", "championships", "finals_appearances", "playoff_appearances",
             "seasons_played", "Career Record", "Career Win%", "Career Points (raw)",
             "Best Season", "Worst Season"]
        ].rename(columns={
            "manager_name": "Manager", "championships": "🏆", "finals_appearances": "Finals",
            "playoff_appearances": "Playoffs", "seasons_played": "Seasons",
        })
        st.markdown(table.to_html(escape=False, index=False, classes="ff-table"), unsafe_allow_html=True)

with tab_records:
    st.caption(
        "Raw records spanning every season, including different scoring eras - a record book "
        "entry is a fact about what happened, not a cross-era ranking claim."
    )
    records = hd.get_league_records()
    if not records:
        st.info("No record data available yet.")
    else:
        c1, c2, c3 = st.columns(3)
        hs, ls = records.get("highest_score"), records.get("lowest_score")
        bb, cg = records.get("biggest_blowout"), records.get("closest_game")
        mpl, lsw = records.get("most_points_in_loss"), records.get("lowest_score_in_win")
        with c1:
            if hs:
                ui.stat_card("Highest Score Ever", f"{hs['score']:.1f}", f"{hs['team_name']} · {hs['season_id']} Wk{hs['week']}")
            if bb:
                ui.stat_card("Biggest Blowout", f"{bb['margin']:.1f} pt margin", f"{bb['team_name']} · {bb['season_id']} Wk{bb['week']}")
        with c2:
            if ls:
                ui.stat_card("Lowest Score Ever", f"{ls['score']:.1f}", f"{ls['team_name']} · {ls['season_id']} Wk{ls['week']}")
            if cg:
                ui.stat_card("Closest Game", f"{cg['margin']:.1f} pt margin", f"{cg['team_name']} · {cg['season_id']} Wk{cg['week']}")
        with c3:
            if mpl:
                ui.stat_card("Most Points in a Loss", f"{mpl['score']:.1f}", f"{mpl['team_name']} · {mpl['season_id']} Wk{mpl['week']}")
            if lsw:
                ui.stat_card("Lowest Score in a Win", f"{lsw['score']:.1f}", f"{lsw['team_name']} · {lsw['season_id']} Wk{lsw['week']}")

with tab_h2h:
    managers = hd.get_managers()
    if managers.empty or len(managers) < 2:
        st.info("Not enough manager data yet.")
    else:
        names = managers["display_name"].tolist()
        ids_by_name = dict(zip(managers["display_name"], managers["manager_id"]))
        name_by_id = dict(zip(managers["manager_id"], managers["display_name"]))
        col_a, col_b = st.columns(2)
        with col_a:
            name_a = st.selectbox("Manager A", names, index=0)
        with col_b:
            default_b = 1 if len(names) > 1 else 0
            name_b = st.selectbox("Manager B", names, index=default_b)

        if name_a == name_b:
            st.warning("Pick two different managers.")
        else:
            result = hd.get_head_to_head(ids_by_name[name_a], ids_by_name[name_b])
            if result.get("games_played", 0) == 0:
                st.info(f"{name_a} and {name_b} have never faced each other.")
            else:
                st.markdown(f"### {name_a} vs {name_b}")
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    ui.stat_card(
                        "All-Time Series",
                        f"{result['a_wins']}-{result['b_wins']}"
                        + (f"-{result['ties']}" if result['ties'] else ""),
                        f"{name_a} leads" if result['a_wins'] > result['b_wins']
                        else f"{name_b} leads" if result['b_wins'] > result['a_wins'] else "Tied series",
                    )
                with c2:
                    ui.stat_card(
                        "Avg Points",
                        f"{result['a_avg_points']:.1f} - {result['b_avg_points']:.1f}",
                        f"{name_a} vs {name_b}",
                    )
                with c3:
                    ui.stat_card(
                        "Playoff Record",
                        f"{result['a_playoff_wins']}-{result['b_playoff_wins']}"
                        if result["playoff_games"] else "No playoff meetings",
                    )
                with c4:
                    streak_mgr = name_by_id.get(result.get("current_streak_manager"))
                    ui.stat_card(
                        "Current Streak",
                        f"{streak_mgr} x{result['current_streak_length']}" if streak_mgr else "—",
                    )

                largest_margin_winner_name = name_by_id.get(result["largest_margin_winner"], result["largest_margin_winner"])
                st.caption(
                    f"Largest victory: {largest_margin_winner_name} by "
                    f"{result['largest_margin']:.1f} ({result['largest_margin_season']} "
                    f"Wk{result['largest_margin_week']}) · Closest game: "
                    f"{result['closest_margin']:.1f} pt margin ({result['closest_season']} "
                    f"Wk{result['closest_week']})"
                )

                history = result["history"].copy()
                history["Result"] = history["a_result"].map({"W": f"{name_a} won", "L": f"{name_b} won", "T": "Tie"})
                history["Score"] = history.apply(lambda r: f"{r['a_score']:.1f} - {r['b_score']:.1f}", axis=1)
                history["Playoff"] = history["is_playoff"].map({1: "Yes", 0: ""})
                display_hist = history[["season_id", "week", "Score", "Result", "Playoff"]].rename(
                    columns={"season_id": "Season", "week": "Week"}
                ).sort_values(["Season", "Week"], ascending=False)
                st.markdown(
                    display_hist.to_html(escape=False, index=False, classes="ff-table"),
                    unsafe_allow_html=True,
                )
