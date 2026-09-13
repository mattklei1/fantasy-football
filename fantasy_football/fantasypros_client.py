"""Thin wrapper around FantasyPros' licensed API (api.fantasypros.com).

Only used with a real, paid API key (`FANTASYPROS_API_KEY`) - see the
"Data sourcing decision" note in PROJECT_BRIEF.md for why this project
never scraped FantasyPros' public rankings pages (their Terms of Use
explicitly prohibit automated reproduction without a licensed API).
"""
from __future__ import annotations

import requests

BASE_URL = "https://api.fantasypros.com/public/v2/json/nfl"

# The 6 standard roster positions this league uses. FantasyPros has no
# separate list for superflex/OP - those slots are just filled by the best
# available QB/RB/WR/TE, whose rank already comes from their own position's
# list below.
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"]

TIMEOUT_SECONDS = 20


def fetch_ros_rankings(api_key: str, position: str, season: int) -> list[dict]:
    """One position's rest-of-season consensus rankings. Returns the raw
    list of player dicts from FantasyPros (see their API docs for full
    field list - notably player_name, pos_rank e.g. "QB1", rank_ecr,
    r2p_pts, player_team_id)."""
    resp = requests.get(
        f"{BASE_URL}/{season}/consensus-rankings",
        params={"type": "ROS", "position": position},
        headers={"x-api-key": api_key},
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json().get("players", [])


def fetch_all_ros_rankings(api_key: str, season: int) -> dict[str, list[dict]]:
    """All 6 positions' ROS rankings, keyed by position. Raises on the
    first request failure - callers should catch broadly and log, same
    pattern as the rest of ingest.py's per-source error handling."""
    return {position: fetch_ros_rankings(api_key, position, season) for position in POSITIONS}
