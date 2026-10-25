#!/usr/bin/env python3
"""One-off investigation (not scheduled): for each team, each of the
most recent few weeks (including the CURRENT, still-in-progress week,
since that's what a live kickoff view would have shown), prints the
real pregame optimal-lineup projection (mean), that team's score stdev,
and the resulting p10/p90 under both the OLD symmetric +/-1.2816
multiplier and the NEW asymmetric -1.661/+1.2816 one - sorted by p90
descending, to find exactly which team/week produced an implausible
200+ p90 and whether it's a MEAN problem (an inflated pregame
projection - e.g. a bye-week/injured player not correctly zeroed out)
or just a genuinely high-mean team where a wide p90 is actually
justified.

Usage: python scripts/investigate_high_projections.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from fantasy_football import db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season
from fantasy_football.metrics import loaders as metric_loaders
from fantasy_football.metrics.cutline_sim import TeamScoreModel, percentile_range
from fantasy_football.metrics.pipeline import compute_and_store_season_metrics
from fantasy_football.metrics.win_probability import MIN_STDEV

OLD_Z10 = -1.2815515655446004


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = db.get_connection()
        db.init_db(conn)
        print("Ingesting full season history...")
        ingest_season(conn, client, season, log=lambda *a: None)
        compute_and_store_season_metrics(conn, season)

        meta = conn.execute(
            "SELECT position_slot_counts FROM seasons WHERE season_id = ?", (season,)
        ).fetchone()
        position_slot_counts = json.loads(meta[0])

        names_df = pd.read_sql_query(
            "SELECT id AS team_pk, team_name FROM teams WHERE season_id = ?", conn, params=(season,)
        )
        names = dict(zip(names_df["team_pk"], names_df["team_name"]))

        row = conn.execute(
            "SELECT MAX(week) FROM weekly_team_scores WHERE season_id = ? AND completed = 1 AND is_playoff = 0",
            (season,),
        ).fetchone()
        last_completed_week = row[0] or 0
        current_week_row = conn.execute("SELECT current_week FROM seasons WHERE season_id = ?", (season,)).fetchone()
        current_week = current_week_row[0]
        print(f"last completed week: {last_completed_week}, current (in-progress) week: {current_week}\n")

        weeks_to_check = sorted(set(range(max(1, last_completed_week - 1), last_completed_week + 1)) | {current_week})

        rows = []
        for week in weeks_to_check:
            mean_by_team = metric_loaders.load_optimal_lineup_points(conn, season, week, position_slot_counts)
            if mean_by_team.empty:
                print(f"week {week}: no pregame roster/projection data yet - skipping")
                continue
            prior_scores = pd.read_sql_query(
                "SELECT team_pk, score FROM weekly_team_scores WHERE season_id = ? AND week < ? AND completed = 1",
                conn, params=(season, week),
            )
            if prior_scores.empty:
                stdev_by_team = pd.Series(MIN_STDEV, index=mean_by_team.index)
            else:
                stdev_by_team = prior_scores.groupby("team_pk")["score"].std(ddof=1).clip(lower=MIN_STDEV)
                stdev_by_team = stdev_by_team.reindex(mean_by_team.index).fillna(MIN_STDEV)

            for pk in mean_by_team.index:
                mean = float(mean_by_team[pk])
                stdev = float(stdev_by_team[pk])
                old_p90 = mean + 1.2815515655446004 * stdev
                new_p10, new_p90 = percentile_range(TeamScoreModel(team_pk=int(pk), mean=mean, stdev=stdev))
                is_completed = week <= last_completed_week
                actual = None
                if is_completed:
                    a = conn.execute(
                        "SELECT score FROM weekly_team_scores WHERE season_id=? AND week=? AND team_pk=? AND completed=1",
                        (season, week, pk),
                    ).fetchone()
                    actual = a[0] if a else None
                rows.append(
                    {
                        "week": week, "team": names.get(int(pk), "?"), "status": "completed" if is_completed else "UPCOMING/LIVE",
                        "mean": mean, "stdev": stdev, "old_p90": old_p90, "new_p10": new_p10, "new_p90": new_p90,
                        "actual": actual,
                    }
                )

        df = pd.DataFrame(rows).sort_values("old_p90", ascending=False)
        print(f"{'week':<6}{'team':<28}{'status':<16}{'mean':>8}{'stdev':>8}{'old_p90':>9}{'new_p90':>9}{'actual':>8}")
        for _, r in df.head(15).iterrows():
            actual_str = f"{r['actual']:.1f}" if r["actual"] is not None else "-"
            print(
                f"{r['week']:<6}{r['team']:<28}{r['status']:<16}{r['mean']:>8.1f}{r['stdev']:>8.1f}"
                f"{r['old_p90']:>9.1f}{r['new_p90']:>9.1f}{actual_str:>8}"
            )

        print("\n--- player-level detail for the single highest old_p90 ---")
        top = df.iloc[0]
        top_week, top_team_name = int(top["week"]), top["team"]
        top_pk = names_df[names_df["team_name"] == top_team_name]["team_pk"].iloc[0]
        roster = pd.read_sql_query(
            """
            SELECT p.player_name, p.default_position, wr.eligible_slots, pws.projected_points
            FROM weekly_rosters wr
            JOIN players p ON p.player_id = wr.player_id
            LEFT JOIN player_week_scores pws ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
            WHERE wr.season_id = ? AND wr.week = ? AND wr.team_pk = ? AND wr.eligible_slots IS NOT NULL
            ORDER BY pws.projected_points DESC
            """,
            conn, params=(season, top_week, int(top_pk)),
        )
        print(f"{top_team_name}, week {top_week}:")
        for _, p in roster.iterrows():
            proj = p["projected_points"] if pd.notna(p["projected_points"]) else 0.0
            print(f"  {p['player_name']:<28} {p['default_position']:<6} projected={proj:.1f}  slots={p['eligible_slots']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
