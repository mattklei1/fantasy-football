"""Compute and persist metrics_weekly for a season. Idempotent like
ingestion - re-running overwrites the same rows via db.upsert, never
duplicates."""
from __future__ import annotations

import math
import sqlite3
from typing import Optional

from .. import db
from . import loaders
from .season_metrics import compute_season_metrics

METRICS_WEEKLY_COLUMNS = [
    "games_played", "points_for", "points_against", "ppg", "last3_ppg",
    "ppg_percentile", "recent_form_percentile",
    "matchup_wins", "matchup_losses", "matchup_ties",
    "median_wins", "median_losses", "median_ties",
    "actual_win_pct", "all_play_wins", "all_play_losses", "all_play_ties",
    "all_play_win_pct", "expected_wins", "luck_wins", "fraud_index", "power_score",
]


def _clean(value):
    """SQLite has no NaN - pandas NaN (e.g. from a week-1 rolling stat with
    no prior data) must become NULL, not a value that breaks storage."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def compute_and_store_season_metrics(conn: sqlite3.Connection, season: int) -> int:
    """Returns the number of team-week rows written."""
    scores_df = loaders.load_weekly_scores(conn, season)
    if scores_df.empty:
        return 0
    matchups_df = loaders.load_matchups(conn, season)
    median_scoring = loaders.season_uses_median_scoring(conn, season)

    result = compute_season_metrics(scores_df, matchups_df, median_scoring)

    for _, row in result.iterrows():
        payload = {
            "season_id": season,
            "week": int(row["week"]),
            "team_pk": int(row["team_pk"]),
        }
        for col in METRICS_WEEKLY_COLUMNS:
            payload[col] = _clean(row.get(col))
        db.upsert(conn, "metrics_weekly", payload, conflict_cols=["season_id", "week", "team_pk"])

    conn.commit()
    return len(result)


def compute_and_store_all_seasons(conn: Optional[sqlite3.Connection] = None) -> dict[int, int]:
    own_conn = conn is None
    conn = conn or db.get_connection()
    counts = {}
    for (season,) in conn.execute("SELECT season_id FROM seasons ORDER BY season_id"):
        counts[season] = compute_and_store_season_metrics(conn, season)
    if own_conn:
        conn.close()
    return counts
