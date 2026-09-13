"""Read-model queries for the History page - spans ALL seasons (unlike
dashboard_data.py, which is scoped to one season at a time), joining raw
matchup/team data to MANAGER identity (not team_pk, which resets every
season) via team_owners. Streamlit-cached, same convention as
dashboard_data.py.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import db
from . import dashboard_data as dd
from .metrics.history import compute_head_to_head, compute_league_records


def _primary_manager_sql(team_alias: str) -> str:
    return db.primary_owner_join_sql(team_alias)


@st.cache_data(ttl=300)
def get_managers() -> pd.DataFrame:
    """Only PRIMARY manager identities - excludes secondary co-owner
    aliases (see db.primary_owner_join_sql) that would otherwise show up
    as a selectable "manager" that can never actually match a matchup,
    since matchup resolution always uses the primary identity."""
    conn = dd.get_connection()
    return pd.read_sql_query(
        f"SELECT manager_id, display_name FROM managers "
        f"WHERE manager_id IN ({db.primary_manager_ids_sql()}) "
        f"ORDER BY display_name",
        conn,
    )


@st.cache_data(ttl=300)
def get_all_matchups_by_manager() -> pd.DataFrame:
    """One row per completed matchup, ALL seasons, resolved to manager
    identity (not team_pk). Used for head-to-head - includes playoffs,
    since a rivalry's playoff history matters."""
    conn = dd.get_connection()
    query = f"""
        SELECT m.season_id, m.week, m.is_playoff, m.home_score, m.away_score,
               ht_mgr.manager_id AS home_manager_id, ht_mgr.display_name AS home_manager_name,
               at_mgr.manager_id AS away_manager_id, at_mgr.display_name AS away_manager_name
        FROM matchups m
        JOIN teams ht ON ht.id = m.home_team_pk
        JOIN teams at ON at.id = m.away_team_pk
        {_primary_manager_sql('ht')}
        {_primary_manager_sql('at')}
        WHERE m.completed = 1
    """
    return pd.read_sql_query(query, conn)


@st.cache_data(ttl=300)
def get_all_team_weeks() -> pd.DataFrame:
    """One row per team per completed matchup-week (both sides
    unpivoted), ALL seasons - used for league records."""
    conn = dd.get_connection()
    query = """
        SELECT m.season_id, m.week, m.is_playoff,
               ht.id AS team_pk, ht.team_name, m.home_score AS score, m.away_score AS opp_score
        FROM matchups m JOIN teams ht ON ht.id = m.home_team_pk
        WHERE m.completed = 1
        UNION ALL
        SELECT m.season_id, m.week, m.is_playoff,
               at.id AS team_pk, at.team_name, m.away_score AS score, m.home_score AS opp_score
        FROM matchups m JOIN teams at ON at.id = m.away_team_pk
        WHERE m.completed = 1
    """
    return pd.read_sql_query(query, conn)


@st.cache_data(ttl=300)
def get_hall_of_fame() -> pd.DataFrame:
    """One row per manager: championships, finals/playoff appearances,
    career record (ESPN's official combined record, summed across every
    season owned), career points (raw - informational only, NOT a fair
    cross-era ranking basis), and best/worst season by season-relative
    PPG percentile (metrics_weekly.ppg_percentile at each season's final
    week - THIS is the fair, era-normalized comparison, per the
    cross-season design note in ingest.py/season_metrics.py)."""
    conn = dd.get_connection()

    teams_query = f"""
        SELECT t.season_id, t.team_name, t.wins, t.losses, t.ties, t.points_for,
               t.final_standing, t_mgr.manager_id, t_mgr.display_name AS manager_name
        FROM teams t
        {_primary_manager_sql('t')}
        WHERE t_mgr.manager_id IS NOT NULL
    """
    teams = pd.read_sql_query(teams_query, conn)
    if teams.empty:
        return teams

    playoff_seasons = pd.read_sql_query(
        f"""
        SELECT DISTINCT m.season_id, t_mgr.manager_id
        FROM matchups m
        JOIN teams t ON t.id IN (m.home_team_pk, m.away_team_pk)
        {_primary_manager_sql('t')}
        WHERE m.is_playoff = 1 AND t_mgr.manager_id IS NOT NULL
        """,
        conn,
    )
    playoff_counts = playoff_seasons.groupby("manager_id").size().rename("playoff_appearances")

    best_worst = pd.read_sql_query(
        f"""
        SELECT m.season_id, m.week, m.team_pk, m.ppg_percentile, m.ppg, t.team_name,
               t_mgr.manager_id
        FROM metrics_weekly m
        JOIN teams t ON t.id = m.team_pk
        {_primary_manager_sql('t')}
        WHERE m.ppg_percentile IS NOT NULL AND t_mgr.manager_id IS NOT NULL
        """,
        conn,
    )

    agg = teams.groupby(["manager_id", "manager_name"]).agg(
        seasons_played=("season_id", "nunique"),
        championships=("final_standing", lambda s: int((s == 1).sum())),
        finals_appearances=("final_standing", lambda s: int((s <= 2).sum())),
        career_wins=("wins", "sum"),
        career_losses=("losses", "sum"),
        career_ties=("ties", "sum"),
        career_points=("points_for", "sum"),
    ).reset_index()

    agg = agg.merge(playoff_counts, on="manager_id", how="left")
    agg["playoff_appearances"] = agg["playoff_appearances"].fillna(0).astype(int)

    if not best_worst.empty:
        season_end = best_worst.sort_values("week").groupby(["manager_id", "season_id"]).tail(1)
        best_idx = season_end.groupby("manager_id")["ppg_percentile"].idxmax()
        worst_idx = season_end.groupby("manager_id")["ppg_percentile"].idxmin()
        best = season_end.loc[best_idx].set_index("manager_id")
        worst = season_end.loc[worst_idx].set_index("manager_id")
        agg["best_season"] = agg["manager_id"].map(best["season_id"])
        agg["best_season_percentile"] = agg["manager_id"].map(best["ppg_percentile"])
        agg["best_season_team"] = agg["manager_id"].map(best["team_name"])
        agg["worst_season"] = agg["manager_id"].map(worst["season_id"])
        agg["worst_season_percentile"] = agg["manager_id"].map(worst["ppg_percentile"])
        agg["worst_season_team"] = agg["manager_id"].map(worst["team_name"])

    return agg.sort_values("championships", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=300)
def get_league_records() -> dict:
    team_weeks = get_all_team_weeks()
    return compute_league_records(team_weeks)


def get_head_to_head(manager_a: str, manager_b: str) -> dict:
    matchups = get_all_matchups_by_manager()
    return compute_head_to_head(matchups, manager_a, manager_b)
