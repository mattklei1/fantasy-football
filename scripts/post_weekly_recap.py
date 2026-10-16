#!/usr/bin/env python3
"""GitHub Actions entry point: post the full Weekly Recap (the same
HEADLINE/GAME OF THE WEEK/.../NEXT WEEK'S GAME TO WATCH narrative built
in Phase 8 for the Streamlit page) to GroupMe once a real week's games
have finished (Monday Night Football included).

Fires on a loose daily-ish schedule (see .github/workflows/
weekly-recap.yml) rather than targeting one precise Tuesday-morning
window - GitHub Actions' own cron scheduler turned out to be unreliable
for this repo (confirmed via real run history, 2026-10-16): a workflow
can fire hours late or skip days outright regardless of what's
declared, so a narrow time-of-day gate meant the recap silently never
went out some weeks. Correctness now comes from
weekly_recap_tracking.py's durable "last week posted" marker (committed
to main, same pattern as playoff_odds_snapshots.py), not the clock -
whatever day/time GitHub actually runs this, it posts the latest
completed week's recap exactly once. Self-gates BEFORE any expensive
work: one lightweight ESPN call gets the real current week, and if
that week's recap is already marked posted, this exits immediately
without the full temp-DB ingest.

Builds a throwaway SQLite database in a temp directory for this one run
rather than touching data/league.db - same reasoning as the other
scheduled scripts here: this runs from GitHub Actions, a separate
ephemeral environment with no access to (and no dependency on) the
deployed Streamlit app's local disk. The temp DB only needs the current
season's data, so this is a single-season ingest, not the full
historical backfill.

Uses commentary.get_or_generate_weekly_recap() UNCHANGED - Claude if
ANTHROPIC_API_KEY is set, the deterministic placeholder otherwise.

Usage: python scripts/post_weekly_recap.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import commentary, config, db, weekly_recap_tracking
from fantasy_football.espn_client import ESPNClient
from fantasy_football.groupme_client import send_long_message
from fantasy_football.ingest import ingest_season
from fantasy_football.metrics.pipeline import compute_and_store_lineup_efficiency, compute_and_store_season_metrics


def _latest_completed_week(conn: sqlite3.Connection, season: int) -> int | None:
    row = conn.execute(
        "SELECT MAX(week) FROM weekly_team_scores WHERE season_id = ? AND completed = 1 AND is_playoff = 0",
        (season,),
    ).fetchone()
    return row[0] if row else None


def main() -> int:
    bot_id = config.groupme_bot_id()
    if not bot_id:
        print("GROUPME_BOT_ID not set - skipping post.")
        return 0

    client = ESPNClient()
    season = client.credentials.current_season

    # Cheap pre-check before any real ingest work: ESPN's own current_week
    # (one lightweight call) tells us the most this run could possibly
    # have to post - if even that's already marked posted, there's
    # nothing a full ingest could change about that conclusion.
    league = client.get_league(season)
    last_posted = weekly_recap_tracking.load_last_posted_week(season)
    if last_posted is not None and league.current_week - 1 <= last_posted:
        print(f"Week {last_posted} already posted and no later week has finished yet - skipping.")
        return 0

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
        if last_posted is not None and week <= last_posted:
            print(f"Week {week} already posted - skipping.")
            return 0

        result = commentary.get_or_generate_weekly_recap(conn, season, week, log=print, league=league)

    if result is None:
        print(f"No recap available for week {week} - skipping post.")
        return 0

    message = f"WEEK {week} RECAP\n\n" + result["commentary"]
    send_long_message(bot_id, message)
    print(f"Posted weekly recap for week {week} (source={result['source']})")

    marker_result = weekly_recap_tracking.save_last_posted_week(season, week)
    if not marker_result["success"]:
        # The recap already went out - don't fail the workflow over the
        # marker commit, but a failure here means next run would try to
        # re-post the same week, so make it loud in the log.
        print(f"[warn] failed to record week {week} as posted: {marker_result['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
