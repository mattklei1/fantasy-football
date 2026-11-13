#!/usr/bin/env python3
"""One-off: find the league's real recent TRADE_ACCEPT transactions (from
the `transactions` table ingest_recent_activity already populates - see
ingest.py), grade each one for BOTH sides using the same marginal
starting-lineup-value engine the War Room Trade Evaluator uses
(war_room_data.evaluate_trade), and measure each involved team's
resulting championship-odds swing via the same counterfactual
re-simulation technique used elsewhere in this project (flip ONE input -
here, which team a traded player sits on - hold everything else fixed,
re-run metrics.playoff_sim.simulate_season, diff the output).

Read-only analysis - writes nothing, sends nothing. Prints everything to
this run's log for review.

Usage: python scripts/grade_trades.py
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
from fantasy_football.metrics import loaders as metric_loaders
from fantasy_football.metrics.lineup_optimizer import RosterPlayer, optimal_lineup
from fantasy_football.metrics.playoff_sim import simulate_season
from fantasy_football.metrics.roster_strength import (
    compute_player_values,
    compute_team_roster_strength,
    roster_strength_to_points,
)

SIM_SEED = 42  # same seed for the with-trade and without-trade run - common
# random numbers, so the only thing that can move championship_pct between
# the two runs is the shrinkage_prior swap itself, not fresh simulation noise.


def _group_trades(conn: sqlite3.Connection, season: int) -> dict[str, list[tuple]]:
    """activity_date -> list of (team_pk, action_type, player_id, player_name)
    rows, for every TRADE_SENT/TRADE_RECEIVED leg this ingest run pulled
    from ESPN's real recent-activity feed (see ingest.ingest_recent_activity -
    already wired into ingest_season for the current season). One
    activity_date == one real trade event (ESPN bundles every player leg
    of one trade under a single activity timestamp)."""
    rows = conn.execute(
        """
        SELECT activity_date, team_pk, action_type, player_id, player_name
        FROM transactions
        WHERE season_id = ? AND action_type IN ('TRADE_SENT', 'TRADE_RECEIVED')
        ORDER BY activity_date
        """,
        (season,),
    ).fetchall()
    trades: dict[str, list[tuple]] = {}
    for activity_date, team_pk, action_type, player_id, player_name in rows:
        trades.setdefault(activity_date, []).append((team_pk, action_type, player_id, player_name))
    return trades


def _sides(legs: list[tuple]) -> dict:
    """Resolves one trade's legs into {team_pk: [player_ids sent away]} -
    the shape war_room_data.evaluate_trade() needs. Only handles the
    standard 2-team case (every real trade this league has ever run)."""
    sent = {(pk, pid): name for pk, atype, pid, name in legs if atype == "TRADE_SENT"}
    out_by_team: dict[int, list[int]] = {}
    names_by_id: dict[int, str] = {}
    for (pk, pid), name in sent.items():
        out_by_team.setdefault(pk, []).append(pid)
        names_by_id[pid] = name
    return out_by_team, names_by_id


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
        print("Ingesting real league data (rosters, rankings, recent activity)...")
        ingest_season(conn, client, season, log=print)

        trades = _group_trades(conn, season)
        print(f"\nFound {len(trades)} distinct real trade event(s) in the recent-activity window.\n")
        if not trades:
            print("No TRADE_SENT/TRADE_RECEIVED activity found - nothing to grade.")
            return 0

        team_names = dict(conn.execute("SELECT id, team_name FROM teams WHERE season_id = ?", (season,)).fetchall())
        meta = conn.execute(
            "SELECT current_week, reg_season_count, position_slot_counts, median_scoring, playoff_team_count "
            "FROM seasons WHERE season_id = ?",
            (season,),
        ).fetchone()
        current_week, reg_season_count, position_slot_counts_json, median_scoring, playoff_team_count = meta
        position_slot_counts = json.loads(position_slot_counts_json)

        rosters_df = war_room_data.get_trade_rosters(season)  # current (post-trade) reality
        team_state, remaining_matchups = metric_loaders.load_playoff_sim_state(conn, season)

        raw_week_rows = pd.read_sql_query(
            """
            SELECT wr.team_pk, wr.player_id, p.default_position AS position,
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
            conn, params=(season, current_week),
        )
        raw_week_rows["eligible_slots"] = raw_week_rows["eligible_slots"].apply(
            lambda s: frozenset(json.loads(s)) if pd.notna(s) else frozenset()
        )
        raw_week_rows["projected_points"] = raw_week_rows["projected_points"].fillna(0.0)

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

        real_strength = compute_team_roster_strength(
            compute_player_values(raw_week_rows), week=current_week, reg_season_count=reg_season_count
        ).set_index("team_pk")["roster_strength"]
        real_optimal_points = _optimal_points_all(raw_week_rows)
        team_pks_sorted = sorted(real_optimal_points.index)
        shrinkage_after_all = roster_strength_to_points(
            real_strength.reindex(team_pks_sorted), real_optimal_points.reindex(team_pks_sorted)
        )

        for date, legs in trades.items():
            out_by_team, names_by_id = _sides(legs)
            team_pks = list(out_by_team.keys())
            if len(team_pks) != 2:
                print(f"=== Trade on {date}: skipping - not a standard 2-team trade (teams: {team_pks}) ===\n")
                continue
            team_a_pk, team_b_pk = team_pks
            team_a_name, team_b_name = team_names.get(team_a_pk), team_names.get(team_b_pk)
            out_a, out_b = out_by_team[team_a_pk], out_by_team[team_b_pk]

            print(f"=== TRADE on {date}: {team_a_name} <-> {team_b_name} ===")
            print(f"{team_a_name} sent: {[names_by_id[p] for p in out_a]}")
            print(f"{team_b_name} sent: {[names_by_id[p] for p in out_b]}")

            # --- Trade grade: marginal starting-lineup value, both sides ---
            pre_trade_rosters = rosters_df.copy()
            pre_trade_rosters.loc[pre_trade_rosters["player_id"].isin(out_a), ["team_pk", "team_name"]] = [
                team_a_pk, team_a_name
            ]
            pre_trade_rosters.loc[pre_trade_rosters["player_id"].isin(out_b), ["team_pk", "team_name"]] = [
                team_b_pk, team_b_name
            ]
            grade = war_room_data.evaluate_trade(
                pre_trade_rosters, team_a_name, team_b_name, out_a, out_b, position_slot_counts
            )
            for team_name, side in grade.items():
                print(
                    f"  {team_name}: optimal-lineup value {side['before']:.1f} -> {side['after']:.1f} "
                    f"(gain {side['gain']:+.1f}); newly starting: {side['newly_starting'] or '-'}; "
                    f"newly benched: {side['newly_benched'] or '-'}"
                )

            # --- Championship-odds impact: counterfactual re-simulation ---
            swapped_rows = _swap_team(raw_week_rows, out_a, team_a_pk)
            swapped_rows = _swap_team(swapped_rows, out_b, team_b_pk)
            pre_strength = compute_team_roster_strength(
                compute_player_values(swapped_rows), week=current_week, reg_season_count=reg_season_count
            ).set_index("team_pk")["roster_strength"]
            pre_optimal_points = _optimal_points_all(swapped_rows)
            shrinkage_before_all = roster_strength_to_points(
                pre_strength.reindex(team_pks_sorted), pre_optimal_points.reindex(team_pks_sorted)
            )

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

            for pk, name in ((team_a_pk, team_a_name), (team_b_pk, team_b_name)):
                champ_before = 100 * sim_before.loc[pk, "championship_pct"]
                champ_after = 100 * sim_after.loc[pk, "championship_pct"]
                playoff_before = 100 * sim_before.loc[pk, "playoff_pct"]
                playoff_after = 100 * sim_after.loc[pk, "playoff_pct"]
                print(
                    f"  {name} championship odds: {champ_before:.1f}% -> {champ_after:.1f}% "
                    f"({champ_after - champ_before:+.1f} pts); playoff odds: {playoff_before:.1f}% -> "
                    f"{playoff_after:.1f}% ({playoff_after - playoff_before:+.1f} pts)"
                )
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
