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


def fetch_player_espn_id_map(api_key: str) -> dict[int, int]:
    """FantasyPros' OWN FantasyPros-player-id -> ESPN-player-id cross-
    reference (they support importing/syncing ESPN leagues directly, so
    they maintain this mapping themselves - authoritative, not a
    reconstruction of it). Confirmed 2026-09-13 via their public v2 API
    docs (api.fantasypros.com/public/v2/docs): GET /{sport}/players takes
    an `external_ids` query param (colon-delimited, e.g. "espn" or
    "yahoo:espn:cbs") that adds an `espn_id` field to each player object.
    One call covers the whole player pool (~8500 players across all
    sports/positions, ~3800 with an espn_id populated - players with no
    professional relevance today, e.g. long-retired players, don't have
    one). Prefer this over name/team matching (player_matching.py)
    whenever it has an entry - see match_players_for_position()."""
    resp = requests.get(
        f"{BASE_URL}/players",
        headers={"x-api-key": api_key},
        params={"external_ids": "espn"},
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    result = {}
    for p in resp.json().get("players", []):
        espn_id = p.get("espn_id")
        if not espn_id:
            continue
        try:
            result[p["player_id"]] = int(espn_id)
        except (TypeError, ValueError):
            continue
    return result
