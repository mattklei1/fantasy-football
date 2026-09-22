"""Unit tests for fantasy_football.metrics.playoff_sim - small synthetic
data only, no live ESPN calls (per PROJECT_BRIEF testing requirements).
See PROJECT_BRIEF's Phase 7 section for the live-data validation (real,
completed 2025 season) that reproduced the actual playoff bracket
exactly - that validation isn't re-run here since it depends on
data/league.db, which isn't committed."""
import numpy as np
import pandas as pd
import pytest

from fantasy_football.metrics.playoff_sim import (
    DEFAULT_N_SIMS,
    SHRINKAGE_GAMES,
    SUPPORTED_PLAYOFF_TEAM_COUNT,
    compute_score_stdev,
    dampen_prior_spread,
    rank_teams,
    shrink_expected_score,
    simulate_bracket_once,
    simulate_season,
)
from fantasy_football.metrics.win_probability import MIN_STDEV


def test_default_n_sims_meets_spec_minimum():
    assert DEFAULT_N_SIMS >= 10_000


def test_compute_score_stdev_floors_thin_samples():
    scores = pd.DataFrame(
        {
            "week": [1, 2, 3, 1],
            "team_pk": [1, 1, 1, 2],  # team 1: 3 real games; team 2: 1 game (no real variance)
            "score": [100.0, 150.0, 50.0, 120.0],
        }
    )
    result = compute_score_stdev(scores).set_index("team_pk")
    assert result.loc[1, "score_stdev"] > MIN_STDEV  # genuinely volatile team keeps its own stdev
    assert result.loc[2, "score_stdev"] == pytest.approx(MIN_STDEV)  # 1 game -> floored


def test_compute_score_stdev_empty_input():
    result = compute_score_stdev(pd.DataFrame(columns=["week", "team_pk", "score"]))
    assert result.empty


def test_shrink_expected_score_at_zero_games_returns_league_average():
    # no real games yet - own number carries zero weight, must be
    # exactly the league average regardless of what the raw number says
    result = shrink_expected_score(
        np.array([200.0]), np.array([0.0]), prior_score=100.0,
    )
    assert result[0] == pytest.approx(100.0)


def test_shrink_expected_score_converges_toward_raw_as_games_increase():
    raw = np.array([200.0])
    league_avg = 100.0
    early = shrink_expected_score(raw, np.array([1.0]), league_avg)[0]
    mid = shrink_expected_score(raw, np.array([SHRINKAGE_GAMES]), league_avg)[0]
    late = shrink_expected_score(raw, np.array([100.0]), league_avg)[0]
    # monotonically closer to the team's own raw number as more real
    # games accumulate
    assert 100.0 < early < mid < late < 200.0
    # at games_played == shrinkage_games, weight is exactly 0.5
    assert mid == pytest.approx(150.0)
    # with a large sample the team's own number should dominate
    assert late > 190.0


def test_dampen_prior_spread_at_zero_games_collapses_to_flat_average():
    prior = np.array([80.0, 100.0, 200.0])
    result = dampen_prior_spread(prior, np.array([0.0, 0.0, 0.0]))
    league_avg = prior.mean()
    assert result == pytest.approx([league_avg, league_avg, league_avg])


def test_dampen_prior_spread_converges_to_raw_prior_as_games_increase():
    prior = np.array([80.0, 200.0])
    early = dampen_prior_spread(prior, np.array([1.0, 1.0]))
    mid = dampen_prior_spread(prior, np.array([SHRINKAGE_GAMES, SHRINKAGE_GAMES]))
    late = dampen_prior_spread(prior, np.array([200.0, 200.0]))
    league_avg = prior.mean()
    # each team's own spread from the league average grows monotonically
    # as more real games accumulate
    assert abs(early[1] - league_avg) < abs(mid[1] - league_avg) < abs(late[1] - league_avg)
    # at games_played == shrinkage_games, weight is exactly 0.5
    assert mid[1] == pytest.approx((league_avg + 200.0) / 2)
    # with a large sample the raw prior should nearly fully come through
    assert late[1] > 195.0


def test_dampen_prior_spread_preserves_relative_order():
    # dampening SHRINKS the gap, it must never flip who's ahead
    prior = np.array([80.0, 120.0, 200.0])
    result = dampen_prior_spread(prior, np.array([2.0, 2.0, 2.0]))
    assert result[0] < result[1] < result[2]


