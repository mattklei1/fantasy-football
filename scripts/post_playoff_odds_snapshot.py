#!/usr/bin/env python3
"""GitHub Actions entry point: capture this week's Playoff Odds Monte
Carlo simulation results (fantasy_football.playoff_odds_snapshots) - the
data behind the Playoff Odds page's trend-over-time chart.

Fires once a day (see .github/workflows/playoff-odds-snapshot.yml).
Self-gates BEFORE any expensive work: one lightweight ESPN call
(client.get_league) gets the real current week, and if that week's
snapshot is already saved, this exits immediately without doing the
full temp-DB ingest + metrics computation - unlike the matchup-snapshot
collector's live-window gate, a full ingest+metrics run here is real
work, so most daily firings (every day except the one that lands after
a week rolls over) should cost as little as possible.

Same throwaway-temp-DB pattern as the other scheduled scripts here (see
post_waiver_recommendations.py's docstring for the full reasoning).

Does NOT post to GroupMe - purely a background data-collection job.

Usage: python scripts/post_playoff_odds_snapshot.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season
from fantasy_football.metrics.pipeline import compute_and_store_season_metrics


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season
    league = client.get_league(season)
    last_completed_week = league.current_week - 1
    if last_completed_week < 1:
        print("No completed regular-season week yet - skipping.")
        return 0

    from fantasy_football import playoff_odds_snapshots

    existing = playoff_odds_snapshots.load_snapshots(season)
    if str(last_completed_week) in existing:
        print(f"Week {last_completed_week} already snapshotted - skipping.")
        return 0

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = db.get_connection()
        db.init_db(conn)
        ingest_season(conn, client, season, log=print)
        compute_and_store_season_metrics(conn, season)

        from fantasy_football import dashboard_data as dd

        dd.clear_all_caches()
        df = dd.get_playoff_simulation(season)
        if df.empty:
            print("Playoff simulation returned no data - skipping.")
            return 0

        rows = df[
            ["team_pk", "team_name", "championship_pct", "playoff_pct", "bye_pct", "seed1_pct"]
        ].to_dict("records")
        result = playoff_odds_snapshots.save_snapshot(season, last_completed_week, rows)

    if result["success"]:
        print(f"Saved week {last_completed_week} playoff odds snapshot ({len(rows)} teams)")
        return 0
    print(f"Snapshot NOT saved: {result['message']}")
    return 0  # never fail the whole workflow over an optional data point


if __name__ == "__main__":
    sys.exit(main())
