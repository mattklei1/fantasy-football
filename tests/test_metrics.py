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
from fantasy_football.metrics.win_probability import TeamProjection, win_probability
from fantasy_football.metrics.history import compute_head_to_head, compute_league_records, compute_streaks
from fantasy_football.metrics.lineup_optimizer import RosterPlayer, optimal_lineup, starting_slots
from fantasy_football.metrics.lineup_efficiency import compute_lineup_efficiency
from fantasy_football.metrics.roster_strength import (
    bench_weight_for_week,
    compute_player_values,
    compute_team_roster_strength,
    rank_to_score,
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


def test_win_probability_equal_teams_is_fifty_fifty():
    a = TeamProjection(team_pk=1, expected_score=100.0, stdev=15.0)
    b = TeamProjection(team_pk=2, expected_score=100.0, stdev=15.0)
    assert win_probability(a, b) == pytest.approx(0.5)


def test_win_probability_favors_higher_expected_score():
    a = TeamProjection(team_pk=1, expected_score=120.0, stdev=15.0)
    b = TeamProjection(team_pk=2, expected_score=100.0, stdev=15.0)
    assert win_probability(a, b) > 0.5
    # symmetry: P(A beats B) + P(B beats A) == 1
    assert win_probability(a, b) + win_probability(b, a) == pytest.approx(1.0)


def test_win_probability_tighter_stdev_increases_confidence():
    a = TeamProjection(team_pk=1, expected_score=110.0, stdev=5.0)
    b = TeamProjection(team_pk=2, expected_score=100.0, stdev=5.0)
    tight = win_probability(a, b)

    a_wide = TeamProjection(team_pk=1, expected_score=110.0, stdev=40.0)
    b_wide = TeamProjection(team_pk=2, expected_score=100.0, stdev=40.0)
    wide = win_probability(a_wide, b_wide)

    assert tight > wide > 0.5


def test_bench_weight_decays_to_floor_and_holds():
    # week 1: full bye-season value; week 14 (end of reg season): floor only
    assert bench_weight_for_week(1, reg_season_count=14) == pytest.approx(0.35)
    assert bench_weight_for_week(14, reg_season_count=14) == pytest.approx(0.10)
    # playoffs (beyond reg season) - floor persists, never hits zero (injury risk doesn't stop)
    assert bench_weight_for_week(17, reg_season_count=14) == pytest.approx(0.10)
    # monotonically non-increasing across the regular season
    weights = [bench_weight_for_week(w, reg_season_count=14) for w in range(1, 15)]
    assert all(weights[i] >= weights[i + 1] for i in range(len(weights) - 1))


def test_rank_to_score_monotonic_and_bounded():
    assert rank_to_score(1) == pytest.approx(1.0)
    assert rank_to_score(None) is None
    assert rank_to_score(0) is None
    r13 = rank_to_score(13)
    r25 = rank_to_score(25)
    assert 0 < r25 < r13 < 1.0


def test_player_values_percentile_computed_within_position():
    # a QB with a mediocre raw projection among QBs should NOT be dragged
    # down by comparison to a much-higher-scoring RB position group
    roster = pd.DataFrame(
        {
            "team_pk": [1, 1, 1, 1],
            "player_id": [1, 2, 3, 4],
            "position": ["QB", "QB", "RB", "RB"],
            "slot_position": ["QB", "BE", "RB", "BE"],
            "is_starter": [1, 0, 1, 0],
            "projected_points": [20.0, 15.0, 25.0, 5.0],
            "pos_rank": [None, None, None, None],
        }
    )
    result = compute_player_values(roster)
    qb_best = result[result["player_id"] == 1]["projection_percentile"].iloc[0]
    # best QB (20 pts, top of the QB group) should get the top QB percentile
    # regardless of RB scores being numerically higher
    assert qb_best == pytest.approx(1.0)


def test_player_value_blends_fantasypros_signal_when_present():
    roster = pd.DataFrame(
        {
            "team_pk": [1, 1],
            "player_id": [1, 2],
            "position": ["QB", "QB"],
            "slot_position": ["QB", "BE"],
            "is_starter": [1, 0],
            "projected_points": [20.0, 10.0],
            "pos_rank": [None, None],
            "fp_pos_rank": [1, 25],
        }
    )
    result = compute_player_values(roster).set_index("player_id")
    # with only the FantasyPros signal present (no ESPN pos_rank), a
    # top-ranked FP player should score much higher than a QB25
    assert result.loc[1, "player_value"] > result.loc[2, "player_value"]
    # missing signals renormalize rather than drag the score toward 0 -
    # a QB1 with only 1 of 3 signals present should still score near 1.0,
    # not get capped near the FantasyPros weight (40/75 ~= 0.53)
    assert result.loc[1, "player_value"] == pytest.approx(1.0, abs=0.05)


def test_player_value_falls_back_when_fantasypros_column_absent():
    # older call sites (or a DB with no FANTASYPROS_API_KEY configured)
    # won't have an fp_pos_rank column at all - must not crash
    roster = pd.DataFrame(
        {
            "team_pk": [1],
            "player_id": [1],
            "position": ["QB"],
            "slot_position": ["QB"],
            "is_starter": [1],
            "projected_points": [20.0],
            "pos_rank": [1],
        }
    )
    result = compute_player_values(roster)
    assert result["player_value"].iloc[0] == pytest.approx(1.0)


def test_team_roster_strength_excludes_ir_and_splits_starter_bench():
    roster = pd.DataFrame(
        {
            "team_pk": [1, 1, 1],
            "player_id": [1, 2, 3],
            "position": ["QB", "QB", "QB"],
            "slot_position": ["QB", "BE", "IR"],
            "is_starter": [1, 0, 0],
            "projected_points": [20.0, 10.0, 0.0],
            "pos_rank": [1, 20, 999],
        }
    )
    valued = compute_player_values(roster)
    result = compute_team_roster_strength(valued, week=1, reg_season_count=14)
    row = result.iloc[0]
    # IR player must not appear in either pool - only 1 starter, 1 bench player feed the averages
    assert row["starter_value"] == valued[valued["player_id"] == 1]["player_value"].iloc[0]
    assert row["bench_value"] == valued[valued["player_id"] == 2]["player_value"].iloc[0]
    assert row["bench_weight"] == pytest.approx(0.35)
    assert 0 <= row["roster_strength"] <= 100


def test_starting_slots_excludes_bench_and_ir():
    slots = starting_slots({"QB": 1, "RB": 2, "BE": 6, "IR": 1, "": 0})
    assert slots == ["QB", "RB", "RB"]


def test_optimal_lineup_picks_highest_scorer_per_slot():
    players = [
        RosterPlayer(1, 10.0, frozenset({"QB", "BE"})),
        RosterPlayer(2, 20.0, frozenset({"QB", "BE"})),
        RosterPlayer(3, 5.0, frozenset({"RB", "BE"})),
    ]
    points, assignment = optimal_lineup(players, {"QB": 1, "RB": 1, "BE": 2})
    assert points == pytest.approx(25.0)
    assert set(assignment.values()) == {2, 3}  # the 20pt QB and the only RB, not the 10pt QB


def test_optimal_lineup_beats_naive_greedy_on_flex_contention():
    # A naive "assign highest scorer to whichever slot comes first" greedy
    # can misallocate when a flex slot creates contention. True optimum:
    # RB_A -> dedicated RB slot, WR_A -> flex (19 total), NOT RB_A -> flex.
    players = [
        RosterPlayer(1, 10.0, frozenset({"RB", "RB/WR/TE", "BE"})),  # RB_A
        RosterPlayer(2, 8.0, frozenset({"RB", "RB/WR/TE", "BE"})),   # RB_B
        RosterPlayer(3, 9.0, frozenset({"WR", "RB/WR/TE", "BE"})),   # WR_A
    ]
    points, assignment = optimal_lineup(players, {"RB": 1, "RB/WR/TE": 1, "BE": 1})
    assert points == pytest.approx(19.0)
    assert set(assignment.values()) == {1, 3}


def test_optimal_lineup_leaves_unfillable_slot_empty():
    # no player eligible for TE - that slot must stay empty, not crash
    players = [RosterPlayer(1, 10.0, frozenset({"QB", "BE"}))]
    points, assignment = optimal_lineup(players, {"QB": 1, "TE": 1})
    assert points == pytest.approx(10.0)
    assert len(assignment) == 1


def test_lineup_efficiency_flags_manager_caused_loss():
    # Team 1, week 1: actual lineup scores 15 (left a 25pt bench player
    # unstarted), opponent scored 20. Actual result: loss. But optimal
    # lineup (25) would have beaten 20 - a manager-caused loss.
    roster = pd.DataFrame(
        {
            "week": [1, 1],
            "team_pk": [1, 1],
            "player_id": [1, 2],
            "points": [15.0, 25.0],
            "is_starter": [1, 0],
            "eligible_slots": [frozenset({"QB", "BE"}), frozenset({"QB", "BE"})],
        }
    )
    matchups = pd.DataFrame(
        {"week": [1], "home_team_pk": [1], "away_team_pk": [2], "home_score": [15.0], "away_score": [20.0]}
    )
    result = compute_lineup_efficiency(roster, matchups, {"QB": 1, "BE": 1})
    row = result.iloc[0]
    assert row["actual_starter_points"] == pytest.approx(15.0)
    assert row["optimal_starter_points"] == pytest.approx(25.0)
    assert row["points_left_on_bench"] == pytest.approx(10.0)
    assert row["lineup_efficiency"] == pytest.approx(15.0 / 25.0)
    assert row["manager_caused_losses"] == 1
    assert row["optimal_wins"] == 1
    # actual record: team 1 scored 15, opponent scored 20 -> a real loss
    assert row["matchup_wins"] == 0
    assert row["matchup_losses"] == 1
    assert row["matchup_ties"] == 0


def test_decision_accuracy_survives_a_single_bench_blowup():
    # 5 starting slots. 4 players are correctly started either way. The
    # 5th slot: actual started a 5pt scrub over a bench player who
    # blows up for 50 - a single wrong swap, but it wrecks points-based
    # efficiency while decision accuracy correctly shows "4/5 right."
    correct_ids = [1, 2, 3, 4]
    roster = pd.DataFrame(
        {
            "week": [1] * 6,
            "team_pk": [1] * 6,
            "player_id": correct_ids + [5, 6],
            "points": [10.0, 10.0, 10.0, 10.0, 5.0, 50.0],
            "is_starter": [1, 1, 1, 1, 1, 0],  # player 5 started (wrong), player 6 benched (should've started)
            "eligible_slots": [frozenset({"QB", "BE"})] * 6,
        }
    )
    matchups = pd.DataFrame(
        {"week": [1], "home_team_pk": [1], "away_team_pk": [2], "home_score": [45.0], "away_score": [30.0]}
    )
    result = compute_lineup_efficiency(roster, matchups, {"QB": 5, "BE": 1})
    row = result.iloc[0]

    assert row["actual_starter_points"] == pytest.approx(45.0)   # 40 + 5
    assert row["optimal_starter_points"] == pytest.approx(90.0)  # 40 + 50
    assert row["lineup_efficiency"] == pytest.approx(45.0 / 90.0)  # looks like a disastrous 50% week

    # but only ONE of the five decisions was actually wrong
    assert row["correct_decisions"] == 4
    assert row["total_decisions"] == 5
    assert row["decision_accuracy"] == pytest.approx(0.8)


def test_compute_streaks_finds_longest_and_current():
    results = pd.Series(["W", "W", "L", "W", "W", "W", "L"])
    streaks = compute_streaks(results)
    assert streaks["longest_win_streak"] == 3
    assert streaks["longest_loss_streak"] == 1
    assert streaks["current_streak_result"] == "L"
    assert streaks["current_streak_length"] == 1


def test_compute_streaks_all_wins():
    streaks = compute_streaks(pd.Series(["W", "W", "W"]))
    assert streaks["longest_win_streak"] == 3
    assert streaks["current_streak_result"] == "W"
    assert streaks["current_streak_length"] == 3


def _mock_h2h_matchups():
    # Manager A vs Manager B across 2 seasons: A wins big in S1 wk1,
    # loses close in S1 wk2, A wins the S2 playoff game
    return pd.DataFrame(
        {
            "season_id": [2023, 2023, 2024],
            "week": [1, 2, 15],
            "is_playoff": [0, 0, 1],
            "matchup_type": ["NONE", "NONE", "WINNERS_BRACKET"],
            "home_manager_id": ["A", "B", "A"],
            "away_manager_id": ["B", "A", "B"],
            "home_score": [130.0, 102.0, 110.0],
            "away_score": [80.0, 100.0, 105.0],
        }
    )


def test_head_to_head_record_and_points():
    result = compute_head_to_head(_mock_h2h_matchups(), "A", "B")
    assert result["games_played"] == 3
    assert result["a_wins"] == 2
    assert result["b_wins"] == 1
    assert result["a_total_points"] == pytest.approx(130.0 + 100.0 + 110.0)
    assert result["b_total_points"] == pytest.approx(80.0 + 102.0 + 105.0)
    assert result["playoff_games"] == 1
    assert result["a_playoff_wins"] == 1
    assert result["b_playoff_wins"] == 0


def test_head_to_head_largest_margin_and_closest_game():
    result = compute_head_to_head(_mock_h2h_matchups(), "A", "B")
    assert result["largest_margin"] == pytest.approx(50.0)  # 130-80 in week 1
    assert result["largest_margin_winner"] == "A"
    assert result["closest_margin"] == pytest.approx(2.0)  # 102-100 in week 2


def test_head_to_head_current_streak():
    # A's chronological results: W (wk1), L (wk2), W (wk playoff) -> current streak is W x1
    result = compute_head_to_head(_mock_h2h_matchups(), "A", "B")
    assert result["current_streak_manager"] == "A"
    assert result["current_streak_length"] == 1


def test_head_to_head_no_games_played():
    result = compute_head_to_head(_mock_h2h_matchups(), "A", "C")
    assert result["games_played"] == 0


def test_head_to_head_playoff_record_excludes_losers_consolation_ladder():
    # ESPN tags the consolation bracket (for teams that MISSED the
    # playoffs) as is_playoff=1 too - playoff_games/wins must not count it,
    # only the real playoff bracket (matchup_type in REAL_PLAYOFF_MATCHUP_TYPES)
    matchups = pd.DataFrame(
        {
            "season_id": [2023, 2024],
            "week": [15, 16],
            "is_playoff": [1, 1],
            "matchup_type": ["WINNERS_BRACKET", "LOSERS_CONSOLATION_LADDER"],
            "home_manager_id": ["A", "A"],
            "away_manager_id": ["B", "B"],
            "home_score": [120.0, 90.0],
            "away_score": [100.0, 95.0],
        }
    )
    result = compute_head_to_head(matchups, "A", "B")
    assert result["games_played"] == 2  # counts both in the all-time series
    assert result["a_wins"] == 1  # won game 1 (120-100), lost game 2 (90-95)
    assert result["playoff_games"] == 1  # but only the real playoff bracket game
    assert result["a_playoff_wins"] == 1
    assert result["b_playoff_wins"] == 0


def test_league_records_identifies_extremes():
    records = pd.DataFrame(
        {
            "season_id": [2020, 2020, 2021, 2021, 2022, 2022],
            "week": [1, 1, 5, 5, 3, 3],
            "team_pk": [1, 2, 1, 2, 4, 3],
            "team_name": [
                "Blowout Team", "Loser Team", "Bad Beat Team", "Sneaky Winner",
                "Nailbiter B", "Nailbiter A",
            ],
            "score": [200.0, 50.0, 90.0, 60.0, 99.0, 100.0],
            "opp_score": [50.0, 200.0, 60.0, 90.0, 100.0, 99.0],
            "is_playoff": [0, 0, 0, 0, 0, 0],
        }
    )
    result = compute_league_records(records)
    assert result["highest_score"]["team_name"] == "Blowout Team"
    assert result["lowest_score"]["team_name"] == "Loser Team"
    assert result["biggest_blowout"]["team_name"] == "Blowout Team"
    assert result["biggest_blowout"]["margin"] == pytest.approx(150.0)
    # "Nailbiter B" (the LOSING side, margin=-1) sorts first on ties - this
    # specifically catches a real bug where the raw signed margin was
    # displayed instead of the absolute value (showed -1 instead of 1)
    assert result["closest_game"]["margin"] == pytest.approx(1.0)
    assert result["closest_game"]["margin"] >= 0
    # week 5: "Bad Beat Team" won 90-60, "Sneaky Winner" lost 60-90
    # "Nailbiter B" lost 99-100 - the highest score of any loss in this set
    assert result["most_points_in_loss"]["team_name"] == "Nailbiter B"
    assert result["lowest_score_in_win"]["team_name"] == "Bad Beat Team"  # won with only 90, the least of any win


def test_compute_season_metrics_empty_input_returns_empty():
    empty_scores = pd.DataFrame(columns=["week", "team_pk", "score"])
    empty_matchups = pd.DataFrame(columns=["week", "home_team_pk", "away_team_pk", "home_score", "away_score"])
    result = compute_season_metrics(empty_scores, empty_matchups, median_scoring=False)
    assert result.empty
