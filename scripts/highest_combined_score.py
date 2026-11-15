#!/usr/bin/env python3
"""One-off: across every season this league has real data for, find the
highest combined score (both teams added together) in a single
matchup - regular season and playoffs reported separately.

Read-only analysis - writes nothing, sends nothing.

Usage: python scripts/highest_combined_score.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season

SEASONS = list(range(2015, 2027))  # this league's real full history (see fantasy_football/ama.py's
# own schema note: "Every view already spans ALL seasons (2015-present)")


def main() -> int:
    client = ESPNClient()

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = sqlite3.connect(db.DB_PATH)
        db.init_db(conn)

        for season in SEASONS:
            try:
                print(f"Ingesting season {season}...")
                ingest_season(conn, client, season, log=print)
            except Exception as exc:  # noqa: BLE001 - a season this league didn't play shouldn't kill the rest
                print(f"[warn] season {season}: {exc}")

        real_seasons = [r[0] for r in conn.execute("SELECT season_id FROM seasons ORDER BY season_id")]
        print(f"\nReal seasons found in the DB: {real_seasons}\n")

        for label, is_playoff in (("REGULAR SEASON", 0), ("PLAYOFFS", 1)):
            rows = conn.execute(
                """
                SELECT m.season_id, m.week, t1.team_name AS home_name, m.home_score,
                       t2.team_name AS away_name, m.away_score,
                       (m.home_score + m.away_score) AS combined
                FROM matchups m
                JOIN teams t1 ON t1.id = m.home_team_pk
                JOIN teams t2 ON t2.id = m.away_team_pk
                WHERE m.completed = 1 AND m.is_playoff = ?
                  AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL
                ORDER BY combined DESC
                LIMIT 5
                """,
                (is_playoff,),
            ).fetchall()
            print(f"=== Top 5 highest combined scores - {label} ===")
            for season_id, week, home_name, home_score, away_name, away_score, combined in rows:
                print(
                    f"  {combined:.2f} total - Season {season_id} Week {week}: "
                    f"{home_name} {home_score:.2f} vs {away_name} {away_score:.2f}"
                )
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
