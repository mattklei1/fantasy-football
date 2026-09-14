"""Unit tests for fantasy_football.war_room_data - pure logic only
(blend_trade_value, optimal_roster_value, evaluate_trade), no network/DB
calls per PROJECT_BRIEF testing requirements. The rest of war_room_data.py
(live FantasyPros/ESPN calls, SQLite reads) is exercised via
in-browser/AppTest validation instead, same convention as
dashboard_data.py's live-data functions."""
import json

import pandas as pd
import pytest

from fantasy_football.war_room_data import (
    BENCH_SLOT_ID,
    NO_SLOT_ID,
    _waiver_claim_payload,
    blend_trade_value,
    build_waiver_suggestion_reasoning,
    evaluate_trade,
    find_win_win_trades,
    optimal_roster_value,
    slot_name_to_id,
)


def test_blends_both_signals_when_present():
    value = blend_trade_value(1.0, 0.5, "WR", {})
    # weighted average of 1.0 (weight 40) and 0.5 (weight 15): (40+7.5)/55
    assert value == pytest.approx((40 * 1.0 + 15 * 0.5) / 55)


def test_falls_back_to_only_present_signal_without_penalizing_missing_one():
    fp_only = blend_trade_value(0.8, None, "WR", {})
    assert fp_only == pytest.approx(0.8)

    espn_only = blend_trade_value(None, 0.8, "WR", {})
    assert espn_only == pytest.approx(0.8)


def test_zero_when_no_signal_present_at_all():
    assert blend_trade_value(None, None, "WR", {}) == 0.0


def test_fantasypros_signal_weighted_more_than_espn_signal():
    # same score on either signal alone should give the same value either
    # way (weights only matter when they DISAGREE) - so disagree them:
    fp_leaning = blend_trade_value(1.0, 0.0, "WR", {})
    espn_leaning = blend_trade_value(0.0, 1.0, "WR", {})
    assert fp_leaning > espn_leaning


def test_superflex_scarcity_multiplier_boosts_qb_value():
    flat = blend_trade_value(0.8, 0.8, "QB", {})
    superflex = blend_trade_value(0.8, 0.8, "QB", {"QB": 1.5})
    assert superflex == pytest.approx(flat * 1.5)


def test_scarcity_multiplier_does_not_affect_other_positions():
    scarcity = {"QB": 1.5}
    rb = blend_trade_value(0.6, 0.6, "RB", scarcity)
    rb_no_scarcity = blend_trade_value(0.6, 0.6, "RB", {})
    assert rb == pytest.approx(rb_no_scarcity)


# --- optimal_roster_value / evaluate_trade --------------------------------
# A single-QB/single-RB toy league (position_slot_counts = {"QB": 1, "RB": 1})
# keeps the optimal-lineup math easy to hand-verify while still exercising
# the real Hungarian-algorithm solver (metrics/lineup_optimizer.py), not a
# reimplementation of it.

SLOT_COUNTS = {"QB": 1, "RB": 1}


def _roster_row(team_name, player_id, player_name, position, value_score, eligible_slots):
    return {
        "team_name": team_name, "player_id": player_id, "player_name": player_name,
        "position": position, "value_score": value_score,
        "eligible_slots": json.dumps(eligible_slots),
    }


def test_optimal_roster_value_only_counts_players_that_actually_start():
    df = pd.DataFrame(
        [
            _roster_row("Team A", 1, "QB Starter", "QB", 50.0, ["QB", "BE"]),
            _roster_row("Team A", 2, "RB Starter", "RB", 60.0, ["RB", "BE"]),
            _roster_row("Team A", 3, "RB Backup", "RB", 20.0, ["RB", "BE"]),
        ]
    )
    total, starters = optimal_roster_value(df, SLOT_COUNTS)
    assert total == pytest.approx(110.0)  # 50 + 60, NOT +20 for the buried backup
    assert starters == {1, 2}


