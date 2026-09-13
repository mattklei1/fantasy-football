"""SQLite persistence layer. Raw sqlite3 (no ORM) - schema is created
idempotently via CREATE TABLE IF NOT EXISTS, and all writes go through the
generic `upsert` helper so refreshes never create duplicate rows.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

from .config import DB_PATH

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS seasons (
    season_id INTEGER PRIMARY KEY,
    league_id INTEGER NOT NULL,
    league_name TEXT,
    current_week INTEGER,
    reg_season_count INTEGER,
    playoff_team_count INTEGER,
    scoring_type TEXT,
    median_scoring INTEGER NOT NULL DEFAULT 0,
    reception_points REAL,
    position_slot_counts TEXT,
    scoring_format_json TEXT,
    is_current INTEGER NOT NULL DEFAULT 0,
    last_ingested_at TEXT
);

CREATE TABLE IF NOT EXISTS managers (
    manager_id TEXT PRIMARY KEY,
    display_name TEXT,
    first_name TEXT,
    last_name TEXT
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    espn_team_id INTEGER NOT NULL,
    team_name TEXT,
    team_abbrev TEXT,
    division_id INTEGER,
    division_name TEXT,
    wins INTEGER,
    losses INTEGER,
    ties INTEGER,
    points_for REAL,
    points_against REAL,
    standing INTEGER,
    final_standing INTEGER,
    streak_type TEXT,
    streak_length INTEGER,
    UNIQUE(season_id, espn_team_id)
);

CREATE TABLE IF NOT EXISTS team_owners (
    team_pk INTEGER NOT NULL REFERENCES teams(id),
    manager_id TEXT NOT NULL REFERENCES managers(manager_id),
    PRIMARY KEY (team_pk, manager_id)
);

CREATE TABLE IF NOT EXISTS matchups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    home_team_pk INTEGER NOT NULL REFERENCES teams(id),
    away_team_pk INTEGER NOT NULL REFERENCES teams(id),
    home_score REAL,
    away_score REAL,
    is_playoff INTEGER NOT NULL DEFAULT 0,
    matchup_type TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    UNIQUE(season_id, week, home_team_pk, away_team_pk)
);

CREATE TABLE IF NOT EXISTS weekly_team_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    team_pk INTEGER NOT NULL REFERENCES teams(id),
    score REAL,
    projected_score REAL,
    is_playoff INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    UNIQUE(season_id, week, team_pk)
);

CREATE TABLE IF NOT EXISTS players (
    player_id INTEGER PRIMARY KEY,
    player_name TEXT,
    default_position TEXT
);

CREATE TABLE IF NOT EXISTS weekly_rosters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    team_pk INTEGER NOT NULL REFERENCES teams(id),
    player_id INTEGER NOT NULL REFERENCES players(player_id),
    slot_position TEXT,
    pro_team TEXT,
    -- JSON list of slot names this player was legally eligible for THAT
    -- week (e.g. ["RB","RB/WR","RB/WR/TE","OP","BE","IR"]) - needed for
    -- the optimal-lineup solver (Phase 5). Snapshotted per week, not on
    -- the player dimension, since ESPN eligibility can shift mid-season.
    eligible_slots TEXT,
    is_starter INTEGER NOT NULL DEFAULT 0,
    UNIQUE(season_id, week, team_pk, player_id)
);

CREATE TABLE IF NOT EXISTS player_week_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players(player_id),
    points REAL,
    projected_points REAL,
    UNIQUE(season_id, week, player_id)
);

CREATE TABLE IF NOT EXISTS draft_picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    team_pk INTEGER REFERENCES teams(id),
    nominating_team_pk INTEGER REFERENCES teams(id),
    player_id INTEGER,
    player_name TEXT,
    round_num INTEGER,
    round_pick INTEGER,
    overall_pick INTEGER,
    bid_amount INTEGER,
    keeper_status INTEGER NOT NULL DEFAULT 0,
    UNIQUE(season_id, round_num, round_pick)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    activity_date TEXT,
    team_pk INTEGER REFERENCES teams(id),
    action_type TEXT,
    player_id INTEGER,
    player_name TEXT,
    bid_amount INTEGER,
    UNIQUE(season_id, activity_date, team_pk, action_type, player_id)
);

CREATE TABLE IF NOT EXISTS metrics_weekly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    team_pk INTEGER NOT NULL REFERENCES teams(id),
    -- all fields below are SEASON-TO-DATE cumulative THROUGH this week,
    -- so metrics_weekly is a snapshot history (enables rank-movement charts)
    games_played INTEGER,
    points_for REAL,
    points_against REAL,
    ppg REAL,
    last3_ppg REAL,
    ppg_percentile REAL,
    recent_form_percentile REAL,
    -- real head-to-head result vs that week's scheduled opponent
    matchup_wins INTEGER,
    matchup_losses INTEGER,
    matchup_ties INTEGER,
    -- top-half-of-league bonus result (ESPN's WIN_BONUS_TOP_HALF format,
    -- this league since 2025 - see seasons.median_scoring). Computed for
    -- EVERY season regardless of whether that season's rules counted it
    -- (informational "would-be" top-half record for older seasons), but
    -- only folded into actual_win_pct below when median_scoring is on.
    median_wins INTEGER,
    median_losses INTEGER,
    median_ties INTEGER,
    -- ESPN's official displayed record = matchup + median combined when
    -- median_scoring is on, matchup-only otherwise. This is what
    -- determines standings/seeding, so fraud_index and power_score use it.
    actual_win_pct REAL,
    all_play_wins INTEGER,
    all_play_losses INTEGER,
    all_play_ties INTEGER,
    all_play_win_pct REAL,
    -- Luck Wins uses MATCHUP wins only (not the combined record) vs.
    -- all-play expected wins, isolating pure opponent-schedule luck from
    -- the median bonus, which isn't opponent-dependent at all.
    expected_wins REAL,
    luck_wins REAL,
    fraud_index REAL,
    power_score REAL,
    -- Phase 5 (optimal lineup engine). Season-to-date cumulative like
    -- everything else above; only computable for year>=2019 (needs
    -- box_scores-derived per-player eligibility data).
    lineup_efficiency REAL,
    actual_starter_points REAL,
    optimal_starter_points REAL,
    points_left_on_bench REAL,
    -- record recomputed as if every week's lineup had been the optimal
    -- legal one, vs. the opponent's REAL actual score that week
    optimal_wins INTEGER,
    optimal_losses INTEGER,
    optimal_ties INTEGER,
    -- weeks where the optimal lineup would have won but the actual
    -- lineup lost/tied - a loss the manager caused, not bad luck
    manager_caused_losses INTEGER,
    UNIQUE(season_id, week, team_pk)
);

CREATE TABLE IF NOT EXISTS player_rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players(player_id),
    pos_rank INTEGER,
    percent_owned REAL,
    percent_started REAL,
    UNIQUE(season_id, week, player_id)
);

CREATE TABLE IF NOT EXISTS roster_strength_weekly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    team_pk INTEGER NOT NULL REFERENCES teams(id),
    starter_value REAL,
    bench_value REAL,
    starter_weight REAL,
    bench_weight REAL,
    roster_strength REAL,
    UNIQUE(season_id, week, team_pk)
);

CREATE TABLE IF NOT EXISTS refresh_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    seasons_refreshed TEXT,
    detail TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added to tables after they already held real data - CREATE
# TABLE IF NOT EXISTS won't retrofit these onto an existing table, so
# they're applied via ALTER TABLE ... ADD COLUMN (idempotent: skipped if
# already present). New tables get the column for free from SCHEMA_SQL
# above; this only matters for upgrading a database from before the
# column existed.
MIGRATIONS = {
    "weekly_rosters": {"eligible_slots": "TEXT"},
    "metrics_weekly": {
        "optimal_wins": "INTEGER",
        "optimal_losses": "INTEGER",
        "optimal_ties": "INTEGER",
        "manager_caused_losses": "INTEGER",
    },
}


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col_name, col_type in columns.items():
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    _apply_migrations(conn)


def get_team_pk(conn: sqlite3.Connection, season_id: int, espn_team_id: int) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM teams WHERE season_id = ? AND espn_team_id = ?",
        (season_id, espn_team_id),
    ).fetchone()
    return row[0] if row else None


def upsert(
    conn: sqlite3.Connection,
    table: str,
    row: dict,
    conflict_cols: Iterable[str],
    update_cols: Optional[Iterable[str]] = None,
) -> None:
    """Insert `row` into `table`, updating in place on conflict with
    `conflict_cols` (which must match a UNIQUE/PRIMARY KEY constraint).
    This is what keeps refreshes idempotent - re-running ingestion never
    creates duplicate rows, it just overwrites with the latest values.
    """
    cols = list(row.keys())
    conflict_cols = list(conflict_cols)
    if update_cols is None:
        update_cols = [c for c in cols if c not in conflict_cols]
    else:
        update_cols = list(update_cols)

    placeholders = ",".join("?" for _ in cols)
    col_list = ",".join(cols)
    conflict_list = ",".join(conflict_cols)

    if update_cols:
        update_clause = ",".join(f"{c}=excluded.{c}" for c in update_cols)
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict_list}) DO UPDATE SET {update_clause}"
        )
    else:
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict_list}) DO NOTHING"
        )
    conn.execute(sql, [row[c] for c in cols])
