"""WAR ROOM page: commissioner-only tools built on data this app already
has licensed/ingested access to - a live full FantasyPros rankings
browser, a live waiver board (the same suggested-FAAB heuristic behind
the public GroupMe waiver report, applied to the whole free-agent pool
instead of only after the fact to claims that already happened), and a
superflex-aware trade calculator. Visible in navigation to everyone
(deliberately, per the commissioner's request - the locked teaser is half
the fun), but only fantasy_football.ui_common.is_admin() renders real
content; everyone else sees a locked screen."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from fantasy_football import config
from fantasy_football import ui_common as ui
from fantasy_football import war_room_data as wr

st.set_page_config(page_title="War Room", page_icon="🔒", layout="wide")
ui.inject_css()

season, week = ui.render_sidebar()

if not ui.is_admin():
    st.title("🔒 War Room")
    st.markdown(
        "### Commissioner-only\n"
        "This page is locked. Ask the commissioner nicely."
    )
    st.stop()

st.title("🔒 War Room")
st.caption("Commissioner-only - not visible to anyone else on this deployment.")

if not config.fantasypros_api_key():
    st.warning(
        "No FANTASYPROS_API_KEY configured - Rankings Browser and Waiver Board suggested values "
        "will be empty. Trade Calculator still works off ESPN's own positional rank alone."
    )

tab_rankings, tab_waiver, tab_trade = st.tabs(["Rankings Browser", "Waiver Board", "Trade Calculator"])

with tab_rankings:
    st.caption(
        "Live FantasyPros rest-of-season consensus rankings - every ranked player at every "
        "position, not just this league's rostered ones (unlike the public Roster Strength "
        "page, which only tracks players someone here has actually rostered). \"Rostered By\" "
        "cross-references this league's current rosters."
    )
    with st.spinner("Pulling live FantasyPros rankings..."):
        rankings_df = wr.get_rankings_browser(season)
    if rankings_df.empty:
        st.info("No rankings data available right now - check FANTASYPROS_API_KEY, or try again later.")
    else:
        col1, col2 = st.columns([1, 2])
        with col1:
            positions = ["All"] + sorted(rankings_df["position"].dropna().unique().tolist())
            position_filter = st.selectbox("Position", positions, key="wr_rank_pos")
        with col2:
            search = st.text_input("Search player", key="wr_rank_search")

        filtered = rankings_df
        if position_filter != "All":
            filtered = filtered[filtered["position"] == position_filter]
        if search:
            filtered = filtered[filtered["player_name"].str.contains(search, case=False, na=False)]

        display = filtered.rename(
            columns={
                "player_name": "Player", "position": "Pos", "pro_team": "Team",
                "rank_ecr": "Overall Rank", "pos_rank": "Pos Rank", "ros_points": "ROS Pts",
                "rostered_by": "Rostered By",
            }
        )
        st.dataframe(
            display[["Player", "Pos", "Team", "Overall Rank", "Pos Rank", "ROS Pts", "Rostered By"]],
            hide_index=True,
            use_container_width=True,
            column_config={"ROS Pts": st.column_config.NumberColumn(format="%.1f")},
        )
        st.caption(f"{len(filtered)} of {len(rankings_df)} ranked players shown.")

with tab_waiver:
    st.caption(
        "Every currently available free agent, ranked by our own suggested-FAAB heuristic "
        "(the same model behind the weekly GroupMe waiver report - see Methodology below), "
        "applied here to the live pool instead of only after the fact to claims that already "
        "happened."
    )
    with st.spinner("Pulling live free agents..."):
        waiver_df = wr.get_waiver_board(season)
    if waiver_df.empty:
        st.info("No free agent data available right now.")
    else:
        positions = ["All"] + sorted(waiver_df["position"].dropna().unique().tolist())
        position_filter = st.selectbox("Position", positions, key="wr_waiver_pos")
        filtered = waiver_df if position_filter == "All" else waiver_df[waiver_df["position"] == position_filter]

        display = filtered.rename(
            columns={
                "player_name": "Player", "position": "Pos", "pro_team": "Team",
                "injury_status": "Status", "percent_owned": "Owned %", "percent_started": "Started %",
                "fp_pos_rank": "FP Pos Rank", "suggested_bid": "Suggested Bid",
            }
        )
        st.dataframe(
            display[["Player", "Pos", "Team", "Status", "Owned %", "Started %", "FP Pos Rank", "Suggested Bid"]],
            hide_index=True,
            use_container_width=True,
            column_config={
                "Owned %": st.column_config.NumberColumn(format="%.1f%%"),
                "Started %": st.column_config.NumberColumn(format="%.1f%%"),
                "Suggested Bid": st.column_config.NumberColumn(format="$%.0f"),
            },
        )
        st.caption(f"{len(filtered)} of {len(waiver_df)} free agents shown.")

        with st.expander("Methodology"):
            st.markdown(
                """
