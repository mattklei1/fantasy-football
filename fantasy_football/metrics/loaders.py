"""Pull season data out of SQLite into pandas DataFrames for metrics calc."""
from __future__ import annotations

import json
import sqlite3

import pandas as pd


def _scope_matchup_type_filter(scope: str) -> str:
    """'regular' = matchup_type NONE only (the metrics/ package default
    everywhere else). 'playoffs' = matchup_type WINNERS_BRACKET only -
    deliberately excludes LOSERS_CONSOLATION_LADDER/
    WINNERS_CONSOLATION_LADDER (consolation games don't have a
    championship on the line) and any bye week (a team with no matchup
    row that week is simply absent from the join, not zero-filled).
    'all' = regular season + true championship playoff weeks, still
    excluding consolation - a team with fewer playoff appearances
    naturally contributes fewer weeks to its own numerator/denominator
    under 'all', which is correct, not a bug to normalize away."""
    if scope == "regular":
        return "'NONE'"
    if scope == "playoffs":
        return "'WINNERS_BRACKET'"
    if scope == "all":
        return "'NONE', 'WINNERS_BRACKET'"
    raise ValueError(f"unknown scope: {scope!r}")


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


def load_matchups_by_scope(conn: sqlite3.Connection, season: int, scope: str = "regular") -> pd.DataFrame:
    """Like load_matchups, but selectable by scope ('regular' matches
    load_matchups exactly; 'playoffs'/'all' add championship-bracket
    weeks - see _scope_matchup_type_filter). Used by the Lineup
    Efficiency page's scope filter; load_matchups stays regular-season-
    only for everything that persists into metrics_weekly."""
    types = _scope_matchup_type_filter(scope)
    return pd.read_sql_query(
        f"SELECT week, home_team_pk, away_team_pk, home_score, away_score "
        f"FROM matchups WHERE season_id = ? AND completed = 1 AND matchup_type IN ({types}) "
        f"ORDER BY week",
        conn,
        params=(season,),
    )


def season_uses_median_scoring(conn: sqlite3.Connection, season: int) -> bool:
    row = conn.execute(
        "SELECT median_scoring FROM seasons WHERE season_id = ?", (season,)
    ).fetchone()
    return bool(row[0]) if row else False


