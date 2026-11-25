#!/usr/bin/env python3
"""One-off: real league context for the Aaron Rodgers benching news
(Steelers benched him for Mason Rudolph the day before Thanksgiving) -
who in the USC Pike League rosters either QB, what slot they're in,
and this week's real point projection for both.

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

PLAYER_NAMES = ["Aaron Rodgers", "Mason Rudolph"]


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = sqlite3.connect(db.DB_PATH)
        db.init_db(conn)
        print("Ingesting real league data...")
        ingest_season(conn, client, season, log=print)

        current_week = conn.execute(
            "SELECT current_week FROM seasons WHERE season_id = ?", (season,)
        ).fetchone()[0]
        print(f"current_week={current_week}\n")

        for name in PLAYER_NAMES:
            row = conn.execute("SELECT player_id FROM players WHERE player_name = ?", (name,)).fetchone()
            if row is None:
                print(f"{name}: not found in this league's player pool (never rostered)")
                continue
            player_id = row[0]
            roster_row = conn.execute(
                """
                SELECT t.team_name, wr.slot_position, wr.is_starter, pws.projected_points
                FROM weekly_rosters wr
                JOIN teams t ON t.id = wr.team_pk
                LEFT JOIN player_week_scores pws
                    ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
                WHERE wr.season_id = ? AND wr.week = ? AND wr.player_id = ?
                """,
                (season, current_week, player_id),
            ).fetchone()
            if roster_row is None:
                print(f"{name}: not currently rostered by any team in this league (free agent)")
            else:
                team_name, slot, is_starter, proj = roster_row
                print(
                    f"{name}: rostered by {team_name!r}, slot={slot}, "
                    f"starter={'yes' if is_starter else 'no'}, week-{current_week} proj={proj}"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
