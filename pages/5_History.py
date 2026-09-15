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
ui.render_sidebar(support_all_time=True)

st.title("History")

tab_hof, tab_records, tab_h2h = st.tabs(["Hall of Fame", "League Records", "Head-to-Head"])

with tab_hof:
    st.caption(
        "Championships/finals/playoff appearances and both records are exact facts. Playoff "
        "appearances/record count only the real playoff bracket (including placement games "
        "among teams that qualified) - NOT the separate consolation bracket ESPN runs for "
        "teams that missed the playoffs, even though ESPN tags both as \"playoff\" weeks. "
        "Best/Worst Season and Normalized Points use season-relative percentile, NOT raw "
        "points - this league's scoring rules have changed over time (PPR value, roster/flex "
        "slots), so raw point totals from different eras aren't a fair comparison; percentile "
        "compares each season only to its own field. Raw Career Points is shown for reference "
        "only, not for ranking - same reason."
    )
    hof = hd.get_hall_of_fame()
    if hof.empty:
        st.info("No history data available yet.")
    else:
        display = hof.copy()
        display["Regular Season Record"] = display.apply(
            lambda r: ui.format_record(r["reg_wins"], r["reg_losses"], r["reg_ties"]), axis=1
        )

        def _playoff_record(r):
            record = ui.format_record(r["playoff_wins"], r["playoff_losses"], r["playoff_ties"])
            byes = int(r.get("playoff_byes", 0) or 0)
            if byes:
                record += f" ({byes} bye{'s' if byes != 1 else ''})"
            return record

        display["Playoff Record"] = display.apply(_playoff_record, axis=1)

        def _championships(r):
            n = int(r["championships"])
            years = r.get("championship_years") or []
            if n and years:
                return f"{n} ({', '.join(str(y) for y in years)})"
            return str(n)

        display["🏆"] = display.apply(_championships, axis=1)
        display["Career Points For"] = display["career_points_for"].round(0).astype(int)
        display["Career Points Against"] = display["career_points_against"].round(0).astype(int)
        display["Norm. Points For"] = (display["career_points_for_pct"] * 100).round(0).astype(int)
        display["Norm. Points Against"] = (display["career_points_against_pct"] * 100).round(0).astype(int)
        display["Best Season"] = display["best_season"] if "best_season" in display.columns else None
        display["Worst Season"] = display["worst_season"] if "worst_season" in display.columns else None
        for col in ("Best Season", "Worst Season"):
            display[col] = display[col].apply(lambda v: int(v) if pd.notna(v) else None)

        # Sort by the real numeric championship count (not the formatted
        # "N (years)" string) BEFORE selecting/renaming down to it.
        display = display.sort_values("championships", ascending=False)

        table = display[
            ["manager_name", "🏆", "finals_appearances", "playoff_appearances",
             "Regular Season Record", "Playoff Record",
             "Career Points For", "Career Points Against",
             "Norm. Points For", "Norm. Points Against",
             "Best Season", "Worst Season"]
        ].rename(columns={
            "manager_name": "Manager", "finals_appearances": "Finals", "playoff_appearances": "Playoffs",
        })

        st.dataframe(
            table,
            hide_index=True,
            use_container_width=True,
            column_config={
                "🏆": st.column_config.TextColumn(help="Championship count, with the winning year(s) in parentheses."),
                "Playoff Record": st.column_config.TextColumn(
                    help="Real playoff bracket games only (including placement games among "
                    "teams that qualified) - excludes the consolation bracket. A bye (top seed "
                    "advancing without playing) is noted in parentheses but NOT counted as a win.",
                ),
                "Career Points For": st.column_config.NumberColumn(format="localized"),
                "Career Points Against": st.column_config.NumberColumn(format="localized"),
                "Norm. Points For": st.column_config.NumberColumn(
                    format="%dth pct avg",
                    help="Average, across every season played, of that season's points-for "
                    "percentile within its own field - era-normalized, so a fair cross-season "
                    "comparison unlike raw points.",
                ),
                "Norm. Points Against": st.column_config.NumberColumn(
                    format="%dth pct avg",
                    help="Same as Norm. Points For, but for points allowed to opponents.",
                ),
                "Playoffs": st.column_config.NumberColumn(
                    help="Real playoff bracket appearances only - excludes ESPN's separate "
                    "consolation ladder for teams that missed the playoffs.",
                ),
                "Best Season": st.column_config.NumberColumn(format="%d", help="Year of the season with the highest season-relative PPG percentile."),
                "Worst Season": st.column_config.NumberColumn(format="%d", help="Year of the season with the lowest season-relative PPG percentile."),
            },
        )

with tab_records:
    st.caption(
        "Records span every season, including different scoring eras. Highest/Lowest Score Ever "
        "are picked by season-relative percentile (fair across eras with different scoring "
        "settings), not raw points - the headline number is still the real raw score, with the "
        "percentile as context. Everything else here (blowouts, closest games) is a raw fact "
        "about what happened, not a cross-era ranking claim."
    )
    records = hd.get_league_records()
    if not records:
        st.info("No record data available yet.")
    else:
        def who(d, prefix=""):
            return d.get(f"{prefix}manager_name") or d.get(f"{prefix}team_name")

        c1, c2, c3 = st.columns(3)
        hs, ls = records.get("highest_score"), records.get("lowest_score")
        hs3 = records.get("highest_scores") or ([hs] if hs else [])
        bb, cg = records.get("biggest_blowout"), records.get("closest_game")
        mpl, lsw = records.get("most_points_in_loss"), records.get("lowest_score_in_win")
        with c1:
            if hs3:
                # Top 3, raw + normalized points both shown to 2 decimals -
                # enough to actually tell two close scores apart (user,
                # 2026-09-16: "show me the top 3... raw points and
                # normalized points (with decimals, enough to show
                # difference between scores)").
                rank_lines = []
                for rank, row in enumerate(hs3, start=1):
                    pct = f" · {row['score_percentile']*100:.2f}th pctile" if "score_percentile" in row else ""
                    rank_lines.append(
                        f"#{rank} {row['score']:.2f} - {who(row)} · {row['season_id']} Wk{row['week']}{pct}"
                    )
                ui.stat_card("Highest Score Ever", f"{hs3[0]['score']:.2f}", "<br>".join(rank_lines))
            if bb:
                ui.stat_card(
                    "Biggest Blowout", f"{bb['margin']:.1f} pt margin",
                    f"{who(bb)} over {who(bb, 'opp_')} · {bb['season_id']} Wk{bb['week']}",
                )
        with c2:
            if ls:
                pct = f" · {ls['score_percentile']*100:.0f}th pctile that season" if "score_percentile" in ls else ""
                ui.stat_card("Lowest Score Ever", f"{ls['score']:.1f}", f"{who(ls)} · {ls['season_id']} Wk{ls['week']}{pct}")
            if cg:
                ui.stat_card(
                    "Closest Game", f"{cg['margin']:.1f} pt margin",
                    f"{who(cg)} vs {who(cg, 'opp_')} · {cg['season_id']} Wk{cg['week']}",
                )
        with c3:
            if mpl:
                ui.stat_card("Most Points in a Loss", f"{mpl['score']:.1f}", f"{who(mpl)} · {mpl['season_id']} Wk{mpl['week']}")
            if lsw:
                ui.stat_card("Lowest Score in a Win", f"{lsw['score']:.1f}", f"{who(lsw)} · {lsw['season_id']} Wk{lsw['week']}")

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
