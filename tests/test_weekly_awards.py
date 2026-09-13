"""Unit tests for fantasy_football.metrics.weekly_awards - small synthetic
data only, no live ESPN calls (per PROJECT_BRIEF testing requirements)."""
import pandas as pd
import pytest

from fantasy_football.metrics.weekly_awards import compute_weekly_awards

# A 6-team single week engineered so every award has an unambiguous winner:
# team 1 (150, winner) vs team 2 (40, loser)   -> biggest blowout candidate, team1=highest score
# team 3 (100, winner) vs team 4 (95, loser)   -> closest game candidate
# team 5 (90, winner)  vs team 6 (20, loser)   -> team6 = lowest score
#
# All-play (6 teams, scores 150,40,100,95,90,20 - ranked desc: 150,100,95,90,40,20):
#   team1(150): beats all 5 others -> all_play_win=5 (best on paper, and won -> not a great "lucky win" candidate)
#   team3(100): beats 100? no ties; beats 95,90,40,20 -> 4 wins (won, decent record)
#   team4(95):  beats 90,40,20 -> 3 wins (LOST despite a winning-record score -> unluckiest loss)
#   team5(90):  beats 40,20 -> 2 wins (won, weakest winning record among winners -> luckiest win)
#   team2(40):  beats 20 -> 1 win (LOST, weak score - not the bad beat, not the unluckiest)
#   team6(20):  beats nobody -> 0 wins (LOST, also lowest score)
SCORES = pd.DataFrame(
    {
        "week": [1] * 6,
        "team_pk": [1, 2, 3, 4, 5, 6],
        "score": [150.0, 40.0, 100.0, 95.0, 90.0, 20.0],
    }
)
MATCHUPS = pd.DataFrame(
    {
        "week": [1, 1, 1],
        "home_team_pk": [1, 3, 5],
        "away_team_pk": [2, 4, 6],
        "home_score": [150.0, 100.0, 90.0],
        "away_score": [40.0, 95.0, 20.0],
    }
)


def test_highest_and_lowest_score():
    awards = compute_weekly_awards(SCORES, MATCHUPS)
    assert awards["highest_score"]["team_pk"] == 1
    assert awards["lowest_score"]["team_pk"] == 6


def test_biggest_blowout_and_closest_game():
    awards = compute_weekly_awards(SCORES, MATCHUPS)
    # team1 (150) vs team2 (40): margin 110 - the blowout
    assert {awards["biggest_blowout"]["home_team_pk"], awards["biggest_blowout"]["away_team_pk"]} == {1, 2}
    assert awards["biggest_blowout"]["margin"] == pytest.approx(110.0)
    # team3 (100) vs team4 (95): margin 5 - the nail-biter
    assert {awards["closest_game"]["home_team_pk"], awards["closest_game"]["away_team_pk"]} == {3, 4}
    assert awards["closest_game"]["margin"] == pytest.approx(5.0)


def test_bad_beat_is_highest_scoring_loser():
    awards = compute_weekly_awards(SCORES, MATCHUPS)
    # team4 scored 95 and still lost to team3's 100 - the highest score among all losers
    assert awards["bad_beat"]["team_pk"] == 4
    assert awards["bad_beat"]["score"] == pytest.approx(95.0)


def test_unluckiest_loss_uses_all_play_not_just_raw_score():
    awards = compute_weekly_awards(SCORES, MATCHUPS)
    # team4 (95, lost) has the best all-play record (3 wins) among the 3 losers
    # (team2 has 1, team6 has 0) - distinct concept from bad_beat, though they
    # happen to agree here since team4 is also the top-scoring loser
    assert awards["unluckiest_loss"]["team_pk"] == 4
    assert awards["unluckiest_loss"]["all_play_wins"] == 3


def test_luckiest_win_is_weakest_all_play_record_among_winners():
    awards = compute_weekly_awards(SCORES, MATCHUPS)
    # team5 (90, won) has the WORST all-play record (2 wins) among the 3
    # winners (team1 has 5, team3 has 4) - won despite being the weakest
    # winning team that week
    assert awards["luckiest_win"]["team_pk"] == 5
    assert awards["luckiest_win"]["all_play_wins"] == 2


def test_manager_of_the_week_is_highest_score_among_winners():
    awards = compute_weekly_awards(SCORES, MATCHUPS)
    assert awards["manager_of_the_week"]["team_pk"] == 1


def test_lineup_efficiency_awards_are_none_without_roster_data():
    awards = compute_weekly_awards(SCORES, MATCHUPS)
    assert awards["best_lineup_efficiency"] is None
    assert awards["worst_lineup_efficiency"] is None
    assert awards["coaching_disaster"] is None


def test_coaching_disaster_detects_a_flipped_loss():
    # team4 (95) actually lost to team3 (100), but its BENCH had a big
    # game it never used - if the optimal lineup would have scored more
    # than team3's real 100, that's a coaching disaster (loss the manager
    # caused, not bad luck).
    roster = pd.DataFrame(
        {
            "week": [1, 1, 1],
            "team_pk": [4, 4, 3],
            "player_id": [401, 402, 301],
            "is_starter": [1, 0, 1],
            "points": [95.0, 40.0, 100.0],
            "eligible_slots": [frozenset({"QB"}), frozenset({"QB"}), frozenset({"QB"})],
        }
    )
    position_slot_counts = {"QB": 1}
    awards = compute_weekly_awards(SCORES, MATCHUPS, roster_df=roster, position_slot_counts=position_slot_counts)
    # team4's optimal lineup (bench player 402 instead of starter 401) =
    # 40 points, which is WORSE than the real 95 - so this does NOT flip
    # the result. Confirm the module falls back to "biggest points left
    # on bench" rather than fabricating a flip that didn't happen.
    assert awards["coaching_disaster"]["flipped_result"] is False


def test_coaching_disaster_flips_when_bench_beats_the_optimal_call():
    # give team4 a bench player who scored higher than its starter - now
    # the optimal lineup (bench player 403, 120 pts) beats team3's real 100
    roster = pd.DataFrame(
        {
            "week": [1, 1, 1],
            "team_pk": [4, 4, 3],
            "player_id": [401, 403, 301],
            "is_starter": [1, 0, 1],
            "points": [95.0, 120.0, 100.0],
            "eligible_slots": [frozenset({"QB"}), frozenset({"QB"}), frozenset({"QB"})],
        }
    )
    position_slot_counts = {"QB": 1}
    awards = compute_weekly_awards(SCORES, MATCHUPS, roster_df=roster, position_slot_counts=position_slot_counts)
    assert awards["coaching_disaster"]["team_pk"] == 4
    assert awards["coaching_disaster"]["flipped_result"] is True
    assert awards["coaching_disaster"]["points_left_on_bench"] == pytest.approx(25.0)


def test_empty_inputs_return_empty_dict():
    empty = pd.DataFrame(columns=["week", "team_pk", "score"])
    assert compute_weekly_awards(empty, MATCHUPS) == {}
    assert compute_weekly_awards(SCORES, pd.DataFrame(columns=["week", "home_team_pk", "away_team_pk", "home_score", "away_score"])) == {}
