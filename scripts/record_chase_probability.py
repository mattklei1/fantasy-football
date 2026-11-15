#!/usr/bin/env python3
"""One-off: live % chance that tonight's McConkey Kong vs House of the
Rising Sun God combined score beats each of the all-time regular-season
combined-score records.

Model: combined_final ~ Normal(mean=current live-adjusted projected
combined, stdev=sqrt(team_a_stdev^2 + team_b_stdev^2)), where each
team's stdev is that team's REAL this-season score_stdev (metrics.
playoff_sim.compute_score_stdev - the same per-team week-to-week
scoring variance the Monte Carlo playoff simulator itself uses,
floored at MIN_STDEV). This is a simplification - it doesn't shrink
variance as players finish their games today - so it's a genuinely
conservative (wider-than-reality-right-now) estimate, flagged as such
rather than passed off as more precise than it is.

Read-only - writes nothing, sends nothing.
"""
from __future__ import annotations

import math
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season
from fantasy_football.metrics.loaders import load_weekly_scores
from fantasy_football.metrics.playoff_sim import compute_score_stdev

RECORDS = [346.40, 344.00, 334.50]


def _norm_sf(x: float) -> float:
    """1 - CDF(x) for a standard normal, no scipy dependency needed."""
    return 0.5 * math.erfc(x / math.sqrt(2))


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season
    league = client.get_league(season)
    swid = client.credentials.swid

    my_team = next((t for t in league.teams if any(o.get("id") == swid for o in t.owners)), None)
    week = league.current_week
    box_scores = league.box_scores(week)
    mine = next((bs for bs in box_scores if bs.home_team == my_team or bs.away_team == my_team), None)
    home_name = mine.home_team.team_name
    away_name = mine.away_team.team_name
    live_actual = (mine.home_score or 0) + (mine.away_score or 0)
    live_projected = (mine.home_projected or 0) + (mine.away_projected or 0)
    print(f"Live now: {home_name} {mine.home_score:.2f} - {away_name} {mine.away_score:.2f}")
    print(f"Live projected final combined: {live_projected:.2f}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = sqlite3.connect(db.DB_PATH)
        db.init_db(conn)
        ingest_season(conn, client, season, log=print)

        team_pk_by_name = {
            name: pk for pk, name in conn.execute("SELECT id, team_name FROM teams WHERE season_id = ?", (season,))
        }
        stdev_df = compute_score_stdev(load_weekly_scores(conn, season)).set_index("team_pk")["score_stdev"]
        stdev_home = stdev_df.get(team_pk_by_name[home_name])
        stdev_away = stdev_df.get(team_pk_by_name[away_name])
        combined_stdev = math.sqrt(stdev_home**2 + stdev_away**2)
        print(f"\nReal season score_stdev: {home_name} {stdev_home:.2f}, {away_name} {stdev_away:.2f}")
        print(f"Combined stdev: {combined_stdev:.2f}")

        for record in RECORDS:
            z = (record - live_projected) / combined_stdev
            p_beat = _norm_sf(z)
            print(f"  P(combined final > {record:.2f}) = {p_beat * 100:.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
