"""Unit tests for fantasy_football.war_room_data - pure logic only
(blend_trade_value, optimal_roster_value, evaluate_trade), no network/DB
calls per PROJECT_BRIEF testing requirements. The rest of war_room_data.py
(live FantasyPros/ESPN calls, SQLite reads) is exercised via
in-browser/AppTest validation instead, same convention as
dashboard_data.py's live-data functions."""
import json

import pandas as pd
import pytest

from fantasy_football.war_room_data import blend_trade_value, evaluate_trade, optimal_roster_value


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
