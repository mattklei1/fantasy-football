"""Read-model queries for the Streamlit dashboard. This layer is allowed to
depend on Streamlit (st.cache_data) since its only consumer is the
dashboard pages - it is presentation plumbing, not the deterministic
metrics engine (that's fantasy_football/metrics/, which stays framework-
and DB-free so it's unit-testable with mock data).
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd
import streamlit as st

from . import db
from .metrics.win_probability import TeamProjection, expected_score, win_probability


def get_connection() -> sqlite3.Connection:
    """Deliberately NOT cached with st.cache_resource - Streamlit can run
    script reruns on different threads within the same session, and a
    cached sqlite3.Connection is thread-affine (raises
    'SQLite objects created in a thread can only be used in that same
    thread' otherwise). Opening a fresh connection to a local file is
    cheap, so just do that every call."""
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data(ttl=60)
def get_available_seasons() -> list[int]:
    conn = get_connection()
    rows = conn.execute("SELECT season_id FROM seasons ORDER BY season_id DESC").fetchall()
    return [r["season_id"] for r in rows]


@st.cache_data(ttl=60)
def get_season_meta(season: int) -> dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM seasons WHERE season_id = ?", (season,)).fetchone()
    return dict(row) if row else {}


@st.cache_data(ttl=60)
def get_last_refresh() -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM refresh_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else {}


@st.cache_data(ttl=60)
def get_latest_metrics_week(season: int) -> int | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(week) AS w FROM metrics_weekly WHERE season_id = ?", (season,)
    ).fetchone()
    return row["w"] if row and row["w"] is not None else None


def _team_manager_join_sql() -> str:
    """teams -> primary (most-tenured) manager display name. See
    db.primary_owner_join_sql for why this isn't just an arbitrary pick."""
    return db.primary_owner_join_sql("t", mgr_alias="mgr", owner_alias="primary_owner")


@st.cache_data(ttl=60)
def get_standings(season: int, through_week: int | None = None) -> pd.DataFrame:
    """One row per team, metrics as of `through_week` (default: latest
    available), with the prior week's power_score rank for a Δ column."""
    conn = get_connection()
    if through_week is None:
        through_week = get_latest_metrics_week(season)
    if through_week is None:
        return pd.DataFrame()

    query = f"""
        SELECT t.id AS team_pk, t.team_name, mgr.display_name AS manager_name,
               m.week, m.games_played, m.matchup_wins, m.matchup_losses, m.matchup_ties,
               m.median_wins, m.median_losses, m.median_ties,
               m.actual_win_pct, m.points_for, m.points_against, m.ppg, m.last3_ppg,
               m.all_play_wins, m.all_play_losses, m.all_play_ties, m.all_play_win_pct,
               m.expected_wins, m.luck_wins, m.fraud_index, m.power_score
        FROM metrics_weekly m
        JOIN teams t ON t.id = m.team_pk
        {_team_manager_join_sql()}
        WHERE m.season_id = ? AND m.week = ?
    """
    df = pd.read_sql_query(query, conn, params=(season, through_week))
    if df.empty:
        return df

    df["power_rank"] = df["power_score"].rank(ascending=False, method="min").astype(int)

    prev_week = through_week - 1
    if prev_week >= 1:
        prev = pd.read_sql_query(
            "SELECT team_pk, power_score FROM metrics_weekly WHERE season_id = ? AND week = ?",
            conn, params=(season, prev_week),
        )
        if not prev.empty:
            prev["prev_rank"] = prev["power_score"].rank(ascending=False, method="min").astype(int)
            df = df.merge(prev[["team_pk", "prev_rank"]], on="team_pk", how="left")
            df["rank_change"] = df["prev_rank"] - df["power_rank"]
        else:
            df["rank_change"] = None
    else:
        df["rank_change"] = None

    return df.sort_values("power_rank").reset_index(drop=True)


@st.cache_data(ttl=60)
def get_matchups_for_week(season: int, week: int) -> pd.DataFrame:
    conn = get_connection()
    query = f"""
        SELECT mu.week, mu.home_team_pk, mu.away_team_pk, mu.home_score, mu.away_score,
               mu.is_playoff, mu.matchup_type, mu.completed,
               ht.team_name AS home_team_name, at.team_name AS away_team_name
        FROM matchups mu
        JOIN teams ht ON ht.id = mu.home_team_pk
        JOIN teams at ON at.id = mu.away_team_pk
        WHERE mu.season_id = ? AND mu.week = ?
        ORDER BY mu.id
    """
    return pd.read_sql_query(query, conn, params=(season, week))


@st.cache_data(ttl=60)
def get_team_weekly_scores(season: int) -> pd.DataFrame:
    """Regular-season completed scores per team - used for stdev and the
    fun Luck-page callouts (highest score in a loss, etc)."""
    conn = get_connection()
    query = """
        SELECT wts.week, wts.team_pk, wts.score, t.team_name
        FROM weekly_team_scores wts
        JOIN teams t ON t.id = wts.team_pk
        WHERE wts.season_id = ? AND wts.completed = 1 AND wts.is_playoff = 0
    """
    return pd.read_sql_query(query, conn, params=(season,))


@st.cache_data(ttl=60)
def get_regular_season_matchups(season: int) -> pd.DataFrame:
    conn = get_connection()
    query = """
        SELECT mu.week, mu.home_team_pk, mu.away_team_pk, mu.home_score, mu.away_score,
               ht.team_name AS home_team_name, at.team_name AS away_team_name
        FROM matchups mu
        JOIN teams ht ON ht.id = mu.home_team_pk
        JOIN teams at ON at.id = mu.away_team_pk
        WHERE mu.season_id = ? AND mu.completed = 1 AND mu.is_playoff = 0
    """
    return pd.read_sql_query(query, conn, params=(season,))


@st.cache_data(ttl=60)
def get_luck_page_extras(season: int) -> dict:
    """Fun/notable stat callouts for the Luck page, computed from raw
    regular-season data (not stored in metrics_weekly - these are
    one-off season aggregates, not a per-week snapshot series)."""
    from .metrics.season_metrics import compute_matchup_results

    scores = get_team_weekly_scores(season)
    matchups = get_regular_season_matchups(season)
    if scores.empty or matchups.empty:
        return {}

    matchup_results = compute_matchup_results(
        matchups.rename(columns={})[["week", "home_team_pk", "away_team_pk", "home_score", "away_score"]]
    )
    merged = scores.merge(matchup_results, on=["week", "team_pk"], how="left")

    losses = merged[merged["matchup_loss"] == 1]
    wins = merged[merged["matchup_win"] == 1]

    highest_loss = losses.loc[losses["score"].idxmax()] if not losses.empty else None
    lowest_win = wins.loc[wins["score"].idxmin()] if not wins.empty else None

    # top-3 / bottom-3 weekly scores that lost/won, per week
    top3_losses = 0
    bottom3_wins = 0
    for _, g in merged.groupby("week"):
        ranked = g.sort_values("score", ascending=False).reset_index(drop=True)
        n = len(ranked)
        top3 = ranked.iloc[: min(3, n)]
        bottom3 = ranked.iloc[max(0, n - 3):]
        top3_losses += int((top3["matchup_loss"] == 1).sum())
        bottom3_wins += int((bottom3["matchup_win"] == 1).sum())

    return {
        "highest_score_in_loss": highest_loss,
        "lowest_score_in_win": lowest_win,
        "top3_scores_that_lost": top3_losses,
        "bottom3_scores_that_won": bottom3_wins,
    }


@st.cache_data(ttl=60)
def get_lineup_efficiency(season: int, through_week: int | None = None) -> pd.DataFrame:
    conn = get_connection()
    if through_week is None:
        through_week = get_latest_metrics_week(season)
    if through_week is None:
        return pd.DataFrame()
    query = f"""
        SELECT t.id AS team_pk, t.team_name, mgr.display_name AS manager_name,
               m.week, m.games_played,
               m.actual_starter_points, m.optimal_starter_points, m.lineup_efficiency,
               m.points_left_on_bench, m.optimal_wins, m.optimal_losses, m.optimal_ties,
               m.manager_caused_losses, m.matchup_wins, m.matchup_losses, m.matchup_ties,
               m.correct_decisions, m.total_decisions, m.decision_accuracy
        FROM metrics_weekly m
        JOIN teams t ON t.id = m.team_pk
        {_team_manager_join_sql()}
        WHERE m.season_id = ? AND m.week = ? AND m.lineup_efficiency IS NOT NULL
        ORDER BY m.lineup_efficiency DESC
    """
    return pd.read_sql_query(query, conn, params=(season, through_week))


@st.cache_data(ttl=60)
def get_lineup_efficiency_by_scope(season: int, scope: str) -> pd.DataFrame:
    """Regular season reuses the persisted metrics_weekly data via
    get_lineup_efficiency. Playoffs/all are computed live (see
    metrics/loaders.py's scope-aware loaders) - cheap enough for one
    season, and keeps metrics_weekly's stored meaning ("regular season
    season-to-date") from getting muddied by a scope that isn't that.
    Returns the FINAL cumulative row per team for the selected scope."""
    if scope == "regular":
        return get_lineup_efficiency(season)

    from .metrics import loaders as metric_loaders
    from .metrics.lineup_efficiency import compute_lineup_efficiency

    conn = get_connection()
    roster_df = metric_loaders.load_roster_with_points_by_scope(conn, season, scope=scope)
    if roster_df.empty:
        return pd.DataFrame()
    matchups_df = metric_loaders.load_matchups_by_scope(conn, season, scope=scope)
    row = conn.execute("SELECT position_slot_counts FROM seasons WHERE season_id = ?", (season,)).fetchone()
    if not row or not row[0]:
        return pd.DataFrame()
    position_slot_counts = json.loads(row[0])

    result = compute_lineup_efficiency(roster_df, matchups_df, position_slot_counts)
    if result.empty:
        return result

    final = result.sort_values("week").groupby("team_pk").tail(1).reset_index(drop=True)

    teams_query = f"""
        SELECT t.id AS team_pk, t.team_name, mgr.display_name AS manager_name
        FROM teams t {_team_manager_join_sql()} WHERE t.season_id = ?
    """
    teams_df = pd.read_sql_query(teams_query, conn, params=(season,))
    merged = final.merge(teams_df, on="team_pk", how="left")
    return merged.sort_values("lineup_efficiency", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=60)
def get_roster_strength(season: int) -> pd.DataFrame:
    """Roster Strength is only ever computed for the current week of the
    current season (ESPN's posRank has no history to backfill) - one row
    per team, or empty if it hasn't been computed yet this season."""
    conn = get_connection()
    query = f"""
        SELECT rs.week, t.id AS team_pk, t.team_name, mgr.display_name AS manager_name,
               rs.starter_value, rs.bench_value, rs.starter_weight, rs.bench_weight, rs.roster_strength
        FROM roster_strength_weekly rs
        JOIN teams t ON t.id = rs.team_pk
        {_team_manager_join_sql()}
        WHERE rs.season_id = ?
        ORDER BY rs.roster_strength DESC
    """
    return pd.read_sql_query(query, conn, params=(season,))


@st.cache_data(ttl=300)
def get_playoff_simulation(season: int) -> pd.DataFrame:
    """Monte Carlo playoff odds (see metrics/playoff_sim.py) - one row
    per team: playoff_pct, bye_pct, seed1_pct, championship_pct. Empty if
    no regular-season week has completed yet this season, or if this
    season's playoff_team_count isn't the 6-team/top-2-bye format the
    simulator supports (every season this league has ever run, but
    checked explicitly rather than assumed). A longer cache TTL than the
    rest of this module (5 min vs 60s) since 10,000 simulated seasons is
    real compute, not a cheap query - the odds don't need to be
    second-fresh anyway."""
    from .metrics import loaders as metric_loaders
    from .metrics.playoff_sim import simulate_season

    meta = get_season_meta(season)
    if not meta or meta.get("playoff_team_count") != 6:
        return pd.DataFrame()

    team_state, remaining_matchups = metric_loaders.load_playoff_sim_state(get_connection(), season)
    if team_state.empty:
        return pd.DataFrame()

    result = simulate_season(
        team_state,
        remaining_matchups,
        median_scoring=bool(meta.get("median_scoring")),
        reg_season_count=meta["reg_season_count"],
    )

    teams_query = f"""
        SELECT t.id AS team_pk, t.team_name, mgr.display_name AS manager_name
        FROM teams t {_team_manager_join_sql()} WHERE t.season_id = ?
    """
    teams_df = pd.read_sql_query(teams_query, get_connection(), params=(season,))
    merged = result.merge(teams_df, on="team_pk", how="left")
    return merged.sort_values("championship_pct", ascending=False).reset_index(drop=True)


def team_stdev_map(season: int) -> dict[int, float]:
    scores = get_team_weekly_scores(season)
    if scores.empty:
        return {}
    return scores.groupby("team_pk")["score"].std(ddof=1).fillna(0).to_dict()


def project_matchup_win_probability(
    season: int, home_team_pk: int, away_team_pk: int, as_of_week: int
) -> float | None:
    """Custom projection (NOT ESPN's) for a not-yet-played matchup, using
    each team's metrics snapshot as of the last completed week."""
    conn = get_connection()
    stdevs = team_stdev_map(season)
    rows = conn.execute(
        "SELECT team_pk, ppg, last3_ppg FROM metrics_weekly WHERE season_id = ? AND week = ? "
        "AND team_pk IN (?, ?)",
        (season, as_of_week, home_team_pk, away_team_pk),
    ).fetchall()
    by_team = {r["team_pk"]: r for r in rows}
    if home_team_pk not in by_team or away_team_pk not in by_team:
        return None

    home_row, away_row = by_team[home_team_pk], by_team[away_team_pk]
    home_proj = TeamProjection(
        team_pk=home_team_pk,
        expected_score=expected_score(home_row["ppg"], home_row["last3_ppg"]),
        stdev=stdevs.get(home_team_pk, 0.0),
    )
    away_proj = TeamProjection(
        team_pk=away_team_pk,
        expected_score=expected_score(away_row["ppg"], away_row["last3_ppg"]),
        stdev=stdevs.get(away_team_pk, 0.0),
    )
    return win_probability(home_proj, away_proj)


def clear_all_caches() -> None:
    st.cache_data.clear()
