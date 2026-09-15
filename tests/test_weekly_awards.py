"""Unit tests for fantasy_football.metrics.weekly_awards - small synthetic
data only, no live ESPN calls (per PROJECT_BRIEF testing requirements)."""
import pandas as pd
import pytest

from fantasy_football.metrics.weekly_awards import _smart_lineup_call, compute_weekly_awards

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


def test_bad_beat_falls_back_to_highest_scoring_loser_without_roster_data():
    # no roster_df passed at all - pre-2019 seasons have no box-score
    # eligibility data, so bad_beat degrades to the old, plain stat.
    awards = compute_weekly_awards(SCORES, MATCHUPS)
    assert awards["bad_beat"]["team_pk"] == 4
    assert awards["bad_beat"]["score"] == pytest.approx(95.0)
    assert "player_name" not in awards["bad_beat"]


def test_bad_beat_none_when_no_single_starter_shortfall_would_have_flipped_anything():
    # team2 (40) lost badly to team1 (150), and also sits well below the
    # week's median. Its worst starter missed their projection by only 3
    # points - nowhere near enough to flip the matchup OR clear the
    # median on their own. This is "just a down week", not a bad beat,
    # and should NOT be reported as one.
    roster = pd.DataFrame(
        {
            "week": [1, 1],
            "team_pk": [2, 2],
            "player_id": [201, 202],
            "player_name": ["Mild Miss", "Solid Starter"],
            "position": ["WR", "RB"],
            "is_starter": [1, 1],
            "points": [15.0, 25.0],
            "projected_points": [18.0, 25.0],
        }
    )
    awards = compute_weekly_awards(SCORES, MATCHUPS, roster_df=roster)
    assert awards["bad_beat"] is None


def test_bad_beat_identifies_the_single_underperforming_starter_that_flipped_the_matchup():
    # team4 (95) lost to team3 (100). Its starting QB was projected for 30
    # but scored only 5 - a 25-point shortfall. Had he hit his projection,
    # team4's would-be score is 95-5+30=120, which beats team3's real 100.
    roster = pd.DataFrame(
        {
            "week": [1, 1],
            "team_pk": [4, 4],
            "player_id": [401, 402],
            "player_name": ["Hurt QB", "Fine RB"],
            "position": ["QB", "RB"],
            "is_starter": [1, 1],
            "points": [5.0, 90.0],
            "projected_points": [30.0, 90.0],
        }
    )
    awards = compute_weekly_awards(SCORES, MATCHUPS, roster_df=roster)
    bb = awards["bad_beat"]
    assert bb["team_pk"] == 4
    assert bb["player_name"] == "Hurt QB"
    assert bb["position"] == "QB"
    assert bb["projected_points"] == pytest.approx(30.0)
    assert bb["actual_points"] == pytest.approx(5.0)
    assert bb["shortfall"] == pytest.approx(25.0)
    assert bb["flipped_matchup"] is True


def test_bad_beat_only_considers_actual_starters_not_the_bench():
    # team4's bench has a huge shortfall, but a bench player never affects
    # the real result - only a rostered STARTER's underperformance counts.
    roster = pd.DataFrame(
        {
            "week": [1, 1],
            "team_pk": [4, 4],
            "player_id": [401, 402],
            "player_name": ["Starter", "Benched Bust"],
            "position": ["RB", "RB"],
            "is_starter": [1, 0],
            "points": [95.0, 2.0],
            "projected_points": [95.0, 50.0],
        }
    )
    awards = compute_weekly_awards(SCORES, MATCHUPS, roster_df=roster)
    assert awards["bad_beat"] is None


def test_bad_beat_picks_the_biggest_shortfall_among_multiple_qualifying_teams():
    # team2 (40, lost to team1's 150) qualifies via a MEDIAN flip (a
    # 60-point shortfall: would-be score 90->100 clears the recomputed
    # median of 97.5) and team4 (95, lost to team3's 100) qualifies via a
    # MATCHUP flip (an 80-point shortfall: would-be score 175 beats
    # team3's 100) - team4's bigger shortfall wins even though team2's
    # raw score was worse.
    roster = pd.DataFrame(
        {
            "week": [1, 1],
            "team_pk": [2, 4],
            "player_id": [201, 401],
            "player_name": ["Small Miss", "Big Miss"],
            "position": ["WR", "WR"],
            "is_starter": [1, 1],
            "points": [10.0, 20.0],
            "projected_points": [70.0, 100.0],
        }
    )
    awards = compute_weekly_awards(SCORES, MATCHUPS, roster_df=roster)
    assert awards["bad_beat"]["team_pk"] == 4
    assert awards["bad_beat"]["player_name"] == "Big Miss"
    assert awards["bad_beat"]["shortfall"] == pytest.approx(80.0)


def test_unluckiest_loss_uses_all_play_not_just_raw_score():
    awards = compute_weekly_awards(SCORES, MATCHUPS)
    # team4 (95, lost) has the best all-play record (3 wins) among the 3 losers
    # (team2 has 1, team6 has 0) - distinct concept from bad_beat, though they
    # happen to agree here since team4 is also the top-scoring loser
    assert awards["unluckiest_loss"]["team_pk"] == 4
    assert awards["unluckiest_loss"]["all_play_wins"] == 3
    # 6 teams -> 5 all-play opponents (n-1, a team never plays itself) -
    # 3 wins + 2 losses, NOT 3 wins + 3 losses out of the full team count
    assert awards["unluckiest_loss"]["all_play_losses"] == 2


def test_luckiest_win_is_weakest_all_play_record_among_winners():
    awards = compute_weekly_awards(SCORES, MATCHUPS)
    # team5 (90, won) has the WORST all-play record (2 wins) among the 3
    # winners (team1 has 5, team3 has 4) - won despite being the weakest
    # winning team that week
    assert awards["luckiest_win"]["team_pk"] == 5
    assert awards["luckiest_win"]["all_play_wins"] == 2
    assert awards["luckiest_win"]["all_play_losses"] == 3


