"""Playoff probability via Monte Carlo simulation (>=10,000 trials per
the spec). Pure/DB-free like the rest of metrics/ - dashboard_data.py
does the DB reads that feed this.

Model: each remaining regular-season game's score is sampled
Normal(expected_score, team's own weekly stdev), where the RAW
expected_score for the NEAREST remaining week = 60% season PPG + 40%
last-3-week PPG - the same formula (and the same win_probability.
expected_score()/MIN_STDEV) as the Matchups page's single-game win
probability, so the two features agree with each other. A team's
STDEV is FROZEN at its current real value for an entire trial - both
its remaining regular-season games and, if it reaches the playoffs, its
playoff games. Its expected_score is NOT frozen past the nearest
remaining week: shrink_expected_score()'s early-season shrinkage is
recomputed every remaining week using that TRIAL's own running (real-
then-simulated) season-to-date average and an incrementing games_played,
so a real early-season outlier's drag on later weeks fades exactly like
real accumulating evidence would (fixed 2026-09-16 - see simulate_
season()'s own docstring for the real case that motivated it; last3_ppg
itself isn't separately tracked per trial, so it's only used for the
nearest remaining week). Stdev staying frozen (a team's own week-to-week
volatility is a far more stable property than its mean scoring level) is
the one simplifying assumption left here, stated rather than silently
baked in.

Early-season shrinkage: the raw expected_score above is blended toward
the LEAGUE-WIDE average PPG, weighted by how many real games a team has
played (games_played / (games_played + SHRINKAGE_GAMES) of weight on the
team's own number) - see shrink_expected_score(). Without this, a single
week-1 fluke (one team scores 40+ points more than the field, purely by
matchup luck) got treated as a fully-earned, permanent skill gap: with
zero regression to the mean and a too-tight stdev floor (see MIN_STDEV's
own docstring), that team would show as a near-lock for the championship
after ONE real game (confirmed live 2026-09-15 - a team sat at 97.4%
after week 1). SHRINKAGE_GAMES=8 was calibrated, not guessed: swept K
across this league's 10 real completed seasons (2015-2024), measuring
RMSE between a shrunk projection (using only the first W games) and each
team's ACTUAL rest-of-season PPG, for every team/season/W. K=8 sits at
the RMSE knee (11.79 vs. 14.99 for no shrinkage at all, vs. 11.41-11.39
for K=10-15 - most of the benefit, without discarding real signal a team
has already shown).

Seeding: this league is single-division (see PROJECT_BRIEF), so seeding
is a flat sort by combined win pct (matchup + median-scoring bonus wins
when active, matching metrics_weekly.actual_win_pct's own formula) then
total points scored - this league's real `playoff_seed_tie_rule`
(`TOTAL_POINTS_SCORED`, confirmed live 2026-09-13). Verified by
reproducing the real seed order for 10 completed seasons (2015-2024)
from the same two-key sort used here.

Playoff bracket: HARDCODED to this league's real 6-team/top-2-bye
format (every season 2015-present) - seed1 plays the winner of seed4 vs
seed5, seed2 plays the winner of seed3 vs seed6, seed3-6 do NOT get
reseeded after round 1. This exact fixed (non-reseeding) shape was
confirmed by walking all 10 completed seasons' real bracket results,
including 4 seasons where a lower seed upset a higher seed in round 1
(2017, 2019, 2020, 2023) - in every one of those, the upsetting team
still played the SAME seed (1 or 2) the un-upset bracket would have
predicted, not a re-ranked opponent. Per the spec's "if unsupported,
document the limitation, don't guess" instruction: only
playoff_team_count == 6 is supported; anything else raises rather than
inventing an unverified bracket shape (this league has never used any
other playoff_team_count, so this isn't expected to bite in practice).
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from .win_probability import MIN_STDEV, expected_score

DEFAULT_N_SIMS = 10_000
SUPPORTED_PLAYOFF_TEAM_COUNT = 6
SHRINKAGE_GAMES = 8  # see module docstring's "Early-season shrinkage" - calibrated via RMSE sweep, not guessed

TEAM_STATE_COLUMNS = [
    "team_pk", "season_ppg", "last3_ppg", "score_stdev",
    "matchup_wins", "matchup_losses", "matchup_ties",
    "median_wins", "median_losses", "median_ties", "points_for",
]


def compute_score_stdev(scores_df: pd.DataFrame) -> pd.DataFrame:
    """scores_df: week, team_pk, score for every completed regular-season
    week so far. Returns team_pk, score_stdev - each team's own sample
    stdev (ddof=1), floored at MIN_STDEV (same floor as the Matchups
    page's win probability) since a team with 0-1 real games has no
    meaningful sample variance yet and would otherwise make the
    simulation falsely overconfident (near-deterministic outcomes from
    a single early-season data point)."""
    if scores_df.empty:
        return pd.DataFrame(columns=["team_pk", "score_stdev"])
    stdev = scores_df.groupby("team_pk")["score"].std(ddof=1).fillna(0.0).clip(lower=MIN_STDEV)
    return stdev.reset_index().rename(columns={"score": "score_stdev"})


def shrink_expected_score(
    raw_expected_score: np.ndarray, games_played: np.ndarray, prior_score: float | np.ndarray,
    shrinkage_games: float = SHRINKAGE_GAMES,
) -> np.ndarray:
    """Blends each team's raw expected_score toward `prior_score`,
    weighted by games_played/(games_played + shrinkage_games) - a
    standard small-sample shrinkage estimator (see module docstring's
    "Early-season shrinkage"). At games_played=0 this returns exactly
    prior_score (no real data yet); as games_played grows, it converges
    toward the team's own raw_expected_score. prior_score is usually the
    flat league-wide average season_ppg (a single float, broadcast to
    every team), but simulate_season()'s shrinkage_prior lets a caller
    pass a per-team array instead - e.g. each team's current Roster
    Strength remapped to real points, a more informative prior than a
    flat average (see simulate_season()'s own docstring)."""
    weight = games_played / (games_played + shrinkage_games)
    return weight * raw_expected_score + (1 - weight) * prior_score


def rank_teams(team_ids: list, win_pct: dict, points_for: dict) -> list:
    """Seed order (best to worst) - win pct desc, then total points
    scored desc (this league's real playoff_seed_tie_rule)."""
    return sorted(team_ids, key=lambda t: (-win_pct[t], -points_for[t]))


def simulate_bracket_once(seed_order: list, score_sampler: Callable[[object], float]):
    """Plays out one trial of the fixed 6-team/top-2-bye bracket (see
    module docstring for why it's fixed, not reseeded) given a
    score_sampler(team_id) -> float callable (a fresh sampled score each
    time it's called - deterministic for tests, random for real sims).
    Returns the champion's team id."""
    seed1, seed2, seed3, seed4, seed5, seed6 = seed_order[:6]
    r1_a_winner = seed3 if score_sampler(seed3) > score_sampler(seed6) else seed6  # 3 vs 6
    r1_b_winner = seed4 if score_sampler(seed4) > score_sampler(seed5) else seed5  # 4 vs 5
    semi_a_winner = seed1 if score_sampler(seed1) > score_sampler(r1_b_winner) else r1_b_winner
    semi_b_winner = seed2 if score_sampler(seed2) > score_sampler(r1_a_winner) else r1_a_winner
    return semi_a_winner if score_sampler(semi_a_winner) > score_sampler(semi_b_winner) else semi_b_winner


def simulate_season(
    team_state: pd.DataFrame,
    remaining_matchups: pd.DataFrame,
    median_scoring: bool,
    reg_season_count: int,
    playoff_team_count: int = SUPPORTED_PLAYOFF_TEAM_COUNT,
    n_sims: int = DEFAULT_N_SIMS,
    rng: np.random.Generator | None = None,
    shrinkage_games_played: np.ndarray | None = None,
    shrinkage_prior: np.ndarray | None = None,
) -> pd.DataFrame:
    """team_state columns: see TEAM_STATE_COLUMNS - one row per team, all
    REAL cumulative values as of right now (season_ppg/last3_ppg/points_for/
    matchup+median records through the last completed week; score_stdev
    from compute_score_stdev()).
    remaining_matchups columns: week, home_team_pk, away_team_pk - every
    not-yet-completed regular-season matchup (includes an in-progress
    current week, simulated like any other remaining game rather than
    blending in a partial live score - a deliberate v1 simplification).

    shrinkage_games_played: optional override for the "how many real
    games of evidence do we have" signal that ONLY the early-season
    shrinkage step (shrink_expected_score) uses - defaults to the real
    matchup record (matchup_wins+losses+ties) when not given, which is
    correct for every normal in-season call. Exists for
    playoff_odds_snapshots.compute_week0_snapshot_rows()'s "Week 0"
    snapshot, where team_state's real matchup record is genuinely 0-0-0
    (no games played) but season_ppg/last3_ppg already carry real signal
    (each team's optimal Week-1 lineup's ESPN pregame projection) that
    the normal games_played=0 -> "no data yet, use the league average"
    shrinkage rule would otherwise completely discard - see that
    function's own docstring for the RMSE calibration behind the value
    it passes here.

    shrinkage_prior: optional per-team override for what
    shrink_expected_score() regresses toward - defaults to the flat
    league-wide average season_ppg when not given (every normal in-
    season call before 2026-09-16). Exists so a team's CURRENT Roster
    Strength (dashboard_data.get_playoff_simulation()) can serve as a
    team-specific, more informative prior than a flat league average -
    a team with 1-2 real games played still gets shrunk mostly toward
    a prior, so that prior being roster-quality-aware (not just "the
    average team") means the model reflects real roster changes
    (trades, injuries, waiver moves) immediately, not only once enough
    games accumulate to outweigh a generic average. User, 2026-09-16:
    "Playoff odds should be using roster strength in its simulation for
    future weeks though. A higher roster strength for future matchups
    would indicate a higher % chance of winning that matchup." Must be
    the same shape as shrinkage_games_played/team_state (one value per
    team_pk, in team_state's row order) if given.

    Returns team_pk, playoff_pct, bye_pct, seed1_pct, championship_pct.
    """
    if playoff_team_count != SUPPORTED_PLAYOFF_TEAM_COUNT:
        raise NotImplementedError(
            f"Playoff bracket structure is only verified for a "
            f"{SUPPORTED_PLAYOFF_TEAM_COUNT}-team/top-2-bye format (this league's format every "
            f"season since 2015) - got playoff_team_count={playoff_team_count}. Not simulating "
            f"rather than guessing at an unverified bracket shape."
        )
    if team_state.empty:
        return pd.DataFrame(columns=["team_pk", "playoff_pct", "bye_pct", "seed1_pct", "championship_pct"])

    rng = rng if rng is not None else np.random.default_rng()
    team_pks = team_state["team_pk"].tolist()
    n_teams = len(team_pks)
    pos = {pk: i for i, pk in enumerate(team_pks)}

    raw_expected = expected_score(team_state["season_ppg"].to_numpy(), team_state["last3_ppg"].to_numpy())
    if shrinkage_games_played is not None:
        games_played = np.asarray(shrinkage_games_played, dtype=float)
    else:
        games_played = (
            team_state["matchup_wins"] + team_state["matchup_losses"] + team_state["matchup_ties"]
        ).to_numpy(dtype=float)
    if shrinkage_prior is not None:
        # reshaped to (n_teams, 1) so it broadcasts against the (n_teams,
        # n_sims) arrays the per-week shrinkage below now uses
        prior = np.asarray(shrinkage_prior, dtype=float)[:, None]
    else:
        prior = float(team_state["season_ppg"].mean())
    stdev = team_state["score_stdev"].to_numpy()

    matchup_wins = np.tile(team_state["matchup_wins"].to_numpy(dtype=float)[:, None], n_sims)
    matchup_losses = np.tile(team_state["matchup_losses"].to_numpy(dtype=float)[:, None], n_sims)
    matchup_ties = team_state["matchup_ties"].to_numpy(dtype=float)[:, None]
    median_wins = np.tile(team_state["median_wins"].to_numpy(dtype=float)[:, None], n_sims)
    median_losses = np.tile(team_state["median_losses"].to_numpy(dtype=float)[:, None], n_sims)
    median_ties = team_state["median_ties"].to_numpy(dtype=float)[:, None]
    points_for = np.tile(team_state["points_for"].to_numpy(dtype=float)[:, None], n_sims)

    # Running per-trial state for expected score: starts at each team's
    # real season-to-date average, then evolves week by week as THAT
    # TRIAL's own simulated scores come in - games_played genuinely
    # increments as the simulated season progresses, so shrink_expected_
    # score() trusts the team's own (real-then-simulated) average more
    # with each passing week, same as it would in reality. Previously
    # `expected`/games_played were computed ONCE and reused unchanged for
    # every remaining week - a real Week-1 outlier stayed discounted at
    # the SAME rate through Week 14 in every trial regardless of how that
    # trial's simulated season actually went (an explicitly documented
    # simplification - see this function's docstring - that turned out
    # to matter a lot in practice: fixed 2026-09-16 after a real case, a
    # team with a historically bad Week 1 but an average Roster Strength
    # staying discounted the entire season in every trial. User: "1 week
    # of scores isn't very significant over the course of a full
    # season... there's a very real chance his points scored recovers to
    # average or above average"). last3_ppg isn't separately tracked per
    # trial (would need each team's individual real recent scores, not
    # just the already-blended real average) - the real season_ppg/
    # last3_ppg blend is used for the nearest remaining week only (still
    # the most real-recent-form-sensitive one); the running season-to-
    # date average takes over from the second remaining week onward.
    sim_games_played = np.tile(games_played[:, None], n_sims).astype(float)
    sim_points_for = np.tile(team_state["points_for"].to_numpy(dtype=float)[:, None], n_sims)
    # (n_teams, n_sims) throughout, even before any remaining-week loop
    # iteration runs, so it's always a valid, correctly-shaped "current
    # expected score" for playoff-bracket sampling below - both when
    # regular-season games remain (updated each iteration) and when none
    # do (e.g. simulating the playoffs alone after the real regular
    # season already finished).
    week_expected = shrink_expected_score(raw_expected[:, None], sim_games_played, prior)
    first_remaining_week = True

    for week in sorted(remaining_matchups["week"].unique()):
        if not first_remaining_week:
            running_season_ppg = sim_points_for / sim_games_played
            week_expected = shrink_expected_score(running_season_ppg, sim_games_played, prior)
        first_remaining_week = False

        week_scores = rng.normal(loc=week_expected, scale=stdev[:, None], size=(n_teams, n_sims))
        week_scores = np.clip(week_scores, 0, None)
        points_for += week_scores
        sim_points_for += week_scores
        sim_games_played += 1

        if median_scoring:
            week_median = np.median(week_scores, axis=0)
            median_wins += week_scores > week_median[None, :]
            median_losses += week_scores < week_median[None, :]

        for row in remaining_matchups.loc[remaining_matchups["week"] == week].itertuples():
            hi, ai = pos[row.home_team_pk], pos[row.away_team_pk]
            home_win = week_scores[hi] > week_scores[ai]
            matchup_wins[hi] += home_win
            matchup_losses[hi] += ~home_win
            matchup_wins[ai] += ~home_win
            matchup_losses[ai] += home_win

    matchup_win_equiv = matchup_wins + 0.5 * matchup_ties
    if median_scoring:
        combined_win_equiv = matchup_win_equiv + median_wins + 0.5 * median_ties
        win_pct = combined_win_equiv / (reg_season_count * 2)
    else:
        win_pct = matchup_win_equiv / reg_season_count

    playoff_count = np.zeros(n_teams, dtype=int)
    bye_count = np.zeros(n_teams, dtype=int)
    seed1_count = np.zeros(n_teams, dtype=int)
    championship_count = np.zeros(n_teams, dtype=int)

    for s in range(n_sims):
        win_pct_s = {i: win_pct[i, s] for i in range(n_teams)}
        points_for_s = {i: points_for[i, s] for i in range(n_teams)}
        seed_order = rank_teams(list(range(n_teams)), win_pct_s, points_for_s)

        for rank, team_i in enumerate(seed_order[:playoff_team_count]):
            playoff_count[team_i] += 1
            if rank < 2:
                bye_count[team_i] += 1
            if rank == 0:
                seed1_count[team_i] += 1

        def sample_playoff_score(team_i: int) -> float:
            # week_expected here is each team's expected score for the
            # week AFTER the real regular season's last simulated week -
            # i.e. this trial's own fully-evolved end-of-season estimate,
            # not a single frozen value shared across every trial.
            return max(0.0, rng.normal(week_expected[team_i, s], stdev[team_i]))

        champion_i = simulate_bracket_once(seed_order, sample_playoff_score)
        championship_count[champion_i] += 1

    return pd.DataFrame(
        {
            "team_pk": team_pks,
            "playoff_pct": playoff_count / n_sims,
            "bye_pct": bye_count / n_sims,
            "seed1_pct": seed1_count / n_sims,
            "championship_pct": championship_count / n_sims,
        }
    )