**Suggested Bid** is our own heuristic - nobody publishes a real FAAB price (checked FantasyPros'
full API, ESPN's Player object, and Yahoo - none expose one). It blends FantasyPros' rest-of-season
positional rank (70%) with ESPN's league-wide `percent_owned` as a demand signal (30%), scaled to
this league's real $200 budget and real per-position ceilings calibrated against this league's own
2025 waiver history (see `metrics/waiver_value.py`), with a superflex-aware QB premium (this
league's `OP` slot count inflates real QB demand). Explicitly a rough estimate, not a market price -
useful for sanity-checking a bid, not gospel.

Free agents with no FantasyPros match (deep bench players, or anyone FantasyPros doesn't rank at
that position) show no suggested bid rather than a fabricated one.
                """
            )

with tab_trade:
    st.caption(
        "Value-based, superflex-aware trade evaluator. Value blends FantasyPros' rest-of-season "
        "rank and ESPN's season-long positional rank (same decay curve as Roster Strength), with "
        "this league's real OP-slot QB premium applied - a relative comparison to sanity-check a "
        "proposed deal, not a market price."
    )
    rosters_df = wr.get_trade_rosters(season)
    if rosters_df.empty:
        st.info("No roster data available yet this season.")
    else:
        teams = sorted(rosters_df["team_name"].dropna().unique().tolist())
        col_a, col_b = st.columns(2)
        with col_a:
            team_a = st.selectbox("Team A", teams, index=0, key="wr_trade_team_a")
        with col_b:
            default_b_index = 1 if len(teams) > 1 else 0
            team_b = st.selectbox("Team B", teams, index=default_b_index, key="wr_trade_team_b")

        if team_a == team_b:
            st.warning("Pick two different teams.")
        else:
            roster_a = rosters_df[rosters_df["team_name"] == team_a]
            roster_b = rosters_df[rosters_df["team_name"] == team_b]

            def _label(row: pd.Series) -> str:
                return f"{row['player_name']} ({row['position']}, {row['value_score']:.0f} val)"

            col_a2, col_b2 = st.columns(2)
            with col_a2:
                st.markdown(f"**{team_a} sends:**")
                value_by_label_a = {_label(r): r["value_score"] for _, r in roster_a.iterrows()}
                picks_a = st.multiselect(
                    f"{team_a} players", list(value_by_label_a.keys()), key="wr_trade_picks_a"
                )
            with col_b2:
                st.markdown(f"**{team_b} sends:**")
                value_by_label_b = {_label(r): r["value_score"] for _, r in roster_b.iterrows()}
                picks_b = st.multiselect(
                    f"{team_b} players", list(value_by_label_b.keys()), key="wr_trade_picks_b"
                )

            value_a = sum(value_by_label_a[p] for p in picks_a)
            value_b = sum(value_by_label_b[p] for p in picks_b)
            total_value = value_a + value_b

            if not picks_a and not picks_b:
                st.caption("Select players from each side to evaluate the trade.")
            else:
                c1, c2, c3 = st.columns(3)
                with c1:
                    ui.stat_card(f"{team_a} sends", f"{value_a:.0f} val", f"{len(picks_a)} player(s)")
                with c2:
                    ui.stat_card(f"{team_b} sends", f"{value_b:.0f} val", f"{len(picks_b)} player(s)")
                with c3:
                    if total_value == 0:
                        verdict, sub = "No value on either side", "check the picks above"
                    else:
                        gap_pct = abs(value_a - value_b) / total_value * 100
                        if gap_pct <= 10:
                            verdict = "Fair trade"
                        else:
                            # A team WINS by receiving more than it sends -
                            # team_a receives value_b (what team_b sends),
                            # so team_a comes out ahead when value_b > value_a.
                            winner = team_a if value_b > value_a else team_b
                            verdict = f"{winner} wins the trade"
                        sub = f"{gap_pct:.0f}% value gap"
                    ui.stat_card("Verdict", verdict, sub)

            with st.expander(f"Full rosters ({team_a} vs {team_b}, by value)"):
                roster_cols = st.columns(2)
                with roster_cols[0]:
                    st.markdown(f"**{team_a}**")
                    st.dataframe(
                        roster_a[["player_name", "position", "slot_position", "value_score"]].rename(
                            columns={
                                "player_name": "Player", "position": "Pos",
                                "slot_position": "Slot", "value_score": "Value",
                            }
                        ),
                        hide_index=True, use_container_width=True,
                    )
                with roster_cols[1]:
                    st.markdown(f"**{team_b}**")
                    st.dataframe(
                        roster_b[["player_name", "position", "slot_position", "value_score"]].rename(
                            columns={
                                "player_name": "Player", "position": "Pos",
                                "slot_position": "Slot", "value_score": "Value",
                            }
                        ),
                        hide_index=True, use_container_width=True,
                    )