def test_optimal_roster_value_empty_roster_is_zero_not_an_error():
    df = pd.DataFrame(columns=["player_id", "value_score", "eligible_slots"])
    total, starters = optimal_roster_value(df, SLOT_COUNTS)
    assert total == 0.0
    assert starters == set()


def test_evaluate_trade_2for1_gain_is_marginal_not_the_raw_incoming_value():
    """The exact scenario the user described: trading 2 bench-caliber
    players for 1 difference-maker should NOT show a gain equal to the
    star's full raw value - it should show only the upgrade over whoever
    he actually replaces in the lineup, because the roster can only start
    one RB either way."""
    rosters = pd.DataFrame(
        [
            _roster_row("Team A", 1, "QB A1", "QB", 50.0, ["QB", "BE"]),
            _roster_row("Team A", 2, "RB A1", "RB", 60.0, ["RB", "BE"]),
            _roster_row("Team A", 3, "RB A2 (bench)", "RB", 20.0, ["RB", "BE"]),
            _roster_row("Team A", 4, "Throwaway (bench)", "RB", 10.0, ["RB", "BE"]),
            _roster_row("Team B", 5, "QB B1", "QB", 40.0, ["QB", "BE"]),
            _roster_row("Team B", 6, "RB Star", "RB", 90.0, ["RB", "BE"]),
        ]
    )
    result = evaluate_trade(
        rosters, "Team A", "Team B",
        player_ids_out_a=[3, 4], player_ids_out_b=[6],
        position_slot_counts=SLOT_COUNTS,
    )

    # Team A: before 50+60=110, after 50+90=140 (RB Star replaces RB A1,
    # NOT a naive +90 for "receiving a 90-value player") - true gain is 30.
    assert result["Team A"]["before"] == pytest.approx(110.0)
    assert result["Team A"]["after"] == pytest.approx(140.0)
    assert result["Team A"]["gain"] == pytest.approx(30.0)
    assert "RB Star" in result["Team A"]["newly_starting"]
    assert "RB A1" in result["Team A"]["newly_benched"]

    # Team B: before 40+90=130, after only 40+20=60 (their best remaining
    # RB is the throwaway 20-value pickup) - a real, large loss.
    assert result["Team B"]["before"] == pytest.approx(130.0)
    assert result["Team B"]["after"] == pytest.approx(60.0)
    assert result["Team B"]["gain"] == pytest.approx(-70.0)


def test_evaluate_trade_no_bench_impact_when_receiving_player_still_sits():
    """Adding a player who can't crack the lineup anyway (already 2-deep
    at a 1-starter position) should show ~zero gain - the model should
    NOT reward hoarding depth that never plays."""
    rosters = pd.DataFrame(
        [
            _roster_row("Team A", 1, "QB A1", "QB", 50.0, ["QB", "BE"]),
            _roster_row("Team A", 2, "RB A1", "RB", 60.0, ["RB", "BE"]),
            _roster_row("Team B", 3, "QB B1", "QB", 10.0, ["QB", "BE"]),
            _roster_row("Team B", 4, "RB B1 (mediocre)", "RB", 25.0, ["RB", "BE"]),
        ]
    )
    result = evaluate_trade(
        rosters, "Team A", "Team B",
        player_ids_out_a=[], player_ids_out_b=[4],
        position_slot_counts=SLOT_COUNTS,
    )
    # Team A already starts a better RB (60) than the incoming 25-value
    # player, so he just sits - gain should be 0, not +25.
    assert result["Team A"]["gain"] == pytest.approx(0.0)
    assert result["Team A"]["newly_starting"] == []


# --- build_waiver_suggestion_reasoning -------------------------------------

def test_reasoning_flags_missing_position_entirely():
    text = build_waiver_suggestion_reasoning("TE", 40.0, None, 0, 10.0, 50.0)
    assert "nobody rostered at TE" in text


def test_reasoning_flags_real_starter_upgrade():
    text = build_waiver_suggestion_reasoning("WR", 70.0, 50.0, 2, 15.0, 50.0)
    assert "upgrade" in text
    assert "70" in text and "50" in text