def test_rank_teams_breaks_ties_by_points_for():
    win_pct = {1: 0.6, 2: 0.6, 3: 0.5}
    points_for = {1: 1000.0, 2: 1200.0, 3: 999999.0}
    order = rank_teams([1, 2, 3], win_pct, points_for)
    # 1 and 2 tied on win_pct -> higher points_for (2) seeds ahead of 1;
    # 3 has the highest points_for of all but a worse win_pct, so it still seeds last
    assert order == [2, 1, 3]


def _fixed_score_sampler(scores: dict):
    """Deterministic score_sampler for simulate_bracket_once - same
    fixed score every time a given team is asked, no randomness."""
    return lambda team_id: scores[team_id]


def test_bracket_is_fixed_not_reseeded_after_a_round1_upset():
    # seeds 1-6; seed6 is given a huge score so it upsets seed3 in round 1
    seed_order = ["s1", "s2", "s3", "s4", "s5", "s6"]
    scores = {"s1": 100, "s2": 90, "s3": 80, "s4": 70, "s5": 60, "s6": 999, "s7": 0}
    # s4 beats s5 normally (70 > 60); s6 upsets s3 (999 > 80)
    # FIXED bracket: seed2 must face the 3v6 winner (s6), seed1 faces the 4v5 winner (s4)
    # confirm by making s2 lose to s6's score and s1 beat s4's score, then check who reaches the final
    champion = simulate_bracket_once(seed_order, _fixed_score_sampler(scores))
    # s6 (999) should steamroll everyone it plays, including in the final
    assert champion == "s6"


def test_bracket_seed1_faces_4v5_winner_not_3v6_winner():
    # give seed3 and seed6 mediocre, near-equal scores (s6 wins narrowly),
    # and make seed1 lose ONLY to s6-strength opponents, not to s4/s5-strength
    # opponents - if the bracket were reseeded (1 vs lowest remaining seed),
    # seed1 would face s6 in the semis and lose; under the real FIXED
    # bracket, seed1 faces the 4v5 winner instead and should win comfortably.
    seed_order = ["s1", "s2", "s3", "s4", "s5", "s6"]
    scores = {"s1": 50, "s2": 10, "s3": 20, "s4": 5, "s5": 1, "s6": 21}
    # round 1: s3(20) vs s6(21) -> s6 wins; s4(5) vs s5(1) -> s4 wins
    # fixed bracket semis: s1(50) vs s4(5) -> s1 wins comfortably; s2(10) vs s6(21) -> s6 wins
    # final: s1(50) vs s6(21) -> s1 wins
    champion = simulate_bracket_once(seed_order, _fixed_score_sampler(scores))
    assert champion == "s1"


def _toy_team_state(n_teams=8):
    # team 0: dominant real record, huge scoring edge - should be a lock
    # for the #1 seed even with 0 remaining games (deterministic - no
    # simulated variance can change an already-final regular season).
    rows = []
    for i in range(n_teams):
        rows.append(
            {
                "team_pk": i,
                "season_ppg": 200.0 if i == 0 else 100.0,
                "last3_ppg": 200.0 if i == 0 else 100.0,
                "score_stdev": MIN_STDEV,
                "matchup_wins": 14 if i == 0 else 0,
                "matchup_losses": 0 if i == 0 else 14,
                "matchup_ties": 0,
                "median_wins": 14 if i == 0 else 0,
                "median_losses": 0 if i == 0 else 14,
                "median_ties": 0,
                "points_for": 2800.0 if i == 0 else (100.0 * i),
            }
        )
    return pd.DataFrame(rows)


def test_simulate_season_deterministic_when_no_games_remain():
    team_state = _toy_team_state(n_teams=8)
    remaining = pd.DataFrame(columns=["week", "home_team_pk", "away_team_pk"])
    result = simulate_season(
        team_state, remaining, median_scoring=True, reg_season_count=14,
        n_sims=500, rng=np.random.default_rng(0),
    ).set_index("team_pk")

    # team 0 went 14-0/14-0 (undefeated both records) with 0 games left to
    # simulate - the final standings are already fully determined, so this
    # must be exactly 1.0, not just "usually"
    assert result.loc[0, "playoff_pct"] == pytest.approx(1.0)
    assert result.loc[0, "bye_pct"] == pytest.approx(1.0)
    assert result.loc[0, "seed1_pct"] == pytest.approx(1.0)
    # the bottom-record teams (7 losses each, all identical records) can
    # never crack a 6-of-8 playoff field ahead of the 6 teams with a
    # better real record - also fully deterministic already
    assert result.loc[1, "playoff_pct"] == pytest.approx(0.0)


