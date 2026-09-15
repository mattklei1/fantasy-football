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


def fetch_weekly_rankings(api_key: str, position: str, season: int, week: int) -> list[dict]:
    """One position's weekly consensus rankings for a specific week -
    same shape as fetch_ros_rankings but type=weekly. Used for QB (which
    has no cross-position weekly list - see fetch_weekly_overall_rankings
    - so QB decisions use its own position-scoped rank directly, same as
    ROS's per-position rank is used for anything not needing cross-
    position comparison)."""
    resp = requests.get(
        f"{BASE_URL}/{season}/consensus-rankings",
        params={"type": "weekly", "week": week, "position": position},
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


def fetch_adp_rankings(api_key: str, position: str, season: int) -> list[dict]:
    """One position's Average Draft Position consensus - FantasyPros'
    real, distinct draft-time ranking (NOT rest-of-season). Confirmed
    live (2026-09-16): `type="DRAFT"` and `type="PRESEASON"` are NOT
    real supported values - both return byte-identical responses to an
    outright garbage/invalid `type` string, i.e. the API silently falls
    back to some default list rather than a draft-specific one. `type=
    "ADP"` IS genuinely distinct (different response, same player-list
    schema as ROS/weekly: rank_ecr, pos_rank, tier, etc. - directly
    usable by player_matching.parse_pos_rank() and roster_strength.
    rank_to_score() unchanged). Used for the Week-0 Roster Strength
    snapshot (see ingest.ingest_fantasypros_adp_rankings) - ADP reflects
    draft-day consensus and, unlike ROS, doesn't drift with in-season
    performance, so it's the right signal for "what did we know right
    after the draft" even when fetched well into the season."""
    resp = requests.get(
        f"{BASE_URL}/{season}/consensus-rankings",
        params={"type": "ADP", "position": position},
        headers={"x-api-key": api_key},
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json().get("players", [])


def fetch_all_adp_rankings(api_key: str, season: int) -> dict[str, list[dict]]:
    """All 6 positions' ADP rankings, keyed by position - see
    fetch_adp_rankings()."""
    return {position: fetch_adp_rankings(api_key, position, season) for position in POSITIONS}


def fetch_overall_ros_rankings(api_key: str, season: int) -> list[dict]:
    """The TRUE cross-position rest-of-season ranking (position="ALL"),
    NOT the same as calling fetch_ros_rankings() once per position and
    concatenating - confirmed live (2026-09-14) that each POSITION-
    scoped call's own `rank_ecr` is identical to that player's `pos_rank`
    number (e.g. the K1 kicker's rank_ecr is 1, not his real ~186th
    overall standing) - it's just that position's rank restated, not a
    real cross-position number. This "ALL" call is what actually answers
    "how good is this player relative to the ENTIRE player pool" - e.g.
    the #1 kicker (K1) came back rank_ecr=186 here, correctly behind
    ~185 skill-position players, not tied with the #1 QB/RB. `player_id`
    is confirmed consistent with the per-position calls (same FantasyPros
    player database), so this can be joined against fetch_ros_rankings()
    results by player_id."""
    resp = requests.get(
        f"{BASE_URL}/{season}/consensus-rankings",
        params={"type": "ROS", "position": "ALL"},
        headers={"x-api-key": api_key},
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json().get("players", [])


def fetch_weekly_overall_rankings(api_key: str, season: int, week: int) -> list[dict]:
    """The TRUE cross-position WEEKLY consensus ranking (type=weekly,
    position=ALL) for one specific week - same "position=ALL gives a
    real cross-position order, not positional rank restated" property
    confirmed for ROS rankings (see fetch_overall_ros_rankings), verified
    live for weekly too (2026-09-14): Week 1 2026 had RBs/WRs correctly
    interleaved (Jahmyr Gibbs #1, Ja'Marr Chase #12, etc.), not each
    position's own #1 tied together. This is what drives the lineup-
    optimization feature's weekly "who should start" comparisons -
    chosen over a single named analyst's rankings after confirming (a)
    FantasyPros' `filters` query param for isolating one expert doesn't
    actually work despite being documented (tested live: identical
    results whether filtering to expert 317, a different expert, or no
    filter at all) and (b) Justin Boone (expert_id 317, Yahoo! Sports,
    FantasyPros' #1 overall weekly accuracy ranker as of 2026-09-14)
    isn't even a registered contributor for RB/WR/TE, only QB/K/DST - so
    a broad weekly consensus (which DOES include him for the positions
    he covers) is what's actually usable."""
    resp = requests.get(
        f"{BASE_URL}/{season}/consensus-rankings",
        params={"type": "weekly", "week": week, "position": "ALL"},
        headers={"x-api-key": api_key},
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json().get("players", [])


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