def test_reasoning_does_not_claim_upgrade_when_worse_than_current_best():
    text = build_waiver_suggestion_reasoning("WR", 30.0, 50.0, 2, 15.0, 50.0)
    assert "upgrade" not in text


def test_reasoning_flags_thin_bench_depth():
    zero_depth = build_waiver_suggestion_reasoning("RB", 20.0, 60.0, 0, 10.0, 50.0)
    one_depth = build_waiver_suggestion_reasoning("RB", 20.0, 60.0, 1, 10.0, 50.0)
    two_depth = build_waiver_suggestion_reasoning("RB", 20.0, 60.0, 2, 10.0, 50.0)
    assert "zero bench depth" in zero_depth
    assert "only one bench player" in one_depth
    assert "bench depth" not in two_depth and "bench player" not in two_depth


def test_reasoning_flags_bid_exceeding_remaining_budget():
    text = build_waiver_suggestion_reasoning("RB", 20.0, 60.0, 2, 75.0, 50.0)
    assert "exceeds your $50" in text


def test_reasoning_falls_back_to_generic_when_nothing_else_applies():
    text = build_waiver_suggestion_reasoning("K", 20.0, 60.0, 2, 5.0, 50.0)
    assert text == "best available K on waivers right now"


# --- _waiver_claim_payload -------------------------------------------------
# Matches the EXACT shape confirmed live against the real league on
# 2026-09-14 (see PROJECT_BRIEF) - these tests lock that shape in so a
# future refactor can't silently drift from what ESPN actually accepts.

def test_waiver_claim_payload_matches_verified_live_shape():
    payload = _waiver_claim_payload(
        team_id=1, add_player_id=4566158, drop_player_id=4696044,
        bid_amount=1, scoring_period=1, member_id="{00000000-0000-0000-0000-000000000000}",
    )
    assert payload == {
        "isLeagueManager": False,
        "teamId": 1,
        "type": "WAIVER",
        "memberId": "{00000000-0000-0000-0000-000000000000}",
        "bidAmount": 1,
        "scoringPeriodId": 1,
        "executionType": "EXECUTE",
        "items": [
            {
                "playerId": 4566158, "type": "ADD",
                "fromLineupSlotId": NO_SLOT_ID, "toLineupSlotId": BENCH_SLOT_ID, "toTeamId": 1,
            },
            {
                "playerId": 4696044, "type": "DROP",
                "fromLineupSlotId": BENCH_SLOT_ID, "toLineupSlotId": NO_SLOT_ID, "fromTeamId": 1,
            },
        ],
    }


def test_waiver_claim_payload_omits_drop_item_when_no_drop():
    payload = _waiver_claim_payload(
        team_id=1, add_player_id=4566158, drop_player_id=None,
        bid_amount=5, scoring_period=2, member_id="{SOME-SWID}",
    )
    assert len(payload["items"]) == 1
    assert payload["items"][0]["type"] == "ADD"


def test_waiver_claim_payload_bench_slot_id_is_20():
    # locked in from the real confirmed response - a magic-number typo
    # here would silently target the wrong slot on a real claim
    assert BENCH_SLOT_ID == 20
    assert NO_SLOT_ID == -1


def test_waiver_claim_payload_rounds_bid_to_whole_dollar():
    payload = _waiver_claim_payload(
        team_id=1, add_player_id=1, drop_player_id=None,
        bid_amount=25.805765853658542, scoring_period=1, member_id="{X}",
    )
    assert payload["bidAmount"] == 26


# --- find_win_win_trades ----------------------------------------------
# Same toy 1-QB/1-RB league as evaluate_trade's tests above.

