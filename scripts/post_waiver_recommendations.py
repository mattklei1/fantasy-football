#!/usr/bin/env python3
"""GitHub Actions entry point: post this week's waiver-wire recommendations
to the commissioner's PRIVATE GroupMe bot (not the shared league bot -
these are one manager's own suggested claims, not something the whole
league should see) every Tuesday around 5pm ET, ahead of that night's
waiver processing.

Builds a throwaway SQLite database in a temp directory for this one run
rather than touching data/league.db - same reasoning as the other
scripts here: this runs from GitHub Actions, a separate ephemeral
environment with no access to (and no dependency on) the deployed
Streamlit app's local disk. Reuses war_room_data.get_my_waiver_suggestions()
UNCHANGED (same rank-based suggestion logic the War Room UI uses) by
pointing db.DB_PATH at the throwaway database for this process's
lifetime - db.get_connection() reads that module-level name at call
time, so this cleanly redirects every war_room_data/dashboard_data call
without changing either module.

Usage: python scripts/post_waiver_recommendations.py
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
from fantasy_football.schedule_guard import PACIFIC, is_target_time_now
from fantasy_football.waiver_recommendations_report import build_message

# 5pm ET is always 2pm Pacific - both US Eastern and Pacific observe DST
# on the same calendar days, so the 3-hour offset between them never
# shifts (unlike converting a fixed ET time to a fixed UTC cron time,
# which DOES need the DST-safety-window treatment - see the .github
# workflow for that half of this).
TARGET_HOUR_PACIFIC = 14
TUESDAY = 1  # datetime.weekday(): Monday=0


def _find_my_team(league, swid: str):
    return next((t for t in league.teams if any(o.get("id") == swid for o in t.owners)), None)


def main() -> int:
    now = datetime.datetime.now(PACIFIC)
    if now.weekday() != TUESDAY:
        print(f"Not Tuesday (Pacific weekday={now.weekday()}) - skipping.")
        return 0
    if not is_target_time_now(TARGET_HOUR_PACIFIC, 0):
        print("Not the target time yet (DST-safety window) - skipping.")
        return 0

    bot_id = config.groupme_personal_bot_id()
    if not bot_id:
        print("GROUPME_PERSONAL_BOT_ID not set - skipping post.")
        return 0

    client = ESPNClient()
    season = client.credentials.current_season
    league = client.get_league(season)
    week = league.current_week

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
        my_team_pk = row[0]

        suggestions = wr.get_my_waiver_suggestions(season, my_team_pk, top_n=10)

        budget_remaining = wr.FAAB_BUDGET_TOTAL
        if my_team.acquisition_budget_spent is not None:
            budget_remaining = wr.FAAB_BUDGET_TOTAL - my_team.acquisition_budget_spent

        overlooked: list[dict] = []
        api_key = config.anthropic_api_key()
        if api_key:
            try:
                from fantasy_football.waiver_targets_crossref import (
                    fetch_espn_yahoo_targets,
                    find_overlooked_targets,
                )

                targets = fetch_espn_yahoo_targets(season, week, api_key)
                board = wr.get_waiver_board(season)
                already_suggested = {s["player_id"] for s in suggestions}
                overlooked = find_overlooked_targets(targets, board, already_suggested)
            except Exception as exc:  # noqa: BLE001 - the cross-check is a nice-to-have, never block our own real recommendations
                print(f"[warn] ESPN/Yahoo cross-check failed: {exc}")

    message = build_message(
        suggestions, overlooked, week, budget_remaining, my_team.team_name, league_name=league.settings.name
    )
    send_long_message(bot_id, message)
    print(f"Posted week {week} waiver recommendations for {my_team.team_name} ({len(suggestions)} picks, {len(overlooked)} overlooked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