def load_roster_with_points(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    """One row per team-week-player for every completed REGULAR SEASON
    week (consistent scope with the rest of metrics/ - see the
    regular-season-only note on load_matchups) - week, team_pk,
    player_id, points, is_starter, eligible_slots (parsed to a
    frozenset). Only populated for year>=2019 (eligible_slots requires
    box_scores-derived data) and only for players ingested since the
    eligible_slots column was added - older rows will have it as NULL
    and get filtered out (a player who can't legally fill any slot is
    correctly excluded from the optimizer, not a bug)."""
    query = """
        SELECT wr.week, wr.team_pk, wr.player_id, wr.is_starter, wr.eligible_slots,
               COALESCE(pws.points, 0) AS points
        FROM weekly_rosters wr
        JOIN weekly_team_scores wts ON wts.season_id = wr.season_id AND wts.week = wr.week AND wts.team_pk = wr.team_pk
        LEFT JOIN player_week_scores pws
            ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
        WHERE wr.season_id = ? AND wts.completed = 1 AND wts.is_playoff = 0
              AND wr.eligible_slots IS NOT NULL
    """
    df = pd.read_sql_query(query, conn, params=(season,))
    if not df.empty:
        df["eligible_slots"] = df["eligible_slots"].apply(lambda s: frozenset(json.loads(s)))
    return df


def load_roster_with_projections(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    """Like load_roster_with_points, but also carries player_name,
    position, slot_position (the actual ESPN slot a player occupied
    that week - distinct from eligible_slots, the set they COULD have
    filled) and projected_points - for the weekly recap's "smart lineup
    call" award (metrics/weekly_awards.py), which needs to compare a
    started player's actual production against a BENCHED, slot-eligible
    alternative's higher pregame projection. Same completed-regular-
    season-only scope and eligible_slots-required filter as
    load_roster_with_points."""
    query = """
        SELECT wr.week, wr.team_pk, wr.player_id, p.player_name, p.default_position AS position,
               wr.slot_position, wr.is_starter, wr.eligible_slots,
               COALESCE(pws.points, 0) AS points, pws.projected_points
        FROM weekly_rosters wr
        JOIN players p ON p.player_id = wr.player_id
        JOIN weekly_team_scores wts ON wts.season_id = wr.season_id AND wts.week = wr.week AND wts.team_pk = wr.team_pk
        LEFT JOIN player_week_scores pws
            ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
        WHERE wr.season_id = ? AND wts.completed = 1 AND wts.is_playoff = 0
              AND wr.eligible_slots IS NOT NULL
    """
    df = pd.read_sql_query(query, conn, params=(season,))
    if not df.empty:
        df["eligible_slots"] = df["eligible_slots"].apply(lambda s: frozenset(json.loads(s)))
    return df


def load_roster_with_points_by_scope(conn: sqlite3.Connection, season: int, scope: str = "regular") -> pd.DataFrame:
    """Like load_roster_with_points, but selectable by scope. weekly_rosters
    doesn't carry matchup_type directly, so this joins through matchups
    (unpivoted to one row per team-week) to determine it per team-week -
    a team with no matchup row that week (a playoff bye) is simply absent
    from the join, so bye weeks are excluded automatically."""
    types = _scope_matchup_type_filter(scope)
    query = f"""
        WITH team_week_type AS (
            SELECT season_id, week, home_team_pk AS team_pk, matchup_type FROM matchups WHERE completed = 1
            UNION ALL
            SELECT season_id, week, away_team_pk AS team_pk, matchup_type FROM matchups WHERE completed = 1
        )
        SELECT wr.week, wr.team_pk, wr.player_id, wr.is_starter, wr.eligible_slots,
               COALESCE(pws.points, 0) AS points
        FROM weekly_rosters wr
        JOIN team_week_type twt
            ON twt.season_id = wr.season_id AND twt.week = wr.week AND twt.team_pk = wr.team_pk
        LEFT JOIN player_week_scores pws
            ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
        WHERE wr.season_id = ? AND wr.eligible_slots IS NOT NULL
              AND twt.matchup_type IN ({types})
    """
    df = pd.read_sql_query(query, conn, params=(season,))
    if not df.empty:
        df["eligible_slots"] = df["eligible_slots"].apply(lambda s: frozenset(json.loads(s)))
    return df


def load_playoff_sim_state(conn: sqlite3.Connection, season: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Inputs for the Playoff Odds page's Monte Carlo simulation (see
    metrics/playoff_sim.py). Returns (team_state, remaining_matchups):

    team_state: one row per team - season_ppg/last3_ppg/points_for and
    the real matchup+median win/loss/tie record, all taken directly from
    the LATEST completed week's metrics_weekly row (already-validated,
    single source of truth - not recomputed here), plus score_stdev
    freshly computed from raw weekly scores (playoff_sim.
    compute_score_stdev). Empty if no regular-season week has completed
    yet this season (can't project without at least one real data point).

    remaining_matchups: week, home_team_pk, away_team_pk for every
    not-yet-completed regular-season matchup - includes the current
    in-progress week (simulated like any other remaining game, a
    deliberate v1 simplification - see playoff_sim.py)."""
    from .playoff_sim import compute_score_stdev

    latest_week = conn.execute(
        "SELECT MAX(week) FROM metrics_weekly WHERE season_id = ?", (season,)
    ).fetchone()[0]
    if latest_week is None:
        return pd.DataFrame(), pd.DataFrame()

    team_state = pd.read_sql_query(
        """
        SELECT team_pk, ppg AS season_ppg, last3_ppg, points_for,
               matchup_wins, matchup_losses, matchup_ties,
               median_wins, median_losses, median_ties
        FROM metrics_weekly WHERE season_id = ? AND week = ?
        """,
        conn,
        params=(season, latest_week),
    )
    stdev = compute_score_stdev(load_weekly_scores(conn, season))
    team_state = team_state.merge(stdev, on="team_pk", how="left")

    remaining_matchups = pd.read_sql_query(
        "SELECT week, home_team_pk, away_team_pk FROM matchups "
        "WHERE season_id = ? AND completed = 0 AND matchup_type = 'NONE' ORDER BY week",
        conn,
        params=(season,),
    )
    return team_state, remaining_matchups


def load_roster_for_week(conn: sqlite3.Connection, season: int, week: int) -> pd.DataFrame:
    """One row per rostered player for Roster Strength: team_pk, player_id,
    position, slot_position, is_starter, projected_points, fp_pos_rank
    (FantasyPros' ROS positional rank, only present when
    FANTASYPROS_API_KEY is configured - see
    ingest.py:ingest_fantasypros_rankings). Deliberately does NOT include
    ESPN's own season-to-date positional rank (player_rankings.pos_rank)
    - that signal was dropped from Roster Strength's blend 2026-09-15,
    see roster_strength.py's module docstring for why."""
    query = """
        SELECT wr.team_pk, wr.player_id, p.default_position AS position,
               wr.slot_position, wr.is_starter,
               pws.projected_points,
               fpr.pos_rank AS fp_pos_rank
        FROM weekly_rosters wr
        JOIN players p ON p.player_id = wr.player_id
        LEFT JOIN player_week_scores pws
            ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
        LEFT JOIN fantasypros_rankings fpr
            ON fpr.season_id = wr.season_id AND fpr.week = wr.week AND fpr.player_id = wr.player_id
        WHERE wr.season_id = ? AND wr.week = ?
    """
    return pd.read_sql_query(query, conn, params=(season, week))
