#!/usr/bin/env python3
"""One-off: why did McConkey Kong's Roster Strength drop? Prints the
real current-week per-player breakdown (is_starter, position, this
week's point projection, FantasyPros rank, blended player_value) plus
the team's real roster_strength/shrinkage-prior number, and a
counterfactual: what would the number be if Jaxon Smith-Njigba were
starting with a normal (non-bye) projection instead of benched at 0.

Read-only - writes nothing, sends nothing.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season
from fantasy_football.metrics.lineup_optimizer import RosterPlayer, optimal_lineup
from fantasy_football.metrics.pipeline import compute_and_store_lineup_efficiency, compute_and_store_season_metrics
from fantasy_football.metrics.roster_strength import (
    compute_player_values,
    compute_team_roster_strength,
    roster_strength_to_points,
)

TEAM_NAME = "McConkey Kong"


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = sqlite3.connect(db.DB_PATH)
        db.init_db(conn)
        print("Ingesting real league data...")
        ingest_season(conn, client, season, log=print)
        compute_and_store_season_metrics(conn, season)
        compute_and_store_lineup_efficiency(conn, season)

        team_pk_by_name = {
            name: pk for pk, name in conn.execute("SELECT id, team_name FROM teams WHERE season_id = ?", (season,))
        }
        print(f"team_pk map: {team_pk_by_name}")
        my_pk = team_pk_by_name[TEAM_NAME]

        meta = conn.execute(
            "SELECT current_week, reg_season_count, position_slot_counts FROM seasons WHERE season_id = ?",
            (season,),
        ).fetchone()
        current_week, reg_season_count, position_slot_counts_json = meta
        position_slot_counts = json.loads(position_slot_counts_json)
        print(f"current_week={current_week}")

        import pandas as pd
        raw_df = pd.read_sql_query(
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
            conn, params=(season, current_week),
        )
        raw_df["eligible_slots"] = raw_df["eligible_slots"].apply(
            lambda s: frozenset(json.loads(s)) if pd.notna(s) else frozenset()
        )
        raw_df["projected_points"] = raw_df["projected_points"].fillna(0.0)

        print(f"\n=== {TEAM_NAME} current roster (week {current_week}) ===")
        valued_all = compute_player_values(raw_df)
        mine = valued_all[valued_all["team_pk"] == my_pk].sort_values("is_starter", ascending=False)
        for r in mine.itertuples():
            print(
                f"  {r.player_name:<26} pos={r.position:<4} slot={r.slot_position:<4} "
                f"starter={int(r.is_starter)} proj_pts={r.projected_points:.1f} "
                f"fp_pos_rank={r.fp_pos_rank} player_value={r.player_value:.3f}"
            )

        strength_all = compute_team_roster_strength(valued_all, week=current_week, reg_season_count=reg_season_count).set_index("team_pk")["roster_strength"]

        def _optimal_points_all(df):
            pts = {}
            for team_pk, g in df.groupby("team_pk"):
                players = [RosterPlayer(int(r.player_id), float(r.projected_points), r.eligible_slots) for r in g.itertuples()]
                total, _ = optimal_lineup(players, position_slot_counts)
                pts[int(team_pk)] = total
            return pd.Series(pts)

        optimal_points_all = _optimal_points_all(raw_df)
        team_pks_sorted = sorted(optimal_points_all.index)
        shrinkage_real = roster_strength_to_points(
            strength_all.reindex(team_pks_sorted), optimal_points_all.reindex(team_pks_sorted)
        )
        print(f"\nReal roster_strength (0-100): {strength_all.get(my_pk):.2f}")
        print(f"Real shrinkage prior (pts): {shrinkage_real.get(my_pk):.2f}")

        # Counterfactual: JSN starting with a normal (non-bye) projection
        jsn_row = raw_df[(raw_df["team_pk"] == my_pk) & (raw_df["player_name"] == "Jaxon Smith-Njigba")]
        if not jsn_row.empty:
            jsn_id = int(jsn_row.iloc[0]["player_id"])
            NORMAL_JSN_PROJECTION = 17.5  # his typical WR1 weekly projection this season, not a bye-week 0
            cf_df = raw_df.copy()
            cf_df.loc[cf_df["player_id"] == jsn_id, "is_starter"] = 1
            cf_df.loc[cf_df["player_id"] == jsn_id, "projected_points"] = NORMAL_JSN_PROJECTION
            # if someone else is currently starting in the slot JSN would take, bench them - approximate by
            # just re-running optimal_lineup's own selection logic via compute_team_roster_strength's
            # real starter/bench split, which already reflects is_starter directly
            valued_cf = compute_player_values(cf_df)
            strength_cf = compute_team_roster_strength(valued_cf, week=current_week, reg_season_count=reg_season_count).set_index("team_pk")["roster_strength"]
            optimal_points_cf = _optimal_points_all(cf_df)
            shrinkage_cf = roster_strength_to_points(
                strength_cf.reindex(team_pks_sorted), optimal_points_cf.reindex(team_pks_sorted)
            )
            print(f"\n[counterfactual] JSN forced to starter with proj={NORMAL_JSN_PROJECTION}:")
            print(f"  roster_strength: {strength_cf.get(my_pk):.2f} (real: {strength_all.get(my_pk):.2f})")
            print(f"  shrinkage prior (pts): {shrinkage_cf.get(my_pk):.2f} (real: {shrinkage_real.get(my_pk):.2f})")
        else:
            print("\nJaxon Smith-Njigba not found on this roster this week.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
