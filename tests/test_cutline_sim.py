"""Unit tests for fantasy_football.metrics.cutline_sim - pure logic only,
no network/DB (matches the rest of metrics/'s testing convention)."""
import numpy as np
import pytest

from fantasy_football.metrics.cutline_sim import (
    TeamScoreModel,
    percentile_range,
    simulate_cutline_probabilities,
)


def test_percentile_range_is_symmetric_around_mean():
    team = TeamScoreModel(team_pk=1, mean=100.0, stdev=20.0)
    p10, p90 = percentile_range(team)
    assert p10 == pytest.approx(100.0 - 1.2815515655446004 * 20.0)
    assert p90 == pytest.approx(100.0 + 1.2815515655446004 * 20.0)
    # symmetric around the mean
    assert (100.0 - p10) == pytest.approx(p90 - 100.0)


def test_percentile_range_floors_at_zero_for_low_mean_high_stdev():
    team = TeamScoreModel(team_pk=1, mean=5.0, stdev=20.0)
    p10, _ = percentile_range(team)
    assert p10 == 0.0


def test_simulate_cutline_probabilities_empty_list():
    assert simulate_cutline_probabilities([]) == {}


def test_simulate_cutline_probabilities_best_team_almost_always_makes_it():
    # 12 teams, one way out ahead of the pack (stdev tight enough that
    # overlap is essentially impossible) - should be ~1.0
    teams = [TeamScoreModel(team_pk=1, mean=200.0, stdev=5.0)] + [
        TeamScoreModel(team_pk=i, mean=100.0, stdev=5.0) for i in range(2, 13)
    ]
    probs = simulate_cutline_probabilities(teams, n_sims=5000, rng=np.random.default_rng(42))
    assert probs[1] > 0.999


def test_simulate_cutline_probabilities_worst_team_almost_never_makes_it():
    teams = [TeamScoreModel(team_pk=1, mean=10.0, stdev=5.0)] + [
        TeamScoreModel(team_pk=i, mean=100.0, stdev=5.0) for i in range(2, 13)
    ]
    probs = simulate_cutline_probabilities(teams, n_sims=5000, rng=np.random.default_rng(42))
    assert probs[1] < 0.001


def test_simulate_cutline_probabilities_identical_teams_are_all_near_50_50():
    # 12 identical teams - top half is a coin flip for everyone
    teams = [TeamScoreModel(team_pk=i, mean=100.0, stdev=15.0) for i in range(1, 13)]
    probs = simulate_cutline_probabilities(teams, n_sims=20000, rng=np.random.default_rng(7))
    for p in probs.values():
        assert 0.4 < p < 0.6


def test_simulate_cutline_probabilities_ranks_middle_of_pack_teams_between_extremes():
    # A monotonically increasing set of means - probabilities should be
    # monotonically increasing too, and the exact-median team lands near
    # the top-half boundary (a coin flip, since it IS the boundary).
    teams = [TeamScoreModel(team_pk=i, mean=float(i * 10), stdev=8.0) for i in range(1, 13)]
    probs = simulate_cutline_probabilities(teams, n_sims=20000, rng=np.random.default_rng(3))
    ordered = [probs[i] for i in range(1, 13)]
    assert ordered == sorted(ordered)
    # team 6 and team 7 straddle the cutoff (top-6-of-12 make it) - team 6
    # (7th-highest, just missing) and team 7 (6th-highest, just making
    # it) should both be closer to a coin flip than the clear-cut
    # extremes, with team 7 > team 6
    assert probs[7] > probs[6]
    assert 0.15 < probs[6] < 0.85
    assert 0.15 < probs[7] < 0.85
