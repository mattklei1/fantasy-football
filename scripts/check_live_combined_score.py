#!/usr/bin/env python3
"""One-off: live check - is the current week's matchup for "my team" on
pace to beat the all-time regular-season combined-score record (346.40,
found by scripts/highest_combined_score.py across 2015-2026)?

Uses ESPN's own live box score projections (home_projected/away_
projected - a live-adjusted "projected final" that blends actual points
already scored with projections for players who haven't played yet),
not a static pregame projection.

Read-only - writes nothing, sends nothing.

Usage: python scripts/check_live_combined_score.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football.espn_client import ESPNClient

ALL_TIME_REGULAR_SEASON_RECORD = 346.40  # Season 2026 Week 3: Hammer Time 178.20 vs House of the Rising Sun God 168.20


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season
    league = client.get_league(season)
    swid = client.credentials.swid

    my_team = next((t for t in league.teams if any(o.get("id") == swid for o in t.owners)), None)
    if my_team is None:
        print("Could not resolve 'my team' from the configured SWID.")
        return 1
    print(f"My team: {my_team.team_name} (espn_team_id={my_team.team_id})")

    week = league.current_week
    box_scores = league.box_scores(week)
    mine = next(
        (bs for bs in box_scores if bs.home_team == my_team or bs.away_team == my_team),
        None,
    )
    if mine is None:
        print(f"No matchup found for {my_team.team_name} in week {week}.")
        return 1

    home_name = mine.home_team.team_name if mine.home_team else "BYE"
    away_name = mine.away_team.team_name if mine.away_team else "BYE"
    print(f"\nWeek {week}: {home_name} vs {away_name}")
    print(f"  Live score:      {home_name} {mine.home_score:.2f}  -  {away_name} {mine.away_score:.2f}")
    print(f"  Projected final: {home_name} {mine.home_projected:.2f}  -  {away_name} {mine.away_projected:.2f}")

    live_combined = (mine.home_score or 0) + (mine.away_score or 0)
    projected_combined = (mine.home_projected or 0) + (mine.away_projected or 0)
    print(f"\n  Combined so far:      {live_combined:.2f}")
    print(f"  Combined projected:   {projected_combined:.2f}")
    print(f"  All-time regular-season record: {ALL_TIME_REGULAR_SEASON_RECORD:.2f}")
    print(
        f"  On pace to {'BEAT' if projected_combined > ALL_TIME_REGULAR_SEASON_RECORD else 'fall short of'} "
        f"the record by {abs(projected_combined - ALL_TIME_REGULAR_SEASON_RECORD):.2f} points"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
