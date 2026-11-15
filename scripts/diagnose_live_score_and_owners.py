#!/usr/bin/env python3
"""One-off: two unrelated real-data checks in one dispatch.

1. Per-player breakdown of the live McConkey Kong vs House of the
   Rising Sun God box score, to sanity-check the "319 actual looks
   weird" combined-score reading against the real lineup ESPN is
   scoring right now.
2. Real manager (owner) identity behind each team in the top-3
   all-time regular-season combined-score matchups - team_name is
   NOT a stable identity (renames/relinks happen), so this resolves
   through team_owners/managers the same way ama.py's own schema note
   says to ("manager_name is the stable identity... not team_name").

Read-only - writes nothing, sends nothing.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season

# (season, week, team_name) for each side of the top-3 all-time
# regular-season combined-score matchups found by
# scripts/highest_combined_score.py.
RECORD_TEAMS = [
    (2026, 3, "Hammer Time"),
    (2026, 3, "House of the Rising Sun God"),
    (2024, 14, "The Home Deebo"),
    (2024, 14, "Waddle you Picken? "),
    (2024, 9, "Derelic My Balls"),
    (2024, 9, "Waddle you Picken? "),
]


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season
    league = client.get_league(season)
    swid = client.credentials.swid

    my_team = next((t for t in league.teams if any(o.get("id") == swid for o in t.owners)), None)
    week = league.current_week
    box_scores = league.box_scores(week)
    mine = next((bs for bs in box_scores if bs.home_team == my_team or bs.away_team == my_team), None)

    print("=== Live per-player breakdown ===")
    for side, team, lineup in (("HOME", mine.home_team, mine.home_lineup), ("AWAY", mine.away_team, mine.away_lineup)):
        print(f"\n{side}: {team.team_name}")
        starter_total = 0.0
        for p in lineup:
            flag = "" if p.slot_position not in ("BE", "IR") else "  (bench)"
            print(
                f"  {p.name:<26} slot={p.slot_position:<4} points={p.points:>6.2f} "
                f"proj={p.projected_points:>6.2f} game_played={p.game_played}%{flag}"
            )
            if p.slot_position not in ("BE", "IR"):
                starter_total += p.points
        print(f"  -> sum of starters' real points: {starter_total:.2f}")
    print(f"\nESPN's own home_score/away_score: {mine.home_score:.2f} / {mine.away_score:.2f}")
    print(f"Combined (ESPN's own field): {(mine.home_score or 0) + (mine.away_score or 0):.2f}")

    print("\n\n=== Record matchup owners ===")
    seasons_needed = sorted({s for s, _, _ in RECORD_TEAMS})
    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = sqlite3.connect(db.DB_PATH)
        db.init_db(conn)
        for s in seasons_needed:
            print(f"Ingesting season {s}...")
            ingest_season(conn, client, s, log=print)

        for s, wk, team_name in RECORD_TEAMS:
            rows = conn.execute(
                """
                SELECT m.display_name, m.first_name, m.last_name
                FROM teams t
                JOIN team_owners o ON o.team_pk = t.id
                JOIN managers m ON m.manager_id = o.manager_id
                WHERE t.season_id = ? AND t.team_name = ?
                """,
                (s, team_name),
            ).fetchall()
            names = [r[0] or f"{r[1]} {r[2]}" for r in rows] or ["(no owner found)"]
            print(f"  Season {s} Wk{wk}: {team_name!r} -> {', '.join(names)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
