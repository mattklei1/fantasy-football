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
from .metrics.history import REAL_PLAYOFF_MATCHUP_TYPES, compute_head_to_head, compute_league_records


def _primary_manager_sql(team_alias: str) -> str:
    return db.primary_owner_join_sql(team_alias)


@st.cache_data(ttl=300)
def _canonical_manager_map() -> dict[str, str]:
    """See db.build_canonical_manager_map - merges manager_ids that are
    the same real person under a different ESPN member id (an account
    re-link with no co-owner overlap for primary_owner_join_sql's own
    ranking to catch), so every function below can blindly remap a raw
    per-season manager_id onto one persistent cross-season identity."""
    return db.build_canonical_manager_map(dd.get_connection())


@st.cache_data(ttl=300)
def _canonical_manager_names() -> dict[str, str]:
    """canonical manager_id -> that manager's OWN display name (from
    ITS OWN managers row specifically, not whichever alias a given
    per-season query happened to join). Remapping manager_id to
    canonical without ALSO normalizing manager_name this way left a
    real bug: two merged aliases whose first/last name differ even
    trivially (found 2026-09-16: "omkar ganesan" vs. "Omkar Ganesan" -
    same real person, different capitalization on file for each of his
    two ESPN member ids) still fracture a groupby(["manager_id",
    "manager_name"]) into two rows despite sharing one manager_id,
    silently splitting that person's stats (e.g. a championship
    recorded on the row groupby happened to undercount)."""
    conn = dd.get_connection()
    canonical_ids = set(_canonical_manager_map().values())
    if not canonical_ids:
        return {}
    placeholders = ",".join("?" * len(canonical_ids))
    rows = conn.execute(
        f"SELECT manager_id, {db.manager_full_name_sql('managers')} FROM managers "
        f"WHERE manager_id IN ({placeholders})",
        tuple(canonical_ids),
    ).fetchall()
    return dict(rows)


@st.cache_data(ttl=300)
def get_primary_manager_id(team_pk: int) -> str | None:
    """Resolve a SEASON-SPECIFIC team_pk to its primary manager's
    persistent identity - used by the Matchups page to look up a
    cross-season head-to-head for two teams that only exist as team_pk
    within one season."""
    conn = dd.get_connection()
    row = conn.execute(
        f"SELECT t_mgr.manager_id FROM teams t {_primary_manager_sql('t')} WHERE t.id = ?",
        (team_pk,),
    ).fetchone()
    if not row:
        return None
    return _canonical_manager_map().get(row[0], row[0])


@st.cache_data(ttl=300)
def get_managers() -> pd.DataFrame:
    """Only PRIMARY manager identities - excludes secondary co-owner
    aliases (see db.primary_owner_join_sql) AND cross-season re-link
    aliases (see db.build_canonical_manager_map) that would otherwise
    show up as a second, selectable "manager" for the same real person
    who can never actually match a matchup played under their OTHER
    alias. Name shown is the real full name when ESPN has it on file,
    not the account display name/username (see db.manager_full_name_sql)."""
    conn = dd.get_connection()
    df = pd.read_sql_query(
        f"SELECT manager_id, {db.manager_full_name_sql('managers')} AS display_name FROM managers "
        f"WHERE manager_id IN ({db.primary_manager_ids_sql()})",
        conn,
    )
    canonical_map = _canonical_manager_map()
    canonical_names = _canonical_manager_names()
    df["manager_id"] = df["manager_id"].map(lambda m: canonical_map.get(m, m))
    # Use the canonical id's OWN name (not whichever alias's name this
    # particular pre-remap row happened to carry) - two merged aliases
    # can differ trivially (e.g. capitalization), which would otherwise
    # leave the dropped duplicate's name showing at random.
    df["display_name"] = df["manager_id"].map(lambda m: canonical_names.get(m, m)).combine_first(df["display_name"])
    df = df.drop_duplicates(subset="manager_id").sort_values("display_name").reset_index(drop=True)
    return df


