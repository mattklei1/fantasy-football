"""Pull season data out of SQLite into pandas DataFrames for metrics calc."""
from __future__ import annotations

import json
import sqlite3

import pandas as pd


def _scope_matchup_type_filter(scope: str) -> str:
    """'regular' = matchup_type NONE only (the metrics/ package default
    everywhere else). 'playoffs' = REAL_PLAYOFF_MATCHUP_TYPES
    (WINNERS_BRACKET + WINNERS_CONSOLATION_LADDER - every team that
    actually qualified for the playoffs, championship path AND the
    placement bracket for a qualified team that lost early) - the SAME
    definition history.py's Playoff Record already uses, so a manager's
    playoff appearance/game count here matches the History page instead
    of silently disagreeing with it (user, 2026-09-16: "the seasons and
    records aren't aligning with the historical playoff records for
    these teams. Different number of games and seasons in the
    playoffs" - this page used to count WINNERS_BRACKET only, a
    narrower "championship path only" definition nothing else in this
    project uses). Still excludes LOSERS_CONSOLATION_LADDER (the
    separate bracket for teams that MISSED the playoffs entirely) and
    any bye week (no matchup row exists for one - a team with no
    matchup row that week is simply absent from the join, not
    zero-filled). 'all' = regular season + those same real playoff
    weeks - a team with fewer playoff appearances naturally contributes
    fewer weeks to its own numerator/denominator under 'all', which is
    correct, not a bug to normalize away."""
    from .history import REAL_PLAYOFF_MATCHUP_TYPES

    if scope == "regular":
        return "'NONE'"
    if scope == "playoffs":
        return ", ".join(f"'{t}'" for t in REAL_PLAYOFF_MATCHUP_TYPES)
    if scope == "all":
        return ", ".join(["'NONE'"] + [f"'{t}'" for t in REAL_PLAYOFF_MATCHUP_TYPES])
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


def load_optimal_lineup_points(
    conn: sqlite3.Connection, season: int, week: int, position_slot_counts: dict
) -> pd.Series:
    """team_pk -> that week's OPTIMAL (eligible-slots-respecting) lineup
    total using ESPN's real point projections for that week - the real
    point-scale "what would a perfect lineup from this roster be
    projected for" anchor used by roster_strength.roster_strength_to_
    points() to give a 0-100 Roster Strength score real point units
    (see playoff_odds_snapshots.compute_week0_team_state() and
    dashboard_data.get_playoff_simulation()). Empty Series if that
    week's roster/projection data isn't available."""
    from .lineup_optimizer import RosterPlayer, optimal_lineup

    rows = pd.read_sql_query(
        """
        SELECT wr.team_pk, wr.player_id, wr.eligible_slots, pws.projected_points
        FROM weekly_rosters wr
        LEFT JOIN player_week_scores pws
            ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
        WHERE wr.season_id = ? AND wr.week = ? AND wr.eligible_slots IS NOT NULL
        """,
        conn, params=(season, week),
    )
    if rows.empty:
        return pd.Series(dtype=float)
    rows["eligible_slots"] = rows["eligible_slots"].apply(lambda s: frozenset(json.loads(s)))
    rows["projected_points"] = rows["projected_points"].fillna(0.0)

    points_by_team: dict[int, float] = {}
    for team_pk, g in rows.groupby("team_pk"):
        players = [
            RosterPlayer(int(r.player_id), float(r.projected_points), r.eligible_slots)
            for r in g.itertuples()
        ]
        optimal_points, _ = optimal_lineup(players, position_slot_counts)
        points_by_team[int(team_pk)] = optimal_points
    return pd.Series(points_by_team)


def load_roster_strength_shrinkage_prior(
    conn: sqlite3.Connection, season: int, week: int, position_slot_counts: dict, reg_season_count: int
) -> pd.Series:
    """team_pk -> that week's Roster Strength (the SAME live signal the
    Roster Strength page shows - real current roster, this week's ESPN
    projection, live FantasyPros ROS rank) remapped to real point units
    via roster_strength.roster_strength_to_points(). Used as playoff_
    sim.simulate_season()'s shrinkage_prior (see dashboard_data.
    get_playoff_simulation()) - a team-specific, more informative prior
    than a flat league average for early-season expected-score shrinkage,
    so a trade/injury/waiver move shows up in the playoff simulation the
    moment it shows up in Roster Strength, not only once enough real
    games accumulate to outweigh a generic average (user, 2026-09-16:
    "Playoff odds should be using roster strength in its simulation for
    future weeks... A higher roster strength for future matchups would
    indicate a higher % chance of winning that matchup"). Empty Series
    if that week's roster/projection data isn't available yet (degrades
    to simulate_season()'s default flat-average prior)."""
    from .roster_strength import compute_player_values, compute_team_roster_strength, roster_strength_to_points

    optimal_points = load_optimal_lineup_points(conn, season, week, position_slot_counts)
    if optimal_points.empty:
        return pd.Series(dtype=float)

    roster_df = load_roster_for_week(conn, season, week)
    valued = compute_player_values(roster_df)
    strength = compute_team_roster_strength(valued, week=week, reg_season_count=reg_season_count).set_index(
        "team_pk"
    )["roster_strength"]

    team_pks = sorted(optimal_points.index)
    optimal_points = optimal_points.reindex(team_pks)
    strength = strength.reindex(team_pks)
    return roster_strength_to_points(strength, optimal_points)


def load_week0_roster_for_strength(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    """Like load_roster_for_week(season, 1), but fp_pos_rank comes from
    the week=0 FantasyPros ADP snapshot (ingest.ingest_fantasypros_adp_
    rankings) instead of week=1's ROS rank - draft-day consensus, not a
    rank that's already drifted forward with in-season performance. Real
    roster composition and ESPN weekly projection still come from the
    real Week-1 data (nothing to draft-day-snapshot there - Week 1's
    roster IS the draft result, and ESPN's Week-1 projection is already
    confirmed frozen at its pregame value). Used only by playoff_odds_
    snapshots.compute_week0_team_state() - see roster_strength.py's
    module docstring for why FantasyPros is 40% of the Roster Strength
    blend and ESPN weekly projection is the other 20%."""
    query = """
        SELECT wr.team_pk, wr.player_id, p.default_position AS position,
               wr.slot_position, wr.is_starter,
               pws.projected_points,
               fpr.pos_rank AS fp_pos_rank
        FROM weekly_rosters wr
        JOIN players p ON p.player_id = wr.player_id
        LEFT JOIN player_week_scores pws
            ON pws.season_id = wr.season_id AND pws.week = 1 AND pws.player_id = wr.player_id
        LEFT JOIN fantasypros_rankings fpr
            ON fpr.season_id = wr.season_id AND fpr.week = 0 AND fpr.player_id = wr.player_id
        WHERE wr.season_id = ? AND wr.week = 1
    """
    return pd.read_sql_query(query, conn, params=(season,))