def test_find_win_win_trades_surfaces_a_real_1for1_mutual_upgrade():
    """Mine is QB-weak/RB-fine; Team B is RB-weak/QB-fine (with a near-
    equal QB backup so giving up their starter barely costs them) -
    trading my RB starter for their QB starter upgrades BOTH sides'
    optimal lineup value."""
    rosters = pd.DataFrame(
        [
            _roster_row("Mine", 1, "QB Mine", "QB", 30.0, ["QB", "BE"]),
            _roster_row("Mine", 2, "RB Mine 1", "RB", 80.0, ["RB", "BE"]),
            _roster_row("Mine", 3, "RB Mine 2", "RB", 70.0, ["RB", "BE"]),
            _roster_row("Team B", 4, "QB B1", "QB", 85.0, ["QB", "BE"]),
            _roster_row("Team B", 5, "QB B2", "QB", 80.0, ["QB", "BE"]),
            _roster_row("Team B", 6, "RB B", "RB", 20.0, ["RB", "BE"]),
        ]
    )
    results = find_win_win_trades(rosters, "Mine", SLOT_COUNTS, gain_threshold=5.0)
    matches = [
        r for r in results
        if r["opponent"] == "Team B" and r["players_out"] == ["RB Mine 1"] and r["players_in"] == ["QB B1"]
    ]
    assert len(matches) == 1
    trade = matches[0]
    assert trade["my_gain"] > 5.0
    assert trade["their_gain"] > 5.0


def test_find_win_win_trades_excludes_opponents_with_no_real_win_win():
    """Team C is worse than Mine at everything with no redundant depth -
    any trade that helps Mine hurts them, so nothing should surface."""
    rosters = pd.DataFrame(
        [
            _roster_row("Mine", 1, "QB Mine", "QB", 50.0, ["QB", "BE"]),
            _roster_row("Mine", 2, "RB Mine", "RB", 60.0, ["RB", "BE"]),
            _roster_row("Team C", 3, "QB C", "QB", 10.0, ["QB", "BE"]),
            _roster_row("Team C", 4, "RB C", "RB", 5.0, ["RB", "BE"]),
        ]
    )
    results = find_win_win_trades(rosters, "Mine", SLOT_COUNTS, gain_threshold=5.0)
    assert results == []


def test_find_win_win_trades_sorts_by_min_gain_descending():
    """Team B's trade is a bigger mutual win than Team D's smaller one -
    Team B should be ranked first."""
    rosters = pd.DataFrame(
        [
            _roster_row("Mine", 1, "QB Mine", "QB", 30.0, ["QB", "BE"]),
            _roster_row("Mine", 2, "RB Mine 1", "RB", 80.0, ["RB", "BE"]),
            _roster_row("Mine", 3, "RB Mine 2", "RB", 70.0, ["RB", "BE"]),
            # Big mutual win (same fixture as the first test above)
            _roster_row("Team B", 4, "QB B1", "QB", 85.0, ["QB", "BE"]),
            _roster_row("Team B", 5, "QB B2", "QB", 80.0, ["QB", "BE"]),
            _roster_row("Team B", 6, "RB B", "RB", 20.0, ["RB", "BE"]),
            # Smaller mutual win - a modest QB upgrade for a modest RB upgrade
            _roster_row("Team D", 7, "QB D1", "QB", 36.0, ["QB", "BE"]),
            _roster_row("Team D", 8, "QB D2", "QB", 34.0, ["QB", "BE"]),
            _roster_row("Team D", 9, "RB D", "RB", 66.0, ["RB", "BE"]),
        ]
    )
    results = find_win_win_trades(rosters, "Mine", SLOT_COUNTS, gain_threshold=1.0)
    opponents_in_order = [r["opponent"] for r in results]
    assert opponents_in_order.index("Team B") < opponents_in_order.index("Team D")