@st.cache_data(ttl=300)
def get_all_matchups_by_manager() -> pd.DataFrame:
    """One row per completed matchup, ALL seasons, resolved to manager
    identity (not team_pk). Used for head-to-head - includes playoffs,
    since a rivalry's playoff history matters."""
    conn = dd.get_connection()
    query = f"""
        SELECT m.season_id, m.week, m.is_playoff, m.matchup_type, m.home_score, m.away_score,
               ht_mgr.manager_id AS home_manager_id,
               {db.manager_full_name_sql('ht_mgr')} AS home_manager_name,
               at_mgr.manager_id AS away_manager_id,
               {db.manager_full_name_sql('at_mgr')} AS away_manager_name
        FROM matchups m
        JOIN teams ht ON ht.id = m.home_team_pk
        JOIN teams at ON at.id = m.away_team_pk
        {_primary_manager_sql('ht')}
        {_primary_manager_sql('at')}
        WHERE m.completed = 1
    """
    df = pd.read_sql_query(query, conn)
    canonical_map = _canonical_manager_map()
    canonical_names = _canonical_manager_names()
    df["home_manager_id"] = df["home_manager_id"].map(lambda m: canonical_map.get(m, m))
    df["away_manager_id"] = df["away_manager_id"].map(lambda m: canonical_map.get(m, m))
    df["home_manager_name"] = df["home_manager_id"].map(canonical_names).combine_first(df["home_manager_name"])
    df["away_manager_name"] = df["away_manager_id"].map(canonical_names).combine_first(df["away_manager_name"])
    return df


@st.cache_data(ttl=300)
def get_all_team_weeks() -> pd.DataFrame:
    """One row per team per completed matchup-week (both sides
    unpivoted), ALL seasons - used for league records. Includes the
    OPPONENT's team/manager too, so a record like Biggest Blowout can
    show both sides, not just the team that set it.

    Also includes a THIRD source: a playoff-bye week's real score (see
    ingest._ingest_team_week - fixed 2026-09-16, user: "Are you sure
    the most points scored tile is correct? I remember a week I scored
    196 that isn't on here" - a bye team's own score used to never get
    stored anywhere at all, so it could never show up in Highest/Lowest
    Score Ever even after that fix landed, since this function only
    ever read from `matchups`, which structurally has no row for a bye
    - no opponent to pair with). These bye rows get opp_score/opp_
    team_name/opp_manager_name = NULL - correct for Highest/Lowest
    Score Ever (score-only, no opponent needed), and pandas' NaN
    handling in compute_league_records() (idxmax/idxmin skip NaN,
    `< 0`/`> 0` comparisons against NaN are always False) means a bye
    row is automatically and correctly EXCLUDED from every opponent-
    relative record (Biggest Blowout, Closest Game, Most Points in a
    Loss, Lowest Score in a Win) without any extra filtering here."""
    conn = dd.get_connection()
    ht_mgr = db.manager_full_name_sql("ht_mgr")
    at_mgr = db.manager_full_name_sql("at_mgr")
    bye_mgr = db.manager_full_name_sql("t_mgr")
    query = f"""
        SELECT m.season_id, m.week, m.is_playoff,
               ht.id AS team_pk, ht.team_name, {ht_mgr} AS manager_name,
               m.home_score AS score, m.away_score AS opp_score,
               at.team_name AS opp_team_name, {at_mgr} AS opp_manager_name
        FROM matchups m
        JOIN teams ht ON ht.id = m.home_team_pk
        JOIN teams at ON at.id = m.away_team_pk
        {_primary_manager_sql('ht')}
        {_primary_manager_sql('at')}
        WHERE m.completed = 1
        UNION ALL
        SELECT m.season_id, m.week, m.is_playoff,
               at.id AS team_pk, at.team_name, {at_mgr} AS manager_name,
               m.away_score AS score, m.home_score AS opp_score,
               ht.team_name AS opp_team_name, {ht_mgr} AS opp_manager_name
        FROM matchups m
        JOIN teams ht ON ht.id = m.home_team_pk
        JOIN teams at ON at.id = m.away_team_pk
        {_primary_manager_sql('ht')}
        {_primary_manager_sql('at')}
        WHERE m.completed = 1
        UNION ALL
        SELECT wts.season_id, wts.week, wts.is_playoff,
               t.id AS team_pk, t.team_name, {bye_mgr} AS manager_name,
               wts.score AS score, NULL AS opp_score,
               NULL AS opp_team_name, NULL AS opp_manager_name
        FROM weekly_team_scores wts
        JOIN teams t ON t.id = wts.team_pk
        {_primary_manager_sql('t')}
        WHERE wts.completed = 1
          AND NOT EXISTS (
              SELECT 1 FROM matchups m
              WHERE m.season_id = wts.season_id AND m.week = wts.week
                AND (m.home_team_pk = wts.team_pk OR m.away_team_pk = wts.team_pk)
          )
    """
    return pd.read_sql_query(query, conn)


