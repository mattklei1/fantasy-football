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

-- A real playoff-bracket bye (a REAL_PLAYOFF_MATCHUP_TYPES week - see
-- metrics/history.py - with only one side scheduled) - ESPN's box_scores() returns the
-- other side as None rather than a real matchup, so byes are otherwise
-- invisible: ingest_week_boxscores used to just `continue` past them,
-- meaning nothing in the DB ever recorded who got one. Added 2026-09-16
-- so the History page's Playoff Record can note "(N byes)" alongside a
-- manager's actual playoff win/loss record without counting a bye as a
-- win (user: "dont count it as a W but let them know").
CREATE TABLE IF NOT EXISTS playoff_byes (
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    team_pk INTEGER NOT NULL REFERENCES teams(id),
    matchup_type TEXT,
    PRIMARY KEY (season_id, week, team_pk)
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

-- First-observed projected score per (season, week, team) - a "start of
-- week" baseline. ESPN itself doesn't preserve this: weekly_team_scores.
-- projected_score is overwritten on every refresh as the live game
-- updates it, so without a separate snapshot there's no way to answer
-- "what were we projected for BEFORE this week started." INSERT OR
-- IGNORE means whichever refresh sees a given week first "locks in" its
-- projection permanently - not literally always a Tuesday-morning value
-- if the app's first-ever look at a week happens mid-week, but the
-- earliest this app could have captured it, which is the best available.
CREATE TABLE IF NOT EXISTS matchup_projection_snapshots (
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    team_pk INTEGER NOT NULL REFERENCES teams(id),
    projected_score REAL,
    captured_at TEXT,
    PRIMARY KEY (season_id, week, team_pk)
);

-- One row, updated in place, tracking the last time Roster Strength's
-- inputs (ESPN weekly projection + ESPN player rankings + FantasyPros ROS
-- rankings) were actually refreshed - see schedule_guard.
-- should_refresh_daily(). Deliberately separate from refresh_log:
-- refresh_log tracks the general ESPN data refresh, which can and should
-- happen anytime (scores, standings); Roster Strength is gated to once a
-- day (see should_refresh_daily's own docstring for why this used to be
-- weekly and no longer is) so it doesn't redo the FantasyPros/ESPN rank
-- pull on every page load.
CREATE TABLE IF NOT EXISTS roster_strength_refresh_log (
    season_id INTEGER PRIMARY KEY REFERENCES seasons(season_id),
    refreshed_at TEXT
);

-- Separate from roster_strength_refresh_log above: THIS tracks only the
-- FantasyPros ROS ingestion step specifically, updated only on a
-- genuinely successful fetch+match (not on a skipped/failed attempt),
-- since the Roster Strength page's "As of" date is deliberately anchored
-- to the FantasyPros signal alone (user feedback 2026-09-15: "I want the
-- as of date to really be when the fantasypros rest of season rankings
-- are updated last") - not the shared roster_strength_refresh_log above,
-- which covers the whole daily batch (team info + ESPN rank + ESPN
-- projection too) and could tick forward even if the FantasyPros step
-- itself failed (e.g. no API key configured, or their API erroring).
CREATE TABLE IF NOT EXISTS fantasypros_refresh_log (
    season_id INTEGER PRIMARY KEY REFERENCES seasons(season_id),
    refreshed_at TEXT
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
    -- POINTS-BLIND companion to lineup_efficiency: counts start/sit
    -- decisions right vs. wrong (set overlap of actual vs. optimal
    -- starters), not points. Keeps a single boom/bust bench outlier from
    -- dominating the read on decision quality - see lineup_efficiency.py.
    correct_decisions INTEGER,
    total_decisions INTEGER,
    decision_accuracy REAL,
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

CREATE TABLE IF NOT EXISTS fantasypros_rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players(player_id),
    position TEXT,
    rank_ecr INTEGER,
    pos_rank INTEGER,
    ros_points REAL,
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

CREATE TABLE IF NOT EXISTS weekly_recaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    -- structured facts this recap was generated from (weekly awards,
    -- standings movers, next week's projected game to watch, etc.) -
    -- saved alongside the prose so the exact inputs Claude/the
    -- placeholder saw are always inspectable, not just the output.
    facts_json TEXT NOT NULL,
    commentary_text TEXT NOT NULL,
    source TEXT NOT NULL,  -- 'claude' | 'placeholder' (no API key, or Claude call failed/refused)
    generated_at TEXT NOT NULL,
    UNIQUE(season_id, week)
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
        "correct_decisions": "INTEGER",
        "total_decisions": "INTEGER",
        "decision_accuracy": "REAL",
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
    _create_ama_views(conn)


def _ranked_owners_sql() -> str:
    """Every (team_pk, manager_id) co-ownership, ranked within each team
    by the manager's total tenure (rn=1 is the primary owner). Shared by
    primary_owner_join_sql and primary_manager_ids_sql so the definition
    of "primary" can't drift between them."""
    return """
        SELECT o.team_pk, o.manager_id,
               ROW_NUMBER() OVER (
                   PARTITION BY o.team_pk
                   ORDER BY (SELECT COUNT(*) FROM team_owners o2 WHERE o2.manager_id = o.manager_id) DESC,
                            o.manager_id
               ) AS rn
        FROM team_owners o
    """


def primary_manager_ids_sql() -> str:
    """Query returning the distinct manager_ids that are EVER a primary
    (most-tenured) owner of some team - i.e. the real, de-duplicated
    roster of managers, excluding secondary co-owner aliases like the
    ESPN account-relink duplicates described in primary_owner_join_sql."""
    return f"SELECT DISTINCT manager_id FROM ({_ranked_owners_sql()}) WHERE rn = 1"


def manager_full_name_sql(mgr_alias: str) -> str:
    """SQL expression: `{mgr_alias}`'s real full name (first + last, as
    ESPN has them on file) when available, falling back to `display_name`
    (ESPN's account display name, which is very often just a username,
    e.g. "aaron0044") only when first/last are missing. Used on the
    History page specifically - Hall of Fame and Head-to-Head want real
    names, not usernames."""
    return (
        f"CASE WHEN TRIM(COALESCE({mgr_alias}.first_name, '') || ' ' || COALESCE({mgr_alias}.last_name, '')) != '' "
        f"THEN TRIM({mgr_alias}.first_name || ' ' || {mgr_alias}.last_name) "
        f"ELSE {mgr_alias}.display_name END"
    )


def primary_owner_join_sql(team_alias: str, mgr_alias: Optional[str] = None, owner_alias: Optional[str] = None) -> str:
    """SQL fragment: resolve team `{team_alias}` to its PRIMARY manager -
    the most-tenured co-owner (ranked by total team_owners rows across
    ALL seasons/teams), not an arbitrary alphabetically-first pick.

    Why this matters: for the 2026 season, ESPN listed a freshly
    re-linked account as an ADDITIONAL co-owner alongside 2 managers'
    long-standing member ids (same real people, confirmed by matching
    first/last name - ESPN's member id is not as permanently stable as
    the espn-api docs imply). Picking alphabetically-first among
    co-owners split each person's career history across two separate
    manager identities on the History/Hall of Fame page - found while
    validating Phase 6. Ranking by tenure instead makes this self-
    correcting if it happens again for someone else in a future season.

    Produces joined aliases `{owner_alias}` and `{mgr_alias}` (default
    `{team_alias}_owner` / `{team_alias}_mgr`) - the latter has
    `.manager_id` / `.display_name`."""
    mgr_alias = mgr_alias or f"{team_alias}_mgr"
    owner_alias = owner_alias or f"{team_alias}_owner"
    return f"""
        LEFT JOIN (
            SELECT team_pk, manager_id FROM ({_ranked_owners_sql()}) ranked WHERE rn = 1
        ) {owner_alias} ON {owner_alias}.team_pk = {team_alias}.id
        LEFT JOIN managers {mgr_alias} ON {mgr_alias}.manager_id = {owner_alias}.manager_id
    """


def build_canonical_manager_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Maps every manager_id to one CANONICAL identity - merges manager_
    ids that are really the same real person under a different ESPN
    member id. primary_owner_join_sql (above) already handles this when
    ESPN lists both ids as CO-OWNERS of the same team in the same
    season (ranks by tenure, picks one) - but ESPN can also silently
    re-link an account to a brand-new member id with NO co-owner
    overlap at all, so a later season lists only the new id as sole
    owner while an earlier season's team_owners row still has only the
    old one (found 2026-09-16: "BIR and Friends"' Brent Richwood was
    the sole owner under one member id for 2023, a different member id
    for 2024-2026 - same real person, confirmed by matching first/last
    name, exactly like the co-owner case's own docstring describes, but
    with no season where both ids ever appear together for
    primary_owner_join_sql's ranking to even see). Left unfixed, every
    cross-season aggregation (Hall of Fame, head-to-head, all-time
    lineup efficiency) silently splits one person's career into two
    separate "manager" rows/identities.

    Groups manager_ids by (first_name, last_name), lowercased and
    trimmed - a group only forms when BOTH names are present and
    non-blank, so accounts with no real name on file (username only)
    never get merged on a coincidental blank-string match. Within a
    group, the manager_id with the most total team_owners rows (i.e.
    the most tenured alias, same ranking primary_owner_join_sql already
    uses) is canonical; every id in the group - canonical one included -
    maps to it, so callers can blindly remap every manager_id without a
    membership check first."""
    rows = conn.execute(
        "SELECT manager_id, TRIM(COALESCE(first_name, '')) AS fn, TRIM(COALESCE(last_name, '')) AS ln "
        "FROM managers"
    ).fetchall()
    tenure = dict(conn.execute("SELECT manager_id, COUNT(*) FROM team_owners GROUP BY manager_id").fetchall())

    groups: dict[tuple[str, str], list[str]] = {}
    for manager_id, fn, ln in rows:
        if not fn or not ln:
            continue
        groups.setdefault((fn.lower(), ln.lower()), []).append(manager_id)

    canonical_map: dict[str, str] = {}
    for ids in groups.values():
        canonical = sorted(ids, key=lambda mid: (-tenure.get(mid, 0), mid))[0]
        for manager_id in ids:
            canonical_map[manager_id] = canonical
    return canonical_map


AMA_VIEW_NAMES = [
    "ama_seasons", "ama_teams", "ama_matchups", "ama_standings", "ama_lineup_efficiency",
    "ama_roster_strength", "ama_draft_picks", "ama_transactions", "ama_player_weeks", "ama_weekly_recaps",
]


def _create_ama_views(conn: sqlite3.Connection) -> None:
    """"ama_*" views: the ONLY objects the Ask Me Anything feature's
    Gemini-generated SQL is allowed to read (see fantasy_football/
    ama_query.py's authorizer-based enforcement - this is a curation/
    quality aid, not the security boundary itself; the boundary is the
    authorizer's fantasypros_rankings denylist, which holds regardless
    of what's defined here). Deliberately excludes `fantasypros_rankings`
    entirely - no view here ever references it - and pre-bakes the
    primary-owner manager-identity join so generated SQL doesn't have to
    reconstruct that window-function logic itself.

    Unconditionally DROPs and recreates every view on each call (a view
    holds no data, so this is always safe) rather than using an
    IF-NOT-EXISTS guard on CREATE VIEW - that guard is idempotent for a
    brand-new database but silently keeps a STALE definition in any
    database that already has the view, which is exactly what happened
    here: a real bug fix to these view definitions (raw ESPN
    `display_name` usernames -> real names, 2026-09-13) had no effect at
    all on an existing `data/league.db` until this was changed to always
    recreate."""
    owner_join_home = primary_owner_join_sql("ht", mgr_alias="hmgr", owner_alias="ho")
    owner_join_away = primary_owner_join_sql("at", mgr_alias="amgr", owner_alias="ao")
    owner_join = primary_owner_join_sql("t", mgr_alias="mgr", owner_alias="owner")

    for view_name in AMA_VIEW_NAMES:
        conn.execute(f"DROP VIEW IF EXISTS {view_name}")

    conn.executescript(f"""
        CREATE VIEW ama_seasons AS
        SELECT season_id, league_name, current_week, reg_season_count, playoff_team_count,
               scoring_type, median_scoring, reception_points
        FROM seasons;

        CREATE VIEW ama_teams AS
        SELECT t.id AS team_pk, t.season_id, t.team_name, {manager_full_name_sql('mgr')} AS manager_name
        FROM teams t {owner_join};

        CREATE VIEW ama_matchups AS
        SELECT m.season_id, m.week, m.matchup_type, m.completed, m.is_playoff,
               ht.team_name AS home_team_name, {manager_full_name_sql('hmgr')} AS home_manager_name, m.home_score,
               at.team_name AS away_team_name, {manager_full_name_sql('amgr')} AS away_manager_name, m.away_score
        FROM matchups m
        JOIN teams ht ON ht.id = m.home_team_pk {owner_join_home}
        JOIN teams at ON at.id = m.away_team_pk {owner_join_away};

        CREATE VIEW ama_standings AS
        SELECT mw.season_id, mw.week, t.team_name, {manager_full_name_sql('mgr')} AS manager_name,
               mw.games_played, mw.matchup_wins, mw.matchup_losses, mw.matchup_ties,
               mw.median_wins, mw.median_losses, mw.median_ties, mw.actual_win_pct,
               mw.points_for, mw.points_against, mw.ppg, mw.last3_ppg, mw.ppg_percentile,
               mw.all_play_wins, mw.all_play_losses, mw.all_play_ties, mw.all_play_win_pct,
               mw.expected_wins, mw.luck_wins, mw.fraud_index, mw.power_score
        FROM metrics_weekly mw
        JOIN teams t ON t.id = mw.team_pk {owner_join};

        CREATE VIEW ama_lineup_efficiency AS
        SELECT mw.season_id, mw.week, t.team_name, {manager_full_name_sql('mgr')} AS manager_name,
               mw.lineup_efficiency, mw.actual_starter_points, mw.optimal_starter_points,
               mw.points_left_on_bench, mw.optimal_wins, mw.optimal_losses, mw.optimal_ties,
               mw.manager_caused_losses, mw.correct_decisions, mw.total_decisions, mw.decision_accuracy
        FROM metrics_weekly mw
        JOIN teams t ON t.id = mw.team_pk {owner_join}
        WHERE mw.lineup_efficiency IS NOT NULL;

        CREATE VIEW ama_roster_strength AS
        SELECT rs.season_id, rs.week, t.team_name, {manager_full_name_sql('mgr')} AS manager_name,
               rs.starter_value, rs.bench_value, rs.starter_weight, rs.bench_weight, rs.roster_strength
        FROM roster_strength_weekly rs
        JOIN teams t ON t.id = rs.team_pk {owner_join};

        CREATE VIEW ama_draft_picks AS
        SELECT dp.season_id, t.team_name, {manager_full_name_sql('mgr')} AS manager_name,
               dp.player_name, dp.round_num, dp.round_pick, dp.overall_pick,
               dp.bid_amount, dp.keeper_status
        FROM draft_picks dp
        LEFT JOIN teams t ON t.id = dp.team_pk {owner_join};

        CREATE VIEW ama_transactions AS
        SELECT tx.season_id, tx.activity_date, t.team_name, {manager_full_name_sql('mgr')} AS manager_name,
               tx.action_type, tx.player_name, tx.bid_amount
        FROM transactions tx
        LEFT JOIN teams t ON t.id = tx.team_pk {owner_join};

        CREATE VIEW ama_player_weeks AS
        SELECT wr.season_id, wr.week, t.team_name, {manager_full_name_sql('mgr')} AS manager_name,
               p.player_name, p.default_position, wr.is_starter, wr.slot_position,
               pws.points, pws.projected_points
        FROM weekly_rosters wr
        JOIN teams t ON t.id = wr.team_pk {owner_join}
        JOIN players p ON p.player_id = wr.player_id
        LEFT JOIN player_week_scores pws
            ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id;

        CREATE VIEW ama_weekly_recaps AS
        SELECT season_id, week, commentary_text, source, generated_at
        FROM weekly_recaps;
    """)
    conn.commit()


def get_team_pk(conn: sqlite3.Connection, season_id: int, espn_team_id: int) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM teams WHERE season_id = ? AND espn_team_id = ?",
        (season_id, espn_team_id),
    ).fetchone()
    return row[0] if row else None


def record_projection_snapshot(
    conn: sqlite3.Connection, season_id: int, week: int, team_pk: int, projected_score: Optional[float]
) -> None:
    """No-ops (INSERT OR IGNORE) if a snapshot for this (season, week,
    team) already exists - see matchup_projection_snapshots' schema
    comment for why that's the point."""
    conn.execute(
        "INSERT OR IGNORE INTO matchup_projection_snapshots "
        "(season_id, week, team_pk, projected_score, captured_at) VALUES (?, ?, ?, ?, datetime('now'))",
        (season_id, week, team_pk, projected_score),
    )
    conn.commit()


def get_projection_snapshots(conn: sqlite3.Connection, season_id: int, week: int) -> dict[int, float]:
    rows = conn.execute(
        "SELECT team_pk, projected_score FROM matchup_projection_snapshots WHERE season_id = ? AND week = ?",
        (season_id, week),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def get_roster_strength_last_refreshed(conn: sqlite3.Connection, season_id: int) -> Optional[str]:
    row = conn.execute(
        "SELECT refreshed_at FROM roster_strength_refresh_log WHERE season_id = ?", (season_id,)
    ).fetchone()
    return row[0] if row else None


def record_roster_strength_refresh(conn: sqlite3.Connection, season_id: int) -> None:
    conn.execute(
        "INSERT INTO roster_strength_refresh_log (season_id, refreshed_at) VALUES (?, datetime('now')) "
        "ON CONFLICT(season_id) DO UPDATE SET refreshed_at = excluded.refreshed_at",
        (season_id,),
    )
    conn.commit()


def get_fantasypros_last_refreshed(conn: sqlite3.Connection, season_id: int) -> Optional[str]:
    row = conn.execute(
        "SELECT refreshed_at FROM fantasypros_refresh_log WHERE season_id = ?", (season_id,)
    ).fetchone()
    return row[0] if row else None


def record_fantasypros_refresh(conn: sqlite3.Connection, season_id: int) -> None:
    conn.execute(
        "INSERT INTO fantasypros_refresh_log (season_id, refreshed_at) VALUES (?, datetime('now')) "
        "ON CONFLICT(season_id) DO UPDATE SET refreshed_at = excluded.refreshed_at",
        (season_id,),
    )
    conn.commit()


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