def test_find_win_win_trades_respects_max_results():
    rosters = pd.DataFrame(
        [
            _roster_row("Mine", 1, "QB Mine", "QB", 30.0, ["QB", "BE"]),
            _roster_row("Mine", 2, "RB Mine 1", "RB", 80.0, ["RB", "BE"]),
            _roster_row("Mine", 3, "RB Mine 2", "RB", 70.0, ["RB", "BE"]),
            _roster_row("Team B", 4, "QB B1", "QB", 85.0, ["QB", "BE"]),
            _roster_row("Team B", 5, "QB B2", "QB", 80.0, ["QB", "BE"]),
            _roster_row("Team B", 6, "RB B", "RB", 20.0, ["RB", "BE"]),
        ]
    )
    results = find_win_win_trades(rosters, "Mine", SLOT_COUNTS, gain_threshold=5.0, max_results=1)
    assert len(results) <= 1


def test_find_win_win_trades_my_team_not_in_rosters_returns_empty():
    rosters = pd.DataFrame(
        [_roster_row("Team B", 1, "QB B", "QB", 50.0, ["QB", "BE"])]
    )
    assert find_win_win_trades(rosters, "Nobody Here", SLOT_COUNTS) == []


# --- slot_name_to_id --------------------------------------------------
# Regression coverage for a real bug found live 2026-09-14: espn_api's own
# POSITION_MAP merges both directions (int->name AND name->int) into one
# dict, but its name-keyed side is incomplete/inconsistent for exactly
# the slots this feature needs - no "BE"/"IR"/"OP" entries at all, and
# slot 23 is keyed "FLEX" there instead of "RB/WR/TE" (this project's own
# naming, matching league.settings.position_slot_counts). Trusting that
# side directly KeyErrors on any real flex-slot lineup submission -
# slot_name_to_id() must invert the (complete, consistently-named)
# int-keyed side instead.

def test_slot_name_to_id_matches_live_verified_espn_values():
    # Locked in against the real numeric ids confirmed live against this
    # league's own ESPN account (see PROJECT_BRIEF) - a regression here
    # would silently target the wrong slot on a real lineup submission.
    assert slot_name_to_id("QB") == 0
    assert slot_name_to_id("RB") == 2
    assert slot_name_to_id("WR") == 4
    assert slot_name_to_id("TE") == 6
    assert slot_name_to_id("OP") == 7
    assert slot_name_to_id("D/ST") == 16
    assert slot_name_to_id("K") == 17
    assert slot_name_to_id("BE") == 20
    assert slot_name_to_id("IR") == 21
    assert slot_name_to_id("RB/WR/TE") == 23


def test_find_win_win_trades_2for1_throw_in_surfaces_a_real_mutual_upgrade():
    """A 2-for-1 (my 2 weakest players, both irrelevant bench filler, for
    their 1 real starter) can be a real win-win when the opponent has a
    near-equal backup at the position they're giving up (so it barely
    costs them) and my two throw-ins happen to fill two of their weak
    spots better than what they currently start there."""
    rosters = pd.DataFrame(
        [
            _roster_row("Mine", 1, "QB Mine", "QB", 30.0, ["QB", "BE"]),
            _roster_row("Mine", 2, "RB Mine", "RB", 70.0, ["RB", "BE"]),
            _roster_row("Mine", 3, "Bench QB", "QB", 8.0, ["QB", "BE"]),
            _roster_row("Mine", 4, "Bench RB", "RB", 9.0, ["RB", "BE"]),
            _roster_row("Team E", 5, "QB E1", "QB", 85.0, ["QB", "BE"]),
            _roster_row("Team E", 6, "QB E2", "QB", 83.0, ["QB", "BE"]),
            _roster_row("Team E", 7, "RB E", "RB", 5.0, ["RB", "BE"]),
        ]
    )
    results = find_win_win_trades(rosters, "Mine", SLOT_COUNTS, gain_threshold=1.0)
    two_for_one = [
        r for r in results
        if r["opponent"] == "Team E" and set(r["players_out"]) == {"Bench QB", "Bench RB"}
        and r["players_in"] in (["QB E1"], ["QB E2"])
    ]
    assert len(two_for_one) >= 1
    for trade in two_for_one:
        assert trade["my_gain"] > 1.0
        assert trade["their_gain"] > 1.0
