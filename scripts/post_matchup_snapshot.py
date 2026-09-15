#!/usr/bin/env python3
"""GitHub Actions entry point: capture one live win-probability/
projected-score/median-cutline snapshot for every matchup this week and
append it to this week's snapshot manifest (fantasy_football.
matchup_snapshots) - the data behind the Matchups page's new win-
probability-over-time chart.

Fires every 10 minutes (see .github/workflows/matchup-snapshot.yml) but
self-gates on schedule_guard.is_within_live_window() - real scores only
change during actual NFL broadcast windows, so this no-ops (a few
seconds of runtime, no ESPN/GitHub calls) the rest of the time rather
than collecting a full week of identical pregame readings.

Same throwaway-temp-DB pattern as the other scheduled scripts here (see
post_waiver_recommendations.py's docstring for the full reasoning) -
this runs in a separate, ephemeral GitHub Actions environment with no
access to the deployed app's local disk, so it rebuilds just enough of
this season's data itself.

Does NOT post to GroupMe - purely a background data-collection job.

Usage: python scripts/post_matchup_snapshot.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season
from fantasy_football.schedule_guard import is_within_live_window


def main() -> int:
    if not is_within_live_window():
        print("Outside real NFL game windows - skipping.")
        return 0

    client = ESPNClient()
    season = client.credentials.current_season

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = db.get_connection()
        db.init_db(conn)
        ingest_season(conn, client, season, log=print)

        from fantasy_football import matchup_snapshots

        league = client.get_league(season)
        week = league.current_week
        snapshot = matchup_snapshots.capture_snapshot(season, week)
        if snapshot is None:
            print(f"No live matchups for week {week} yet - skipping.")
            return 0

        result = matchup_snapshots.append_snapshot(season, week, snapshot)

    if result["success"]:
        print(f"Saved week {week} snapshot ({len(snapshot['teams'])} teams) at {snapshot['timestamp']}")
        return 0
    print(f"Snapshot NOT saved: {result['message']}")
    return 0  # never fail the whole workflow over an optional data point


if __name__ == "__main__":
    sys.exit(main())
