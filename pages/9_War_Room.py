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

GAIN_NEUTRAL_THRESHOLD = 5.0  # value-score points; a "gain" smaller than this counts as a wash for that side

with tab_trade:
    st.caption(
        "Marginal, starter-slot-aware trade evaluator - NOT a sum-the-players calculator. Value "
        "blends FantasyPros' rest-of-season rank and ESPN's season-long positional rank with this "
        "league's real OP-slot QB premium, but the verdict is each side's change in its own "
        "BEST-POSSIBLE STARTING LINEUP value (same optimal-lineup solver as Lineup Efficiency), "
        "not raw value added up. That's what makes a 2-for-1 price correctly: two bench players "
        "you were never starting anyway cost you almost nothing to give up, and a single "
        "difference-maker is only worth the upgrade over whoever he actually replaces in your "
        "lineup - not his full standalone value. See Methodology below for the research this is "
        "based on."
    )
    rosters_df = wr.get_trade_rosters(season)
    if rosters_df.empty:
        st.info("No roster data available yet this season.")
    else:
        slot_counts = wr.get_position_slot_counts(season)
        fa_ceiling = wr.get_free_agent_value_ceiling(season)
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
            _, starters_a_now = wr.optimal_roster_value(roster_a, slot_counts)
            _, starters_b_now = wr.optimal_roster_value(roster_b, slot_counts)

            def _label(row: pd.Series, starters_now: set) -> str:
                tag = ""
                if row["player_id"] not in starters_now:
                    cap = fa_ceiling.get(row["position"])
                    if cap is not None and row["value_score"] <= cap * 1.1:
                        tag = " \U0001f504 replaceable via waivers"
                return f"{row['player_name']} ({row['position']}, {row['value_score']:.0f} val){tag}"

            col_a2, col_b2 = st.columns(2)
            with col_a2:
                st.markdown(f"**{team_a} sends:**")
                id_by_label_a = {_label(r, starters_a_now): r["player_id"] for _, r in roster_a.iterrows()}
                picks_a = st.multiselect(
                    f"{team_a} players", list(id_by_label_a.keys()), key="wr_trade_picks_a"
                )
            with col_b2:
                st.markdown(f"**{team_b} sends:**")
                id_by_label_b = {_label(r, starters_b_now): r["player_id"] for _, r in roster_b.iterrows()}
                picks_b = st.multiselect(
                    f"{team_b} players", list(id_by_label_b.keys()), key="wr_trade_picks_b"
                )

            st.caption("\U0001f504 = not currently a starter for that team, and a comparable or better free agent is available right now.")

            player_ids_out_a = [id_by_label_a[p] for p in picks_a]
            player_ids_out_b = [id_by_label_b[p] for p in picks_b]

            if not picks_a and not picks_b:
                st.caption("Select players from each side to evaluate the trade.")
            else:
                result = wr.evaluate_trade(rosters_df, team_a, team_b, player_ids_out_a, player_ids_out_b, slot_counts)
                gain_a, gain_b = result[team_a]["gain"], result[team_b]["gain"]

                c1, c2, c3 = st.columns(3)
                with c1:
                    ui.stat_card(
                        f"{team_a} lineup impact", f"{gain_a:+.0f} val",
                        f"{result[team_a]['before']:.0f} → {result[team_a]['after']:.0f}",
                    )
                with c2:
                    ui.stat_card(
                        f"{team_b} lineup impact", f"{gain_b:+.0f} val",
                        f"{result[team_b]['before']:.0f} → {result[team_b]['after']:.0f}",
                    )
                with c3:
                    a_up, b_up = gain_a > GAIN_NEUTRAL_THRESHOLD, gain_b > GAIN_NEUTRAL_THRESHOLD
                    if a_up and b_up:
                        verdict, sub = "Win-win trade", "both sides upgrade their starting lineup"
                    elif a_up:
                        verdict, sub = f"{team_a} wins the trade", f"+{gain_a:.0f} val vs {gain_b:+.0f} for {team_b}"
                    elif b_up:
                        verdict, sub = f"{team_b} wins the trade", f"+{gain_b:.0f} val vs {gain_a:+.0f} for {team_a}"
                    else:
                        verdict, sub = "Lopsided for both sides", "neither lineup actually improves - re-check this deal"
                    ui.stat_card("Verdict", verdict, sub)

                move_cols = st.columns(2)
                with move_cols[0]:
                    if result[team_a]["newly_starting"]:
                        st.caption(f"**{team_a}** now starting: {', '.join(result[team_a]['newly_starting'])}")
                    if result[team_a]["newly_benched"]:
                        st.caption(f"**{team_a}** now benched: {', '.join(result[team_a]['newly_benched'])}")
                with move_cols[1]:
                    if result[team_b]["newly_starting"]:
                        st.caption(f"**{team_b}** now starting: {', '.join(result[team_b]['newly_starting'])}")
                    if result[team_b]["newly_benched"]:
                        st.caption(f"**{team_b}** now benched: {', '.join(result[team_b]['newly_benched'])}")

                incoming_a = roster_b[roster_b["player_id"].isin(player_ids_out_b)]
                incoming_b = roster_a[roster_a["player_id"].isin(player_ids_out_a)]
                best_cols = st.columns(2)
                with best_cols[0]:
                    if not incoming_a.empty:
                        best = incoming_a.loc[incoming_a["value_score"].idxmax()]
                        st.caption(f"Best player {team_a} gets: **{best['player_name']}** ({best['value_score']:.0f} val)")
                with best_cols[1]:
                    if not incoming_b.empty:
                        best = incoming_b.loc[incoming_b["value_score"].idxmax()]
                        st.caption(f"Best player {team_b} gets: **{best['player_name']}** ({best['value_score']:.0f} val)")

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

            with st.expander("Methodology"):
                st.markdown(
                    """
**Why not just add up the players' values?** Researched before building this: real trade
calculators and VORP/stars-and-scrubs strategy writeups agree a flat sum breaks down the moment
the two sides trade different PLAYER COUNTS - a roster can only start a fixed number of players
per position, so a 2-for-1 where the two outgoing players were bench depth barely costs you
anything (you were never starting them), while the incoming star's full value only matters up to
the upgrade he provides over whoever he replaces in your actual lineup.

**The fix**: each side's verdict is the MARGINAL change in its own best-possible starting lineup
value (before vs. after the trade) - the exact same optimal-lineup solver Lineup Efficiency uses
to find the best legal lineup from real weekly points, just fed each player's rest-of-season value
instead. This automatically prices in:
- **Team need** - an add at a position you're already deep at barely moves your total; the same
  player at a position where you're starting a replacement-level guy moves it a lot.
- **"You can only start one of them"** - getting a 2nd great player at a position you're already
  set at only helps if he's better than your current starter there, never both at once.
- **Waiver-replaceable depth** (\U0001f504 tag above) - an outgoing bench player with a comparable
  or better free agent currently available isn't a real loss, since you can refill that spot
  yourself rather than treating it as value walking out the door.

**Not modeled**: draft pick value, injury risk, and strength-of-schedule/bye-week timing - this is
a snapshot of current rest-of-season value, not a full trade-deadline calculus.
                    """
                )
