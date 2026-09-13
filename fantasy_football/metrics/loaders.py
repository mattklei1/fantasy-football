"""Pull season data out of SQLite into pandas DataFrames for metrics calc."""
from __future__ import annotations

import sqlite3

import pandas as pd


def load_weekly_scores(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    """One row per team per completed REGULAR SEASON week: week, team_pk,
    score. Playoff weeks are deliberately excluded here - see the
    "regular-season-only" note on load_matchups below."""
    return pd.read_sql_query(
        "SELECT week, team_pk, score FROM weekly_team_scores "
        "WHERE season_id = ? AND completed = 1 AND is_playoff = 0 "
        "ORDER BY week, team_pk",
        conn,
        params=(season,),
    )


def load_matchups(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    """One row per completed REGULAR SEASON matchup (real head-to-head
    only, bye weeks never get a matchups row - see ingest.py).

    Metrics (All-Play, Luck, Fraud, Power Score) are scoped to the regular
    season on purpose, confirmed by reconciling against ESPN's own
    team.wins/.losses/.points_for: those stop accumulating at the end of
    the regular season (verified empirically - 2025's week-14 cumulative
    matchup+median record and points_for match ESPN's reported team
    totals exactly, weeks 15-17 are not included in them). Including
    playoff weeks would also mix in an inconsistent field size (the
    playoff bracket shrinks - top seeds get byes, bottom-half teams drop
    into a smaller consolation ladder), which would corrupt All-Play and
    the top-half/median comparison. Playoff RESULTS (bracket outcome,
    championships) are a separate Hall-of-Fame concern for Phase 6, read
    directly off `matchups.matchup_type`/`is_playoff`, not blended into
    this weekly snapshot."""
    return pd.read_sql_query(
        "SELECT week, home_team_pk, away_team_pk, home_score, away_score "
        "FROM matchups WHERE season_id = ? AND completed = 1 AND is_playoff = 0 "
        "ORDER BY week",
        conn,
        params=(season,),
    )


def season_uses_median_scoring(conn: sqlite3.Connection, season: int) -> bool:
    row = conn.execute(
        "SELECT median_scoring FROM seasons WHERE season_id = ?", (season,)
    ).fetchone()
    return bool(row[0]) if row else False


def load_roster_for_week(conn: sqlite3.Connection, season: int, week: int) -> pd.DataFrame:
    """One row per rostered player for Roster Strength: team_pk, player_id,
    position, slot_position, is_starter, projected_points, pos_rank."""
    query = """
        SELECT wr.team_pk, wr.player_id, p.default_position AS position,
               wr.slot_position, wr.is_starter,
               pws.projected_points, pr.pos_rank
        FROM weekly_rosters wr
        JOIN players p ON p.player_id = wr.player_id
        LEFT JOIN player_week_scores pws
            ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
        LEFT JOIN player_rankings pr
            ON pr.season_id = wr.season_id AND pr.week = wr.week AND pr.player_id = wr.player_id
        WHERE wr.season_id = ? AND wr.week = ?
    """
    return pd.read_sql_query(query, conn, params=(season, week))
