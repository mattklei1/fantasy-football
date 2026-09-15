#!/usr/bin/env python3
"""GitHub Actions entry point: capture the Playoff Odds Monte Carlo
simulation results (fantasy_football.playoff_odds_snapshots) that back
the Playoff Odds page's trend-over-time chart - both the latest
completed real week, AND (once, ever, per season) "Week 0" - real
playoff odds computed from Week-1 ROSTER STRENGTH, right after the
draft before any games (user feedback 2026-09-15: "show week 0... after
draft but before week 1 games... calculate using roster strength at
that time"; 2026-09-16: "Rebuild week 0 playoff odds. It should be
roster strength" - see playoff_odds_snapshots.compute_week0_snapshot_
rows()/compute_week0_team_state()).

Before computing Week 0, also does a ONE-TIME ingest of FantasyPros'
real Average Draft Position rankings (ingest_fantasypros_adp_rankings) -
genuine draft-time consensus, distinct from and not the same as their
rest-of-season rankings (confirmed live: FantasyPros has no separate
"draft" ranking type despite what its query params suggest - ADP is the
real, correct signal for this). Best-effort: if FANTASYPROS_API_KEY
isn't configured or the call fails, Roster Strength degrades to its
ESPN-only signal, same as the live Roster Strength page does.

Fires once a day (see .github/workflows/playoff-odds-snapshot.yml).
Self-gates BEFORE any expensive work: one lightweight ESPN call
(client.get_league) gets the real current week, and if BOTH Week 0 and
the latest completed week are already saved, this exits immediately
without doing the full temp-DB ingest + metrics computation - a full
ingest+metrics run here is real work, so most daily firings (every day
except the one that lands after a week rolls over, or the very first
run once Week-1 rosters exist) should cost as little as possible.

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

from fantasy_football import config, db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_fantasypros_adp_rankings, ingest_season
from fantasy_football.metrics.pipeline import compute_and_store_season_metrics


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season
    league = client.get_league(season)
    last_completed_week = league.current_week - 1

    from fantasy_football import playoff_odds_snapshots

    existing = playoff_odds_snapshots.load_snapshots(season)
    need_week0 = "0" not in existing
    need_latest = last_completed_week >= 1 and str(last_completed_week) not in existing

    if not need_week0 and not need_latest:
        print("Nothing new to snapshot (Week 0 and the latest completed week are both already saved) - skipping.")
        return 0

    ok = True
    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = db.get_connection()
        db.init_db(conn)
        ingest_season(conn, client, season, log=print)
        compute_and_store_season_metrics(conn, season)

        if need_week0:
            fp_api_key = config.fantasypros_api_key()
            if fp_api_key:
                try:
                    ingest_fantasypros_adp_rankings(conn, season, fp_api_key, log=print)
                except Exception as exc:  # noqa: BLE001 - Roster Strength degrades gracefully without it
                    print(f"[warn] FantasyPros ADP (Week 0) ingest failed: {exc}")
            week0_rows = playoff_odds_snapshots.compute_week0_snapshot_rows(conn, season)
            if week0_rows:
                result = playoff_odds_snapshots.save_snapshot(season, 0, week0_rows)
                if result["success"]:
                    print(f"Saved Week 0 playoff odds snapshot ({len(week0_rows)} teams)")
                else:
                    print(f"Week 0 snapshot NOT saved: {result['message']}")
                    ok = False
            else:
                print("Week 0 snapshot not computable yet (no Week-1 roster/projection data) - skipping.")

        if need_latest:
            from fantasy_football import dashboard_data as dd

            dd.clear_all_caches()
            df = dd.get_playoff_simulation(season)
            if df.empty:
                print(f"Playoff simulation returned no data for week {last_completed_week} - skipping.")
            else:
                rows = df[
                    ["team_pk", "team_name", "championship_pct", "playoff_pct", "bye_pct", "seed1_pct"]
                ].to_dict("records")
                result = playoff_odds_snapshots.save_snapshot(season, last_completed_week, rows)
                if result["success"]:
                    print(f"Saved week {last_completed_week} playoff odds snapshot ({len(rows)} teams)")
                else:
                    print(f"Week {last_completed_week} snapshot NOT saved: {result['message']}")
                    ok = False

    if not ok:
        print("(one or more snapshots failed to save - not failing the workflow over optional data)")
    return 0  # never fail the whole workflow over an optional data point


if __name__ == "__main__":
    sys.exit(main())