@st.cache_data(ttl=300)
def get_hall_of_fame() -> pd.DataFrame:
    """One row per manager: championships, finals/playoff appearances,
    career record split into regular-season vs. playoff (see
    REAL_PLAYOFF_MATCHUP_TYPES above), career points for/against (raw -
    informational only, NOT a fair cross-era ranking basis - AND
    normalized as each season's percentile-within-field, averaged across
    a career, which IS fair cross-era since every era is only ever
    compared to its own season's field), and best/worst season by
    season-relative PPG percentile (metrics_weekly.ppg_percentile at each
    season's final week - the same cross-season design note in
    ingest.py/season_metrics.py). Also returns `championship_years` (list
    of season_id, sorted), `playoff_byes` (a real playoff-bracket bye -
    NOT counted in playoff_wins/losses/ties, a bye isn't a game played),
    and `last_place_finishes` (regular-season standing only - see the
    dedicated comment above its computation for why that's NOT the same
    as `teams.final_standing`)."""
    conn = dd.get_connection()

    teams_query = f"""
        SELECT t.season_id, t.team_name, t.points_for, t.points_against,
               t.final_standing, t_mgr.manager_id, {db.manager_full_name_sql('t_mgr')} AS manager_name
        FROM teams t
        {_primary_manager_sql('t')}
        WHERE t_mgr.manager_id IS NOT NULL
    """
    teams = pd.read_sql_query(teams_query, conn)
    if teams.empty:
        return teams

    canonical_map = _canonical_manager_map()
    canonical_names = _canonical_manager_names()
    teams["manager_id"] = teams["manager_id"].map(lambda m: canonical_map.get(m, m))
    # manager_name must be normalized to the canonical id's OWN name too -
    # groupby(["manager_id", "manager_name"]) below would otherwise still
    # fracture into two rows for two merged aliases whose name differs
    # even trivially (see _canonical_manager_names' docstring).
    teams["manager_name"] = teams["manager_id"].map(canonical_names).combine_first(teams["manager_name"])

    # Career record, split regular-season vs. playoff, derived directly
    # from actual matchup scores (NOT teams.wins/losses/ties, which is
    # ESPN's own combined season record and doesn't cleanly separate the
    # two) - one row per team per completed matchup, win/loss/tie computed
    # from score vs. opponent score, same approach season_metrics.py uses
    # for a single season.
    team_weeks = pd.read_sql_query(
        f"""
        SELECT m.season_id, m.is_playoff, m.matchup_type, t_mgr.manager_id,
               CASE WHEN t.id = m.home_team_pk THEN m.home_score ELSE m.away_score END AS score,
               CASE WHEN t.id = m.home_team_pk THEN m.away_score ELSE m.home_score END AS opp_score
        FROM matchups m
        JOIN teams t ON t.id IN (m.home_team_pk, m.away_team_pk)
        {_primary_manager_sql('t')}
        WHERE m.completed = 1 AND t_mgr.manager_id IS NOT NULL
        """,
        conn,
    )
    team_weeks["manager_id"] = team_weeks["manager_id"].map(lambda m: canonical_map.get(m, m))
    team_weeks["win"] = (team_weeks["score"] > team_weeks["opp_score"]).astype(int)
    team_weeks["loss"] = (team_weeks["score"] < team_weeks["opp_score"]).astype(int)
    team_weeks["tie"] = (team_weeks["score"] == team_weeks["opp_score"]).astype(int)

    reg_weeks = team_weeks[team_weeks["is_playoff"] == 0]
    playoff_weeks = team_weeks[team_weeks["matchup_type"].isin(REAL_PLAYOFF_MATCHUP_TYPES)]

    reg_record = reg_weeks.groupby("manager_id")[["win", "loss", "tie"]].sum().rename(
        columns={"win": "reg_wins", "loss": "reg_losses", "tie": "reg_ties"}
    )
    playoff_record = playoff_weeks.groupby("manager_id")[["win", "loss", "tie"]].sum().rename(
        columns={"win": "playoff_wins", "loss": "playoff_losses", "tie": "playoff_ties"}
    )

    playoff_counts = (
        playoff_weeks.groupby(["season_id", "manager_id"]).size().reset_index()
        .groupby("manager_id").size().rename("playoff_appearances")
    )

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
    if not best_worst.empty:
        best_worst["manager_id"] = best_worst["manager_id"].map(lambda m: canonical_map.get(m, m))

    # Normalized points for/against: each team-season's percentile WITHIN
    # that season's field (era-normalized, same reasoning as ppg_percentile
    # below), then averaged across every season a manager played - so a
    # manager's normalized score answers "on average, how did my scoring
    # compare to that year's league," not a raw-points era-biased number.
    teams["points_for_pct"] = teams.groupby("season_id")["points_for"].rank(pct=True)
    teams["points_against_pct"] = teams.groupby("season_id")["points_against"].rank(pct=True)

    championship_years = (
        teams[teams["final_standing"] == 1].groupby("manager_id")["season_id"]
        .apply(lambda s: sorted(int(y) for y in s)).rename("championship_years")
    )

    agg = teams.groupby(["manager_id", "manager_name"]).agg(
        seasons_played=("season_id", "nunique"),
        championships=("final_standing", lambda s: int((s == 1).sum())),
        finals_appearances=("final_standing", lambda s: int((s <= 2).sum())),
        career_points_for=("points_for", "sum"),
        career_points_against=("points_against", "sum"),
        career_points_for_pct=("points_for_pct", "mean"),
        career_points_against_pct=("points_against_pct", "mean"),
    ).reset_index()

    agg = agg.merge(reg_record, on="manager_id", how="left")
    agg = agg.merge(playoff_record, on="manager_id", how="left")
    agg = agg.merge(playoff_counts, on="manager_id", how="left")
    agg = agg.merge(championship_years, on="manager_id", how="left")
    for col in (
        "reg_wins", "reg_losses", "reg_ties", "playoff_wins", "playoff_losses", "playoff_ties",
        "playoff_appearances",
    ):
        agg[col] = agg[col].fillna(0).astype(int)
    agg["championship_years"] = agg["championship_years"].apply(lambda v: v if isinstance(v, list) else [])

    # Real playoff-bracket byes (top seed advances without playing a
    # game) - see db.playoff_byes/ingest.py's bye-capture. NOT part of
    # playoff_wins/losses/ties above (a bye isn't a game played), shown
    # alongside the Playoff Record as context only (user, 2026-09-16:
    # "add in parentheses how many byes that team has had as well. dont
    # count it as a W but let them know").
    byes = pd.read_sql_query(
        f"SELECT pb.season_id, pb.week, t_mgr.manager_id FROM playoff_byes pb "
        f"JOIN teams t ON t.id = pb.team_pk {_primary_manager_sql('t')} "
        f"WHERE t_mgr.manager_id IS NOT NULL",
        conn,
    )
    if not byes.empty:
        byes["manager_id"] = byes["manager_id"].map(lambda m: canonical_map.get(m, m))
        bye_counts = byes.groupby("manager_id").size().rename("playoff_byes")
        agg = agg.merge(bye_counts, on="manager_id", how="left")
    else:
        agg["playoff_byes"] = 0
    agg["playoff_byes"] = agg["playoff_byes"].fillna(0).astype(int)

    # Regular-season last-place finishes - ranked by the SAME combined
    # win% + points-for tiebreak real ESPN standings use (metrics_
    # weekly.actual_win_pct, already folds in the median bonus when a
    # season uses it), taken at exactly week = reg_season_count (the
    # final REGULAR season week) - deliberately NOT teams.final_standing
    # (ESPN's post-FULL-season rank, which reflects whoever lost every
    # game in the separate LOSERS_CONSOLATION_LADDER "toilet bowl"
    # bracket, not who was actually worst during the regular season -
    # user, 2026-09-16: "This is based on regular season last place
    # finish. Disregard what happened in the losers consolation
    # bracket"). A season still in progress has no row at week =
    # reg_season_count yet, so it's naturally excluded until it's over.
    last_place_rows = pd.read_sql_query(
        f"""
        SELECT m.season_id, m.actual_win_pct, m.points_for, t_mgr.manager_id
        FROM metrics_weekly m
        JOIN seasons s ON s.season_id = m.season_id
        JOIN teams t ON t.id = m.team_pk
        {_primary_manager_sql('t')}
        WHERE m.week = s.reg_season_count AND t_mgr.manager_id IS NOT NULL
        """,
        conn,
    )
    if not last_place_rows.empty:
        last_place_rows["manager_id"] = last_place_rows["manager_id"].map(lambda m: canonical_map.get(m, m))
        worst_per_season = (
            last_place_rows.sort_values(["actual_win_pct", "points_for"]).groupby("season_id").head(1)
        )
        last_place_counts = worst_per_season.groupby("manager_id").size().rename("last_place_finishes")
        agg = agg.merge(last_place_counts, on="manager_id", how="left")
    else:
        agg["last_place_finishes"] = 0
    agg["last_place_finishes"] = agg["last_place_finishes"].fillna(0).astype(int)

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
    if not team_weeks.empty:
        # Season-relative percentile (same rank-based approach as
        # metrics_weekly.ppg_percentile elsewhere in this project) - lets
        # Highest/Lowest Score Ever be picked by how exceptional a score
        # was WITHIN its own season's field, not raw points, which skews
        # toward high-PPR/high-scoring eras. Computed here (not inside
        # compute_league_records) so that function stays pure/DB-free.
        team_weeks = team_weeks.copy()
        team_weeks["score_percentile"] = team_weeks.groupby("season_id")["score"].rank(pct=True)
    return compute_league_records(team_weeks)


