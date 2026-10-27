#!/usr/bin/env python3
"""One-off: generate the Weekly Recap and PRINT it, without posting to
GroupMe - for reviewing a draft before scripts/post_weekly_recap.py (or
the scheduled workflow) sends it for real. Same ingest/generation path
as post_weekly_recap.py, just without the send_long_message call or the
weekly_recap_tracking marker write.

Usage: python scripts/draft_weekly_recap.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import commentary, db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season
from fantasy_football.metrics.pipeline import compute_and_store_lineup_efficiency, compute_and_store_season_metrics


def _latest_completed_week(conn: sqlite3.Connection, season: int) -> int | None:
    row = conn.execute(
        "SELECT MAX(week) FROM weekly_team_scores WHERE season_id = ? AND completed = 1 AND is_playoff = 0",
        (season,),
    ).fetchone()
    return row[0] if row else None


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season

    with tempfile.TemporaryDirectory() as tmp_dir:
        conn = sqlite3.connect(Path(tmp_dir) / "league.db")
        db.init_db(conn)
        print("Ingesting...")
        ingest_season(conn, client, season, log=lambda *a: None)
        compute_and_store_season_metrics(conn, season)
        compute_and_store_lineup_efficiency(conn, season)

        week = _latest_completed_week(conn, season)
        print("latest completed week:", week)

        league = client.get_league(season)
        result = commentary.get_or_generate_weekly_recap(conn, season, week, log=print, league=league)

    if result is None:
        print("No recap available.")
        return 0

    message = f"WEEK {week} RECAP\n\n" + result["commentary"]
    print("source:", result["source"])
    print("=====")
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
