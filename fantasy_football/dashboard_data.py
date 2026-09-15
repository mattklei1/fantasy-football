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
from .metrics.cutline_sim import TeamScoreModel, percentile_range, simulate_cutline_probabilities
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
    """teams -> primary (most-tenured) manager. See db.primary_owner_join_sql
    for why this isn't just an arbitrary pick. Callers must select
    `{_manager_name_sql()} AS manager_name`, NOT `mgr.display_name` - the
    latter is ESPN's raw account display name/username (e.g.
    "aaron0044"), not a real name; see _manager_name_sql()."""
    return db.primary_owner_join_sql("t", mgr_alias="mgr", owner_alias="primary_owner")


def _manager_name_sql() -> str:
    """Real full name (first+last) when available, falling back to the
    ESPN display name/username otherwise - same expression History/Ask
    Me Anything already use (db.manager_full_name_sql), now shared here
    too. A real bug found 2026-09-13 via mobile review: every query below
    was previously selecting the joined manager's raw `.display_name`
    directly (e.g. "Tmoskowitz007", "jeffreydriscoll") instead of this,
    so Home/Luck/Roster Strength/Lineup Efficiency/Playoff Odds showed
    ESPN usernames while History/Matchups/Ask Me Anything (which built
    their own separate real-name lookups) correctly showed real names -
    an inconsistency nobody had normalized before."""
    return db.manager_full_name_sql("mgr")


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
        SELECT t.id AS team_pk, t.team_name, {_manager_name_sql()} AS manager_name,
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

    # rank_change is movement SINCE WEEK 1 (season-long trajectory: "rose
    # from 8th to 2nd"), not week-over-week - a deliberate choice (2026-09-
    # 13) so the Home page's Δ tells a season-arc story rather than just
    # noisy single-week wobble. Week-over-week movement still exists
    # separately for the Weekly Recap narrative (see commentary.py, which
    # intentionally keeps its own week-over-week comparison - that's about
    # "what happened this week," a different question from this one).
    if through_week > 1:
        week1 = pd.read_sql_query(
            "SELECT team_pk, power_score FROM metrics_weekly WHERE season_id = ? AND week = 1",
            conn, params=(season,),
        )
        if not week1.empty:
            week1["week1_rank"] = week1["power_score"].rank(ascending=False, method="min").astype(int)
            df = df.merge(week1[["team_pk", "week1_rank"]], on="team_pk", how="left")
            df["rank_change"] = df["week1_rank"] - df["power_rank"]
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
        SELECT t.id AS team_pk, t.team_name, {_manager_name_sql()} AS manager_name,
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
        SELECT t.id AS team_pk, t.team_name, {_manager_name_sql()} AS manager_name
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
        SELECT rs.week, t.id AS team_pk, t.team_name, {_manager_name_sql()} AS manager_name,
               rs.starter_value, rs.bench_value, rs.starter_weight, rs.bench_weight, rs.roster_strength
        FROM roster_strength_weekly rs
        JOIN teams t ON t.id = rs.team_pk
        {_team_manager_join_sql()}
        WHERE rs.season_id = ?
        ORDER BY rs.roster_strength DESC
    """
    return pd.read_sql_query(query, conn, params=(season,))


@st.cache_data(ttl=60)
def get_fantasypros_last_refreshed(season: int) -> str | None:
    """UTC timestamp string (SQLite datetime('now') format) of the last
    SUCCESSFUL FantasyPros ROS ingestion for this season - see
    db.record_fantasypros_refresh(). Deliberately separate from the
    broader roster-strength refresh timestamp: the Roster Strength page's
    "As of" date is anchored specifically to this, its single largest
    (40/75 weight) and only genuinely forward-looking signal."""
    return db.get_fantasypros_last_refreshed(get_connection(), season)


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
        SELECT t.id AS team_pk, t.team_name, {_manager_name_sql()} AS manager_name
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


@st.cache_data(ttl=60)
def get_live_box_scores(season: int, week: int) -> pd.DataFrame:
    """Live ESPN box scores for a week - unlike the rest of this module,
    this hits ESPN directly (cached briefly, 60s) rather than reading the
    local DB, since the DB only refreshes on a restart or a manual
    button-click and this is what gives the Matchups page a genuinely
    "right now" score/projection during live Sunday games. Also records
    each team's CURRENT projected score as that week's "start of week"
    snapshot the first time it's ever seen (see
    matchup_projection_snapshots' schema comment) - a side effect inside
    a cached read, same pattern ensure_data_bootstrapped() already uses
    elsewhere in this app.

    espn_api's BoxScore has no separate "pre-week" vs "live" projected
    field - home_projected/away_projected is ONE number that's the summed
    starter projections before kickoff and ESPN's live-updating total
    once games start (confirmed against the installed espn_api source,
    2026-09-13) - which is exactly why the snapshot table above exists."""
    from .league_context import get_active_espn_client

    client = get_active_espn_client()
    league = client.get_league(season)
    conn = get_connection()
    rows = []
    for bs in league.box_scores(week):
        if bs.home_team is None or bs.away_team is None:
            continue  # playoff bye - no real second team
        home_pk = db.get_team_pk(conn, season, bs.home_team.team_id)
        away_pk = db.get_team_pk(conn, season, bs.away_team.team_id)
        if home_pk is None or away_pk is None:
            continue
        home_proj, away_proj = bs.home_projected or 0.0, bs.away_projected or 0.0
        db.record_projection_snapshot(conn, season, week, home_pk, home_proj)
        db.record_projection_snapshot(conn, season, week, away_pk, away_proj)
        rows.append(
            {
                "home_team_pk": home_pk, "away_team_pk": away_pk,
                "home_score": bs.home_score or 0.0, "away_score": bs.away_score or 0.0,
                "home_projected": home_proj, "away_projected": away_proj,
            }
        )
    return pd.DataFrame(rows)


def get_projection_snapshots(season: int, week: int) -> dict[int, float]:
    return db.get_projection_snapshots(get_connection(), season, week)


#: Fallback scoring stdev for a team with no observed games yet this
#: season (week 1, or a brand-new season). Originally a separate constant
#: from win_probability.MIN_STDEV on purpose (that floor was 5.0 at the
#: time - calibrated, wrongly, for a team with 1 real game played, not
#: zero - and using it here produced near-certain 0%/100% probabilities;
#: real full-lineup projected-score gaps between two random teams are
#: routinely 20-40+ points, checked against this league's real 2025
#: team-level score stdevs, ~14-28 with a ~22 average). MIN_STDEV has
#: since been raised to 20.0 for the same reason (2026-09-15, see its own
#: docstring) - the two constants now happen to agree, kept separate here
#: since they still answer conceptually different questions (a team with
#: literally zero games this season, vs. the shared small-sample floor).
LIVE_PROB_FALLBACK_STDEV = 20.0

#: Never let a live in-progress stdev collapse all the way to 0 while
#: there's still SOME real fraction of the lineup left to play - real
#: weeks still see stat corrections/unexpected late adjustments, and a
#: near-zero-width distribution for a not-quite-finished team would make
#: win probability snap to an overconfident near-0%/100%. Deliberately
#: does NOT apply once a team's week is fully over (fraction_remaining
#: exactly 0, i.e. score_so_far has caught up to projected because
#: nobody's left to play) - see _live_remaining_stdev's own early return
#: for that case, a real zero-uncertainty state, not one that needs
#: padding (user feedback 2026-09-15: "there are some teams that have
#: had all their players play, but their range... is different from
#: their actual score - when their players have all played, the range
#: should be zero").
MIN_REMAINING_STDEV_FRACTION = 0.15


def _live_remaining_stdev(full_stdev: float, projected: float, score_so_far: float) -> float:
    """Scales a team's FULL-GAME season stdev down to the uncertainty
    actually still remaining once part of the lineup has already played.
    Using the season's full-game stdev for a team's ENTIRE live
    distribution regardless of how much of the week is already decided
    badly overstates remaining variance once most of a lineup has
    finished - e.g. a team sitting on 117 real points with just a K left
    to play does NOT have a real 10% chance of finishing 25+ points
    below its projection, the way the unscaled full-game stdev would
    imply (confirmed against a real live case, 2026-09-14: 117.8 scored,
    146.66 projected with a QB+TE left, fallback stdev 20 implied p10 of
    ~121 - barely 3 points of QB+TE combined production at the 10th
    percentile, an unrealistically harsh bust). Variance scales
    (approximately, assuming independent per-player contributions) with
    how much total point production is still undecided - approximated
    here as (projected - score_so_far) / projected, the fraction of the
    team's OWN projected total not yet realized; since stdev is
    sqrt(variance), it scales by the square root of that fraction.

    Once every rostered player has actually played, ESPN's own live
    "projected" field (points scored so far + rest-of-lineup projection)
    converges to EXACTLY score_so_far, since there's no rest-of-lineup
    projection left to add - fraction_remaining hits exactly 0, and this
    returns a real 0.0 stdev rather than the MIN_REMAINING_STDEV_FRACTION
    floor below: a fully-decided week has no remaining uncertainty to pad
    for, unlike a nearly-but-not-fully-finished one (see that constant's
    own docstring)."""
    if projected <= 0:
        return full_stdev
    fraction_remaining = max(0.0, (projected - score_so_far) / projected)
    if fraction_remaining <= 0:
        return 0.0
    return full_stdev * max(MIN_REMAINING_STDEV_FRACTION, fraction_remaining ** 0.5)


def live_win_probability(
    season: int, home_team_pk: int, away_team_pk: int,
    home_projected: float, away_projected: float, home_score: float, away_score: float,
) -> float:
    """Same closed-form model as project_matchup_win_probability, but
    centered on each team's CURRENT live-projected score instead of a
    season-PPG blend - a better "chance to win right now" estimate once a
    week has live data (home_projected already factors in points
    actually scored so far plus the rest of the lineup's projections, see
    get_live_box_scores). Still explicitly NOT ESPN's own number - ESPN's
    API exposes no win probability at all (confirmed against the
    installed espn_api source). home_score/away_score (points already
    scored) narrow each team's stdev via _live_remaining_stdev as the
    week progresses - see that function's docstring for why the raw
    full-game stdev overstates remaining uncertainty once part of a
    lineup has already played."""
    stdevs = team_stdev_map(season)
    home_full_stdev = stdevs.get(home_team_pk) or LIVE_PROB_FALLBACK_STDEV
    away_full_stdev = stdevs.get(away_team_pk) or LIVE_PROB_FALLBACK_STDEV
    home_proj = TeamProjection(
        team_pk=home_team_pk, expected_score=home_projected,
        stdev=_live_remaining_stdev(home_full_stdev, home_projected, home_score),
    )
    away_proj = TeamProjection(
        team_pk=away_team_pk, expected_score=away_projected,
        stdev=_live_remaining_stdev(away_full_stdev, away_projected, away_score),
    )
    return win_probability(home_proj, away_proj)


def live_cutline_analysis(
    season: int, projected_by_team: dict[int, float], score_by_team: dict[int, float]
) -> dict[int, dict]:
    """For every team in projected_by_team (this week's CURRENT live
    projected total), models that team's final score as Normal(current
    projected, that team's own REMAINING-uncertainty stdev - see
    _live_remaining_stdev) - same model/fallback as live_win_probability,
    so the two features stay consistent. score_by_team (points already
    scored) is what narrows that stdev as the week progresses. Returns
    {team_pk: {"p_making_it", "p10", "p90"}}: p_making_it is the Monte
    Carlo probability of finishing in the league's top half this week
    (earning the median bonus win - see metrics.cutline_sim for why this
    needs simulation, not a closed form); p10/p90 are that same team's
    own 10th/90th percentile final score (closed-form, since a team's
    own percentile doesn't depend on anyone else)."""
    stdevs = team_stdev_map(season)
    teams = [
        TeamScoreModel(
            team_pk=pk, mean=proj,
            stdev=_live_remaining_stdev(stdevs.get(pk) or LIVE_PROB_FALLBACK_STDEV, proj, score_by_team.get(pk, 0.0)),
        )
        for pk, proj in projected_by_team.items()
    ]
    p_making_it = simulate_cutline_probabilities(teams)
    result = {}
    for t in teams:
        p10, p90 = percentile_range(t)
        result[t.team_pk] = {"p_making_it": p_making_it[t.team_pk], "p10": p10, "p90": p90}
    return result


def live_score_percentile_range(season: int, team_pk: int, projected: float, score_so_far: float) -> tuple[float, float]:
    """10th/90th percentile of ONE team's live-projected final score -
    same Normal(projected, remaining-uncertainty stdev) model as
    live_win_probability/live_cutline_analysis (see _live_remaining_stdev
    for why the raw full-game stdev isn't used directly), but closed-form
    (metrics.cutline_sim.percentile_range) rather than going through the
    Monte Carlo cutline sim, since a single matchup card just needs this
    one team's own range, not a cross-team rank probability."""
    stdevs = team_stdev_map(season)
    full_stdev = stdevs.get(team_pk) or LIVE_PROB_FALLBACK_STDEV
    team = TeamScoreModel(
        team_pk=team_pk, mean=projected, stdev=_live_remaining_stdev(full_stdev, projected, score_so_far)
    )
    return percentile_range(team)


def clear_all_caches() -> None:
    st.cache_data.clear()
