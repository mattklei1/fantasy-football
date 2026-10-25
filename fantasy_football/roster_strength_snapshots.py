"""Smoothed (EMA) Roster Strength shrinkage prior - dampens day-to-day
Roster Strength churn (FantasyPros ROS rank updates, ESPN weekly
point-projection refreshes) from flowing straight through into playoff
odds, the way it was before. The 2026-10-25 projection-calibration
backtest (scripts/backtest_projection_calibration.py) found that 67% of
real week-to-week playoff-odds swing was explained by this kind of
drift between snapshots, not by that week's actual games - about 2x
more than the games themselves - which reads as noisy/arbitrary to a
league checking in weekly even though it's technically "responding to
real information."

Two separate operations, both needed:
- get_smoothed_prior(): READ-ONLY. Blends today's raw Roster Strength
  with whatever's currently persisted, without writing anything - safe
  to call on every page load / every recap generation (dashboard_data.
  get_playoff_simulation(), commentary._playoff_sim_baseline()).
- update_stored_ema(): the WRITE path. Advances the persisted baseline
  by the same blend, and commits it - called ONCE A DAY, from
  ingest.refresh_daily_data_if_due(), the same cadence Roster
  Strength's other inputs already refresh on. Reads always blend
  against whatever was last PERSISTED (which only moves once/day), so
  a real, lasting roster move (trade, season-ending injury) still
  fully propagates within a few days, while a single day's rank noise
  gets damped rather than publishing straight through.

Same GitHub Contents API commit pattern as playoff_odds_snapshots.py -
one JSON file per season, read/written via github_sync, degrading to
"no stored EMA yet" (falls back to the raw, unsmoothed value) when
GITHUB_TOKEN isn't configured - same tradeoff every other optional
GitHub-durability feature in this project makes.
"""
from __future__ import annotations

import json

import pandas as pd

from . import config

#: How much of each day's fresh, raw value the stored EMA shifts toward
#: - 0.7 means a real, lasting change (trade, injury) is ~90% baked in
#: within about 6 refreshes (days), while a single day's noise gets
#: damped to 30% of its naive effect rather than flowing straight
#: through.
EMA_WEIGHT_CURRENT = 0.7


def _path(season: int) -> str:
    return f"roster_strength_snapshots/{season}.json"


def load_latest_ema(season: int) -> dict[str, float] | None:
    """{team_pk_str: smoothed roster-strength points}, or None if
    nothing's been stored yet (first-ever run this season) or
    GITHUB_TOKEN isn't configured."""
    token = config.github_token()
    if not token:
        return None
    from . import github_sync

    repo = config.github_repo()
    existing = github_sync.get_file_content(repo, token, _path(season), "main")
    if existing is None:
        return None
    try:
        return json.loads(existing[0].decode("utf-8"))
    except json.JSONDecodeError:
        return None


def _blend(raw_by_team: dict[int, float], stored: dict[str, float] | None) -> dict[int, float]:
    if not stored:
        return dict(raw_by_team)
    blended: dict[int, float] = {}
    for team_pk, raw_value in raw_by_team.items():
        prev = stored.get(str(team_pk))
        blended[team_pk] = raw_value if prev is None else EMA_WEIGHT_CURRENT * raw_value + (1 - EMA_WEIGHT_CURRENT) * prev
    return blended


def get_smoothed_prior(
    conn, season: int, week: int, position_slot_counts: dict, reg_season_count: int
) -> pd.Series:
    """Today's real, raw Roster Strength (metrics.loaders.load_roster_
    strength_shrinkage_prior) blended with whatever's currently
    persisted - read-only, writes nothing. Falls back to the raw,
    unsmoothed series when there's no stored EMA yet or GITHUB_TOKEN
    isn't configured. Returns a pd.Series indexed by team_pk, matching
    load_roster_strength_shrinkage_prior's own shape, for drop-in use
    as simulate_season()'s shrinkage_prior."""
    from .metrics import loaders as metric_loaders

    raw = metric_loaders.load_roster_strength_shrinkage_prior(
        conn, season, week, position_slot_counts, reg_season_count
    )
    if raw.empty:
        return raw
    stored = load_latest_ema(season)
    if not stored:
        return raw
    return pd.Series(_blend(raw.to_dict(), stored))


def update_stored_ema(season: int, raw_by_team: dict[int, float]) -> dict:
    """Advances the persisted EMA by blending `raw_by_team` (today's
    real, raw Roster Strength) into whatever's currently stored, and
    commits the result - call this ONCE A DAY (see
    ingest.refresh_daily_data_if_due), not on every read;
    get_smoothed_prior() does the read-time blend separately without
    writing anything. Returns {"success", "message"} - a missing
    GITHUB_TOKEN degrades to a clear no-op message rather than an
    error."""
    token = config.github_token()
    if not token:
        return {"success": False, "message": "GITHUB_TOKEN not configured - EMA not saved."}
    from . import github_sync

    repo = config.github_repo()
    stored = load_latest_ema(season)
    blended = _blend(raw_by_team, stored)

    content_bytes = json.dumps(
        {str(k): v for k, v in blended.items()}, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    result = github_sync.commit_file(
        repo=repo, token=token, path=_path(season), content_bytes=content_bytes,
        message=f"Roster strength EMA update: season {season}", branch="main",
    )
    return {"success": result["success"], "message": result["message"]}
