#!/usr/bin/env python3
"""One-off: resolve the real manager behind "McConkey Kong" (season
2026) through team_owners/managers, same pattern as
diagnose_live_score_and_owners.py's record-matchup owner lookup."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = sqlite3.connect(db.DB_PATH)
        db.init_db(conn)
        ingest_season(conn, client, season, log=print)

        rows = conn.execute(
            """
            SELECT m.display_name, m.first_name, m.last_name
            FROM teams t
            JOIN team_owners o ON o.team_pk = t.id
            JOIN managers m ON m.manager_id = o.manager_id
            WHERE t.season_id = ? AND t.team_name = 'McConkey Kong'
            """,
            (season,),
        ).fetchall()
        names = [r[0] or f"{r[1]} {r[2]}" for r in rows] or ["(no owner found)"]
        print(f"McConkey Kong (season {season}) -> {', '.join(names)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
