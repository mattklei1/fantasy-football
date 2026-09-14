#!/usr/bin/env python3
"""GitHub Actions entry point: post the Sunday game-day lineup alert to
the commissioner's PRIVATE GroupMe bot every Sunday around 8:45am
Pacific, ahead of the early kickoff slate - high-priority zero-projected
starters FIRST, non-optimal decisions vs the weekly consensus SECOND
(per explicit ordering request). Not a shared-group post - see
PROJECT_BRIEF (this manager's own lineup calls, not something the whole
league needs to see).

Same throwaway-temp-DB pattern as post_waiver_recommendations.py /
post_lineup_suggestions.py - see either script's docstring for the full
reasoning.

Usage: python scripts/post_lineup_alert.py
"""
from __future__ import annotations

import datetime
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import config, db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.groupme_client import send_long_message
from fantasy_football.ingest import ingest_season
from fantasy_football.lineup_alert_report import build_sunday_message
from fantasy_football.schedule_guard import PACIFIC, is_target_time_now

TARGET_HOUR_PACIFIC = 8
TARGET_MINUTE_PACIFIC = 45
SUNDAY = 6  # datetime.weekday(): Monday=0


def _find_my_team(league, swid: str):
    return next((t for t in league.teams if any(o.get("id") == swid for o in t.owners)), None)


def main() -> int:
    now = datetime.datetime.now(PACIFIC)
    if now.weekday() != SUNDAY:
        print(f"Not Sunday (Pacific weekday={now.weekday()}) - skipping.")
        return 0
    if not is_target_time_now(TARGET_HOUR_PACIFIC, TARGET_MINUTE_PACIFIC):
        print("Not the target time yet (DST-safety window) - skipping.")
        return 0

    bot_id = config.groupme_personal_bot_id()
    if not bot_id:
        print("GROUPME_PERSONAL_BOT_ID not set - skipping post.")
        return 0

    client = ESPNClient()
    season = client.credentials.current_season
    league = client.get_league(season)

    my_team = _find_my_team(league, client.credentials.swid)
    if my_team is None:
        print("Could not find a team owned by this SWID in the live league - skipping.")
        return 0

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = db.get_connection()
        db.init_db(conn)
        ingest_season(conn, client, season, log=print)

        from fantasy_football import war_room_data as wr

        row = conn.execute(
            "SELECT id FROM teams WHERE season_id = ? AND espn_team_id = ?", (season, my_team.team_id)
        ).fetchone()
        if row is None:
            print("My team wasn't ingested into this run's database - skipping.")
            return 0
        team_pk = row[0]

        result = wr.build_ideal_lineup(season, team_pk)

    message = build_sunday_message(result, my_team.team_name)
    send_long_message(bot_id, message)
    print(
        f"Posted week {result['week']} Sunday lineup alert for {my_team.team_name} "
        f"({len(result['zero_projected_starters'])} zero-projected, {len(result['changes'])} non-optimal)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
