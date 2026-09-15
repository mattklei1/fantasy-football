#!/usr/bin/env python3
"""GitHub Actions entry point: post the full Weekly Recap (the same
HEADLINE/GAME OF THE WEEK/.../NEXT WEEK'S GAME TO WATCH narrative built
in Phase 8 for the Streamlit page) to GroupMe every Tuesday morning,
covering the week that just finished (Monday Night Football included).

Builds a throwaway SQLite database in a temp directory for this one run
rather than touching data/league.db - same reasoning as the other
scripts here: this runs from GitHub Actions, a separate ephemeral
environment with no access to (and no dependency on) the deployed
Streamlit app's local disk. The temp DB only needs the current season's
data, so this is a single-season ingest, not the full historical
backfill.

Uses commentary.get_or_generate_weekly_recap() UNCHANGED - Claude if
ANTHROPIC_API_KEY is set, the deterministic placeholder otherwise. Since
this job runs at most once a week by design (the DST-safety window's
extra firings all no-op before reaching this point), there's no
repeat-generation/repeat-spend concern the way there could be on a
page a person revisits.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import commentary, config, db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.groupme_client import send_long_message
from fantasy_football.ingest import ingest_season
from fantasy_football.metrics.pipeline import compute_and_store_lineup_efficiency, compute_and_store_season_metrics
from fantasy_football.schedule_guard import is_target_time_now

DEFAULT_TARGET_HOUR = 6


def _latest_completed_week(conn: sqlite3.Connection, season: int) -> int | None:
    row = conn.execute(
        "SELECT MAX(week) FROM weekly_team_scores WHERE season_id = ? AND completed = 1 AND is_playoff = 0",
        (season,),
    ).fetchone()
    return row[0] if row else None


def main() -> int:
    if not is_target_time_now(DEFAULT_TARGET_HOUR, 0):
        print("Not the target time yet (DST-safety window) - skipping.")
        return 0

    bot_id = config.groupme_bot_id()
    if not bot_id:
        print("GROUPME_BOT_ID not set - skipping post.")
        return 0

    client = ESPNClient()
    season = client.credentials.current_season

    with tempfile.TemporaryDirectory() as tmp_dir:
        conn = sqlite3.connect(Path(tmp_dir) / "league.db")
        db.init_db(conn)
        ingest_season(conn, client, season, log=print)
        compute_and_store_season_metrics(conn, season)
        compute_and_store_lineup_efficiency(conn, season)

        week = _latest_completed_week(conn, season)
        if not week:
            print("No completed regular-season week yet - skipping post.")
            return 0

        league = client.get_league(season)
        result = commentary.get_or_generate_weekly_recap(conn, season, week, log=print, league=league)

    if result is None:
        print(f"No recap available for week {week} - skipping post.")
        return 0

    message = f"WEEK {week} RECAP\n\n" + result["commentary"]
    send_long_message(bot_id, message)
    print(f"Posted weekly recap for week {week} (source={result['source']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
