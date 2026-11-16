#!/usr/bin/env python3
"""One-off: across every season this league has real data for, find the
highest score ANY SINGLE TEAM has ever put up in one week - regular
season and playoffs reported separately. Companion to
scripts/highest_combined_score.py (combined of both teams).

Read-only analysis - writes nothing, sends nothing.

Usage: python scripts/highest_single_team_score.py
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

SEASONS = list(range(2015, 2027))  # see ama.py's schema note: "Every view already spans ALL seasons (2015-present)"


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
                SELECT wts.season_id, wts.week, t.team_name, wts.score
                FROM weekly_team_scores wts
                JOIN teams t ON t.id = wts.team_pk
                WHERE wts.completed = 1 AND wts.is_playoff = ? AND wts.score IS NOT NULL
                ORDER BY wts.score DESC
                LIMIT 5
                """,
                (is_playoff,),
            ).fetchall()
            print(f"=== Top 5 highest single-team scores - {label} ===")
            for season_id, week, team_name, score in rows:
                print(f"  {score:.2f} - Season {season_id} Week {week}: {team_name}")
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
