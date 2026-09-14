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

tab_lineup, tab_rankings, tab_waiver, tab_mine, tab_trade = st.tabs(
    ["Lineup Optimizer", "Rankings Browser", "Waiver Board", "My Waiver Bids", "Trade Calculator"]
)

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
                "overall_rank": "Overall Rank", "pos_rank": "Pos Rank", "ros_points": "ROS Pts",
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
                "overall_rank": "Overall Rank", "fp_pos_rank": "FP Pos Rank", "suggested_bid": "Suggested Bid",
            }
        )
        st.dataframe(
            display[["Player", "Pos", "Team", "Status", "Owned %", "Started %", "Overall Rank", "FP Pos Rank", "Suggested Bid"]],
            hide_index=True,
            use_container_width=True,
            column_config={
                "Owned %": st.column_config.NumberColumn(format="%.1f%%"),
                "Started %": st.column_config.NumberColumn(format="%.1f%%"),
                "Overall Rank": st.column_config.NumberColumn(format="%d"),
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

with tab_mine:
    st.caption(
        "Your top waiver targets, ranked and diversified across positions (max 3 per position so a "
        "superflex QB run doesn't crowd out everything else), each with a specific suggested drop "
        "from your own roster and plain-language reasoning - position need, whether it's a real "
        "starter upgrade or just bench insurance, and your real remaining FAAB budget (pulled "
        "straight from ESPN's own per-team spend tracker). Every number is a real computed fact - "
        "nothing here is LLM-generated."
    )
    trade_rosters_for_teams = wr.get_trade_rosters(season)
    if trade_rosters_for_teams.empty:
        st.info("No roster data available yet this season.")
    else:
        my_teams = sorted(trade_rosters_for_teams["team_name"].dropna().unique().tolist())
        my_team_name = st.selectbox("Your team", my_teams, key="wr_my_team")
        my_team_pk = int(
            trade_rosters_for_teams.loc[trade_rosters_for_teams["team_name"] == my_team_name, "team_pk"].iloc[0]
        )

        with st.spinner("Building suggestions..."):
            suggestions = wr.get_my_waiver_suggestions(season, my_team_pk, top_n=10)

        real_submit = st.checkbox(
            "⚠️ Actually submit approved claims to ESPN (real transactions, real FAAB budget)",
            value=False, key="wr_real_submit",
            help="Unchecked = dry run only (shows exactly what would be sent, sends nothing). This "
            "resets to unchecked every time you load this page - it's never left on by accident.",
        )
        if not real_submit:
            st.caption("Dry run mode - approving and submitting below will show you the exact payload, not send it.")

        if not suggestions:
            st.info("No suggestions available right now - check FANTASYPROS_API_KEY, or try again later.")
        else:
            approved_indices = []
            for i, s in enumerate(suggestions, start=1):
                with st.container(border=True):
                    cols = st.columns([1, 3, 2, 2])
                    with cols[0]:
                        approve = st.checkbox(
                            "Approve", key=f"wr_approve_{my_team_name}_{s['player_id']}", label_visibility="visible"
                        )
                        if approve:
                            approved_indices.append(i - 1)
                    with cols[1]:
                        st.markdown(f"**#{i}. {s['player_name']}** ({s['position']}, {s['pro_team']})")
                        st.caption(s["reasoning"])
                    with cols[2]:
                        bid_label = f"${s['suggested_bid']:.0f}"
                        if not s["affordable"]:
                            bid_label += " ⚠️"
                        st.metric("Suggested bid", bid_label)
                    with cols[3]:
                        if s["suggested_drop"]:
                            st.metric("Suggested drop", s["suggested_drop"], f"{s['suggested_drop_value']:.0f} val")
                        else:
                            st.caption("No clear drop candidate (empty bench)")

            submit_label = (
                "Submit approved claims to ESPN" if real_submit else "Preview approved claims (dry run)"
            )
            if st.button(submit_label, disabled=not approved_indices, key="wr_submit_claims"):
                results = []
                for idx in approved_indices:
                    s = suggestions[idx]
                    result = wr.submit_waiver_claim(
                        season, my_team_pk, s["player_id"], s["suggested_drop_id"], s["suggested_bid"],
                        dry_run=not real_submit,
                    )
                    results.append((s, result))
                st.session_state["wr_claim_results"] = results

            if st.session_state.get("wr_claim_results"):
                st.markdown("#### Results")
                for s, result in st.session_state["wr_claim_results"]:
                    label = f"{s['player_name']} (add) / {s['suggested_drop'] or 'no drop'}"
                    if result["dry_run"]:
                        st.info(f"DRY RUN - {label}: would send this payload ↓")
                        st.json(result["payload"])
                    elif result["success"]:
                        st.success(f"{label}: {result['message']}")
                    else:
                        st.error(f"{label}: {result['message']}")

    st.divider()
    st.markdown("#### All available players (by overall rest-of-season rank)")
    st.caption(
        "Every free agent on the board, ranked by FantasyPros' TRUE cross-position rest-of-season "
        "rank (not positional rank, and not suggested bid) - so a top kicker or defense doesn't "
        "appear to rank ahead of a real skill-position player just for being #1 at a shallow "
        "position. Browse the full pool yourself and tell me if you want a claim outside the "
        "suggestions above."
    )
    board_for_browsing = wr.get_waiver_board(season)
    if board_for_browsing.empty:
        st.info("No free agent data available right now.")
    else:
        position_options = ["All"] + sorted(board_for_browsing["position"].dropna().unique().tolist())
        position_filter = st.selectbox("Position", position_options, key="wr_mine_position_filter")
        filtered_board = (
            board_for_browsing if position_filter == "All"
            else board_for_browsing[board_for_browsing["position"] == position_filter]
        )
        by_rank = filtered_board[filtered_board["overall_rank"].notna()].sort_values("overall_rank")
        display_board = by_rank.rename(
            columns={
                "player_name": "Player", "position": "Pos", "pro_team": "Team",
                "overall_rank": "Overall Rank", "suggested_bid": "Suggested Bid",
            }
        )
        st.dataframe(
            display_board[["Player", "Pos", "Team", "Overall Rank", "Suggested Bid"]],
            hide_index=True, use_container_width=True,
            column_config={
                "Overall Rank": st.column_config.NumberColumn(format="%d"),
                "Suggested Bid": st.column_config.NumberColumn(format="$%.0f"),
            },
        )

    if not trade_rosters_for_teams.empty:
        st.markdown("#### Your droppable roster")
        st.caption("Your bench only (starters aren't shown here) - worst value first.")
        droppable = wr.get_droppable_roster(season, my_team_pk)
        if droppable.empty:
            st.info("No bench players available to drop.")
        else:
            display_droppable = droppable.rename(
                columns={"player_name": "Player", "position": "Pos", "value_score": "Value"}
            )
            st.dataframe(
                display_droppable[["Player", "Pos", "Value"]],
                hide_index=True, use_container_width=True,
            )
        st.caption(
            "Tell me the exact add/drop/bid you want from these two tables and I'll submit it for you - "
            "not limited to the 10 suggestions above."
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

        my_team_pk = wr.get_my_team_pk(season)
        my_team_name = None
        if my_team_pk is not None:
            match = rosters_df.loc[rosters_df["team_pk"] == my_team_pk, "team_name"]
            if not match.empty:
                my_team_name = match.iloc[0]
        default_a_index = teams.index(my_team_name) if my_team_name in teams else 0
        default_b_index = next((i for i in range(len(teams)) if i != default_a_index), 0)

        col_a, col_b = st.columns(2)
        with col_a:
            team_a = st.selectbox("Team A", teams, index=default_a_index, key="wr_trade_team_a")
        with col_b:
            team_b = st.selectbox("Team B", teams, index=default_b_index, key="wr_trade_team_b")

        if my_team_name:
            with st.expander(f"🔍 Find trades that work for both sides ({my_team_name})", expanded=False):
                st.caption(
                    "Searches every other team for realistic trades (1-for-1, and 2-for-1 using each "
                    "side's 2 lowest-value players as the throw-in) where BOTH teams' optimal starting "
                    "lineup value actually goes up - not just a trade you'd win. Same marginal-value "
                    "model as the calculator below."
                )
                if st.button("Search for win-win trades", key="wr_find_trades"):
                    with st.spinner("Evaluating trades against every other team..."):
                        proposals = wr.find_win_win_trades(rosters_df, my_team_name, slot_counts)
                    if not proposals:
                        st.info("No realistic win-win trades found right now.")
                    else:
                        for p in proposals:
                            with st.container(border=True):
                                st.markdown(f"**vs {p['opponent']}**")
                                cols = st.columns(3)
                                cols[0].markdown(f"You send: {', '.join(p['players_out'])}")
                                cols[1].markdown(f"You get: {', '.join(p['players_in'])}")
                                cols[2].markdown(f"Your gain: **{p['my_gain']:+.0f}** · Their gain: **{p['their_gain']:+.0f}**")

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

with tab_lineup:
    st.caption(
        "Compares your CURRENT starting lineup against FantasyPros' weekly consensus rankings "
        "(not a single named analyst - see Methodology below for why) and real kickoff times, "
        "flagging any non-optimal starts and anyone in your lineup projected for exactly 0 "
        "points. Nothing here changes your real ESPN lineup unless you explicitly submit below."
    )

    lineup_week = wr.get_current_week(season)

    with st.expander(f"📄 Upload weekly rank override (Week {lineup_week})"):
        st.caption(
            "Upload a PDF of your own weekly rankings (any source - Yahoo, an analyst's cheat "
            "sheet, whatever). Parsed ranks supersede FantasyPros' weekly consensus for any "
            f"player they cover, for Week {lineup_week} across every league (matched by player "
            "name, not tied to a specific roster). PDF layouts vary a lot, so this is a "
            "best-effort parse - always review the table below and fix/delete rows before saving."
        )

        active_override = wr.get_active_rank_override_meta(season, lineup_week)
        if active_override:
            uploaded_at = (active_override.get("uploaded_at") or "")[:16].replace("T", " ")
            st.success(
                f"Active override: {len(active_override.get('ranks', []))} players, from "
                f"\"{active_override.get('source_filename') or 'manual'}\", uploaded {uploaded_at} UTC."
            )
            if st.button("Clear this week's override", key="wr_override_clear"):
                clear_result = wr.clear_rank_override(season, lineup_week)
                if not clear_result["committed"]:
                    st.warning(clear_result["commit_message"])
                st.session_state.pop("wr_lineup_result", None)
                st.rerun()

        uploaded_pdf = st.file_uploader("Weekly rankings PDF", type=["pdf"], key="wr_override_upload")
        if uploaded_pdf is not None:
            if st.session_state.get("wr_override_parsed_filename") != uploaded_pdf.name:
                from fantasy_football.rank_override_pdf import parse_pdf_rankings

                try:
                    parsed_rows = parse_pdf_rankings(uploaded_pdf.read())
                    parse_error = None
                except Exception as exc:  # noqa: BLE001 - surface the real error, don't crash the page
                    parsed_rows = []
                    parse_error = f"{type(exc).__name__}: {exc}"
                st.session_state["wr_override_parsed_filename"] = uploaded_pdf.name
                st.session_state["wr_override_parse_error"] = parse_error
                st.session_state["wr_override_rows"] = pd.DataFrame(
                    [{"rank": r.rank, "player_name": r.player_name} for r in parsed_rows]
                )

            if st.session_state.get("wr_override_parse_error"):
                st.error(f"Couldn't read this PDF: {st.session_state['wr_override_parse_error']}")

            rows_df = st.session_state.get("wr_override_rows")
            if rows_df is not None and not rows_df.empty:
                st.caption(f"Parsed {len(rows_df)} rows - review/edit before saving (add or delete rows as needed).")
                edited_rows = st.data_editor(
                    rows_df,
                    num_rows="dynamic",
                    key="wr_override_editor",
                    column_config={
                        "rank": st.column_config.NumberColumn("Rank", min_value=1, step=1),
                        "player_name": st.column_config.TextColumn("Player name"),
                    },
                    hide_index=True,
                )
                if st.button("Save & apply this override", key="wr_override_save"):
                    clean_rows = edited_rows.dropna(subset=["rank", "player_name"]).to_dict("records")
                    save_result = wr.save_rank_override(season, lineup_week, clean_rows, uploaded_pdf.name)
                    if save_result["committed"]:
                        st.success(f"Saved {len(clean_rows)} ranks and committed to git - live everywhere.")
                    else:
                        st.warning(f"Saved {len(clean_rows)} ranks locally only. {save_result['commit_message']}")
                    for key in ("wr_override_parsed_filename", "wr_override_parse_error", "wr_override_rows"):
                        st.session_state.pop(key, None)
                    st.session_state.pop("wr_lineup_result", None)  # force a recompute with the new override
                    st.rerun()
            elif rows_df is not None and not st.session_state.get("wr_override_parse_error"):
                st.warning("Couldn't parse any rank lines out of this PDF - try a different file.")

    lineup_team_pk = wr.get_my_team_pk(season)
    if lineup_team_pk is None:
        st.info("Couldn't resolve your team automatically - check your ESPN credentials.")
    else:
        if st.button("Check my lineup", key="wr_lineup_check"):
            with st.spinner("Pulling weekly rankings and your current lineup..."):
                try:
                    st.session_state["wr_lineup_result"] = wr.build_ideal_lineup(season, lineup_team_pk)
                except Exception as exc:  # noqa: BLE001 - surface real diagnostic info instead of
                    # Streamlit's own generic "AttributeError, redacted" screen, which gives no way
                    # to tell what actually broke without SSH access to the deployed app's logs.
                    st.session_state["wr_lineup_result"] = None
                    st.error(f"Couldn't compute your lineup: {type(exc).__name__}: {exc}")

        result = st.session_state.get("wr_lineup_result")
        if result is not None:
            zero_proj = result["zero_projected_starters"]
            changes = result["changes"]

            if zero_proj:
                st.error("⚠️ HIGH PRIORITY - projected for 0 points and currently starting:")
                for z in zero_proj:
                    st.markdown(f"- **{z['player_name']}** ({z['projected']:.1f} pts projected)")

            if changes:
                st.markdown(f"#### Suggested changes (Week {result['week']})")
                for c in changes:
                    st.markdown(f"- **{c['player_name']}** ({c['position']}): {c['from_slot']} → {c['to_slot']}")

                real_submit = st.checkbox(
                    "⚠️ Actually submit this lineup change to ESPN (real transaction)",
                    value=False, key="wr_lineup_real_submit",
                    help="Unchecked = dry run only (shows exactly what would be sent, sends nothing). "
                    "Resets to unchecked every page load.",
                )
                submit_label = "Submit lineup change to ESPN" if real_submit else "Preview lineup change (dry run)"
                if st.button(submit_label, key="wr_lineup_submit"):
                    moves = [
                        (c["player_id"], wr.slot_name_to_id(c["from_slot"]), wr.slot_name_to_id(c["to_slot"]))
                        for c in changes
                    ]
                    lineup_result = wr.submit_lineup_changes(season, lineup_team_pk, moves, dry_run=not real_submit)
                    if lineup_result["dry_run"]:
                        st.info("Dry run - exact payload that would be sent:")
                        st.json(lineup_result["payload"])
                    elif lineup_result["success"]:
                        st.success(lineup_result["message"])
                    else:
                        st.error(lineup_result["message"])
            elif not zero_proj:
                st.success("Your current lineup already matches the weekly consensus - no changes suggested.")

            detail = result.get("lineup_detail") or []
            if detail:
                st.markdown("#### Full lineup detail")
                st.caption(
                    "Rank used = override rank when one applies, otherwise FantasyPros' weekly rank "
                    "(the same number driving the suggestions above). Matchup = FantasyPros' own "
                    "start/sit grade (A+..F) for that player's matchup this week - the closest signal "
                    "they publish to a bare opponent-strength number."
                )
                detail_df = pd.DataFrame(detail).rename(
                    columns={
                        "player_name": "Player", "position": "Pos", "current_slot": "Current slot",
                        "proposed_slot": "Proposed slot", "fp_rank": "FantasyPros rank",
                        "override_rank": "Override rank", "rank_used": "Rank used",
                        "espn_projected": "ESPN proj", "opponent": "Opp",
                        "matchup_grade": "Matchup", "injury_status": "Injury",
                    }
                )
                display_cols = [
                    "Player", "Pos", "Current slot", "Proposed slot", "FantasyPros rank",
                    "Override rank", "Rank used", "ESPN proj", "Opp", "Matchup", "Injury",
                ]
                st.dataframe(
                    detail_df[display_cols].sort_values("Rank used", na_position="last"),
                    use_container_width=True, hide_index=True,
                )

        with st.expander("Methodology"):
            st.markdown(
                """
**Who/what decides "optimal"**: FantasyPros' weekly consensus rankings (their blended panel of
100+ real analysts, currently including Justin Boone of Yahoo! Sports - FantasyPros' #1 overall
ranker by weekly accuracy as of this writing). The user's original ask was Boone's rankings
specifically, not a blend - two real constraints changed that: FantasyPros' API parameter for
isolating one expert's rankings doesn't actually work despite being documented (confirmed live -
identical results whether filtering to his expert ID, a different expert's, or none at all), and
he isn't even a registered contributor to FantasyPros' RB/WR/TE weekly panels, only QB/K/DST. A
broad weekly consensus - which DOES include him for the positions he covers - is what's actually
usable today.

**QB/OP (superflex)** is decided separately from RB/WR/TE/FLEX, since FantasyPros has no single
rank scale comparable across QB and skill positions - your top 2 QBs by weekly QB rank fill
QB+OP, the standard superflex convention (and consistent with this app's own QB scarcity premium
elsewhere in War Room).

**Slot ordering** (which of your starters sits in a base RB/WR/TE slot vs. the flex slot) is
chosen by real kickoff time - earlier games get the fixed base slots, the flex slot(s) hold
whichever starter(s) have the LATEST kickoff, so you keep maximum flexibility for a late swap.
This has zero effect on scoring - ESPN scores a player identically regardless of which eligible
slot he's sitting in - it's purely a lineup-management preference.
                """
            )