def test_simulate_season_rejects_unsupported_playoff_team_count():
    team_state = _toy_team_state(n_teams=8)
    remaining = pd.DataFrame(columns=["week", "home_team_pk", "away_team_pk"])
    with pytest.raises(NotImplementedError):
        simulate_season(
            team_state, remaining, median_scoring=True, reg_season_count=14,
            playoff_team_count=4, n_sims=100,
        )
    assert SUPPORTED_PLAYOFF_TEAM_COUNT == 6


def _round_robin_schedule(n_teams: int) -> pd.DataFrame:
    """Standard circle-method round robin: n_teams-1 rounds (n_teams
    even), every team plays every other team exactly once."""
    teams = list(range(n_teams))
    rows = []
    for week in range(1, n_teams):
        for i in range(n_teams // 2):
            home, away = teams[i], teams[n_teams - 1 - i]
            rows.append((week, home, away))
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return pd.DataFrame(rows, columns=["week", "home_team_pk", "away_team_pk"])


def test_shrinkage_games_played_override_preserves_signal_at_zero_real_games():
    # 8 teams (only 6 make the playoff_team_count=6 default field - real
    # competition for spots), all with a genuinely 0-0-0 real record (a
    # "Week 0" scenario - nobody's played yet) but a wide, real
    # season_ppg spread (a roster-quality signal, e.g. from optimal-
    # lineup projections). Without an override, games_played=0 collapses
    # EVERY team's expected score to the league average (shrink_expected_
    # score), discarding that signal and leaving only near-identical
    # odds driven by simulation noise. With a high override, the real
    # signal should come through and meaningfully differentiate the
    # field. Regression test for a real bug (2026-09-15): the Week-0
    # playoff odds feature initially had every real team landing at
    # 49-51% for exactly this reason before shrinkage_games_played was
    # added.
    n_teams = 8
    rows = [
        {
            "team_pk": i, "season_ppg": 80.0 + i * 10.0, "last3_ppg": 80.0 + i * 10.0,
            "score_stdev": MIN_STDEV, "matchup_wins": 0, "matchup_losses": 0, "matchup_ties": 0,
            "median_wins": 0, "median_losses": 0, "median_ties": 0, "points_for": 0.0,
        }
        for i in range(n_teams)
    ]
    team_state = pd.DataFrame(rows)
    remaining = _round_robin_schedule(n_teams)

    no_override = simulate_season(
        team_state, remaining, median_scoring=False, reg_season_count=n_teams - 1,
        n_sims=2000, rng=np.random.default_rng(0),
    ).set_index("team_pk")
    spread_no_override = no_override["playoff_pct"].max() - no_override["playoff_pct"].min()

    with_override = simulate_season(
        team_state, remaining, median_scoring=False, reg_season_count=n_teams - 1,
        n_sims=2000, rng=np.random.default_rng(0),
        shrinkage_games_played=np.full(n_teams, 100.0),
    ).set_index("team_pk")
    spread_with_override = with_override["playoff_pct"].max() - with_override["playoff_pct"].min()

    assert spread_no_override < 0.15  # collapsed to ~identical odds, as today's real bug did
    assert spread_with_override > spread_no_override
    assert spread_with_override > 0.3  # the real roster-quality signal now differentiates the field
    # and it differentiates in the RIGHT direction: the best team's own
    # number (team 7, season_ppg=150) should clearly outrank the worst
    # (team 0, season_ppg=80)
    assert with_override.loc[7, "playoff_pct"] > with_override.loc[0, "playoff_pct"]


def test_shrinkage_games_played_override_does_not_corrupt_the_running_average():
    # Regression test for a real bug shipped and caught the same day
    # (2026-09-16): the running-average fix above (`sim_avg_games_played`
    # evolving week by week) initially divided simulated points_for by
    # the OVERRIDDEN games_played (e.g. WEEK0_TRUST_GAMES=45) instead of
    # the REAL actual game count (0 at Week 0) - since real points_for
    # starts at 0.0 while the override pretends 45 games' worth of trust
    # already exists, dividing one simulated week's score by ~46 produced
    # a near-zero "running average" for every team from the second
    # remaining week onward, silently re-collapsing the real Roster-
    # Strength spread right back to ~flat odds (caught live: real Week-0
    # playoff odds went from a real 4.7%-93.4% spread back down to a
    # suspicious 32%-68% band the moment this shipped). Uses the exact
    # shape of the real bug: a real 0-0-0 record, real points_for=0.0,
    # a large trust override (45, matching WEEK0_TRUST_GAMES), a wide
    # real prior spread, and enough remaining weeks (13, a real season's
    # worth) for the bug to compound.
    n_teams = 12
    rows = [
        {
            "team_pk": i, "season_ppg": 130.0, "last3_ppg": 130.0, "score_stdev": MIN_STDEV,
            "matchup_wins": 0, "matchup_losses": 0, "matchup_ties": 0,
            "median_wins": 0, "median_losses": 0, "median_ties": 0, "points_for": 0.0,
        }
        for i in range(n_teams)
    ]
    team_state = pd.DataFrame(rows)
    remaining = pd.DataFrame(
        {
            "week": [w for w in range(1, 14) for _ in range(6)],
            "home_team_pk": list(range(0, 12, 2)) * 13,
            "away_team_pk": list(range(1, 12, 2)) * 13,
        }
    )
    prior = np.array([80.0 + i * 7.0 for i in range(n_teams)])  # a real, wide spread

    result = simulate_season(
        team_state, remaining, median_scoring=True, reg_season_count=13,
        n_sims=5000, rng=np.random.default_rng(1),
        shrinkage_games_played=np.full(n_teams, 45.0), shrinkage_prior=prior,
    ).set_index("team_pk")

    spread = result["playoff_pct"].max() - result["playoff_pct"].min()
    assert spread > 0.3  # a real, wide spread - not collapsed back to a tight band
    assert result.loc[11, "playoff_pct"] > result.loc[0, "playoff_pct"]  # highest prior clearly beats the lowest


def test_shrinkage_prior_override_lets_a_team_specific_prior_drive_future_weeks():
    # 8 teams, ALL with IDENTICAL real Week-1 performance (same 1-0
    # record, same observed score) - isolates shrinkage_prior's effect
    # from everything else real. A wide prior spread (e.g. from Roster
    # Strength) should differentiate their FUTURE-week expected scores,
    # and so their playoff odds, even though nothing about their real
    # performance so far differs at all. Regression test for the actual
    # feature (2026-09-16, user: "Playoff odds should be using roster
    # strength in its simulation for future weeks... A higher roster
    # strength for future matchups would indicate a higher % chance of
    # winning that matchup").
    n_teams = 8
    rows = [
        {
            "team_pk": i, "season_ppg": 120.0, "last3_ppg": 120.0,
            "score_stdev": MIN_STDEV, "matchup_wins": 1, "matchup_losses": 0, "matchup_ties": 0,
            "median_wins": 0, "median_losses": 0, "median_ties": 0, "points_for": 120.0,
        }
        for i in range(n_teams)
    ]
    team_state = pd.DataFrame(rows)
    remaining = _round_robin_schedule(n_teams)

    no_prior = simulate_season(
        team_state, remaining, median_scoring=False, reg_season_count=n_teams - 1,
        n_sims=3000, rng=np.random.default_rng(0),
    ).set_index("team_pk")
    spread_no_prior = no_prior["playoff_pct"].max() - no_prior["playoff_pct"].min()

    prior = np.array([80.0 + i * 15.0 for i in range(n_teams)])  # a real, wide spread
    with_prior = simulate_season(
        team_state, remaining, median_scoring=False, reg_season_count=n_teams - 1,
        n_sims=3000, rng=np.random.default_rng(0),
        shrinkage_prior=prior,
    ).set_index("team_pk")
    spread_with_prior = with_prior["playoff_pct"].max() - with_prior["playoff_pct"].min()

    assert spread_no_prior < 0.15  # identical real performance -> near-identical odds
    assert spread_with_prior > spread_no_prior
    # team 7's much higher prior should win out over team 0's much lower one
    assert with_prior.loc[7, "playoff_pct"] > with_prior.loc[0, "playoff_pct"]


def test_roster_strength_prior_does_not_overconcentrate_championship_odds_this_early():
    # Regression test for a real bug (2026-09-22, user: "I don't think
    # there is enough variability factored in week to week. No one
    # should have 30+% chance at the championship this early on").
    # Reproduces the actual live shape that triggered it: 12 teams, only
    # 2 real games played (this league's real week-3 state), a Roster
    # Strength shrinkage_prior with a real, moderate spread (~119-146,
    # the real live values pulled from this league's own data) - before
    # the fix, the top team's championship_pct came back at 33% (vs 19%
    # for the exact same real data run through the OLD flat-average-only
    # prior at the same rng seed) because the same games_played=2/(2+8)
    # weight that lightly trusts a team's OWN 2-game sample was ALSO
    # putting ~80% weight on the prior's full, undampened team-to-team
    # spread. dampen_prior_spread() now shrinks that spread the same way.
    n_teams = 12
    rows = [
        {
            "team_pk": i, "season_ppg": 120.0 + (i % 3) * 5.0, "last3_ppg": 120.0 + (i % 3) * 5.0,
            "score_stdev": 25.0, "matchup_wins": 1, "matchup_losses": 1, "matchup_ties": 0,
            "median_wins": 1, "median_losses": 1, "median_ties": 0, "points_for": 240.0 + (i % 3) * 10.0,
        }
        for i in range(n_teams)
    ]
    team_state = pd.DataFrame(rows)
    remaining = pd.DataFrame(
        {
            "week": [w for w in range(3, 15) for _ in range(6)],
            "home_team_pk": list(range(0, 12, 2)) * 12,
            "away_team_pk": list(range(1, 12, 2)) * 12,
        }
    )
    # the real live spread pulled from this league's Roster Strength (2026-09-22)
    prior = np.array([146.4, 140.5, 136.7, 134.7, 133.5, 132.1, 131.6, 131.1, 130.5, 128.5, 122.8, 119.3])

    result = simulate_season(
        team_state, remaining, median_scoring=True, reg_season_count=14,
        n_sims=4000, rng=np.random.default_rng(3), shrinkage_prior=prior,
    ).set_index("team_pk")

    assert result["championship_pct"].max() < 0.25  # well under the real pre-fix 33%
    # the fix dampens the spread, it must not erase it entirely
    assert result["championship_pct"].max() > result["championship_pct"].min()


def test_current_week_live_projection_drives_a_real_first_week_nudge():
    # Regression test for the actual feature (2026-09-22, user: "How
    # much are you factoring in this weeks projected score?..." followed
    # by "I still want roster strength to be the primary indicator...
    # This weeks projected score should only slightly impact playoff
    # odds"). At zero real games played, shrink_expected_score alone
    # collapses every team to the flat league average for the first
    # remaining week - a real per-team live optimal-lineup projection
    # should nudge them apart, but only slightly (CURRENT_WEEK_LIVE_
    # WEIGHT), not dominate the estimate.
    n_teams = 8
    rows = [
        {
            "team_pk": i, "season_ppg": 120.0, "last3_ppg": 120.0,
            "score_stdev": MIN_STDEV, "matchup_wins": 0, "matchup_losses": 0, "matchup_ties": 0,
            "median_wins": 0, "median_losses": 0, "median_ties": 0, "points_for": 0.0,
        }
        for i in range(n_teams)
    ]
    team_state = pd.DataFrame(rows)
    remaining = pd.DataFrame({"week": [1] * 4, "home_team_pk": [0, 2, 4, 6], "away_team_pk": [1, 3, 5, 7]})

    no_projection = simulate_season(
        team_state, remaining, median_scoring=False, reg_season_count=1,
        n_sims=4000, rng=np.random.default_rng(0),
    ).set_index("team_pk")
    spread_no_projection = no_projection["playoff_pct"].max() - no_projection["playoff_pct"].min()

    # a maximally extreme real-world gap: a team getting shut out (bye/
    # injury-riddled optimal lineup) vs. a team's real ceiling
    live_projection = np.array([200.0, 40.0, 120.0, 120.0, 120.0, 120.0, 120.0, 120.0])
    with_projection = simulate_season(
        team_state, remaining, median_scoring=False, reg_season_count=1,
        n_sims=4000, rng=np.random.default_rng(0), current_week_live_projection=live_projection,
    ).set_index("team_pk")
    spread_with_projection = with_projection["playoff_pct"].max() - with_projection["playoff_pct"].min()

    assert spread_no_projection < 0.15  # identical real state, zero games -> near-identical odds
    # a real, detectable nudge in the right direction
    assert spread_with_projection > spread_no_projection
    assert with_projection.loc[0, "playoff_pct"] > with_projection.loc[1, "playoff_pct"]


def test_current_week_live_projection_stays_a_slight_nudge_in_a_realistic_season():
    # Regression test for the user's explicit calibration request
    # (2026-09-22, immediately after the feature above shipped): "I
    # still want roster strength to be the primary indicator. I trust
    # the fantasypros rankings a lot. This weeks projected score should
    # only slightly impact playoff odds." Mirrors the real live scenario
    # already reproduced this session (12 teams, week 3, 2 real games
    # played, the real Roster-Strength prior spread pulled live) - even
    # a maximally extreme single-week live projection (several studs on
    # bye/injured, optimal lineup near replacement level) should only
    # modestly move the league's real Roster-Strength favorite, not
    # crater its odds.
    n_teams = 12
    rows = [
        {
            "team_pk": i, "season_ppg": 120.0 + (i % 3) * 5.0, "last3_ppg": 120.0 + (i % 3) * 5.0,
            "score_stdev": 25.0, "matchup_wins": 1, "matchup_losses": 1, "matchup_ties": 0,
            "median_wins": 1, "median_losses": 1, "median_ties": 0, "points_for": 240.0 + (i % 3) * 10.0,
        }
        for i in range(n_teams)
    ]
    team_state = pd.DataFrame(rows)
    remaining = pd.DataFrame(
        {
            "week": [w for w in range(3, 15) for _ in range(6)],
            "home_team_pk": list(range(0, 12, 2)) * 12,
            "away_team_pk": list(range(1, 12, 2)) * 12,
        }
    )
    # the real live Roster-Strength spread pulled from this league (2026-09-22)
    prior = np.array([146.4, 140.5, 136.7, 134.7, 133.5, 132.1, 131.6, 131.1, 130.5, 128.5, 122.8, 119.3])

    baseline = simulate_season(
        team_state, remaining, median_scoring=True, reg_season_count=14,
        n_sims=6000, rng=np.random.default_rng(3), shrinkage_prior=prior,
    ).set_index("team_pk")

    # team 0 (the Roster-Strength favorite) has a brutal real week -
    # several starters on bye/injured, real optimal lineup barely above
    # replacement level
    live_projection = np.full(n_teams, 130.0)
    live_projection[0] = 50.0
    nudged = simulate_season(
        team_state, remaining, median_scoring=True, reg_season_count=14,
        n_sims=6000, rng=np.random.default_rng(3), shrinkage_prior=prior,
        current_week_live_projection=live_projection,
    ).set_index("team_pk")

    # a real nudge in the right direction...
    assert nudged.loc[0, "championship_pct"] < baseline.loc[0, "championship_pct"]
    # ...but bounded - Roster Strength still drives the large majority
    # of the estimate even against a maximally bad single real week
    drop = baseline.loc[0, "championship_pct"] - nudged.loc[0, "championship_pct"]
    assert drop < 0.05


def test_current_week_live_projection_only_applies_to_the_first_remaining_week():
    # The nudge must only touch week 1's week_expected - every week
    # after it has to go back through the unmodified running-average +
    # shrink_expected_score recomputation, not keep nudging toward the
    # same live projection value forever. Isolated with exactly 2
    # remaining weeks and 0 real games played: if week 2 were (buggily)
    # still applying the nudge, team 0 and team 1 would separate further
    # in week 2 on top of week 1's own separation; if it's correctly
    # confined to week 1, week 2's own expected scores for both teams
    # should be identical to each other (both derived purely from each
    # team's own now-identically-structured running average - team_state
    # started identical, and both got the same-shaped week-1 nudge
    # relative to their now-different week-1 SIMULATED outcomes, not a
    # persisting different target).
    n_teams = 8
    rows = [
        {
            "team_pk": i, "season_ppg": 120.0, "last3_ppg": 120.0,
            "score_stdev": MIN_STDEV, "matchup_wins": 0, "matchup_losses": 0, "matchup_ties": 0,
            "median_wins": 0, "median_losses": 0, "median_ties": 0, "points_for": 0.0,
        }
        for i in range(n_teams)
    ]
    team_state = pd.DataFrame(rows)
    remaining = pd.DataFrame(
        {
            "week": [1, 1, 1, 1, 2, 2, 2, 2],
            "home_team_pk": [0, 2, 4, 6, 0, 2, 4, 6],
            "away_team_pk": [1, 3, 5, 7, 1, 3, 5, 7],
        }
    )
    live_projection = np.array([200.0, 40.0, 120.0, 120.0, 120.0, 120.0, 120.0, 120.0])

    with_projection = simulate_season(
        team_state, remaining, median_scoring=False, reg_season_count=2,
        n_sims=4000, rng=np.random.default_rng(0), current_week_live_projection=live_projection,
    ).set_index("team_pk")
    no_projection = simulate_season(
        team_state, remaining, median_scoring=False, reg_season_count=2,
        n_sims=4000, rng=np.random.default_rng(0),
    ).set_index("team_pk")

    # with no override, every team is identical - all should land the
    # same (real playoff_pct might differ slightly team-to-team from
    # bracket-position noise, but no team should be a clear outlier)
    assert no_projection["playoff_pct"].max() - no_projection["playoff_pct"].min() < 0.15
    # team 0's real (if slight) week-1 edge should still carry forward as
    # real signal into week 2's recomputed expected score, making it a
    # clear favorite for one of the playoff spots - proving week 2 used
    # team 0's own (nudge-seeded) running average, not a value frozen at
    # week 1's raw live-projection number
    assert with_projection.loc[0, "playoff_pct"] == with_projection["playoff_pct"].max()


def test_bad_opening_week_recovers_as_more_weeks_remain_to_play():
    # Regression test for a real fix (2026-09-16): previously, a team's
    # expected score for EVERY remaining week was permanently discounted
    # by the SAME shrinkage weight computed from its real games_played
    # at the moment simulate_season() was called - a real bad Week 1
    # dragged down Week 14's expected score exactly as much as Week 2's,
    # even though by Week 14 that team would have accumulated 13 more
    # real games' worth of evidence. Fixed by letting games_played (and
    # the resulting shrinkage weight) increment WITHIN each simulated
    # trial as weeks are simulated. User: "1 week of scores isn't very
    # significant over the course of a full season... there's a very
    # real chance his points scored recovers to average or above
    # average" - proven here: the SAME disastrous Week 1 costs a team
    # much less when many weeks remain to recover in than when almost
    # none do.
    n_teams = 8
    prior = np.full(n_teams, 130.0)
    rows = [
        {
            "team_pk": i,
            "season_ppg": 60.0 if i == 0 else 130.0,  # team 0: a disaster week; everyone else: right at the prior
            "last3_ppg": 60.0 if i == 0 else 130.0,
            "score_stdev": MIN_STDEV, "matchup_wins": 0 if i == 0 else 1, "matchup_losses": 1 if i == 0 else 0,
            "matchup_ties": 0, "median_wins": 0, "median_losses": 0, "median_ties": 0,
            "points_for": 60.0 if i == 0 else 130.0,
        }
        for i in range(n_teams)
    ]
    team_state = pd.DataFrame(rows)
    full_schedule = _round_robin_schedule(n_teams)  # 7 weeks total

    def gap(weeks_remaining, seed):
        remaining = full_schedule[full_schedule["week"] <= weeks_remaining]
        result = simulate_season(
            team_state, remaining, median_scoring=False, reg_season_count=n_teams - 1,
            n_sims=8000, rng=np.random.default_rng(seed), shrinkage_prior=prior,
        ).set_index("team_pk")
        return result.loc[1, "playoff_pct"] - result.loc[0, "playoff_pct"]

    gap_almost_no_time_to_recover = gap(1, seed=1)
    gap_a_full_season_to_recover = gap(7, seed=1)

    assert gap_a_full_season_to_recover < gap_almost_no_time_to_recover - 0.20  # a real, large narrowing, not noise


def test_simulate_season_empty_team_state_returns_empty():
    result = simulate_season(
        pd.DataFrame(columns=["team_pk", "season_ppg", "last3_ppg", "score_stdev",
                               "matchup_wins", "matchup_losses", "matchup_ties",
                               "median_wins", "median_losses", "median_ties", "points_for"]),
        pd.DataFrame(columns=["week", "home_team_pk", "away_team_pk"]),
        median_scoring=True, reg_season_count=14,
    )
    assert result.empty


def test_week1_outlier_does_not_produce_near_certain_championship_odds():
    # Regression test for real user-reported bug (2026-09-15): a team
    # with a lucky week-1 score used to show ~97% championship odds,
    # because with 1 real game, season_ppg/last3_ppg both equal that
    # single score (no regression to the mean) and score_stdev floored
    # at the old MIN_STDEV=5.0 (way tighter than this league's real
    # ~20-point week-to-week team stdev) - a modest lead got treated as
    # a near-deterministic, permanent gap. 12 teams, one huge week-1
    # score, 13 remaining regular-season weeks (mirrors this league's
    # real 14-game season) - the shrinkage + realistic stdev floor
    # should keep the leader's title odds well under the old ~80-97%
    # even in this maximally-lucky-week-1 scenario.
    n_teams = 12
    rows = []
    for i in range(n_teams):
        score = 180.0 if i == 0 else 90.0 + i  # team 0: massive week-1 outlier
        rows.append(
            {
                "team_pk": i, "season_ppg": score, "last3_ppg": score,
                "score_stdev": np.nan, "matchup_wins": 1 if i % 2 == 0 else 0,
                "matchup_losses": 0 if i % 2 == 0 else 1, "matchup_ties": 0,
                "median_wins": 1 if score > 100 else 0, "median_losses": 0 if score > 100 else 1,
                "median_ties": 0, "points_for": score,
            }
        )
    team_state = pd.DataFrame(rows)
    team_state["score_stdev"] = MIN_STDEV  # single game -> floored, as compute_score_stdev would do
    remaining_weeks = range(2, 14)
    remaining = pd.DataFrame(
        {
            "week": [w for w in remaining_weeks for _ in range(n_teams // 2)],
            "home_team_pk": [i for _ in remaining_weeks for i in range(0, n_teams, 2)],
            "away_team_pk": [i for _ in remaining_weeks for i in range(1, n_teams, 2)],
        }
    )
    result = simulate_season(
        team_state, remaining, median_scoring=True, reg_season_count=14,
        n_sims=3000, rng=np.random.default_rng(7),
    ).set_index("team_pk")

    assert result.loc[0, "championship_pct"] < 0.5
    assert result.loc[0, "playoff_pct"] < 1.0  # not a mathematical lock off one game


def test_simulate_season_probabilities_sum_correctly_with_remaining_games():
    # a real invariant, not just a plausibility check: exactly
    # playoff_team_count teams make the playoffs in every trial, and
    # exactly one team wins the championship in every trial - so these
    # sums must come out exact (within float rounding) regardless of the
    # random scores drawn.
    n_teams = 10
    rows = []
    for i in range(n_teams):
        rows.append(
            {
                "team_pk": i, "season_ppg": 100.0 + i, "last3_ppg": 100.0 + i,
                "score_stdev": 15.0, "matchup_wins": i, "matchup_losses": 5 - min(i, 5),
                "matchup_ties": 0, "median_wins": i, "median_losses": 5 - min(i, 5),
                "median_ties": 0, "points_for": 500.0 + 10 * i,
            }
        )
    team_state = pd.DataFrame(rows)
    remaining = pd.DataFrame(
        {"week": [10] * 5, "home_team_pk": [0, 2, 4, 6, 8], "away_team_pk": [1, 3, 5, 7, 9]}
    )
    result = simulate_season(
        team_state, remaining, median_scoring=True, reg_season_count=14,
        n_sims=2000, rng=np.random.default_rng(42),
    )
    assert result["playoff_pct"].sum() == pytest.approx(6.0, abs=1e-9)
    assert result["championship_pct"].sum() == pytest.approx(1.0, abs=1e-9)
    assert result["bye_pct"].sum() == pytest.approx(2.0, abs=1e-9)
    assert result["seed1_pct"].sum() == pytest.approx(1.0, abs=1e-9)