def test_lineup_efficiency_awards_are_none_without_roster_data():
    awards = compute_weekly_awards(SCORES, MATCHUPS)
    assert awards["best_lineup_efficiency"] is None
    assert awards["worst_lineup_efficiency"] is None
    assert awards["coaching_disaster"] is None
    assert awards["smart_lineup_call"] is None


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
    # no flip this week, so the fallback picks the single biggest points-
    # left-on-bench across ALL rostered teams - team3 (a single-player
    # roster here, points_left_on_bench=0) beats team4's negative value
    # (40-95=-55, since its "optimal" bench swap was actually worse), so
    # team3 is "disaster" here. Its optimal (100, same as its real score)
    # vs. the counterfactual median ([150,40,95,90,20] + 100 -> sorted
    # [20,40,90,95,100,150] -> median 92.5): 100 > 92.5.
    assert awards["coaching_disaster"]["team_pk"] == 3
    assert awards["coaching_disaster"]["optimal_beats_median"] is True


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
    # team4's optimal (120) beats the counterfactual median
    # ([150,40,100,90,20] + 120 -> sorted [20,40,90,100,120,150] -> median 95)
    assert awards["coaching_disaster"]["optimal_beats_median"] is True


def test_empty_inputs_return_empty_dict():
    empty = pd.DataFrame(columns=["week", "team_pk", "score"])
    assert compute_weekly_awards(empty, MATCHUPS) == {}
    assert compute_weekly_awards(SCORES, pd.DataFrame(columns=["week", "home_team_pk", "away_team_pk", "home_score", "away_score"])) == {}


# --- smart_lineup_call -------------------------------------------------

def _slc_row(team_pk, player_id, name, position, slot_position, is_starter, points, projected, eligible):
    return {
        "team_pk": team_pk, "player_id": player_id, "player_name": name, "position": position,
        "slot_position": slot_position, "is_starter": is_starter, "points": points,
        "projected_points": projected, "eligible_slots": frozenset(eligible),
    }


def test_smart_lineup_call_finds_a_started_underdog_that_outscored_a_higher_projected_bench_option():
    roster = pd.DataFrame(
        [
            # started WR, projected LOW (8), actually scored 20
            _slc_row(1, 1, "Started WR", "WR", "WR", 1, 20.0, 8.0, {"WR", "FLEX", "BE"}),
            # benched WR, projected HIGHER (15), actually scored only 5 - the "obvious" call that flopped
            _slc_row(1, 2, "Benched WR", "WR", "BE", 0, 5.0, 15.0, {"WR", "FLEX", "BE"}),
        ]
    )
    result = _smart_lineup_call(roster)
    assert result is not None
    assert result["team_pk"] == 1
    assert result["started"]["player_name"] == "Started WR"
    assert result["benched"]["player_name"] == "Benched WR"
    assert result["actual_swing"] == pytest.approx(15.0)


def test_smart_lineup_call_ignores_a_position_ineligible_bench_player():
    roster = pd.DataFrame(
        [
            _slc_row(1, 1, "Started WR", "WR", "WR", 1, 20.0, 8.0, {"WR", "FLEX", "BE"}),
            # a QB can't fill a WR slot - not a real alternative, even though projected higher
            _slc_row(1, 2, "Benched QB", "QB", "BE", 0, 5.0, 30.0, {"QB", "BE"}),
        ]
    )
    assert _smart_lineup_call(roster) is None


def test_smart_lineup_call_ignores_when_the_obvious_pick_would_have_scored_more():
    roster = pd.DataFrame(
        [
            # started WR outprojected by bench WR, but bench WR ALSO outscored them for real -
            # the "obvious" call would have been right, nothing to celebrate
            _slc_row(1, 1, "Started WR", "WR", "WR", 1, 10.0, 8.0, {"WR", "FLEX", "BE"}),
            _slc_row(1, 2, "Benched WR", "WR", "BE", 0, 25.0, 15.0, {"WR", "FLEX", "BE"}),
        ]
    )
    assert _smart_lineup_call(roster) is None


def test_smart_lineup_call_picks_the_biggest_swing_across_teams():
    roster = pd.DataFrame(
        [
            _slc_row(1, 1, "Small Swing Starter", "WR", "WR", 1, 12.0, 8.0, {"WR", "FLEX", "BE"}),
            _slc_row(1, 2, "Small Swing Bench", "WR", "BE", 0, 5.0, 15.0, {"WR", "FLEX", "BE"}),
            _slc_row(2, 3, "Big Swing Starter", "RB", "FLEX", 1, 30.0, 9.0, {"RB", "FLEX", "BE"}),
            _slc_row(2, 4, "Big Swing Bench", "RB", "BE", 0, 2.0, 20.0, {"RB", "FLEX", "BE"}),
        ]
    )
    result = _smart_lineup_call(roster)
    assert result["team_pk"] == 2
    assert result["started"]["player_name"] == "Big Swing Starter"


def test_smart_lineup_call_none_without_projected_points_column():
    roster = pd.DataFrame(
        {
            "team_pk": [1, 1], "player_id": [1, 2], "player_name": ["A", "B"], "position": ["WR", "WR"],
            "slot_position": ["WR", "BE"], "is_starter": [1, 0], "points": [20.0, 5.0],
            "eligible_slots": [frozenset({"WR", "BE"}), frozenset({"WR", "BE"})],
        }
    )
    assert _smart_lineup_call(roster) is None


def test_smart_lineup_call_none_on_empty_input():
    assert _smart_lineup_call(pd.DataFrame()) is None
