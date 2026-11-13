#!/usr/bin/env python3
"""One-off: grade the real Bijan Robinson trade (Robbying Moore TDs sent
Jameson Williams + Ashton Jeanty to Hammer Time for Bijan Robinson,
accepted 2026-10-31) using data AS OF THE WEEK IT TOOK EFFECT - a
genuinely different question from scripts/grade_trades.py's "grade a
recent trade using today's live data."

Data-availability note (read before trusting the numbers): FantasyPros
ROS rank and ESPN's season-to-date positional rank are both LIVE,
cumulative signals - this project has never stored a dated history of
either one, only ever ingesting them for whatever week is CURRENT at
ingest time. So there is no real historical snapshot of either rank
signal for a week back in October reachable from here. What IS fully,
genuinely historical and re-fetched fresh from ESPN for this run:
- the real roster each team held, week by week (ingest_week_boxscores
  backfills this for every past week, not just the latest)
- that week's real pregame point projection per player
- the real completed-game standings and schedule through that week
The rank-based "value" signal silently degrades to projection-only for
the historical week used here, exactly how roster_strength.py's own
blend() already handles any missing signal - a real, pre-existing
design feature being exercised here, not a workaround bolted on.

Usage: python scripts/grade_bijan_trade.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import db, war_room_data
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season
from fantasy_football.metrics.lineup_optimizer import RosterPlayer, optimal_lineup
from fantasy_football.metrics.pipeline import compute_and_store_lineup_efficiency, compute_and_store_season_metrics
from fantasy_football.metrics.playoff_sim import compute_score_stdev, simulate_season
from fantasy_football.metrics.roster_strength import (
    compute_player_values,
    compute_team_roster_strength,
    roster_strength_to_points,
)

SIM_SEED = 42
TEAM_A_NAME = "Robbying Moore TDs"  # sent away Jameson Williams, Ashton Jeanty
TEAM_B_NAME = "Hammer Time"          # sent away Bijan Robinson
PLAYERS_OUT_A = ["Jameson Williams", "Ashton Jeanty"]
PLAYERS_OUT_B = ["Bijan Robinson"]


def _swap_team(df: pd.DataFrame, player_ids: list[int], new_team_pk: int) -> pd.DataFrame:
    out = df.copy()
    out.loc[out["player_id"].isin(player_ids), "team_pk"] = new_team_pk
    return out


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = sqlite3.connect(db.DB_PATH)
        db.init_db(conn)
        print("Ingesting real league data (full season history)...")
        ingest_season(conn, client, season, log=print)
        compute_and_store_season_metrics(conn, season)
        compute_and_store_lineup_efficiency(conn, season)

        team_pk_by_name = {
            name: pk for pk, name in conn.execute("SELECT id, team_name FROM teams WHERE season_id = ?", (season,))
        }
        team_a_pk, team_b_pk = team_pk_by_name[TEAM_A_NAME], team_pk_by_name[TEAM_B_NAME]

        player_id_by_name = {}
        for name in PLAYERS_OUT_A + PLAYERS_OUT_B:
            row = conn.execute("SELECT player_id FROM players WHERE player_name = ?", (name,)).fetchone()
            if row is None:
                raise ValueError(f"no real player named {name!r} found")
            player_id_by_name[name] = row[0]
        out_a = [player_id_by_name[n] for n in PLAYERS_OUT_A]
        out_b = [player_id_by_name[n] for n in PLAYERS_OUT_B]

        # Find the real week this trade's roster move actually landed in -
        # the first week Bijan Robinson's own weekly_rosters row shows him
        # on Hammer Time (his real destination), rather than guessing from
        # the acceptance date alone (ESPN's own processing delay - see
        # grade_trades.py's _at_origin docstring - means acceptance date and
        # effective roster week aren't always the same week).
        bijan_id = player_id_by_name["Bijan Robinson"]
        rows = conn.execute(
            "SELECT week, team_pk FROM weekly_rosters WHERE season_id = ? AND player_id = ? ORDER BY week",
            (season, bijan_id),
        ).fetchall()
        trade_week = next((w for w, pk in rows if pk == team_b_pk), None)
        if trade_week is None:
            raise ValueError("could not find a week where Bijan Robinson's roster shows him on Hammer Time")
        print(f"Trade took effect in week {trade_week} (first week Bijan Robinson shows rostered to {TEAM_B_NAME})")
        print(f"Full Bijan Robinson roster history: {rows}")

        meta = conn.execute(
            "SELECT reg_season_count, position_slot_counts, median_scoring, playoff_team_count "
            "FROM seasons WHERE season_id = ?",
            (season,),
        ).fetchone()
        reg_season_count, position_slot_counts_json, median_scoring, playoff_team_count = meta
        position_slot_counts = json.loads(position_slot_counts_json)

        # --- Trade grade: marginal starting-lineup value, as of trade_week ---
        raw_week_rows = pd.read_sql_query(
            """
            SELECT wr.team_pk, wr.player_id, p.player_name, p.default_position AS position,
                   wr.slot_position, wr.is_starter, wr.eligible_slots,
                   pws.projected_points, fpr.pos_rank AS fp_pos_rank
            FROM weekly_rosters wr
            JOIN players p ON p.player_id = wr.player_id
            LEFT JOIN player_week_scores pws
                ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
            LEFT JOIN fantasypros_rankings fpr
                ON fpr.season_id = wr.season_id AND fpr.week = wr.week AND fpr.player_id = wr.player_id
            WHERE wr.season_id = ? AND wr.week = ?
            """,
            conn, params=(season, trade_week),
        )
        # war_room_data.evaluate_trade's _roster_players() expects
        # eligible_slots as a raw JSON string (it json.loads()s it itself)
        # - keep that version for value_df below BEFORE converting
        # raw_week_rows's own copy to a real frozenset for RosterPlayer.
        value_df_eligible_slots = raw_week_rows["eligible_slots"].copy()
        raw_week_rows["eligible_slots"] = raw_week_rows["eligible_slots"].apply(
            lambda s: frozenset(json.loads(s)) if pd.notna(s) else frozenset()
        )
        raw_week_rows["projected_points"] = raw_week_rows["projected_points"].fillna(0.0)
        print(
            f"FantasyPros rank coverage in week {trade_week}: "
            f"{raw_week_rows['fp_pos_rank'].notna().sum()} / {len(raw_week_rows)} players "
            "(0 is expected/correct - that table only ever holds the CURRENT week, see module docstring)"
        )

        def _optimal_points_all(df: pd.DataFrame) -> pd.Series:
            pts = {}
            for team_pk, g in df.groupby("team_pk"):
                players = [
                    RosterPlayer(int(r.player_id), float(r.projected_points), r.eligible_slots)
                    for r in g.itertuples()
                ]
                total, _ = optimal_lineup(players, position_slot_counts)
                pts[int(team_pk)] = total
            return pd.Series(pts)

        team_pks_sorted = sorted(raw_week_rows["team_pk"].unique())

        def _shrinkage_prior(rows: pd.DataFrame) -> pd.Series:
            strength = compute_team_roster_strength(
                compute_player_values(rows), week=trade_week, reg_season_count=reg_season_count
            ).set_index("team_pk")["roster_strength"]
            optimal_points = _optimal_points_all(rows)
            return roster_strength_to_points(
                strength.reindex(team_pks_sorted), optimal_points.reindex(team_pks_sorted)
            )

        # Trade-value engine wants a value_score column (pure FantasyPros+
        # ESPN rank blend, see war_room_data.blend_trade_value) - build it
        # here directly on the week-trade_week roster rather than calling
        # get_trade_rosters() (which is hardcoded to "latest ingested
        # week"). fp_pos_rank is real-but-empty for this historical week
        # (see note above); espn_pos_rank (player_rankings, also only ever
        # populated for the CURRENT week) is unavailable the same way -
        # value_score renormalizes onto whatever's left, same degrade-
        # gracefully contract as compute_player_values.
        from fantasy_football.metrics.roster_strength import rank_to_score
        from fantasy_football.metrics.waiver_value import position_scarcity_multipliers

        team_names_by_pk = {pk: name for name, pk in team_pk_by_name.items()}
        value_df = raw_week_rows.copy()
        value_df["eligible_slots"] = value_df_eligible_slots
        value_df["team_name"] = value_df["team_pk"].map(team_names_by_pk)
        value_df["fp_score"] = value_df["fp_pos_rank"].apply(rank_to_score)
        value_df["espn_score"] = None  # not reconstructable for a past week - see module docstring
        scarcity = position_scarcity_multipliers(position_slot_counts, week=trade_week)
        value_df["value_score"] = (
            value_df.apply(
                lambda row: war_room_data.blend_trade_value(row["fp_score"], row["espn_score"], row["position"], scarcity)
                * 100,
                axis=1,
            )
        ).round(1)

        pre_trade_value_df = value_df.copy()
        pre_trade_value_df.loc[pre_trade_value_df["player_id"].isin(out_a), ["team_pk", "team_name"]] = [
            team_a_pk, TEAM_A_NAME
        ]
        pre_trade_value_df.loc[pre_trade_value_df["player_id"].isin(out_b), ["team_pk", "team_name"]] = [
            team_b_pk, TEAM_B_NAME
        ]
        grade = war_room_data.evaluate_trade(
            pre_trade_value_df, TEAM_A_NAME, TEAM_B_NAME, out_a, out_b, position_slot_counts
        )
        print(f"\n=== TRADE (effective week {trade_week}): {TEAM_A_NAME} <-> {TEAM_B_NAME} ===")
        print(f"{TEAM_A_NAME} sent: {PLAYERS_OUT_A}")
        print(f"{TEAM_B_NAME} sent: {PLAYERS_OUT_B}")
        for team_name, side in grade.items():
            print(
                f"  {team_name}: optimal-lineup value {side['before']:.1f} -> {side['after']:.1f} "
                f"(gain {side['gain']:+.1f}); newly starting: {side['newly_starting'] or '-'}; "
                f"newly benched: {side['newly_benched'] or '-'}"
            )

        # --- Championship-odds impact, as of right after the trade ---
        before_rows = _swap_team(_swap_team(raw_week_rows, out_a, team_a_pk), out_b, team_b_pk)
        after_rows = _swap_team(_swap_team(raw_week_rows, out_a, team_b_pk), out_b, team_a_pk)
        shrinkage_before_all = _shrinkage_prior(before_rows)
        shrinkage_after_all = _shrinkage_prior(after_rows)
        print(
            f"  [debug] shrinkage prior (pts), week {trade_week}: {TEAM_A_NAME} "
            f"{shrinkage_before_all.get(team_a_pk):.1f} -> {shrinkage_after_all.get(team_a_pk):.1f}; "
            f"{TEAM_B_NAME} {shrinkage_before_all.get(team_b_pk):.1f} -> {shrinkage_after_all.get(team_b_pk):.1f}"
        )

        # team_state AS OF trade_week - 1 (the real standings right before
        # this trade's effective week), NOT today's MAX(week) snapshot -
        # metrics_weekly stores one real cumulative-through-that-week row
        # per team per week (compute_season_metrics cumsum()s in week
        # order), so this is genuinely historical, not approximated.
        as_of_week = trade_week - 1
        team_state = pd.read_sql_query(
            """
            SELECT team_pk, ppg AS season_ppg, last3_ppg, points_for,
                   matchup_wins, matchup_losses, matchup_ties,
                   median_wins, median_losses, median_ties
            FROM metrics_weekly WHERE season_id = ? AND week = ?
            """,
            conn, params=(season, as_of_week),
        )
        scores_so_far = pd.read_sql_query(
            "SELECT week, team_pk, score FROM weekly_team_scores "
            "WHERE season_id = ? AND completed = 1 AND is_playoff = 0 AND week <= ? ORDER BY week, team_pk",
            conn, params=(season, as_of_week),
        )
        stdev = compute_score_stdev(scores_so_far)
        team_state = team_state.merge(stdev, on="team_pk", how="left")
        remaining_matchups = pd.read_sql_query(
            "SELECT week, home_team_pk, away_team_pk FROM matchups "
            "WHERE season_id = ? AND week >= ? AND matchup_type = 'NONE' ORDER BY week",
            conn, params=(season, trade_week),
        )
        print(f"  [debug] team_state as of week {as_of_week}: {len(team_state)} teams; "
              f"remaining_matchups from week {trade_week}: {len(remaining_matchups)} games")

        prior_after = shrinkage_after_all.reindex(team_state["team_pk"]).to_numpy()
        prior_before = shrinkage_before_all.reindex(team_state["team_pk"]).to_numpy()

        sim_after = simulate_season(
            team_state, remaining_matchups, bool(median_scoring), reg_season_count,
            playoff_team_count=playoff_team_count, shrinkage_prior=prior_after,
            rng=np.random.default_rng(SIM_SEED),
        ).set_index("team_pk")
        sim_before = simulate_season(
            team_state, remaining_matchups, bool(median_scoring), reg_season_count,
            playoff_team_count=playoff_team_count, shrinkage_prior=prior_before,
            rng=np.random.default_rng(SIM_SEED),
        ).set_index("team_pk")

        for pk, name in ((team_a_pk, TEAM_A_NAME), (team_b_pk, TEAM_B_NAME)):
            champ_before = 100 * sim_before.loc[pk, "championship_pct"]
            champ_after = 100 * sim_after.loc[pk, "championship_pct"]
            playoff_before = 100 * sim_before.loc[pk, "playoff_pct"]
            playoff_after = 100 * sim_after.loc[pk, "playoff_pct"]
            print(
                f"  {name} championship odds (as of week {trade_week}): {champ_before:.1f}% -> {champ_after:.1f}% "
                f"({champ_after - champ_before:+.1f} pts); playoff odds: {playoff_before:.1f}% -> "
                f"{playoff_after:.1f}% ({playoff_after - playoff_before:+.1f} pts)"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