def get_head_to_head(manager_a: str, manager_b: str) -> dict:
    matchups = get_all_matchups_by_manager()
    return compute_head_to_head(matchups, manager_a, manager_b)


@st.cache_data(ttl=300)
def get_all_time_lineup_efficiency(scope: str) -> pd.DataFrame:
    """All-time Lineup Efficiency, aggregated by MANAGER (not team_pk,
    which resets every season) across every season with eligibility data
    (2019+ - see dashboard_data.get_lineup_efficiency_by_scope, which
    this reuses per-season rather than re-deriving the computation).
    Efficiency/Decision Accuracy are recomputed from the SUMMED raw
    totals (actual/optimal, correct/total), not averaged across seasons
    - same as how a single season's own cumulative number works, so a
    heavy-minutes career isn't diluted by a single lopsided season the
    same way a simple average of percentages would."""
    frames = []
    for season in dd.get_available_seasons():
        df = dd.get_lineup_efficiency_by_scope(season, scope)
        if df.empty:
            continue
        df = df.copy()
        df["season_id"] = season
        frames.append(df)
    if not frames:
        return pd.DataFrame()

    all_df = pd.concat(frames, ignore_index=True)
    all_df["manager_id"] = all_df["team_pk"].apply(get_primary_manager_id)
    all_df = all_df[all_df["manager_id"].notna()]
    if all_df.empty:
        return pd.DataFrame()

    name_by_manager_id = dict(zip(get_managers()["manager_id"], get_managers()["display_name"]))
    all_df["manager_name"] = all_df["manager_id"].map(name_by_manager_id)

    agg = all_df.groupby(["manager_id", "manager_name"]).agg(
        seasons_played=("season_id", "nunique"),
        actual_starter_points=("actual_starter_points", "sum"),
        optimal_starter_points=("optimal_starter_points", "sum"),
        points_left_on_bench=("points_left_on_bench", "sum"),
        matchup_wins=("matchup_wins", "sum"),
        matchup_losses=("matchup_losses", "sum"),
        matchup_ties=("matchup_ties", "sum"),
        optimal_wins=("optimal_wins", "sum"),
        optimal_losses=("optimal_losses", "sum"),
        optimal_ties=("optimal_ties", "sum"),
        manager_caused_losses=("manager_caused_losses", "sum"),
        correct_decisions=("correct_decisions", "sum"),
        total_decisions=("total_decisions", "sum"),
    ).reset_index()

    agg["lineup_efficiency"] = agg["actual_starter_points"] / agg["optimal_starter_points"]
    agg["decision_accuracy"] = agg["correct_decisions"] / agg["total_decisions"]
    return agg.sort_values("lineup_efficiency", ascending=False).reset_index(drop=True)
