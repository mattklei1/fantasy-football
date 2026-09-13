"""Unit tests for fantasy_football.metrics - small synthetic DataFrames
only, no live ESPN calls (per PROJECT_BRIEF testing requirements)."""
import math

import pandas as pd
import pytest

from fantasy_football.metrics.season_metrics import (
    compute_all_play,
    compute_matchup_results,
    compute_median_results,
    compute_season_metrics,
)


def test_all_play_no_ties():
    scores = pd.DataFrame({"week": [1, 1, 1], "team_pk": [1, 2, 3], "score": [10.0, 20.0, 30.0]})
    result = compute_all_play(scores).set_index("team_pk")

    assert result.loc[1, ["all_play_win", "all_play_loss", "all_play_tie"]].tolist() == [0, 2, 0]
    assert result.loc[2, ["all_play_win", "all_play_loss", "all_play_tie"]].tolist() == [1, 1, 0]
    assert result.loc[3, ["all_play_win", "all_play_loss", "all_play_tie"]].tolist() == [2, 0, 0]


def test_all_play_with_tie():
    scores = pd.DataFrame({"week": [1, 1, 1], "team_pk": [1, 2, 3], "score": [10.0, 10.0, 20.0]})
    result = compute_all_play(scores).set_index("team_pk")

    # the two tied-at-10 teams: 0 wins, 1 loss (to the 20), 1 tie (each other)
    assert result.loc[1, ["all_play_win", "all_play_loss", "all_play_tie"]].tolist() == [0, 1, 1]
    assert result.loc[2, ["all_play_win", "all_play_loss", "all_play_tie"]].tolist() == [0, 1, 1]
    assert result.loc[3, ["all_play_win", "all_play_loss", "all_play_tie"]].tolist() == [2, 0, 0]


def test_matchup_results_win_loss_and_points_against():
    matchups = pd.DataFrame(
        {"week": [1], "home_team_pk": [1], "away_team_pk": [2], "home_score": [100.0], "away_score": [90.0]}
    )
    result = compute_matchup_results(matchups).set_index("team_pk")

    assert result.loc[1, "matchup_win"] == 1
    assert result.loc[1, "points_against"] == 90.0
    assert result.loc[2, "matchup_loss"] == 1
    assert result.loc[2, "points_against"] == 100.0


def test_matchup_results_tie():
    matchups = pd.DataFrame(
        {"week": [1], "home_team_pk": [1], "away_team_pk": [2], "home_score": [77.0], "away_score": [77.0]}
    )
    result = compute_matchup_results(matchups).set_index("team_pk")
    assert result.loc[1, "matchup_tie"] == 1
    assert result.loc[2, "matchup_tie"] == 1


def test_median_results_above_below_and_exactly_median():
    # even team count: median = avg(20, 30) = 25
    scores = pd.DataFrame({"week": [1, 1, 1, 1], "team_pk": [1, 2, 3, 4], "score": [10.0, 20.0, 30.0, 40.0]})
    result = compute_median_results(scores).set_index("team_pk")

    assert result.loc[1, "median_loss"] == 1
    assert result.loc[2, "median_loss"] == 1
    assert result.loc[3, "median_win"] == 1
    assert result.loc[4, "median_win"] == 1

    # odd team count where one score IS the exact median -> a tie
    scores_odd = pd.DataFrame({"week": [1, 1, 1], "team_pk": [1, 2, 3], "score": [10.0, 20.0, 30.0]})
    result_odd = compute_median_results(scores_odd).set_index("team_pk")
    assert result_odd.loc[2, "median_tie"] == 1


def _two_week_four_team_season():
    """A beats C every week (and outscores everyone); B beats D every week
    despite B being the 2nd-worst scorer some weeks - this is what makes
    B's matchup record diverge from its all-play quality (a "lucky" win)."""
    scores = pd.DataFrame(
        {
            "week": [1, 1, 1, 1, 2, 2, 2, 2],
            "team_pk": [1, 2, 3, 4, 1, 2, 3, 4],  # A, B, C, D
            "score": [100.0, 90.0, 80.0, 30.0, 110.0, 60.0, 85.0, 25.0],
        }
    )
    matchups = pd.DataFrame(
        {
            "week": [1, 1, 2, 2],
            "home_team_pk": [1, 2, 1, 2],
            "away_team_pk": [3, 4, 3, 4],
            "home_score": [100.0, 90.0, 110.0, 60.0],
            "away_score": [80.0, 30.0, 85.0, 25.0],
        }
    )
    return scores, matchups


def test_expected_wins_and_luck_wins_diverge_for_lucky_team():
    scores, matchups = _two_week_four_team_season()
    result = compute_season_metrics(scores, matchups, median_scoring=False)

    week1 = result[result["week"] == 1].set_index("team_pk")
    # B: beat D (weak team) -> matchup record 1-0, but B is only the
    # 2nd-best scorer of 4 that week -> all-play 2-1 -> expected_wins < 1
    b = week1.loc[2]
    assert b["matchup_wins"] == 1
    assert b["all_play_wins"] == 2 and b["all_play_losses"] == 1
    assert b["expected_wins"] == pytest.approx(2 / 3)
    assert b["luck_wins"] == pytest.approx(1 - 2 / 3)
    assert b["luck_wins"] > 0  # B got lucky relative to its true (all-play) quality

    # A: best scorer AND wins its real matchup every week -> zero luck
    a = week1.loc[1]
    assert a["all_play_wins"] == 3 and a["all_play_losses"] == 0
    assert a["expected_wins"] == pytest.approx(1.0)
    assert a["luck_wins"] == pytest.approx(0.0)


def test_power_score_best_team_hits_100_and_stays_in_bounds():
    scores, matchups = _two_week_four_team_season()
    result = compute_season_metrics(scores, matchups, median_scoring=False)

    assert result["power_score"].between(0, 100).all()

    week2 = result[result["week"] == 2].set_index("team_pk")
    # A: highest PPG both weeks, wins every all-play comparison, wins
    # every real matchup -> every weighted component is exactly 1.0
    assert week2.loc[1, "power_score"] == pytest.approx(100.0)
    # D: lowest PPG both weeks, loses every all-play comparison and every
    # matchup -> strictly the worst power score in the field
    assert week2.loc[4, "power_score"] == week2["power_score"].min()


def test_median_scoring_toggle_changes_actual_win_pct_but_not_luck():
    scores, matchups = _two_week_four_team_season()
    off = compute_season_metrics(scores, matchups, median_scoring=False)
    on = compute_season_metrics(scores, matchups, median_scoring=True)

    # median result is still computed either way (informational)...
    pd.testing.assert_series_equal(
        off["median_wins"].reset_index(drop=True), on["median_wins"].reset_index(drop=True)
    )
    # ...but only folded into actual_win_pct, and thus fraud_index, when the toggle is on
    assert not off["actual_win_pct"].equals(on["actual_win_pct"])
    # Luck Wins is matchup-only by design - the median toggle must NOT move it
    pd.testing.assert_series_equal(
        off["luck_wins"].reset_index(drop=True), on["luck_wins"].reset_index(drop=True)
    )


def test_compute_season_metrics_empty_input_returns_empty():
    empty_scores = pd.DataFrame(columns=["week", "team_pk", "score"])
    empty_matchups = pd.DataFrame(columns=["week", "home_team_pk", "away_team_pk", "home_score", "away_score"])
    result = compute_season_metrics(empty_scores, empty_matchups, median_scoring=False)
    assert result.empty
